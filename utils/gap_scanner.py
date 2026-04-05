"""
HERMES Gap Scanner — Deterministic Missing Data Detection
WO-HERMES-GAP-TRUTH-0002

Detects missing candle and signal windows by comparing expected timestamp
sequences (from timeframe cadence + market hours) against actual DB rows.

Core design rule: gap truth is based on expected sequence continuity, not vibes.

Usage:
    # As library
    scanner = GapScanner(db_config)
    gaps = scanner.scan_candle_gaps('XAU_USD', 'M1', start_utc, end_utc)

    # As CLI
    python gap_scanner.py scan --instrument XAU_USD --timeframe M1 --from 2026-03-31T02:00 --to 2026-03-31T08:00
    python gap_scanner.py verify --recent 24  # verify last 24 hours, exit non-zero on gaps
"""
import argparse
import hashlib
import json
import sys
import os
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass

import pymysql
import pymysql.cursors

# Add parent to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================
# Data structures
# ============================================================

@dataclass
class GapRecord:
    """A detected gap in data continuity."""
    artifact_type: str       # CANDLE or SIGNAL
    instrument: str
    timeframe: str
    gap_start_utc: datetime
    gap_end_utc: datetime
    expected_count: int
    actual_count: int
    missing_count: int

    @property
    def scan_hash(self) -> str:
        """Deterministic hash for idempotent reconciliation."""
        raw = f"{self.artifact_type}|{self.instrument}|{self.timeframe}|{self.gap_start_utc.isoformat()}|{self.gap_end_utc.isoformat()}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        return {
            'artifact_type': self.artifact_type,
            'instrument': self.instrument,
            'timeframe': self.timeframe,
            'gap_start_utc': self.gap_start_utc.isoformat(),
            'gap_end_utc': self.gap_end_utc.isoformat(),
            'expected_count': self.expected_count,
            'actual_count': self.actual_count,
            'missing_count': self.missing_count,
            'scan_hash': self.scan_hash,
        }


# ============================================================
# Timeframe definitions
# ============================================================

TIMEFRAMES = {
    'M1':  {'seconds': 60,    'table': 'candles_M1'},
    'M5':  {'seconds': 300,   'table': 'candles_M5'},
    'M15': {'seconds': 900,   'table': 'candles_M15'},
    'H1':  {'seconds': 3600,  'table': 'candles_H1'},
}

# Signal timeframes — what HERMES actually produces signals for
SIGNAL_TIMEFRAMES = ['M5', 'M15']


# ============================================================
# Market hours — forex specific
# ============================================================

def is_forex_market_open(dt: datetime) -> bool:
    """
    Check if forex market is open at given UTC datetime.

    Forex hours: Sunday 22:00 UTC → Friday 22:00 UTC (continuous).
    Weekday 0=Monday, 6=Sunday.
    """
    dow = dt.weekday()
    hour = dt.hour

    if dow == 5:  # Saturday — always closed
        return False
    if dow == 6:  # Sunday — open only from 22:00
        return hour >= 22
    if dow == 4:  # Friday — closed from 22:00
        return hour < 22
    # Monday through Thursday — always open
    return True


