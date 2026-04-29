#!/usr/bin/env python3
"""
M15 Signal Backfill Script
Adapted from backfill_signals.py (M5) for M15 timeframe.
GOV-ENV-001: Environment-aware configuration

Backfills historical M15 signals from candles_M15 data.
Computes all indicators (RSI, EMA, ATR, regime, session) for candles without signals.

Usage:
    python backfill_signals_m15.py                    # Backfill all missing
    python backfill_signals_m15.py -i XAU_USD         # Single instrument
    python backfill_signals_m15.py --days 30          # Last 30 days only
    python backfill_signals_m15.py --batch 1000       # Custom batch size
    python backfill_signals_m15.py --dry-run          # Preview only
"""

import sys
import argparse
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional
from pathlib import Path

# Setup paths
SCRIPT_DIR = Path(__file__).parent.absolute()
BASE_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(BASE_DIR))

import pymysql
import pymysql.cursors

from env_config import get_db_config, get_env
from utils.indicators import calculate_rsi, calculate_ema, get_ema_state
from utils.atr_calculator import calculate_atr

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

TIMEFRAME = 'M15'
CANDLE_TABLE = 'candles_M15'

# EMA periods to compute
EMA_PERIODS = [9, 12, 20, 21, 26, 50, 200]

# EMA pairs for state computation
EMA_PAIRS = [
    (9, 21),    # Fast scalping
    (12, 26),   # MACD-style
    (20, 50),   # Swing trading
    (50, 200),  # Long-term trend
]

# Minimum candles needed for full signal computation
MIN_HISTORY = 200


def get_session(timestamp: datetime) -> str:
    """Determine trading session from timestamp (UTC)."""
    hour = timestamp.hour
    if 22 <= hour or hour < 7:
        return 'asia'
    elif 7 <= hour < 12:
        return 'london'
    elif 12 <= hour < 17:
        return 'newyork'
    else:
        return 'late_ny'


def determine_regime(ema_states: dict) -> str:
    """Determine market regime from EMA states."""
    bullish = sum(1 for s in ema_states.values() if s == 'above')
    bearish = sum(1 for s in ema_states.values() if s == 'below')
    total = len(ema_states)

    if bullish >= total * 0.75:
        return 'BULL_TREND'
    elif bearish >= total * 0.75:
        return 'BEAR_TREND'
    elif bullish > 0 and bearish > 0:
        return 'TRANSITION'
    else:
        return 'LOW_VOLATILITY'


