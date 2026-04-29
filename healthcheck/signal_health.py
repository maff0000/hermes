#!/usr/bin/env python3
"""
Signal Health Check - Project Hygieia
EPIC-D013: Platform Health Monitoring Service
GOV-ENV-001: Environment-aware configuration

Checks for:
1. Signal freshness - are signals being generated?
2. Data gaps - missing candles during trading hours
3. Coverage - do we have 12 months of data?
4. Automatic backfill trigger when gaps detected

Usage:
    python signal_health.py              # Full health check
    python signal_health.py --check-gaps # Gap detection only
    python signal_health.py --backfill   # Run backfill for gaps
    python signal_health.py --json       # Output as JSON
"""

import sys
import json
import argparse
import subprocess
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
from pathlib import Path

import pymysql
import pymysql.cursors
import redis

# Setup - use relative path from script location
HEALTHCHECK_DIR = Path(__file__).parent.absolute()
BASE_DIR = HEALTHCHECK_DIR.parent
sys.path.insert(0, str(BASE_DIR))

from env_config import get_db_config, get_redis_config, ENV
from utils.discord_alerts import send_health_report, send_stale_alert, AlertLevel
from utils.trading_hours import is_market_open, get_current_session, should_expect_data

# Configuration (environment-aware via GOV-ENV-001)
_db = get_db_config()
DB_CONFIG = {
    'host': _db['host'],
    'port': _db['port'],
    'user': _db['user'],
    'password': _db['password'],
    'database': _db['database'],
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor
}

_redis = get_redis_config()
REDIS_CONFIG = {
    'host': _redis['host'],
    'port': _redis['port'],
    'db': _redis['db'],
    'password': _redis['password'],
}

# Health thresholds
MAX_SIGNAL_AGE_MINUTES = 10  # Signal older than this = stale
MIN_COVERAGE_DAYS = 365     # Minimum days of historical data
MAX_GAP_MINUTES = 30        # Gap larger than this = problem (allows for some weekend buffer)

# Candle timeframes to check (from env or default)
from env_config import get_env_list
CANDLE_TIMEFRAMES = get_env_list('CANDLE_TIMEFRAMES', ['M5'])

# Staleness thresholds per timeframe (in minutes)
TIMEFRAME_STALENESS = {
    'M5': 10,    # M5 should be < 10 min old
    'H1': 70,    # H1 should be < 70 min old (1 hour + buffer)
    'D1': 1500,  # D1 should be < 25 hours old (1 day + buffer)
}


@dataclass
class InstrumentHealth:
    """Health status for a single instrument."""
    instrument: str
    status: str  # HEALTHY, WARNING, CRITICAL
    total_candles: int
    oldest_data: Optional[datetime]
    newest_data: Optional[datetime]
    coverage_days: int
    is_current: bool
    signal_age_seconds: Optional[float]
    gaps_detected: int
    duplicates_detected: int
    issues: List[str]
    # Multi-timeframe support
    candles_by_timeframe: Optional[Dict[str, int]] = None
    latest_by_timeframe: Optional[Dict[str, datetime]] = None


@dataclass
class HealthReport:
    """Complete health report."""
    timestamp: datetime
    overall_status: str  # HEALTHY, WARNING, CRITICAL
    instruments: Dict[str, InstrumentHealth]
    redis_connected: bool
    db_connected: bool
    total_issues: int
    backfill_recommended: List[str]


