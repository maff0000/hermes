#!/usr/bin/env python3
"""
Backfill from tradingSandbox to tradingSignals
EPIC-D002: Standalone Signal Service
GOV-ENV-001: Environment-aware configuration

Copies existing candle data from tradingSandbox to tradingSignals.
"""
import os
import sys
from datetime import datetime
import logging
from pathlib import Path

import pymysql
from pymysql.cursors import DictCursor

# Setup - use relative path from script location
SCRIPT_DIR = Path(__file__).parent.absolute()
BASE_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(BASE_DIR))

from env_config import get_env, get_env_int, get_db_config

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Get base DB config (environment-aware via GOV-ENV-001)
_db = get_db_config()

# Source DB (tradingSandbox)
SOURCE_DB = {
    'host': _db['host'],
    'port': _db['port'],
    'user': _db['user'],
    'password': _db['password'],
    'database': 'tradingSandbox',
    'charset': 'utf8mb4',
    'cursorclass': DictCursor
}

# Target DB (tradingSignals)
TARGET_DB = {
    'host': _db['host'],
    'port': _db['port'],
    'user': _db['user'],
    'password': _db['password'],
    'database': _db['database'],
    'charset': 'utf8mb4',
    'cursorclass': DictCursor
}

TABLES = ['candles_M1', 'candles_M5', 'candles_H1', 'candles_D1']


def copy_table(table: str, batch_size: int = 10000):
    """Copy a table from source to target"""
    logger.info(f"Copying {table}...")

    source_conn = pymysql.connect(**SOURCE_DB)
    target_conn = pymysql.connect(**TARGET_DB)

    try:
        # Get count from source
        with source_conn.cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) as cnt FROM {table}")
            total = cursor.fetchone()['cnt']
            logger.info(f"  Source has {total} rows")

        if total == 0:
            logger.info(f"  Skipping - no data")
            return 0

        # Get existing count in target
        with target_conn.cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) as cnt FROM {table}")
            existing = cursor.fetchone()['cnt']
            if existing > 0:
                logger.info(f"  Target already has {existing} rows")

        # Copy in batches
        offset = 0
        copied = 0

        while offset < total:
            with source_conn.cursor() as src_cursor:
                src_cursor.execute(f"""
                    SELECT instrument, timestamp, open, high, low, close, volume, complete, source
                    FROM {table}
                    ORDER BY id
                    LIMIT {batch_size} OFFSET {offset}
                """)
                rows = src_cursor.fetchall()

            if not rows:
                break

            with target_conn.cursor() as tgt_cursor:
                sql = f"""
                INSERT INTO {table}
                (instrument, timestamp, open, high, low, close, volume, complete, source)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    open = VALUES(open),
                    high = VALUES(high),
                    low = VALUES(low),
                    close = VALUES(close),
                    volume = VALUES(volume)
                """

                values = [
                    (r['instrument'], r['timestamp'], r['open'], r['high'],
                     r['low'], r['close'], r['volume'], r['complete'],
                     r.get('source', 'sandbox_backfill'))
                    for r in rows
                ]

                tgt_cursor.executemany(sql, values)
                target_conn.commit()
                copied += len(rows)

            offset += batch_size
            logger.info(f"  Copied {copied}/{total} rows...")

        logger.info(f"  Completed: {copied} rows copied")
        return copied

    finally:
        source_conn.close()
        target_conn.close()


def main():
    logger.info("=" * 60)
    logger.info("Backfilling from tradingSandbox to tradingSignals")
    logger.info("=" * 60)

    total_copied = 0
    for table in TABLES:
        copied = copy_table(table)
        total_copied += copied

    logger.info("=" * 60)
    logger.info(f"COMPLETE: {total_copied} total rows copied")
    logger.info("=" * 60)


if __name__ == '__main__':
    main()