def compute_signal_from_history(candle: dict, history: List[dict]) -> Optional[dict]:
    """
    Compute all signal indicators from a candle and its history.

    Args:
        candle: Current candle dict with OHLCV
        history: List of prior candles (oldest first)

    Returns:
        Signal dict with all computed indicators
    """
    if len(history) < 50:
        return None

    # Extract close prices
    closes = [float(c['close']) for c in history]
    closes.append(float(candle['close']))

    # RSI
    rsi_14 = round(calculate_rsi(closes, 14), 2)

    # EMAs
    emas = {}
    for period in EMA_PERIODS:
        if len(closes) >= period:
            emas[period] = round(calculate_ema(closes, period), 5)
        else:
            emas[period] = round(float(candle['close']), 5)

    # EMA states
    ema_states = {}
    state_values = {}
    for fast, slow in EMA_PAIRS:
        state = get_ema_state(emas.get(fast, 0), emas.get(slow, 0))
        key = f'ema_{fast}_{slow}_state'
        ema_states[key] = state
        state_values[f'{fast}_{slow}'] = state

    # ATR
    atr_14 = 0.0
    if len(history) >= 15:
        atr_history = history[-15:]
        atr_history.append({
            'open': candle['open'],
            'high': candle['high'],
            'low': candle['low'],
            'close': candle['close']
        })
        atr_val = calculate_atr(atr_history, 14)
        atr_14 = round(atr_val, 5) if atr_val else 0.0

    # Volume ratio (use last 20 candles)
    volumes = [c.get('volume', 0) or 0 for c in history[-20:]]
    avg_vol = sum(volumes) / len(volumes) if volumes else 1
    volume_ratio = round((candle.get('volume', 0) or 1) / max(avg_vol, 1), 4)

    # Regime
    regime = determine_regime(state_values)

    # Session
    session = get_session(candle['timestamp'])

    return {
        'instrument': candle['instrument'],
        'timestamp': candle['timestamp'],
        'timeframe': TIMEFRAME,
        'price_close': round(float(candle['close']), 5),
        'volume': candle.get('volume', 0) or 0,
        'volume_ratio': volume_ratio,
        'rsi_14': rsi_14,
        'ema_9': emas.get(9, 0),
        'ema_21': emas.get(21, 0),
        'ema_12': emas.get(12, 0),
        'ema_26': emas.get(26, 0),
        'ema_20': emas.get(20, 0),
        'ema_50': emas.get(50, 0),
        'ema_200': emas.get(200, 0),
        'ema_9_21_state': ema_states.get('ema_9_21_state', 'cross'),
        'ema_12_26_state': ema_states.get('ema_12_26_state', 'cross'),
        'ema_20_50_state': ema_states.get('ema_20_50_state', 'cross'),
        'ema_50_200_state': ema_states.get('ema_50_200_state', 'cross'),
        'atr_14': atr_14,
        'regime': regime,
        'session': session,
        'version': 1
    }


