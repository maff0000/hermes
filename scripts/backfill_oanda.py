#!/usr/bin/env python3
"""
OANDA Historical Candle Backfill
EPIC-D002: Standalone Signal Service
GOV-ENV-001: Environment-aware configuration

Backfills M1, M5, H1, D1 candles from OANDA REST API.
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta
from typing import List, Optional
import argparse
import logging
from pathlib import Path

import httpx
import pymysql
from pymysql.cursors import DictCursor

# Setup - use relative path from script location
SCRIPT_DIR = Path(__file__).parent.absolute()
BASE_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(BASE_DIR))

from env_config import get_env, get_env_int, get_db_config

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# OANDA config (shared across environments)
API_KEY = get_env('OANDA_API_KEY', '')
ACCOUNT_ID = get_env('OANDA_ACCOUNT_ID', '')
BASE_URL = 'https://api-fxtrade.oanda.com'

# DB config (environment-aware via GOV-ENV-001)
_db = get_db_config()
DB_CONFIG = {
    'host': _db['host'],
    'port': _db['port'],
    'user': _db['user'],
    'password': _db['password'],
    'database': _db['database'],
    'charset': 'utf8mb4',
    'cursorclass': DictCursor
}

# Timeframe mapping
GRANULARITY_MAP = {
    'M1': 'M1',
    'M5': 'M5',
    'H1': 'H1',
    'D1': 'D'
}


async def fetch_candles(
    client: httpx.AsyncClient,
    instrument: str,
    granularity: str,
    from_time: datetime,
    to_time: datetime,
    count: int = 5000
) -> List[dict]:
    """Fetch candles from OANDA API"""
    url = f'{BASE_URL}/v3/instruments/{instrument}/candles'
    params = {
        'granularity': GRANULARITY_MAP.get(granularity, granularity),
        'from': from_time.strftime('%Y-%m-%dT%H:%M:%SZ'),
        'to': to_time.strftime('%Y-%m-%dT%H:%M:%SZ'),
        'price': 'M'  # Mid prices
    }

    response = await client.get(url, params=params)
    if response.status_code != 200:
        logger.error(f"API error: {response.status_code} {response.text}")
        return []

    data = response.json()
    return data.get('candles', [])


def parse_candle(instrument: str, candle: dict, timeframe: str = None) -> Optional[dict]:
    """Parse OANDA candle to our format.

    For D1 candles, adjusts timestamp to represent the trading day that COMPLETED.
    OANDA timestamps D1 candles at 5PM NY (start of period), but we want to store
    the date when trading ended. E.g., candle starting Mon 5PM NY ends Tue 5PM NY,
    so we store it as Tuesday's candle.
    """
    try:
        mid = candle.get('mid', {})
        timestamp = datetime.fromisoformat(candle['time'].replace('Z', '+00:00')).replace(tzinfo=None)

        # For D1 candles: shift timestamp to represent completion date
        # OANDA returns candle START time (5PM NY previous day)
        # We want the DATE when trading completed (next calendar day at midnight)
        if timeframe == 'D1':
            # Add 1 day and truncate to midnight to get completion date
            timestamp = (timestamp + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

        return {
            'instrument': instrument,
            'timestamp': timestamp,
            'open': float(mid.get('o', 0)),
            'high': float(mid.get('h', 0)),
            'low': float(mid.get('l', 0)),
            'close': float(mid.get('c', 0)),
            'volume': candle.get('volume', 0),
            'complete': candle.get('complete', True)
        }
    except Exception as e:
        logger.warning(f"Parse error: {e}")
        return None


def insert_candles(table: str, candles: List[dict]):
    """Insert candles to database"""
    if not candles:
        return 0

    conn = pymysql.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cursor:
            sql = f"""
            INSERT INTO {table}
            (instrument, timestamp, open, high, low, close, volume, complete, source)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'oanda_backfill')
            ON DUPLICATE KEY UPDATE
                open = VALUES(open),
                high = VALUES(high),
                low = VALUES(low),
                close = VALUES(close),
                volume = VALUES(volume),
                complete = VALUES(complete)
            """

            values = [
                (c['instrument'], c['timestamp'], c['open'], c['high'],
                 c['low'], c['close'], c['volume'], c['complete'])
                for c in candles
            ]

            cursor.executemany(sql, values)
            conn.commit()
            return cursor.rowcount
    finally:
        conn.close()


async def backfill_instrument(
    instrument: str,
    timeframe: str,
    start_date: datetime,
    end_date: datetime
):
    """Backfill a single instrument/timeframe"""
    table = f'candles_{timeframe}'
    logger.info(f"Backfilling {instrument} {timeframe} from {start_date} to {end_date}")

    headers = {'Authorization': f'Bearer {API_KEY}'}

    async with httpx.AsyncClient(headers=headers, timeout=60.0) as client:
        current = start_date
        total_inserted = 0

        # Chunk by day for M1, week for others
        if timeframe == 'M1':
            chunk_days = 1
        elif timeframe == 'M5':
            chunk_days = 7
        else:
            chunk_days = 30

        while current < end_date:
            chunk_end = min(current + timedelta(days=chunk_days), end_date)

            candles = await fetch_candles(client, instrument, timeframe, current, chunk_end)

            if candles:
                parsed = [parse_candle(instrument, c, timeframe) for c in candles]
                parsed = [c for c in parsed if c is not None]

                if parsed:
                    inserted = insert_candles(table, parsed)
                    total_inserted += len(parsed)
                    logger.info(f"  {current.date()} - {chunk_end.date()}: {len(parsed)} candles")

            current = chunk_end
            await asyncio.sleep(0.5)  # Rate limiting

        logger.info(f"Completed {instrument} {timeframe}: {total_inserted} total candles")
        return total_inserted


async def main():
    parser = argparse.ArgumentParser(description='Backfill OANDA candles')
    parser.add_argument('--instrument', '-i', default='XAU_USD', help='Instrument (default: XAU_USD)')
    parser.add_argument('--timeframe', '-t', default='M5', choices=['M1', 'M5', 'H1', 'D1'])
    parser.add_argument('--days', '-d', type=int, default=30, help='Days to backfill (default: 30)')
    parser.add_argument('--all-instruments', '-a', action='store_true', help='Backfill all instruments')
    parser.add_argument('--all-timeframes', action='store_true', help='Backfill all timeframes')

    args = parser.parse_args()

    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=args.days)

    instruments = ['XAU_USD', 'XAG_USD', 'XPT_USD', 'XCU_USD'] if args.all_instruments else [args.instrument]
    timeframes = ['M1', 'M5', 'H1', 'D1'] if args.all_timeframes else [args.timeframe]

    for instrument in instruments:
        for timeframe in timeframes:
            await backfill_instrument(instrument, timeframe, start_date, end_date)

    logger.info("Backfill complete!")


if __name__ == '__main__':
    asyncio.run(main())