class SignalHealthChecker:
    """Checks signal pipeline health."""

    def __init__(self):
        self.db = None
        self.redis = None
        self._connect()

    def _connect(self):
        """Establish database and Redis connections."""
        try:
            self.db = pymysql.connect(**DB_CONFIG)
        except Exception as e:
            print(f"[ERROR] Database connection failed: {e}")
            self.db = None

        try:
            self.redis = redis.Redis(**REDIS_CONFIG, decode_responses=True)
            self.redis.ping()
        except Exception as e:
            print(f"[ERROR] Redis connection failed: {e}")
            self.redis = None

    def check_instrument_coverage(self, instrument: str) -> InstrumentHealth:
        """Check data coverage for a single instrument."""
        issues = []

        if not self.db:
            return InstrumentHealth(
                instrument=instrument,
                status='CRITICAL',
                total_candles=0,
                oldest_data=None,
                newest_data=None,
                coverage_days=0,
                is_current=False,
                signal_age_seconds=None,
                gaps_detected=0,
                duplicates_detected=0,
                issues=['Database connection failed']
            )

        cursor = self.db.cursor()

        # Get candle statistics
        cursor.execute("""
            SELECT
                COUNT(*) as total,
                MIN(timestamp) as oldest,
                MAX(timestamp) as newest
            FROM candles_M5
            WHERE instrument = %s
        """, (instrument,))

        row = cursor.fetchone()
        total_candles = row['total'] or 0
        oldest_data = row['oldest']
        newest_data = row['newest']

        # Calculate coverage
        coverage_days = 0
        is_current = False

        if oldest_data and newest_data:
            coverage_days = (newest_data - oldest_data).days
            now = datetime.utcnow()
            is_current = (now - newest_data).total_seconds() < MAX_SIGNAL_AGE_MINUTES * 60

        # Check for duplicates
        cursor.execute("""
            SELECT timestamp, COUNT(*) as cnt
            FROM candles_M5
            WHERE instrument = %s
            GROUP BY timestamp
            HAVING COUNT(*) > 1
        """, (instrument,))
        duplicates = cursor.fetchall()
        duplicates_detected = len(duplicates)

        # Check issues
        if total_candles == 0:
            issues.append('NO DATA')
        elif coverage_days < MIN_COVERAGE_DAYS:
            issues.append(f'INSUFFICIENT COVERAGE: {coverage_days} days (need {MIN_COVERAGE_DAYS})')

        # Check for stale data - but only if market is open
        market_open, market_reason = is_market_open()
        if not is_current and newest_data:
            age_hours = (datetime.utcnow() - newest_data).total_seconds() / 3600
            if market_open:
                # Only flag as stale if market is open
                issues.append(f'STALE DATA: {age_hours:.1f} hours old')
            elif age_hours > 72:
                # Flag if stale for more than 3 days (spans entire weekend)
                issues.append(f'STALE DATA (EXTENDED): {age_hours:.1f} hours old')

        if duplicates_detected > 0:
            issues.append(f'DUPLICATES: {duplicates_detected} duplicate timestamps')

        # Check signal in Redis
        signal_age_seconds = None
        if self.redis:
            key_prefix = _redis['key_prefix']
            signal_key = f"{key_prefix}signals:latest:{instrument}"
            try:
                signal_data = self.redis.hgetall(signal_key)
                if signal_data and 'timestamp' in signal_data:
                    signal_time = datetime.fromisoformat(signal_data['timestamp'])
                    signal_age_seconds = (datetime.utcnow() - signal_time).total_seconds()

                    if signal_age_seconds > MAX_SIGNAL_AGE_MINUTES * 60:
                        issues.append(f'REDIS SIGNAL STALE: {signal_age_seconds/60:.1f} min old')

                    # STORY-D002-09: Check for ATR baseline metrics in Redis signal
                    if 'atr_baseline' not in signal_data or not signal_data.get('atr_baseline'):
                        issues.append('MISSING atr_baseline in Redis signal')
                elif not signal_data:
                    issues.append('NO REDIS SIGNAL')
            except Exception as e:
                issues.append(f'REDIS ERROR: {e}')

        # STORY-D002-09: Check ATR baseline metrics in recent signals
        # WO-TRADING-SIGNALS-UTC-SWEEP-0001 — cutoff computed Python-side as
        # timezone-aware UTC. signals.timestamp is stored UTC; SQL NOW() returns
        # server-local (BST in summer = UTC+1) and would skew this window.
        cutoff_utc = datetime.now(timezone.utc) - timedelta(hours=1)
        cursor.execute("""
            SELECT COUNT(*) as missing_count
            FROM signals
            WHERE instrument = %s
              AND timestamp >= %s
              AND atr_baseline IS NULL
        """, (instrument, cutoff_utc))
        missing_row = cursor.fetchone()
        if missing_row and missing_row['missing_count'] > 0:
            issues.append(f'MISSING ATR METRICS: {missing_row["missing_count"]} recent signals without atr_baseline')

        # Determine status
        if 'NO DATA' in str(issues) or 'CRITICAL' in str(issues):
            status = 'CRITICAL'
        elif issues:
            status = 'WARNING'
        else:
            status = 'HEALTHY'

        cursor.close()

        return InstrumentHealth(
            instrument=instrument,
            status=status,
            total_candles=total_candles,
            oldest_data=oldest_data,
            newest_data=newest_data,
            coverage_days=coverage_days,
            is_current=is_current,
            signal_age_seconds=signal_age_seconds,
            gaps_detected=0,  # Will be filled by gap detection
            duplicates_detected=duplicates_detected,
            issues=issues
        )

    def detect_gaps(self, instrument: str, days_back: int = 7) -> List[Dict]:
        """
        Detect data gaps for an instrument.

        Args:
            instrument: Instrument code
            days_back: Number of days to check

        Returns:
            List of gap dicts with start, end, duration_minutes
        """
        if not self.db:
            return []

        cursor = self.db.cursor()
        cutoff = datetime.utcnow() - timedelta(days=days_back)

        # Get timestamps with gaps > 5 minutes (M5 candles)
        cursor.execute("""
            SELECT
                timestamp,
                TIMESTAMPDIFF(MINUTE, LAG(timestamp) OVER (ORDER BY timestamp), timestamp) as gap_minutes
            FROM candles_M5
            WHERE instrument = %s AND timestamp >= %s
            ORDER BY timestamp
        """, (instrument, cutoff))

        rows = cursor.fetchall()
        cursor.close()

        gaps = []
        for row in rows:
            gap_min = row.get('gap_minutes')
            if gap_min and gap_min > MAX_GAP_MINUTES:
                # Check if this gap spans a weekend (allowed)
                gap_end = row['timestamp']
                gap_start = gap_end - timedelta(minutes=gap_min)

                # Skip weekend gaps (Friday 22:00 to Sunday 22:00)
                if gap_start.weekday() == 4 and gap_end.weekday() == 6:
                    continue  # Friday to Sunday = weekend
                if gap_start.weekday() == 4 and gap_end.weekday() == 0:
                    continue  # Friday to Monday = weekend

                gaps.append({
                    'start': gap_start,
                    'end': gap_end,
                    'duration_minutes': gap_min
                })

        return gaps

    def get_all_instruments(self) -> List[str]:
        """Get list of all configured instruments."""
        if not self.db:
            return []

        cursor = self.db.cursor()
        cursor.execute("SELECT symbol FROM instruments WHERE enabled = 1")
        instruments = [row['symbol'] for row in cursor.fetchall()]
        cursor.close()

        # Fallback to candles table if instruments table is empty
        if not instruments:
            cursor = self.db.cursor()
            cursor.execute("SELECT DISTINCT instrument FROM candles_M5")
            instruments = [row['instrument'] for row in cursor.fetchall()]
            cursor.close()

        return instruments

    def run_full_check(self) -> HealthReport:
        """Run complete health check."""
        instruments = self.get_all_instruments()
        instrument_health = {}
        total_issues = 0
        backfill_recommended = []

        for inst in instruments:
            health = self.check_instrument_coverage(inst)
            instrument_health[inst] = health
            total_issues += len(health.issues)

            # Check if backfill needed
            if health.coverage_days < MIN_COVERAGE_DAYS or not health.is_current:
                backfill_recommended.append(inst)

        # Determine overall status
        statuses = [h.status for h in instrument_health.values()]
        if 'CRITICAL' in statuses:
            overall = 'CRITICAL'
        elif 'WARNING' in statuses:
            overall = 'WARNING'
        else:
            overall = 'HEALTHY'

        return HealthReport(
            timestamp=datetime.utcnow(),
            overall_status=overall,
            instruments=instrument_health,
            redis_connected=self.redis is not None,
            db_connected=self.db is not None,
            total_issues=total_issues,
            backfill_recommended=backfill_recommended
        )

    def print_report(self, report: HealthReport, as_json: bool = False):
        """Print health report."""
        if as_json:
            # Convert to JSON-serializable format
            data = {
                'timestamp': report.timestamp.isoformat(),
                'overall_status': report.overall_status,
                'redis_connected': report.redis_connected,
                'db_connected': report.db_connected,
                'total_issues': report.total_issues,
                'backfill_recommended': report.backfill_recommended,
                'instruments': {}
            }
            for inst, health in report.instruments.items():
                data['instruments'][inst] = {
                    'status': health.status,
                    'total_candles': health.total_candles,
                    'oldest_data': health.oldest_data.isoformat() if health.oldest_data else None,
                    'newest_data': health.newest_data.isoformat() if health.newest_data else None,
                    'coverage_days': health.coverage_days,
                    'is_current': health.is_current,
                    'signal_age_seconds': health.signal_age_seconds,
                    'duplicates_detected': health.duplicates_detected,
                    'issues': health.issues
                }
            print(json.dumps(data, indent=2))
            return

        # Human-readable format
        print("=" * 70)
        print(f"SIGNAL HEALTH CHECK - Project Hygieia")
        print(f"Timestamp: {report.timestamp.isoformat()}")
        print(f"Overall Status: {report.overall_status}")
        print("=" * 70)

        # Trading session info
        market_open, market_reason = is_market_open()
        session = get_current_session()
        print(f"\nMarket Status: {'OPEN' if market_open else 'CLOSED'} - {market_reason}")
        print(f"Session: {session.upper()}")

        print(f"\nConnections:")
        print(f"  Database: {'✓ Connected' if report.db_connected else '✗ FAILED'}")
        print(f"  Redis:    {'✓ Connected' if report.redis_connected else '✗ FAILED'}")

        print(f"\n{'Instrument':<12} {'Status':<10} {'Candles':>10} {'Coverage':>10} {'Current':>8} {'Issues'}")
        print("-" * 70)

        for inst, health in sorted(report.instruments.items()):
            status_icon = {'HEALTHY': '✓', 'WARNING': '⚠', 'CRITICAL': '✗'}.get(health.status, '?')
            current_icon = '✓' if health.is_current else '✗'
            issues_str = ', '.join(health.issues[:2]) if health.issues else '-'
            if len(health.issues) > 2:
                issues_str += f' (+{len(health.issues)-2} more)'

            print(f"{inst:<12} {status_icon} {health.status:<8} {health.total_candles:>10} {health.coverage_days:>7} days {current_icon:>8} {issues_str}")

        print("-" * 70)
        print(f"Total Issues: {report.total_issues}")

        if report.backfill_recommended:
            print(f"\nBackfill Recommended For: {', '.join(report.backfill_recommended)}")

    def trigger_backfill(self, instruments: List[str], days: int = 365):
        """Trigger backfill for specified instruments."""
        script_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            'scripts', 'backfill_oanda.py'
        )

        if not os.path.exists(script_path):
            print(f"[ERROR] Backfill script not found: {script_path}")
            return

        for inst in instruments:
            print(f"[BACKFILL] Starting backfill for {inst} ({days} days)...")
            try:
                subprocess.Popen(
                    ['python3', script_path, '-i', inst, '-t', 'M5', '-d', str(days)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                print(f"[BACKFILL] {inst} backfill started in background")
            except Exception as e:
                print(f"[ERROR] Failed to start backfill for {inst}: {e}")

    def close(self):
        """Close connections."""
        if self.db:
            self.db.close()


def main():
    parser = argparse.ArgumentParser(description='Signal Health Check - Project Hygieia')
    parser.add_argument('--json', action='store_true', help='Output as JSON')
    parser.add_argument('--check-gaps', action='store_true', help='Check for data gaps')
    parser.add_argument('--gap-days', type=int, default=7, help='Days to check for gaps (default: 7)')
    parser.add_argument('--backfill', action='store_true', help='Trigger backfill for gaps')
    parser.add_argument('--days', type=int, default=365, help='Days to backfill (default: 365)')
    parser.add_argument('--discord', action='store_true', help='Send Discord alerts')
    parser.add_argument('--discord-always', action='store_true', help='Always send Discord (even if healthy)')
    args = parser.parse_args()

    checker = SignalHealthChecker()

    try:
        report = checker.run_full_check()
        checker.print_report(report, as_json=args.json)

        # Gap detection if requested
        if args.check_gaps:
            print(f"\n{'='*70}")
            print(f"GAP DETECTION (last {args.gap_days} days)")
            print(f"{'='*70}")

            total_gaps = 0
            for inst in report.instruments.keys():
                gaps = checker.detect_gaps(inst, args.gap_days)
                if gaps:
                    print(f"\n[{inst}] {len(gaps)} gaps detected:")
                    for g in gaps[:5]:
                        print(f"  - {g['start']} to {g['end']} ({g['duration_minutes']} min)")
                    if len(gaps) > 5:
                        print(f"  ... and {len(gaps) - 5} more gaps")
                    total_gaps += len(gaps)

            if total_gaps == 0:
                print("\nNo significant gaps detected.")
            else:
                print(f"\nTotal: {total_gaps} gaps across all instruments")

        # Send Discord alert if requested
        if args.discord or args.discord_always:
            should_alert = (
                args.discord_always or
                report.overall_status in ('WARNING', 'CRITICAL')
            )

            if should_alert:
                # Collect all issues for alert
                all_issues = []
                for inst, health in report.instruments.items():
                    for issue in health.issues:
                        all_issues.append(f"[{inst}] {issue}")

                # Convert instruments to dict format for alert
                inst_dict = {
                    inst: {'status': health.status, 'issues': health.issues}
                    for inst, health in report.instruments.items()
                }

                send_health_report(
                    overall_status=report.overall_status,
                    instruments=inst_dict,
                    issues=all_issues,
                    backfill_recommended=report.backfill_recommended
                )
                print(f"[DISCORD] Alert sent to Discord [{ENV}]")

        if args.backfill and report.backfill_recommended:
            print(f"\n[BACKFILL] Triggering backfill for {len(report.backfill_recommended)} instruments...")
            checker.trigger_backfill(report.backfill_recommended, days=args.days)

        # Exit code based on status
        if report.overall_status == 'CRITICAL':
            sys.exit(2)
        elif report.overall_status == 'WARNING':
            sys.exit(1)
        else:
            sys.exit(0)

    finally:
        checker.close()


if __name__ == '__main__':
    main()