def generate_expected_timestamps(
    timeframe: str,
    start_utc: datetime,
    end_utc: datetime,
) -> List[datetime]:
    """
    Generate the sequence of expected candle timestamps within a window,
    excluding market-closed periods.

    Candle timestamps represent the candle open time.
    """
    tf_seconds = TIMEFRAMES[timeframe]['seconds']
    delta = timedelta(seconds=tf_seconds)

    # Align start to timeframe boundary
    epoch = datetime(2000, 1, 1)
    seconds_since_epoch = int((start_utc - epoch).total_seconds())
    aligned_start = epoch + timedelta(seconds=(seconds_since_epoch // tf_seconds) * tf_seconds)
    if aligned_start < start_utc:
        aligned_start += delta

    expected = []
    current = aligned_start
    while current <= end_utc:
        if is_forex_market_open(current):
            expected.append(current)
        current += delta

    return expected


# ============================================================
# Gap Scanner
# ============================================================

class GapScanner:
    """Deterministic gap detector for HERMES data."""

    def __init__(self, db_config: dict):
        self._db_config = db_config

    def _get_conn(self):
        return pymysql.connect(
            host=self._db_config['host'],
            port=self._db_config['port'],
            user=self._db_config['user'],
            password=self._db_config['password'],
            database=self._db_config['database'],
            autocommit=True,
            connect_timeout=5,
        )

    def _get_actual_timestamps(
        self,
        table: str,
        instrument: str,
        start_utc: datetime,
        end_utc: datetime,
    ) -> set:
        """Fetch actual timestamps from DB for a given window."""
        conn = self._get_conn()
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT timestamp FROM {table} WHERE instrument = %s AND timestamp >= %s AND timestamp <= %s",
                (instrument, start_utc, end_utc)
            )
            rows = cur.fetchall()
        conn.close()
        return {row[0] for row in rows}

    def _get_actual_signal_timestamps(
        self,
        instrument: str,
        timeframe: str,
        start_utc: datetime,
        end_utc: datetime,
    ) -> set:
        """Fetch actual signal timestamps from DB."""
        conn = self._get_conn()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT timestamp FROM signals WHERE instrument = %s AND timeframe = %s AND timestamp >= %s AND timestamp <= %s",
                (instrument, timeframe, start_utc, end_utc)
            )
            rows = cur.fetchall()
        conn.close()
        return {row[0] for row in rows}

    def scan_candle_gaps(
        self,
        instrument: str,
        timeframe: str,
        start_utc: datetime,
        end_utc: datetime,
        min_gap_size: int = 1,
    ) -> List[GapRecord]:
        """
        Scan for candle gaps in a given window.

        Returns a list of contiguous gap windows where expected candles are missing.
        Gaps smaller than min_gap_size are ignored.
        """
        if timeframe not in TIMEFRAMES:
            raise ValueError(f"Unsupported timeframe: {timeframe}")

        table = TIMEFRAMES[timeframe]['table']
        tf_seconds = TIMEFRAMES[timeframe]['seconds']
        delta = timedelta(seconds=tf_seconds)

        expected = generate_expected_timestamps(timeframe, start_utc, end_utc)
        if not expected:
            return []

        actual = self._get_actual_timestamps(table, instrument, start_utc, end_utc)

        # Find missing timestamps
        missing = sorted([ts for ts in expected if ts not in actual])

        if not missing:
            return []

        # Merge contiguous missing timestamps into gap windows
        gaps = []
        gap_start = missing[0]
        prev = missing[0]

        for ts in missing[1:]:
            if (ts - prev).total_seconds() <= tf_seconds * 1.5:
                # Contiguous — extend current gap
                prev = ts
            else:
                # Break — close current gap, start new
                gap_missing = len([m for m in missing if gap_start <= m <= prev])
                if gap_missing >= min_gap_size:
                    gap_expected = len([e for e in expected if gap_start <= e <= prev])
                    gap_actual = gap_expected - gap_missing
                    gaps.append(GapRecord(
                        artifact_type='CANDLE',
                        instrument=instrument,
                        timeframe=timeframe,
                        gap_start_utc=gap_start,
                        gap_end_utc=prev,
                        expected_count=gap_expected,
                        actual_count=max(0, gap_actual),
                        missing_count=gap_missing,
                    ))
                gap_start = ts
                prev = ts

        # Close final gap
        gap_missing = len([m for m in missing if gap_start <= m <= prev])
        if gap_missing >= min_gap_size:
            gap_expected = len([e for e in expected if gap_start <= e <= prev])
            gap_actual = gap_expected - gap_missing
            gaps.append(GapRecord(
                artifact_type='CANDLE',
                instrument=instrument,
                timeframe=timeframe,
                gap_start_utc=gap_start,
                gap_end_utc=prev,
                expected_count=gap_expected,
                actual_count=max(0, gap_actual),
                missing_count=gap_missing,
            ))

        return gaps

    def scan_signal_gaps(
        self,
        instrument: str,
        timeframe: str,
        start_utc: datetime,
        end_utc: datetime,
        min_gap_size: int = 1,
    ) -> List[GapRecord]:
        """
        Scan for signal gaps. Signals are only expected where HERMES
        structurally produces them (M5, M15 timeframes).
        """
        if timeframe not in SIGNAL_TIMEFRAMES:
            return []  # No signals expected for this timeframe

        if timeframe not in TIMEFRAMES:
            raise ValueError(f"Unsupported timeframe: {timeframe}")

        tf_seconds = TIMEFRAMES[timeframe]['seconds']
        delta = timedelta(seconds=tf_seconds)

        expected = generate_expected_timestamps(timeframe, start_utc, end_utc)
        if not expected:
            return []

        actual = self._get_actual_signal_timestamps(instrument, timeframe, start_utc, end_utc)

        missing = sorted([ts for ts in expected if ts not in actual])
        if not missing:
            return []

        # Merge contiguous missing into gap windows
        gaps = []
        gap_start = missing[0]
        prev = missing[0]

        for ts in missing[1:]:
            if (ts - prev).total_seconds() <= tf_seconds * 1.5:
                prev = ts
            else:
                gap_missing = len([m for m in missing if gap_start <= m <= prev])
                if gap_missing >= min_gap_size:
                    gap_expected = len([e for e in expected if gap_start <= e <= prev])
                    gap_actual = gap_expected - gap_missing
                    gaps.append(GapRecord(
                        artifact_type='SIGNAL',
                        instrument=instrument,
                        timeframe=timeframe,
                        gap_start_utc=gap_start,
                        gap_end_utc=prev,
                        expected_count=gap_expected,
                        actual_count=max(0, gap_actual),
                        missing_count=gap_missing,
                    ))
                gap_start = ts
                prev = ts

        gap_missing = len([m for m in missing if gap_start <= m <= prev])
        if gap_missing >= min_gap_size:
            gap_expected = len([e for e in expected if gap_start <= e <= prev])
            gap_actual = gap_expected - gap_missing
            gaps.append(GapRecord(
                artifact_type='SIGNAL',
                instrument=instrument,
                timeframe=timeframe,
                gap_start_utc=gap_start,
                gap_end_utc=prev,
                expected_count=gap_expected,
                actual_count=max(0, gap_actual),
                missing_count=gap_missing,
            ))

        return gaps

    def scan_all(
        self,
        instrument: str,
        start_utc: datetime,
        end_utc: datetime,
        timeframes: List[str] = None,
        include_signals: bool = True,
        min_gap_size: int = 1,
    ) -> List[GapRecord]:
        """Scan all artifact types for gaps."""
        if timeframes is None:
            timeframes = list(TIMEFRAMES.keys())

        all_gaps = []
        for tf in timeframes:
            all_gaps.extend(self.scan_candle_gaps(instrument, tf, start_utc, end_utc, min_gap_size))
            if include_signals:
                all_gaps.extend(self.scan_signal_gaps(instrument, tf, start_utc, end_utc, min_gap_size))

        return all_gaps


