"""
HERMES M1 Derivation Engine — Higher Timeframes from Canonical M1
WO-HERMES-GENERATOR-SPLIT-D
Epic: EPIC-HERMES-SIGNAL-SOURCE-RESILIENCE-001

Derives M5/M15/H1/D1 candles from canonical M1 truth.
This is a PARALLEL path — does NOT replace the existing tick-aggregated path.
Outputs go to comparison tables or are used for equivalence validation.

Design rules:
- Input: canonical_m1 table only
- Output: derived candle data (same OHLCV schema as candles_M5/M15/H1/D1)
- Per-instrument, independent
- UTC-only bucket boundaries
- Deterministic: same M1 input always produces same derived output
- Volume: sum of M1 volumes (tick counts). Explicitly documented.
- D1 boundary: 22:00 UTC (forex day, not calendar day)

Volume semantics (explicit):
- Derived volume = SUM of constituent M1 volumes
- If M1 was live: volume = tick count
- If M1 was repair: volume = broker-reported volume
- This is a KNOWN semantic mismatch. WO-D documents it, does not solve it.
- The mismatch does not affect OHLC truth.
"""
import sys
import os
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass

import pymysql
import pymysql.cursors

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================
# Constants
# ============================================================

TIMEFRAME_SECONDS = {
    'M5': 300,
    'M15': 900,
    'H1': 3600,
}

# Forex day boundary: 22:00 UTC (17:00 ET)
# D1 candle spans 22:00 UTC day N to 21:59 UTC day N+1
FOREX_DAY_BOUNDARY_HOUR = 22


# ============================================================
# Data structures
# ============================================================

@dataclass
class DerivedCandle:
    """A candle derived from canonical M1 data."""
    instrument: str
    timeframe: str
    timestamp: datetime     # Bucket start (UTC)
    open: float
    high: float
    low: float
    close: float
    volume: int             # Sum of constituent M1 volumes
    m1_count: int           # How many M1 candles were used
    expected_m1: int        # How many M1 candles were expected
    complete: bool          # True if m1_count == expected_m1

    @property
    def is_full(self) -> bool:
        """All expected M1 candles are present."""
        return self.m1_count == self.expected_m1

    def to_dict(self) -> dict:
        return {
            'instrument': self.instrument,
            'timeframe': self.timeframe,
            'timestamp': self.timestamp.isoformat(),
            'open': self.open, 'high': self.high,
            'low': self.low, 'close': self.close,
            'volume': self.volume,
            'm1_count': self.m1_count,
            'expected_m1': self.expected_m1,
            'complete': self.complete,
        }


# ============================================================
# Bucket math
# ============================================================

