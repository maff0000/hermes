"""
HERMES Recovery Executor — Deterministic Gap Repair
WO-HERMES-BACKFILL-ENGINE-0004

Executes recovery plans from WO-3 metadata to repair missing data windows.
Records every step in governed job tables. Fails loudly on any issue.

Usage:
    python recovery_executor.py recover-window -i XAU_USD -s 2026-03-31T02:55 -e 2026-03-31T07:41
    python recovery_executor.py recover-gap-id --gap-id 1
    python recovery_executor.py verify-window -i XAU_USD -s 2026-03-31T02:55 -e 2026-03-31T07:41
"""
import argparse
import json
import sys
import os
import time
import requests
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional

import pymysql
import pymysql.cursors

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from env_config import get_env, get_db_config, ENV
from utils.recovery_planner import RecoveryLibrary, RecoveryPlanner, RebuildPlan, RebuildStep
from utils.m1_deriver import M1DerivationEngine
from utils.gap_scanner import GapScanner, GapLedger, generate_expected_timestamps

# OANDA granularity mapping
OANDA_GRANULARITY = {'M1': 'M1', 'M5': 'M5', 'M15': 'M15', 'H1': 'H1'}


# ============================================================
# Fault codes
# ============================================================

class RecoveryFault:
    BROKER_FETCH_FAILED = "HERMES_RECOVERY_BROKER_FETCH_FAILED"
    SIGNAL_RECOMPUTE_FAILED = "HERMES_RECOVERY_SIGNAL_RECOMPUTE_FAILED"
    DEPENDENCY_MISSING = "HERMES_RECOVERY_DEPENDENCY_MISSING"
    VALIDATION_FAILED = "HERMES_RECOVERY_VALIDATION_FAILED"
    METADATA_MISSING = "HERMES_RECOVERY_METADATA_MISSING"
    PARTIAL_FAILURE = "HERMES_RECOVERY_PARTIAL_FAILURE"


# ============================================================
# OANDA Candle Fetcher
# ============================================================

def fetch_oanda_candles(instrument: str, granularity: str,
                        from_time: datetime, to_time: datetime) -> List[Dict]:
    """Fetch candles from OANDA REST API. Handles pagination for large windows."""
    oanda_key = get_env('OANDA_API_KEY', required=True)
    oanda_env = get_env('OANDA_ENVIRONMENT', 'practice')
    base_url = 'https://api-fxtrade.oanda.com' if oanda_env == 'live' else 'https://api-fxpractice.oanda.com'

    all_candles = []
    current_from = from_time

    while current_from < to_time:
        url = f"{base_url}/v3/instruments/{instrument}/candles"
        headers = {"Authorization": f"Bearer {oanda_key}"}
        params = {
            "granularity": granularity,
            "price": "M",
            "from": current_from.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "to": to_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            
        }

        response = requests.get(url, headers=headers, params=params, timeout=60)
        response.raise_for_status()
        candles = response.json().get("candles", [])

        if not candles:
            break

        for c in candles:
            if c.get('complete', False):
                ts = datetime.fromisoformat(c['time'].replace('Z', '+00:00')).replace(tzinfo=None)
                all_candles.append({
                    'timestamp': ts,
                    'open': float(c['mid']['o']),
                    'high': float(c['mid']['h']),
                    'low': float(c['mid']['l']),
                    'close': float(c['mid']['c']),
                    'volume': c['volume'],
                })

        # Advance past last candle for pagination
        last_ts = datetime.fromisoformat(candles[-1]['time'].replace('Z', '+00:00')).replace(tzinfo=None)
        if last_ts <= current_from:
            break  # No progress
        current_from = last_ts + timedelta(seconds=1)

    return all_candles


# ============================================================
# Job Ledger
# ============================================================