# ============================================================
# Gap Persistence — ledger operations
# ============================================================

class GapLedger:
    """Persists and reconciles gap records in hermes_data_gaps."""

    def __init__(self, db_config: dict):
        self._db_config = db_config

    def _get_conn(self):
        return pymysql.connect(
            host=self._db_config['host'],
            port=self._db_config['port'],
            user=self._db_config['user'],
            password=self._db_config['password'],
            database=self._db_config['database'],
            autocommit=True,
            connect_timeout=5,
        )

    def persist_gaps(self, gaps: List[GapRecord]) -> Tuple[int, int]:
        """
        Persist detected gaps. Idempotent via scan_hash.

        Returns (new_count, existing_count).
        """
        if not gaps:
            return 0, 0

        conn = self._get_conn()
        new_count = 0
        existing_count = 0

        with conn.cursor() as cur:
            for gap in gaps:
                # Check if this exact gap already exists
                cur.execute(
                    "SELECT gap_id, status FROM hermes_data_gaps WHERE scan_hash = %s",
                    (gap.scan_hash,)
                )
                existing = cur.fetchone()

                if existing:
                    existing_count += 1
                    # If it was INVALIDATED and we see it again, re-detect
                    if existing[1] == 'INVALIDATED':
                        cur.execute(
                            "UPDATE hermes_data_gaps SET status = 'DETECTED', detected_at = %s, "
                            "actual_count = %s, missing_count = %s WHERE gap_id = %s",
                            (datetime.now(timezone.utc), gap.actual_count, gap.missing_count, existing[0])
                        )
                    continue

                now = datetime.now(timezone.utc)
                diagnostic = json.dumps(gap.to_dict())

                cur.execute(
                    """INSERT INTO hermes_data_gaps
                    (artifact_type, instrument, timeframe, gap_start_utc, gap_end_utc,
                     expected_count, actual_count, missing_count, status, detected_at,
                     scan_hash, diagnostic_json, description, llm_reasoning)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'DETECTED', %s, %s, %s, %s, %s)""",
                    (
                        gap.artifact_type, gap.instrument, gap.timeframe,
                        gap.gap_start_utc, gap.gap_end_utc,
                        gap.expected_count, gap.actual_count, gap.missing_count,
                        now, gap.scan_hash, diagnostic,
                        f"Gap detected: {gap.missing_count} missing {gap.artifact_type} {gap.timeframe} "
                        f"for {gap.instrument} from {gap.gap_start_utc} to {gap.gap_end_utc}",
                        json.dumps({
                            "rationale": f"Scanner found {gap.missing_count} missing "
                            f"{gap.artifact_type.lower()}s in a window where {gap.expected_count} "
                            f"were expected based on timeframe cadence and market hours.",
                            "source": "WO-HERMES-GAP-TRUTH-0002",
                        }),
                    )
                )
                new_count += 1

        conn.close()
        return new_count, existing_count

    def get_unresolved_gaps(
        self,
        instrument: str = None,
        timeframe: str = None,
    ) -> List[Dict]:
        """Get all unresolved (non-RESOLVED, non-INVALIDATED) gaps."""
        conn = self._get_conn()
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            sql = """SELECT gap_id, artifact_type, instrument, timeframe,
                     gap_start_utc, gap_end_utc, expected_count, actual_count,
                     missing_count, status, detected_at
                     FROM hermes_data_gaps
                     WHERE status NOT IN ('RESOLVED', 'INVALIDATED')"""
            params = []

            if instrument:
                sql += " AND instrument = %s"
                params.append(instrument)
            if timeframe:
                sql += " AND timeframe = %s"
                params.append(timeframe)

            sql += " ORDER BY gap_start_utc"
            cur.execute(sql, params)
            rows = cur.fetchall()
        conn.close()
        return rows

    def get_unresolved_count(self) -> int:
        """Get count of unresolved gaps."""
        conn = self._get_conn()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM hermes_data_gaps WHERE status NOT IN ('RESOLVED', 'INVALIDATED')"
            )
            count = cur.fetchone()[0]
        conn.close()
        return count

    def get_latest_gap_summary(self) -> Optional[Dict]:
        """Get most recent unresolved gap for health integration."""
        conn = self._get_conn()
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(
                """SELECT gap_id, artifact_type, instrument, timeframe,
                   gap_start_utc, gap_end_utc, missing_count, status
                   FROM hermes_data_gaps
                   WHERE status NOT IN ('RESOLVED', 'INVALIDATED')
                   ORDER BY detected_at DESC LIMIT 1"""
            )
            row = cur.fetchone()
        conn.close()
        return row

    def reconcile_resolved(self, scanner: GapScanner, instrument: str, timeframe: str):
        """
        Rescan unresolved gaps and mark as RESOLVED if data now exists.
        Called after backfill to verify repair.
        """
        unresolved = self.get_unresolved_gaps(instrument, timeframe)
        conn = self._get_conn()

        for gap in unresolved:
            # Rescan this specific window
            if gap['artifact_type'] == 'CANDLE':
                new_gaps = scanner.scan_candle_gaps(
                    instrument, timeframe,
                    gap['gap_start_utc'], gap['gap_end_utc']
                )
            else:
                new_gaps = scanner.scan_signal_gaps(
                    instrument, timeframe,
                    gap['gap_start_utc'], gap['gap_end_utc']
                )

            if not new_gaps:
                # Gap is resolved — data now exists
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE hermes_data_gaps SET status = 'RESOLVED', resolved_at = %s WHERE gap_id = %s",
                        (datetime.now(timezone.utc), gap['gap_id'])
                    )

        conn.close()


