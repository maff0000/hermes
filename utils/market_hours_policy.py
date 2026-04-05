"""
HERMES Market Hours Policy — Governed Expected-Silence Evaluation
WO-HERMES-CANARY-MARKET-HOURS-0002

Reads per-instrument market-hours policy from hermes_market_hours table.
Determines whether fresh data is expected right now. UTC only.

Reusable by: canary, watchdog, proof program.

Design: minimal v1. No holiday engine. No complex exchange calendar.
Just: weekday open/close + optional maintenance window. Per instrument.
"""
from datetime import datetime, timezone, time as dt_time
from typing import Optional, Tuple, Dict

import pymysql
import pymysql.cursors


class MarketHoursPolicy:
    """
    Evaluates whether fresh market data is expected for an instrument
    at a given UTC timestamp. Reads from hermes_market_hours table.

    Per-instrument. No inheritance. UTC only.
    """

    def __init__(self, db_config: dict):
        self._db_config = db_config
        self._cache = None
        self._cache_ts = 0

    def _get_conn(self):
        return pymysql.connect(**self._db_config, autocommit=True, connect_timeout=5,
                               init_command="SET time_zone = '+00:00'")

    def _load_policies(self) -> Dict[str, dict]:
        """Load and cache all policies. Refresh every 5 minutes."""
        import time
        now = time.monotonic()
        if self._cache and (now - self._cache_ts) < 300:
            return self._cache

        conn = self._get_conn()
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(
                "SELECT instrument, market_type, open_day_utc, open_time_utc, "
                "close_day_utc, close_time_utc, maintenance_start_utc, "
                "maintenance_end_utc, is_enabled FROM hermes_market_hours"
            )
            rows = cur.fetchall()
        conn.close()

        self._cache = {r['instrument']: r for r in rows}
        self._cache_ts = now
        return self._cache

    def is_truth_expected(
        self,
        instrument: str,
        utc_now: Optional[datetime] = None,
    ) -> Tuple[bool, str]:
        """
        Determine if fresh data is expected for this instrument right now.

        Returns: (truth_expected: bool, reason: str)

        Reasons:
        - "MARKET_OPEN" — truth expected
        - "MARKET_CLOSED" — weekend/outside trading hours
        - "SCHEDULED_MAINTENANCE" — inside maintenance window
        - "INSTRUMENT_DISABLED" — policy exists but disabled
        - "NO_POLICY" — no policy row found (fail loud)
        """
        if utc_now is None:
            utc_now = datetime.now(timezone.utc)

        # Strip timezone for comparison if needed
        if utc_now.tzinfo is not None:
            utc_naive = utc_now.replace(tzinfo=None)
        else:
            utc_naive = utc_now

        policies = self._load_policies()
        policy = policies.get(instrument)

        if not policy:
            # No policy = fail loud — do not silently assume open
            return True, "NO_POLICY"

        if not policy['is_enabled']:
            return False, "INSTRUMENT_DISABLED"

        dow = utc_naive.weekday()  # 0=Monday, 6=Sunday
        current_time = utc_naive.time()

        open_day = policy['open_day_utc']    # 6 = Sunday
        open_time = policy['open_time_utc']  # timedelta from midnight
        close_day = policy['close_day_utc']  # 4 = Friday
        close_time = policy['close_time_utc']

        # Convert timedelta to time if needed (MySQL returns timedelta for TIME columns)
        if hasattr(open_time, 'total_seconds'):
            secs = int(open_time.total_seconds())
            open_time = dt_time(secs // 3600, (secs % 3600) // 60, secs % 60)
        if hasattr(close_time, 'total_seconds'):
            secs = int(close_time.total_seconds())
            close_time = dt_time(secs // 3600, (secs % 3600) // 60, secs % 60)

        # Check maintenance window first
        maint_start = policy.get('maintenance_start_utc')
        maint_end = policy.get('maintenance_end_utc')
        if maint_start and maint_end:
            if hasattr(maint_start, 'total_seconds'):
                secs = int(maint_start.total_seconds())
                maint_start = dt_time(secs // 3600, (secs % 3600) // 60, secs % 60)
            if hasattr(maint_end, 'total_seconds'):
                secs = int(maint_end.total_seconds())
                maint_end = dt_time(secs // 3600, (secs % 3600) // 60, secs % 60)

            if maint_start <= maint_end:
                if maint_start <= current_time < maint_end:
                    return False, "SCHEDULED_MAINTENANCE"
            else:
                # Wraps midnight
                if current_time >= maint_start or current_time < maint_end:
                    return False, "SCHEDULED_MAINTENANCE"

        # Forex market hours: open_day open_time → close_day close_time
        # For forex: Sunday 22:00 → Friday 22:00
        # This means:
        #   Saturday (5): always closed
        #   Sunday (6) before 22:00: closed
        #   Sunday (6) 22:00+: open
        #   Friday (4) 22:00+: closed
        #   Monday-Thursday: always open

        if dow == 5:  # Saturday — always closed
            return False, "MARKET_CLOSED"

        if dow == open_day:  # Sunday
            if current_time < open_time:
                return False, "MARKET_CLOSED"
            else:
                return True, "MARKET_OPEN"

        if dow == close_day:  # Friday
            if current_time >= close_time:
                return False, "MARKET_CLOSED"
            else:
                return True, "MARKET_OPEN"

        # Monday through Thursday — always open (for forex)
        if 0 <= dow <= 3:
            return True, "MARKET_OPEN"

        # Should not reach here, but be explicit
        return False, "MARKET_CLOSED"