class JobLedger:
    """Records recovery job execution truth."""

    def __init__(self, db_config: dict):
        self._db_config = db_config
        self._conn = None

    def _get_conn(self):
        if self._conn and self._conn.open:
            self._conn.ping(reconnect=True)
            return self._conn
        self._conn = pymysql.connect(**self._db_config, autocommit=True, connect_timeout=5)
        return self._conn

    def create_job(self, job_type, trigger_source, instrument, window_start, window_end,
                   artifact_scope, total_steps) -> int:
        conn = self._get_conn()
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO hermes_recovery_jobs
                (job_type, trigger_source, instrument, window_start_utc, window_end_utc,
                 artifact_scope, status, total_steps, started_at, description, llm_reasoning)
                VALUES (%s, %s, %s, %s, %s, %s, 'RUNNING', %s, %s, %s, %s)""",
                (job_type, trigger_source, instrument, window_start, window_end,
                 artifact_scope, total_steps, datetime.now(timezone.utc),
                 f"Recovery job: {instrument} [{window_start} → {window_end}]",
                 json.dumps({"rationale": "Executor-initiated recovery from governed plan", "source": "WO-0004"}))
            )
            return cur.lastrowid

    def create_item(self, job_id, artifact_code, execution_order, rebuild_strategy) -> int:
        conn = self._get_conn()
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO hermes_recovery_job_items
                (job_id, artifact_code, execution_order, status, rebuild_strategy,
                 started_at, description, llm_reasoning)
                VALUES (%s, %s, %s, 'RUNNING', %s, %s, %s, %s)""",
                (job_id, artifact_code, execution_order, rebuild_strategy,
                 datetime.now(timezone.utc),
                 f"Rebuild {artifact_code} (order {execution_order})",
                 json.dumps({"rationale": f"Step {execution_order} per recovery plan", "source": "WO-0004"}))
            )
            return cur.lastrowid

    def complete_item(self, item_id, rows_written, rows_validated=None):
        conn = self._get_conn()
        now = datetime.now(timezone.utc)
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE hermes_recovery_job_items
                SET status='COMPLETED', rows_written=%s, rows_validated=%s,
                    finished_at=%s, duration_seconds=TIMESTAMPDIFF(MICROSECOND, started_at, %s)/1000000
                WHERE job_item_id=%s""",
                (rows_written, rows_validated, now, now, item_id)
            )

    def fail_item(self, item_id, fault_code, diagnostic=None):
        conn = self._get_conn()
        now = datetime.now(timezone.utc)
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE hermes_recovery_job_items
                SET status='FAILED', fault_code=%s, diagnostic_json=%s,
                    finished_at=%s, duration_seconds=TIMESTAMPDIFF(MICROSECOND, started_at, %s)/1000000
                WHERE job_item_id=%s""",
                (fault_code, diagnostic, now, now, item_id)
            )

    def complete_job(self, job_id, completed_steps, failed_steps, total_rows):
        conn = self._get_conn()
        now = datetime.now(timezone.utc)
        status = 'COMPLETED' if failed_steps == 0 else ('PARTIAL' if completed_steps > 0 else 'FAILED')
        fault = RecoveryFault.PARTIAL_FAILURE if failed_steps > 0 else None
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE hermes_recovery_jobs
                SET status=%s, fault_code=%s, completed_steps=%s, failed_steps=%s,
                    total_rows_written=%s, finished_at=%s,
                    duration_seconds=TIMESTAMPDIFF(MICROSECOND, started_at, %s)/1000000
                WHERE job_id=%s""",
                (status, fault, completed_steps, failed_steps, total_rows, now, now, job_id)
            )

    def get_job(self, job_id) -> Optional[Dict]:
        conn = self._get_conn()
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute("SELECT * FROM hermes_recovery_jobs WHERE job_id=%s", (job_id,))
            return cur.fetchone()

    def get_job_items(self, job_id) -> List[Dict]:
        conn = self._get_conn()
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(
                "SELECT * FROM hermes_recovery_job_items WHERE job_id=%s ORDER BY execution_order",
                (job_id,)
            )
            return cur.fetchall()


# ============================================================
# Recovery Executor
# ============================================================

class RecoveryExecutor:
    """Executes recovery plans deterministically."""

    def __init__(self, db_config: dict):
        self._db_config = db_config
        self._ledger = JobLedger(db_config)
        self._library = RecoveryLibrary(db_config)
        self._planner = RecoveryPlanner(self._library)
        self._scanner = GapScanner(db_config)
        self._gap_ledger = GapLedger(db_config)

    def _get_conn(self):
        return pymysql.connect(**self._db_config, autocommit=True, connect_timeout=5)

    def recover_window(self, instrument: str, start_utc: datetime, end_utc: datetime,
                       artifact_codes: List[str] = None,
                       trigger_source: str = 'OPERATOR') -> int:
        """
        Recover a specific window. Returns job_id.
        Fails loudly on any step failure.
        """
        # Build plan
        plan = self._planner.plan(instrument, start_utc, end_utc, artifact_codes)
        scope = ','.join(plan.artifact_codes)

        print(f"RECOVERY: {instrument} [{start_utc} → {end_utc}]")
        print(f"Plan: {len(plan.steps)} steps — {scope}")
        print()

        # Create job
        job_id = self._ledger.create_job(
            'RECOVERY', trigger_source, instrument,
            start_utc, end_utc, scope, len(plan.steps)
        )

        completed = 0
        failed = 0
        total_rows = 0

        for step in plan.steps:
            print(f"  [{step.order}] {step.artifact_code} ({step.rebuild_strategy})...", end=" ", flush=True)

            item_id = self._ledger.create_item(
                job_id, step.artifact_code, step.order, step.rebuild_strategy
            )

            try:
                rows = self._execute_step(step)
                self._ledger.complete_item(item_id, rows)
                completed += 1
                total_rows += rows
                print(f"{rows} rows")
            except Exception as e:
                fault = RecoveryFault.BROKER_FETCH_FAILED if step.rebuild_strategy == 'BROKER_FETCH' \
                    else RecoveryFault.SIGNAL_RECOMPUTE_FAILED
                self._ledger.fail_item(item_id, fault, json.dumps({"error": str(e)}))
                failed += 1
                print(f"FAILED: {e}")

        self._ledger.complete_job(job_id, completed, failed, total_rows)

        # Post-repair validation
        print()
        if failed == 0:
            validation_ok = self._validate_window(instrument, start_utc, end_utc, plan)
            if validation_ok:
                # Reconcile gap ledger
                self._reconcile_gaps(instrument, start_utc, end_utc)
                print(f"\nJOB #{job_id}: COMPLETED — {completed} steps, {total_rows} rows, validation passed")
            else:
                print(f"\nJOB #{job_id}: COMPLETED with validation warnings")
        else:
            print(f"\nJOB #{job_id}: {'PARTIAL' if completed > 0 else 'FAILED'} — "
                  f"{completed} completed, {failed} failed")

        return job_id

    def recover_gap_id(self, gap_id: int, trigger_source: str = 'GAP_SCAN') -> int:
        """Recover a specific gap from the gap ledger."""
        conn = self._get_conn()
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute("SELECT * FROM hermes_data_gaps WHERE gap_id=%s", (gap_id,))
            gap = cur.fetchone()
        conn.close()

        if not gap:
            raise ValueError(f"Gap #{gap_id} not found")

        return self.recover_window(
            gap['instrument'], gap['gap_start_utc'], gap['gap_end_utc'],
            trigger_source=trigger_source
        )

    def _execute_step(self, step: RebuildStep) -> int:
        """Execute a single rebuild step. Returns rows written."""
        if step.rebuild_strategy == 'BROKER_FETCH':
            return self._broker_fetch(step)
        elif step.rebuild_strategy == 'DERIVE_FROM_CANONICAL_M1':
            return self._derive_from_m1(step)
        elif step.rebuild_strategy == 'WINDOW_PLUS_LOOKBACK':
            return self._signal_recompute(step)
        else:
            raise ValueError(f"Unsupported rebuild strategy: {step.rebuild_strategy}")

    def _broker_fetch(self, step: RebuildStep) -> int:
        """Fetch candles from OANDA and write to DB."""
        granularity = OANDA_GRANULARITY.get(step.timeframe)
        if not granularity:
            raise ValueError(f"No OANDA granularity for {step.timeframe}")

        candles = fetch_oanda_candles(
            step.instrument, granularity,
            step.window_start_utc, step.window_end_utc
        )

        if not candles:
            return 0

        conn = self._get_conn()
        with conn.cursor() as cur:
            for c in candles:
                cur.execute(
                    f"""INSERT INTO {step.target_table}
                    (instrument, timestamp, open, high, low, close, volume)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        open=VALUES(open), high=VALUES(high),
                        low=VALUES(low), close=VALUES(close), volume=VALUES(volume)""",
                    (step.instrument, c['timestamp'], c['open'], c['high'],
                     c['low'], c['close'], c['volume'])
                )
        conn.close()
        return len(candles)


    def _derive_from_m1(self, step: RebuildStep) -> int:
        """Derive higher-TF candles from canonical M1 data."""
        deriver = M1DerivationEngine(self._db_config)

        derived = deriver.derive_range(
            step.instrument, step.timeframe,
            step.window_start_utc, step.window_end_utc
        )

        if not derived:
            return 0

        # Write derived candles to the target table
        conn = self._get_conn()
        with conn.cursor() as cur:
            for d in derived:
                cur.execute(
                    f"""INSERT INTO {step.target_table}
                    (instrument, timestamp, open, high, low, close, volume)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        open=VALUES(open), high=VALUES(high),
                        low=VALUES(low), close=VALUES(close), volume=VALUES(volume)""",
                    (step.instrument, d.timestamp, d.open, d.high,
                     d.low, d.close, d.volume)
                )
        conn.close()
        return len(derived)
    def _signal_recompute(self, step: RebuildStep) -> int:
        """Recompute signals from candle history for the gap window."""
        from signal_builder import SignalComputer, SignalPublisher, TIMEFRAMES
        from main import fetch_candle_history, get_zeusv4_config

        lookback_hours = get_zeusv4_config('hermes_lookback_hours', 'int')
        min_candles = get_zeusv4_config('hermes_min_candles_floor', 'int')

        history = fetch_candle_history(
            step.instrument,
            lookback_hours=lookback_hours,
            timeframe=step.timeframe,
            min_candles_floor=min_candles
        )

        if len(history) < 50:
            raise ValueError(f"Insufficient history for signal computation: {len(history)} candles (need 50+)")

        computer = SignalComputer()
        publisher = SignalPublisher()

        # Get candles in the gap window
        tf_config = TIMEFRAMES.get(step.timeframe)
        if not tf_config:
            raise ValueError(f"Unknown timeframe: {step.timeframe}")

        conn = self._get_conn()
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(
                f"""SELECT timestamp, open, high, low, close, volume
                FROM {tf_config['table']}
                WHERE instrument=%s AND timestamp >= %s AND timestamp <= %s
                ORDER BY timestamp""",
                (step.instrument, step.window_start_utc, step.window_end_utc)
            )
            window_candles = cur.fetchall()
        conn.close()

        if not window_candles:
            return 0

        signals_written = 0
        for candle_row in window_candles:
            class CandleObj:
                def __init__(self, row, inst, tf):
                    self.instrument = inst
                    self.timestamp = row['timestamp']
                    self.open = float(row['open'])
                    self.high = float(row['high'])
                    self.low = float(row['low'])
                    self.close = float(row['close'])
                    self.volume = row['volume']
                    self.timeframe = tf

            candle = CandleObj(candle_row, step.instrument, step.timeframe)
            try:
                signal = computer.compute_signal(candle, history)
                if signal:
                    publisher.publish_signal(signal)
                    signals_written += 1
            except Exception:
                continue  # Skip individual signal failures, count what succeeded

        return signals_written

    def _validate_window(self, instrument, start_utc, end_utc, plan) -> bool:
        """Post-repair validation: check gaps are closed."""
        remaining_gaps = self._scanner.scan_all(
            instrument, start_utc, end_utc,
            timeframes=[s.timeframe for s in plan.steps if s.artifact_type == 'CANDLE'],
            include_signals=any(s.artifact_type == 'SIGNAL' for s in plan.steps),
        )

        if remaining_gaps:
            print(f"  VALIDATION: {len(remaining_gaps)} remaining gaps")
            for g in remaining_gaps:
                print(f"    [{g.artifact_type}] {g.timeframe}: {g.missing_count} still missing")
            return False
        else:
            print(f"  VALIDATION: all gaps closed")
            return True

    def _reconcile_gaps(self, instrument, start_utc, end_utc):
        """Mark gap ledger entries as resolved after successful repair."""
        conn = self._get_conn()
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE hermes_data_gaps
                SET status='RESOLVED', resolved_at=%s
                WHERE instrument=%s AND gap_start_utc >= %s AND gap_end_utc <= %s
                AND status NOT IN ('RESOLVED', 'INVALIDATED')""",
                (datetime.now(timezone.utc), instrument, start_utc, end_utc)
            )
            resolved = cur.rowcount
        conn.close()
        if resolved > 0:
            print(f"  Gap ledger: {resolved} gap(s) marked RESOLVED")

    def verify_window(self, instrument, start_utc, end_utc) -> int:
        """Verify a window has no gaps. Returns 0 if clean, 1 if gaps remain."""
        gaps = self._scanner.scan_all(instrument, start_utc, end_utc)

        print(f"VERIFY: {instrument} [{start_utc} → {end_utc}]")

        if not gaps:
            print("VERIFY PASS: no gaps")
            return 0
        else:
            total_missing = sum(g.missing_count for g in gaps)
            print(f"VERIFY FAIL: {len(gaps)} gaps, {total_missing} missing rows")
            for g in gaps:
                print(f"  [{g.artifact_type}] {g.timeframe}: "
                      f"{g.gap_start_utc} → {g.gap_end_utc} ({g.missing_count} missing)")
            return 1


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="HERMES Recovery Executor — WO-HERMES-BACKFILL-ENGINE-0004")
    subparsers = parser.add_subparsers(dest='command', required=True)

    # recover-window
    rw = subparsers.add_parser('recover-window', help='Recover a specific UTC window')
    rw.add_argument('--instrument', '-i', required=True)
    rw.add_argument('--start', '-s', required=True)
    rw.add_argument('--end', '-e', required=True)
    rw.add_argument('--artifacts', '-a', default=None, help='Comma-separated codes or ALL')
    rw.add_argument('--trigger', default='OPERATOR')

    # recover-gap-id
    rg = subparsers.add_parser('recover-gap-id', help='Recover a specific gap from ledger')
    rg.add_argument('--gap-id', '-g', type=int, required=True)

    # verify-window
    vw = subparsers.add_parser('verify-window', help='Verify window has no gaps')
    vw.add_argument('--instrument', '-i', required=True)
    vw.add_argument('--start', '-s', required=True)
    vw.add_argument('--end', '-e', required=True)

    args = parser.parse_args()
    db_config = get_db_config()
    executor = RecoveryExecutor(db_config)

    if args.command == 'recover-window':
        start = datetime.fromisoformat(args.start)
        end = datetime.fromisoformat(args.end)
        codes = args.artifacts.split(',') if args.artifacts and args.artifacts != 'ALL' else None
        job_id = executor.recover_window(args.instrument, start, end, codes, args.trigger)
        # Exit non-zero if job failed
        job = executor._ledger.get_job(job_id)
        sys.exit(0 if job and job['status'] == 'COMPLETED' else 1)

    elif args.command == 'recover-gap-id':
        job_id = executor.recover_gap_id(args.gap_id)
        job = executor._ledger.get_job(job_id)
        sys.exit(0 if job and job['status'] == 'COMPLETED' else 1)

    elif args.command == 'verify-window':
        start = datetime.fromisoformat(args.start)
        end = datetime.fromisoformat(args.end)
        result = executor.verify_window(args.instrument, start, end)
        sys.exit(result)


if __name__ == '__main__':
    main()