# ============================================================
# CLI
# ============================================================

def cli_scan(args, db_config):
    """Run gap scan and persist results."""
    scanner = GapScanner(db_config)
    ledger = GapLedger(db_config)

    start = datetime.fromisoformat(args.start)
    end = datetime.fromisoformat(args.end)
    timeframes = [args.timeframe] if args.timeframe else list(TIMEFRAMES.keys())

    print(f"Scanning {args.instrument} from {start} to {end}")
    print(f"Timeframes: {timeframes}")
    print(f"Include signals: {not args.candles_only}")
    print()

    all_gaps = scanner.scan_all(
        instrument=args.instrument,
        start_utc=start,
        end_utc=end,
        timeframes=timeframes,
        include_signals=not args.candles_only,
        min_gap_size=args.min_gap_size,
    )

    if not all_gaps:
        print("No gaps detected.")
        return 0

    # Persist
    if not args.dry_run:
        new, existing = ledger.persist_gaps(all_gaps)
        print(f"Persisted: {new} new, {existing} already known")
    else:
        print("[DRY RUN] Would persist:")

    print(f"\nTotal gaps found: {len(all_gaps)}")
    print()

    for gap in all_gaps:
        duration_min = (gap.gap_end_utc - gap.gap_start_utc).total_seconds() / 60
        print(f"  [{gap.artifact_type}] {gap.instrument} {gap.timeframe}: "
              f"{gap.gap_start_utc} → {gap.gap_end_utc} "
              f"({duration_min:.0f}min, {gap.missing_count} missing of {gap.expected_count} expected)")

    return len(all_gaps)


