"""
Database writer for candles and ticks
EPIC-D002: Standalone Signal Service
GOV-ENV-001: Environment-aware configuration

Persists candles to MariaDB for backtesting.
Includes archive functionality for data >12 months.
"""
import asyncio
import logging
import os
import sys
import subprocess
from datetime import datetime, timedelta
from typing import List, Optional
from queue import Queue
import threading
from pathlib import Path

import pymysql
from pymysql.cursors import DictCursor

# Setup - use relative path from module location
UTILS_DIR = Path(__file__).parent.absolute()
BASE_DIR = UTILS_DIR.parent
sys.path.insert(0, str(BASE_DIR))

from env_config import get_env, get_db_config
from models.candle import Candle, Timeframe
from models.tick import SignalTick

logger = logging.getLogger("signal-service.db")

# Archive directory (environment-aware)
ARCHIVE_DIR = get_env('ARCHIVE_DIR', '/data/signal_history')


class DBWriter:
    """
    Async-safe database writer for candles and ticks.

    Uses a background thread with queue for non-blocking writes.
    """

    def __init__(
        self,
        host: str = None,
        port: int = None,
        user: str = None,
        password: str = None,
        database: str = None,
        write_ticks: bool = False  # Optional tick storage
    ):
        # Use env_config defaults if not explicitly provided
        db_cfg = get_db_config()
        host = host if host is not None else db_cfg['host']
        port = port if port is not None else db_cfg['port']
        user = user if user is not None else db_cfg['user']
        password = password if password is not None else db_cfg['password']
        database = database if database is not None else db_cfg['database']
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database
        self.write_ticks = write_ticks

        # Write queue
        self._queue: Queue = Queue()
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # Stats
        self.candles_written = 0
        self.ticks_written = 0
        self.errors = 0

    def _get_connection(self):
        """Get database connection"""
        return pymysql.connect(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            database=self.database,
            charset='utf8mb4',
            cursorclass=DictCursor,
            autocommit=True
        )

    def start(self):
        """Start background writer thread"""
        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(target=self._writer_loop, daemon=True)
        self._thread.start()
        logger.info("DB writer started")

    def stop(self):
        """Stop background writer"""
        self._running = False
        if self._thread:
            self._queue.put(None)  # Sentinel to wake up thread
            self._thread.join(timeout=5)
        logger.info(f"DB writer stopped. Candles: {self.candles_written}, Ticks: {self.ticks_written}")

    def write_candle(self, candle: Candle):
        """Queue a candle for writing"""
        self._queue.put(("candle", candle))

    def write_tick(self, tick: SignalTick):
        """Queue a tick for writing (if enabled)"""
        if self.write_ticks:
            self._queue.put(("tick", tick))

    def _writer_loop(self):
        """Background writer thread"""
        conn = None
        batch_candles: List[Candle] = []
        batch_ticks: List[SignalTick] = []
        last_flush = datetime.now()

        while self._running:
            try:
                # Get item with timeout
                try:
                    item = self._queue.get(timeout=1.0)
                except:
                    item = None

                if item is None:
                    # Timeout or shutdown - flush batch
                    pass
                elif item[0] == "candle":
                    batch_candles.append(item[1])
                elif item[0] == "tick":
                    batch_ticks.append(item[1])

                # Flush every second or when batch is large
                now = datetime.now()
                should_flush = (
                    (now - last_flush).total_seconds() >= 1.0 or
                    len(batch_candles) >= 100 or
                    len(batch_ticks) >= 1000
                )

                if should_flush and (batch_candles or batch_ticks):
                    if conn is None:
                        conn = self._get_connection()

                    if batch_candles:
                        self._flush_candles(conn, batch_candles)
                        batch_candles = []

                    if batch_ticks:
                        self._flush_ticks(conn, batch_ticks)
                        batch_ticks = []

                    last_flush = now

            except Exception as e:
                logger.error(f"DB writer error: {e}")
                self.errors += 1
                conn = None  # Reconnect on next iteration

        # Final flush on shutdown
        if batch_candles or batch_ticks:
            try:
                if conn is None:
                    conn = self._get_connection()
                if batch_candles:
                    self._flush_candles(conn, batch_candles)
                if batch_ticks:
                    self._flush_ticks(conn, batch_ticks)
            except Exception as e:
                logger.error(f"Final flush error: {e}")

        if conn:
            conn.close()

    def _flush_candles(self, conn, candles: List[Candle]):
        """Write batch of candles to DB"""
        if not candles:
            return

        # Group by timeframe
        by_tf = {}
        for c in candles:
            if c.timeframe not in by_tf:
                by_tf[c.timeframe] = []
            by_tf[c.timeframe].append(c)

        with conn.cursor() as cursor:
            for tf, tf_candles in by_tf.items():
                table = tf.table_name

                sql = f"""
                INSERT INTO {table}
                (instrument, timestamp, open, high, low, close, volume, complete, source)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    high = GREATEST(high, VALUES(high)),
                    low = LEAST(low, VALUES(low)),
                    close = VALUES(close),
                    volume = volume + VALUES(volume),
                    complete = VALUES(complete)
                """

                values = [
                    (c.instrument, c.timestamp, c.open, c.high, c.low,
                     c.close, c.volume, c.complete, "signal_service")
                    for c in tf_candles
                ]

                cursor.executemany(sql, values)
                self.candles_written += len(tf_candles)

        logger.debug(f"Flushed {len(candles)} candles")

    def _flush_ticks(self, conn, ticks: List[SignalTick]):
        """Write batch of ticks to DB"""
        if not ticks:
            return

        with conn.cursor() as cursor:
            sql = """
            INSERT INTO ticks (instrument, timestamp, bid, ask, source)
            VALUES (%s, %s, %s, %s, %s)
            """

            values = [
                (t.instrument, t.timestamp, t.bid, t.ask, t.source.value)
                for t in ticks
            ]

            cursor.executemany(sql, values)
            self.ticks_written += len(ticks)

        logger.debug(f"Flushed {len(ticks)} ticks")


