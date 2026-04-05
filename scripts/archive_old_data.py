#!/usr/bin/env python3
"""
Archive old candle data (>12 months)
EPIC-D002: Standalone Signal Service
GOV-ENV-001: Environment-aware configuration

Compresses old data to archive directory and removes from DB.
"""
import os
import sys
import subprocess
from datetime import datetime, timedelta
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

ARCHIVE_DIR = get_env('ARCHIVE_DIR', '/data/signal_history')
MONTHS_TO_KEEP = get_env_int('ARCHIVE_MONTHS_TO_KEEP', 12)

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

TABLES = ['candles_M1', 'candles_M5', 'candles_H1', 'candles_D1', 'ticks']


def archive_month(table: str, year: int, month: int, row_count: int):
    """Archive a single month of data"""
    archive_file = f"{ARCHIVE_DIR}/{table}_{year}_{month:02d}.sql"
    archive_gz = f"{archive_file}.tar.gz"

    if os.path.exists(archive_gz):
        logger.info(f"  Already archived: {archive_gz}")
        return False

    start_date = f"{year}-{month:02d}-01"
    if month == 12:
        end_date = f"{year + 1}-01-01"
    else:
        end_date = f"{year}-{month + 1:02d}-01"

    logger.info(f"  Archiving {table} {year}-{month:02d} ({row_count} rows)...")

    # Export to SQL
    dump_cmd = [
        "mysqldump",
        "-h", DB_CONFIG['host'],
        "-u", DB_CONFIG['user'],
        f"-p{DB_CONFIG['password']}",
        DB_CONFIG['database'], table,
        f"--where=timestamp >= '{start_date}' AND timestamp < '{end_date}'"
    ]

    with open(archive_file, "w") as f:
        result = subprocess.run(dump_cmd, stdout=f, stderr=subprocess.DEVNULL)

    if result.returncode != 0:
        logger.error(f"  mysqldump failed for {table} {year}-{month:02d}")
        if os.path.exists(archive_file):
            os.remove(archive_file)
        return False

    # Compress
    tar_result = subprocess.run(
        ["tar", "-czf", archive_gz, "-C", ARCHIVE_DIR, f"{table}_{year}_{month:02d}.sql"],
        capture_output=True
    )

    if tar_result.returncode != 0:
        logger.error(f"  tar failed: {tar_result.stderr}")
        return False

    # Remove uncompressed
    os.remove(archive_file)

    # Delete from DB
    conn = pymysql.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cursor:
            cursor.execute(f"""
                DELETE FROM {table}
                WHERE timestamp >= %s AND timestamp < %s
            """, (start_date, end_date))
            deleted = cursor.rowcount
            conn.commit()
            logger.info(f"  Archived and deleted {deleted} rows")
    finally:
        conn.close()

    return True


def main():
    os.makedirs(ARCHIVE_DIR, exist_ok=True)

    cutoff = datetime.now() - timedelta(days=MONTHS_TO_KEEP * 30)
    logger.info(f"Archiving data older than {cutoff.strftime('%Y-%m-%d')}")

    conn = pymysql.connect(**DB_CONFIG)

    try:
        for table in TABLES:
            logger.info(f"Processing {table}...")

            with conn.cursor() as cursor:
                cursor.execute(f"""
                    SELECT
                        YEAR(timestamp) as yr,
                        MONTH(timestamp) as mo,
                        COUNT(*) as cnt
                    FROM {table}
                    WHERE timestamp < %s
                    GROUP BY YEAR(timestamp), MONTH(timestamp)
                    ORDER BY yr, mo
                """, (cutoff,))

                months = cursor.fetchall()

            if not months:
                logger.info(f"  No data to archive")
                continue

            for row in months:
                archive_month(table, row['yr'], row['mo'], row['cnt'])

    finally:
        conn.close()

    logger.info("Archive complete!")


if __name__ == '__main__':
    main()