def cli_verify(args, db_config):
    """Verify mode — scan and report, exit non-zero on unresolved gaps."""
    scanner = GapScanner(db_config)

    if args.recent:
        end = datetime.utcnow()
        start = end - timedelta(hours=args.recent)
    else:
        start = datetime.fromisoformat(args.start)
        end = datetime.fromisoformat(args.end)

    instruments = [args.instrument] if args.instrument else _get_all_instruments(db_config)
    timeframes = [args.timeframe] if args.timeframe else list(TIMEFRAMES.keys())

    total_gaps = 0
    total_missing = 0

    print(f"HERMES Gap Verification")
    print(f"Window: {start} → {end}")
    print(f"Instruments: {instruments}")
    print(f"Timeframes: {timeframes}")
    print()

    for inst in instruments:
        gaps = scanner.scan_all(
            instrument=inst,
            start_utc=start,
            end_utc=end,
            timeframes=timeframes,
            min_gap_size=args.min_gap_size,
        )

        if gaps:
            for gap in gaps:
                duration_min = (gap.gap_end_utc - gap.gap_start_utc).total_seconds() / 60
                print(f"  GAP [{gap.artifact_type}] {gap.instrument} {gap.timeframe}: "
                      f"{gap.gap_start_utc} → {gap.gap_end_utc} "
                      f"({duration_min:.0f}min, {gap.missing_count} missing)")
                total_gaps += 1
                total_missing += gap.missing_count

    print()
    if total_gaps == 0:
        print("VERIFY PASS: No gaps detected.")
        return 0
    else:
        print(f"VERIFY FAIL: {total_gaps} gaps, {total_missing} total missing rows.")
        return 1


def _get_all_instruments(db_config):
    """Get enabled instruments from DB."""
    conn = pymysql.connect(**db_config, autocommit=True)
    with conn.cursor() as cur:
        cur.execute("SELECT symbol FROM instruments WHERE enabled = 1")
        rows = cur.fetchall()
    conn.close()
    if rows:
        return [r[0] for r in rows]
    # Fallback
    conn = pymysql.connect(**db_config, autocommit=True)
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT instrument FROM candles_M5 ORDER BY instrument")
        rows = cur.fetchall()
    conn.close()
    return [r[0] for r in rows]


def main():
    parser = argparse.ArgumentParser(description="HERMES Gap Scanner — WO-HERMES-GAP-TRUTH-0002")
    subparsers = parser.add_subparsers(dest='command', required=True)

    # scan command
    scan_p = subparsers.add_parser('scan', help='Scan for gaps and persist to ledger')
    scan_p.add_argument('--instrument', '-i', required=True)
    scan_p.add_argument('--timeframe', '-t', default=None, help='Specific timeframe or all')
    scan_p.add_argument('--start', '-s', required=True, help='Start UTC (ISO format)')
    scan_p.add_argument('--end', '-e', required=True, help='End UTC (ISO format)')
    scan_p.add_argument('--min-gap-size', type=int, default=1)
    scan_p.add_argument('--candles-only', action='store_true')
    scan_p.add_argument('--dry-run', action='store_true', help='Do not persist')

    # verify command
    verify_p = subparsers.add_parser('verify', help='Verify data continuity (non-zero exit on gaps)')
    verify_p.add_argument('--instrument', '-i', default=None, help='Specific instrument or all')
    verify_p.add_argument('--timeframe', '-t', default=None)
    verify_p.add_argument('--start', '-s', default=None)
    verify_p.add_argument('--end', '-e', default=None)
    verify_p.add_argument('--recent', type=float, default=None, help='Scan last N hours')
    verify_p.add_argument('--min-gap-size', type=int, default=1)

    args = parser.parse_args()

    # Load DB config
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/..')
    from env_config import get_db_config
    db_config = get_db_config()

    if args.command == 'scan':
        gap_count = cli_scan(args, db_config)
        sys.exit(1 if gap_count > 0 else 0)

    elif args.command == 'verify':
        result = cli_verify(args, db_config)
        sys.exit(result)


if __name__ == '__main__':
    main()
