#!/usr/bin/env python3
"""
Backfill M15 signals from existing M15 candles.

The M15 candles exist in candles_M15 but signals were never computed
because main.py gated signal computation to M5 only.

This script:
1. Finds the last M15 signal timestamp per instrument
2. Loads M15 candle history from that point forward
3. Computes signals for each candle
4. Writes them to the signals table

Safe to run multiple times — uses UPSERT.
"""

import os
import sys
import logging
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from env_config import get_db_config
from signal_builder import SignalComputer, SignalPublisher, TIMEFRAMES
from utils.redis_publisher import RedisPublisher
import pymysql
import pymysql.cursors

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')
logger = logging.getLogger("backfill-m15")


def get_instruments(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT instrument FROM candles_M15 ORDER BY instrument")
        return [r['instrument'] for r in cur.fetchall()]


def get_last_m15_signal_ts(conn, instrument):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT MAX(timestamp) as ts FROM signals WHERE instrument=%s AND timeframe='M15'",
            (instrument,),
        )
        row = cur.fetchone()
        return row['ts'] if row and row['ts'] else None


def fetch_m15_candles(conn, instrument, after_ts=None, limit=5000):
    with conn.cursor() as cur:
        if after_ts:
            cur.execute(
                """SELECT timestamp, open, high, low, close, volume
                   FROM candles_M15 WHERE instrument=%s AND timestamp > %s
                   ORDER BY timestamp ASC LIMIT %s""",
                (instrument, after_ts, limit),
            )
        else:
            cur.execute(
                """SELECT timestamp, open, high, low, close, volume
                   FROM candles_M15 WHERE instrument=%s
                   ORDER BY timestamp ASC LIMIT %s""",
                (instrument, limit),
            )
        return cur.fetchall()


def fetch_m15_history(conn, instrument, before_ts, limit=300):
    with conn.cursor() as cur:
        cur.execute(
            """SELECT timestamp, open, high, low, close, volume
               FROM candles_M15 WHERE instrument=%s AND timestamp <= %s
               ORDER BY timestamp DESC LIMIT %s""",
            (instrument, before_ts, limit),
        )
        return list(reversed(cur.fetchall()))


def main():
    db_cfg = get_db_config()
    conn = pymysql.connect(**db_cfg, cursorclass=pymysql.cursors.DictCursor)

    redis_pub = RedisPublisher()
    redis_pub.connect()

    computer = SignalComputer()
    publisher = SignalPublisher(redis_publisher=redis_pub)

    instruments = get_instruments(conn)
    logger.info(f"Instruments with M15 candles: {instruments}")

    total = 0

    for instrument in instruments:
        last_ts = get_last_m15_signal_ts(conn, instrument)
        logger.info(f"[{instrument}] Last M15 signal: {last_ts}")

        candles = fetch_m15_candles(conn, instrument, after_ts=last_ts, limit=5000)
        if not candles:
            logger.info(f"[{instrument}] No M15 candles to backfill")
            continue

        logger.info(f"[{instrument}] Backfilling {len(candles)} M15 candles from {candles[0]['timestamp']} to {candles[-1]['timestamp']}")

        computed = 0
        for candle_row in candles:
            history = fetch_m15_history(conn, instrument, candle_row['timestamp'], limit=300)
            if len(history) < 50:
                continue

            class M15Candle:
                def __init__(self, data, instr):
                    self.instrument = instr
                    self.timestamp = data['timestamp']
                    self.open = float(data['open'])
                    self.high = float(data['high'])
                    self.low = float(data['low'])
                    self.close = float(data['close'])
                    self.volume = data['volume']
                    self.timeframe = 'M15'

            candle_obj = M15Candle(candle_row, instrument)

            try:
                signal = computer.compute_signal(candle_obj, history)
                if signal:
                    publisher.publish_signal(signal)
                    computed += 1
            except Exception as e:
                logger.debug(f"Signal compute error at {candle_row['timestamp']}: {e}")

        logger.info(f"[{instrument}] Computed {computed} M15 signals")
        total += computed

    conn.close()
    logger.info(f"BACKFILL COMPLETE: {total} M15 signals computed across {len(instruments)} instruments")


if __name__ == "__main__":
    main()