class SignalBackfillerM15:
    """Backfills historical M15 signals from candles_M15 data."""

    def __init__(self, batch_size: int = 500):
        self.db_config = get_db_config()
        self.batch_size = batch_size
        self.conn = None
        self.stats = {
            'processed': 0,
            'created': 0,
            'skipped': 0,
            'errors': 0
        }

    def _get_connection(self):
        """Get database connection."""
        if self.conn is None or not self.conn.open:
            self.conn = pymysql.connect(
                host=self.db_config['host'],
                port=self.db_config['port'],
                user=self.db_config['user'],
                password=self.db_config['password'],
                database=self.db_config['database'],
                charset='utf8mb4',
                cursorclass=pymysql.cursors.DictCursor,
                autocommit=False
            )
        return self.conn

    def get_instruments(self) -> List[str]:
        """Get list of instruments from candles_M15."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(f"SELECT DISTINCT instrument FROM {CANDLE_TABLE} ORDER BY instrument")
        instruments = [row['instrument'] for row in cursor.fetchall()]
        cursor.close()
        return instruments

    def get_missing_signal_timestamps(
        self,
        instrument: str,
        start_date: datetime = None,
        limit: int = 50000
    ) -> List[datetime]:
        """
        Get timestamps of M15 candles without corresponding M15 signals.

        Uses LEFT JOIN filtered on timeframe='M15' to avoid matching M5 signals.
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        sql = f"""
            SELECT c.`timestamp`
            FROM {CANDLE_TABLE} c
            LEFT JOIN signals s
                ON c.instrument COLLATE utf8mb4_unicode_ci = s.instrument
                AND c.`timestamp` = s.`timestamp`
                AND s.timeframe = '{TIMEFRAME}'
            WHERE c.instrument = %s
              AND s.id IS NULL
              AND c.complete = 1
        """
        params = [instrument]

        if start_date:
            sql += " AND c.`timestamp` >= %s"
            params.append(start_date)

        sql += " ORDER BY c.`timestamp` ASC LIMIT %s"
        params.append(limit)

        cursor.execute(sql, params)
        timestamps = [row['timestamp'] for row in cursor.fetchall()]
        cursor.close()

        return timestamps

    def get_candle_history(
        self,
        instrument: str,
        timestamp: datetime,
        lookback: int = MIN_HISTORY + 10
    ) -> List[dict]:
        """
        Get M15 candle history before a timestamp.

        Returns list of candles (oldest first).
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(f"""
            SELECT `timestamp`, open, high, low, close, volume
            FROM {CANDLE_TABLE}
            WHERE instrument = %s AND `timestamp` < %s
            ORDER BY `timestamp` DESC
            LIMIT %s
        """, (instrument, timestamp, lookback))

        rows = cursor.fetchall()
        cursor.close()

        # Return in chronological order (oldest first)
        return list(reversed(rows))

    def get_candle(self, instrument: str, timestamp: datetime) -> Optional[dict]:
        """Get a specific M15 candle."""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(f"""
            SELECT instrument, `timestamp`, open, high, low, close, volume, complete
            FROM {CANDLE_TABLE}
            WHERE instrument = %s AND `timestamp` = %s
        """, (instrument, timestamp))

        row = cursor.fetchone()
        cursor.close()
        return row

    def insert_signals_batch(self, signals: List[dict]) -> int:
        """Insert batch of M15 signals to database."""
        if not signals:
            return 0

        conn = self._get_connection()
        cursor = conn.cursor()

        sql = """
        INSERT INTO signals
        (instrument, `timestamp`, timeframe, price_close, volume, volume_ratio,
         rsi_14, ema_9, ema_21, ema_12, ema_26, ema_20, ema_50, ema_200,
         ema_9_21_state, ema_12_26_state, ema_20_50_state, ema_50_200_state,
         atr_14, regime, session, version)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            price_close = VALUES(price_close),
            rsi_14 = VALUES(rsi_14),
            regime = VALUES(regime)
        """

        values = [
            (
                s['instrument'], s['timestamp'], s['timeframe'],
                s['price_close'], s['volume'], s['volume_ratio'],
                s['rsi_14'], s['ema_9'], s['ema_21'], s['ema_12'],
                s['ema_26'], s['ema_20'], s['ema_50'], s['ema_200'],
                s['ema_9_21_state'], s['ema_12_26_state'],
                s['ema_20_50_state'], s['ema_50_200_state'],
                s['atr_14'], s['regime'], s['session'], s['version']
            )
            for s in signals
        ]

        cursor.executemany(sql, values)
        conn.commit()
        inserted = cursor.rowcount
        cursor.close()

        return inserted

    def backfill_instrument(
        self,
        instrument: str,
        start_date: datetime = None,
        dry_run: bool = False,
        progress_interval: int = 1000
    ) -> dict:
        """Backfill M15 signals for a single instrument."""
        logger.info(f"[{instrument}] Starting M15 backfill...")

        # Get missing timestamps
        missing = self.get_missing_signal_timestamps(instrument, start_date)
        total_missing = len(missing)

        if total_missing == 0:
            logger.info(f"[{instrument}] No missing M15 signals found")
            return {'processed': 0, 'created': 0, 'skipped': 0, 'errors': 0}

        logger.info(f"[{instrument}] Found {total_missing} M15 candles without signals")

        stats = {'processed': 0, 'created': 0, 'skipped': 0, 'errors': 0}
        signal_batch = []

        # History cache for sequential optimization
        history_cache = {}

        for i, timestamp in enumerate(missing):
            try:
                # Get the candle
                candle = self.get_candle(instrument, timestamp)
                if not candle:
                    stats['skipped'] += 1
                    continue

                # Get history (use cache if sequential)
                if instrument in history_cache:
                    prev_hist = history_cache[instrument]
                    if len(prev_hist) > 0 and len(prev_hist) < MIN_HISTORY + 50:
                        # Fetch fresh history
                        history = self.get_candle_history(instrument, timestamp)
                    else:
                        # Shift cache
                        history = prev_hist[1:]
                        prev_candle = self.get_candle(instrument, missing[i-1]) if i > 0 else None
                        if prev_candle:
                            history.append({
                                'timestamp': prev_candle['timestamp'],
                                'open': prev_candle['open'],
                                'high': prev_candle['high'],
                                'low': prev_candle['low'],
                                'close': prev_candle['close'],
                                'volume': prev_candle.get('volume', 0)
                            })
                else:
                    history = self.get_candle_history(instrument, timestamp)

                history_cache[instrument] = history

                # Compute signal
                if len(history) < 50:
                    stats['skipped'] += 1
                    continue

                signal = compute_signal_from_history(candle, history)
                if signal is None:
                    stats['skipped'] += 1
                    continue

                signal_batch.append(signal)
                stats['processed'] += 1

                # Batch insert
                if len(signal_batch) >= self.batch_size:
                    if not dry_run:
                        inserted = self.insert_signals_batch(signal_batch)
                        stats['created'] += inserted
                    else:
                        stats['created'] += len(signal_batch)
                    signal_batch = []

                # Progress logging
                if stats['processed'] % progress_interval == 0:
                    pct = (i + 1) / total_missing * 100
                    logger.info(f"[{instrument}] Progress: {stats['processed']}/{total_missing} ({pct:.1f}%)")

            except Exception as e:
                logger.error(f"[{instrument}] Error at {timestamp}: {e}")
                stats['errors'] += 1

        # Insert remaining batch
        if signal_batch:
            if not dry_run:
                inserted = self.insert_signals_batch(signal_batch)
                stats['created'] += inserted
            else:
                stats['created'] += len(signal_batch)

        logger.info(f"[{instrument}] M15 Completed: {stats['processed']} processed, "
                   f"{stats['created']} created, {stats['skipped']} skipped, "
                   f"{stats['errors']} errors")

        return stats

    def backfill_all(
        self,
        instruments: List[str] = None,
        start_date: datetime = None,
        dry_run: bool = False
    ) -> dict:
        """Backfill M15 signals for all instruments."""
        if instruments is None:
            instruments = self.get_instruments()

        logger.info(f"Starting M15 backfill for {len(instruments)} instruments")
        if dry_run:
            logger.info("DRY RUN MODE - no data will be inserted")

        total_stats = {'processed': 0, 'created': 0, 'skipped': 0, 'errors': 0}

        for inst in instruments:
            stats = self.backfill_instrument(inst, start_date, dry_run)
            for k, v in stats.items():
                total_stats[k] += v

        logger.info(f"=== M15 BACKFILL COMPLETE ===")
        logger.info(f"Total processed: {total_stats['processed']}")
        logger.info(f"Total created:   {total_stats['created']}")
        logger.info(f"Total skipped:   {total_stats['skipped']}")
        logger.info(f"Total errors:    {total_stats['errors']}")

        return total_stats

    def close(self):
        """Close database connection."""
        if self.conn:
            self.conn.close()
            self.conn = None


def main():
    parser = argparse.ArgumentParser(description='Backfill historical M15 signals')
    parser.add_argument('-i', '--instrument', type=str, help='Single instrument to backfill')
    parser.add_argument('--days', type=int, help='Only backfill last N days')
    parser.add_argument('--batch', type=int, default=500, help='Batch size (default: 500)')
    parser.add_argument('--dry-run', action='store_true', help='Preview only, no inserts')
    parser.add_argument('-v', '--verbose', action='store_true', help='Verbose logging')
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Calculate start date if days specified
    start_date = None
    if args.days:
        start_date = datetime.now(timezone.utc) - timedelta(days=args.days)
        logger.info(f"Backfilling from {start_date.isoformat()}")

    backfiller = SignalBackfillerM15(batch_size=args.batch)

    try:
        if args.instrument:
            backfiller.backfill_instrument(
                args.instrument,
                start_date=start_date,
                dry_run=args.dry_run
            )
        else:
            backfiller.backfill_all(
                start_date=start_date,
                dry_run=args.dry_run
            )
    finally:
        backfiller.close()


if __name__ == '__main__':
    main()