def archive_old_data(
    database: str = None,
    months_to_keep: int = 12,
    archive_dir: str = ARCHIVE_DIR
):
    """
    Archive candle data older than N months to compressed SQL files.

    Archives to: /data/signal_history/candles_YYYY_MM.sql.tar.gz
    """
    # Use env_config database if not specified
    if database is None:
        database = get_db_config()['database']
    os.makedirs(archive_dir, exist_ok=True)

    cutoff = datetime.now() - timedelta(days=months_to_keep * 30)
    cutoff_str = cutoff.strftime("%Y-%m-%d")

    logger.info(f"Archiving data older than {cutoff_str}")

    # Get months to archive (using env_config)
    db_cfg = get_db_config()
    conn = pymysql.connect(
        host=db_cfg['host'],
        port=db_cfg['port'],
        user=db_cfg['user'],
        password=db_cfg['password'],
        database=database
    )

    try:
        with conn.cursor() as cursor:
            # Find distinct year-months older than cutoff
            for table in ["candles_M1", "candles_M5", "candles_H1", "candles_D1"]:
                cursor.execute(f"""
                    SELECT DISTINCT
                        YEAR(timestamp) as yr,
                        MONTH(timestamp) as mo,
                        COUNT(*) as cnt
                    FROM {table}
                    WHERE timestamp < %s
                    GROUP BY YEAR(timestamp), MONTH(timestamp)
                    ORDER BY yr, mo
                """, (cutoff_str,))

                for row in cursor.fetchall():
                    yr, mo, cnt = row[0], row[1], row[2]
                    archive_month(database, table, yr, mo, cnt, archive_dir)

    finally:
        conn.close()


def archive_month(
    database: str,
    table: str,
    year: int,
    month: int,
    row_count: int,
    archive_dir: str
):
    """Archive a single month of data"""
    archive_file = f"{archive_dir}/{table}_{year}_{month:02d}.sql"
    archive_gz = f"{archive_file}.tar.gz"

    if os.path.exists(archive_gz):
        logger.info(f"Archive exists: {archive_gz}")
        return

    logger.info(f"Archiving {table} {year}-{month:02d} ({row_count} rows)...")

    # Export to SQL
    start_date = f"{year}-{month:02d}-01"
    if month == 12:
        end_date = f"{year + 1}-01-01"
    else:
        end_date = f"{year}-{month + 1:02d}-01"

    # Use env_config for database credentials
    db_cfg = get_db_config()
    dump_cmd = [
        "mysqldump",
        "-h", db_cfg['host'],
        "-P", str(db_cfg['port']),
        "-u", db_cfg['user'],
        f"-p{db_cfg['password']}",
        database, table,
        f"--where=timestamp >= '{start_date}' AND timestamp < '{end_date}'"
    ]

    with open(archive_file, "w") as f:
        subprocess.run(dump_cmd, stdout=f, stderr=subprocess.DEVNULL)

    # Compress
    subprocess.run(
        ["tar", "-czf", archive_gz, "-C", archive_dir, f"{table}_{year}_{month:02d}.sql"],
        check=True
    )

    # Remove uncompressed
    os.remove(archive_file)

    # Delete from DB using env_config
    from env_config import get_db_config
    db_cfg = get_db_config()
    conn = pymysql.connect(
        host=db_cfg['host'],
        port=db_cfg['port'],
        user=db_cfg['user'],
        password=db_cfg['password'],
        database=database
    )
    try:
        with conn.cursor() as cursor:
            cursor.execute(f"""
                DELETE FROM {table}
                WHERE timestamp >= %s AND timestamp < %s
            """, (start_date, end_date))
            conn.commit()
            logger.info(f"Archived and deleted {cursor.rowcount} rows from {table}")
    finally:
        conn.close()


if __name__ == "__main__":
    # Run archiver standalone (env_config already loaded at module level)
    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) > 1 and sys.argv[1] == "archive":
        archive_old_data()
    else:
        print("Usage: python db_writer.py archive")