def get_bucket_start(ts: datetime, tf_seconds: int) -> datetime:
    """Truncate timestamp to timeframe bucket boundary. Matches CandleAggregator logic."""
    epoch = datetime(1970, 1, 1)
    total_seconds = (ts - epoch).total_seconds()
    period_start = int(total_seconds // tf_seconds) * tf_seconds
    return epoch + timedelta(seconds=period_start)


def get_forex_day_start(ts: datetime) -> datetime:
    """Get the forex day start (22:00 UTC previous day) for a given timestamp."""
    if ts.hour >= FOREX_DAY_BOUNDARY_HOUR:
        return ts.replace(hour=FOREX_DAY_BOUNDARY_HOUR, minute=0, second=0, microsecond=0)
    else:
        prev = ts - timedelta(days=1)
        return prev.replace(hour=FOREX_DAY_BOUNDARY_HOUR, minute=0, second=0, microsecond=0)


def get_m1_buckets_for_tf(bucket_start: datetime, timeframe: str) -> Tuple[datetime, datetime, int]:
    """
    Given a higher-TF bucket start, return (m1_start, m1_end, expected_count).
    m1_start is inclusive, m1_end is exclusive.
    """
    if timeframe == 'D1':
        # Forex day: 22:00 UTC to 21:59 UTC next day = 1440 M1 candles
        m1_start = bucket_start
        m1_end = bucket_start + timedelta(hours=24)
        return m1_start, m1_end, 1440
    else:
        tf_seconds = TIMEFRAME_SECONDS[timeframe]
        m1_count = tf_seconds // 60
        m1_start = bucket_start
        m1_end = bucket_start + timedelta(seconds=tf_seconds)
        return m1_start, m1_end, m1_count


# ============================================================
# M1 Derivation Engine
# ============================================================

class M1DerivationEngine:
    """
    Derives higher timeframe candles from canonical M1 truth.

    Per-instrument. Independent. Deterministic.
    Reads from canonical_m1 table. Does NOT write to live candle tables.
    """

    def __init__(self, db_config: dict):
        self._db_config = db_config

    def _get_conn(self):
        return pymysql.connect(**self._db_config, autocommit=True, connect_timeout=5)

    def derive_candle(
        self,
        instrument: str,
        timeframe: str,
        bucket_start: datetime,
    ) -> Optional[DerivedCandle]:
        """
        Derive a single higher-TF candle from canonical M1 data.

        Returns None if no M1 data exists for this bucket.
        Returns DerivedCandle with complete=False if M1 data is partial.
        """
        if timeframe == 'D1':
            m1_start = bucket_start
            m1_end = bucket_start + timedelta(hours=24)
            expected = 1440
        else:
            tf_seconds = TIMEFRAME_SECONDS.get(timeframe)
            if not tf_seconds:
                raise ValueError(f"Unsupported timeframe: {timeframe}")
            m1_start = bucket_start
            m1_end = bucket_start + timedelta(seconds=tf_seconds)
            expected = tf_seconds // 60

        # Fetch canonical M1 candles for this bucket
        conn = self._get_conn()
        try:
            with conn.cursor(pymysql.cursors.DictCursor) as cur:
                cur.execute(
                    """SELECT minute_bucket_utc, open, high, low, close, volume
                    FROM canonical_m1
                    WHERE instrument = %s
                      AND minute_bucket_utc >= %s
                      AND minute_bucket_utc < %s
                    ORDER BY minute_bucket_utc""",
                    (instrument, m1_start, m1_end)
                )
                rows = cur.fetchall()
        finally:
            conn.close()

        if not rows:
            return None

        # Aggregate: first open, max high, min low, last close, sum volume
        derived_open = float(rows[0]['open'])
        derived_high = max(float(r['high']) for r in rows)
        derived_low = min(float(r['low']) for r in rows)
        derived_close = float(rows[-1]['close'])
        derived_volume = sum(r['volume'] for r in rows)

        return DerivedCandle(
            instrument=instrument,
            timeframe=timeframe,
            timestamp=bucket_start,
            open=derived_open,
            high=derived_high,
            low=derived_low,
            close=derived_close,
            volume=derived_volume,
            m1_count=len(rows),
            expected_m1=expected,
            complete=(len(rows) == expected),
        )

    def derive_range(
        self,
        instrument: str,
        timeframe: str,
        start_utc: datetime,
        end_utc: datetime,
    ) -> List[DerivedCandle]:
        """Derive all candles for a given range."""
        if timeframe == 'D1':
            # Iterate by forex day
            results = []
            current = get_forex_day_start(start_utc)
            while current < end_utc:
                candle = self.derive_candle(instrument, 'D1', current)
                if candle:
                    results.append(candle)
                current += timedelta(hours=24)
            return results
        else:
            tf_seconds = TIMEFRAME_SECONDS[timeframe]
            results = []
            current = get_bucket_start(start_utc, tf_seconds)
            while current < end_utc:
                candle = self.derive_candle(instrument, timeframe, current)
                if candle:
                    results.append(candle)
                current += timedelta(seconds=tf_seconds)
            return results

    def compare_with_legacy(
        self,
        instrument: str,
        timeframe: str,
        start_utc: datetime,
        end_utc: datetime,
    ) -> Dict:
        """
        Compare derived candles against legacy candle table.
        Returns comparison report.
        """
        # Get derived candles
        derived = self.derive_range(instrument, timeframe, start_utc, end_utc)

        # Get legacy candles
        legacy_table = f"candles_{timeframe}"
        conn = self._get_conn()
        try:
            with conn.cursor(pymysql.cursors.DictCursor) as cur:
                cur.execute(
                    f"SELECT timestamp, open, high, low, close, volume "
                    f"FROM {legacy_table} "
                    f"WHERE instrument = %s AND timestamp >= %s AND timestamp < %s "
                    f"ORDER BY timestamp",
                    (instrument, start_utc, end_utc)
                )
                legacy_rows = cur.fetchall()
        finally:
            conn.close()

        legacy_by_ts = {r['timestamp']: r for r in legacy_rows}
        derived_by_ts = {d.timestamp: d for d in derived}

        matches = 0
        mismatches = []
        derived_only = []
        legacy_only = []

        all_ts = sorted(set(list(legacy_by_ts.keys()) + list(derived_by_ts.keys())))

        for ts in all_ts:
            d = derived_by_ts.get(ts)
            l = legacy_by_ts.get(ts)

            if d and l:
                ohlc_match = (
                    abs(d.open - float(l['open'])) < 0.001 and
                    abs(d.high - float(l['high'])) < 0.001 and
                    abs(d.low - float(l['low'])) < 0.001 and
                    abs(d.close - float(l['close'])) < 0.001
                )
                if ohlc_match:
                    matches += 1
                else:
                    mismatches.append({
                        'timestamp': ts.isoformat(),
                        'derived': {'o': d.open, 'h': d.high, 'l': d.low, 'c': d.close},
                        'legacy': {'o': float(l['open']), 'h': float(l['high']),
                                   'l': float(l['low']), 'c': float(l['close'])},
                    })
            elif d and not l:
                derived_only.append(ts.isoformat())
            elif l and not d:
                legacy_only.append(ts.isoformat())

        total = len(all_ts)
        match_pct = (matches / total * 100) if total > 0 else 0

        return {
            'instrument': instrument,
            'timeframe': timeframe,
            'window': f"{start_utc.isoformat()} → {end_utc.isoformat()}",
            'total_buckets': total,
            'matches': matches,
            'mismatches': len(mismatches),
            'match_pct': round(match_pct, 2),
            'derived_only': len(derived_only),
            'legacy_only': len(legacy_only),
            'mismatch_details': mismatches[:5],  # First 5 for brevity
            'volume_note': 'Volume comparison skipped — known semantic mismatch (tick count vs broker volume)',
        }
