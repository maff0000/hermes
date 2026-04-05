"""
HERMES Canonical M1 Engine — First Valid Arrival Wins
WO-HERMES-CANONICAL-M1-ENGINE-C
Epic: EPIC-HERMES-SIGNAL-SOURCE-RESILIENCE-001

Accepts candidate M1 candles and decides canonical truth.
Per-instrument. Independent. No grouped transactions.

Rules:
- One canonical row per (instrument, minute_bucket_utc)
- First valid arrival wins
- Duplicates rejected with HERMES_CANONICAL_M1_REJECTED_DUPLICATE
- Late arrivals rejected with HERMES_CANONICAL_M1_REJECTED_LATE
- Invalid candidates rejected with HERMES_CANONICAL_M1_REJECTED_INVALID
- Repair mode can overwrite with explicit marking (ingest_mode=REPAIR)
- Rejection logged to hermes_candidate_log
- No cross-instrument lock, transaction, or dependency
"""
import json
import logging
import sys
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

import pymysql
import pymysql.cursors

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.collection_contract import (
    CandidateM1, AcceptResult, AcceptStatus,
    SourcePolicyReader,
)

logger = logging.getLogger("hermes.canonical_engine")


# ============================================================
# Fault codes
# ============================================================

class CanonicalFault:
    REJECTED_DUPLICATE = "HERMES_CANONICAL_M1_REJECTED_DUPLICATE"
    REJECTED_LATE = "HERMES_CANONICAL_M1_REJECTED_LATE"
    REJECTED_INVALID = "HERMES_CANONICAL_M1_REJECTED_INVALID"
    REPAIR_OVERWRITE = "HERMES_CANONICAL_M1_REPAIR_OVERWRITE"


# ============================================================
# Canonical M1 Engine
# ============================================================

class CanonicalM1Engine:
    """
    Accepts or rejects candidate M1 candles for canonical truth.

    ISOLATION RULES:
    - Each submit_candidate() call operates on ONE instrument
    - No transaction spans multiple instruments
    - No lock or mutex groups instruments
    - One instrument's failure does NOT block another
    - Connection is per-call (no shared state between instruments)
    """

    def __init__(self, db_config: dict, logger=None):
        self._db_config = db_config
        self._policy_reader = SourcePolicyReader(db_config)
        self._log = logger or logging.getLogger("hermes.canonical_engine")

        # Load config
        self._reject_if_exists = self._load_bool_config('canonical_m1_reject_if_exists', True)
        self._log_rejected = self._load_bool_config('canonical_m1_log_rejected_candidates', True)
        self._default_acceptance_window = self._load_int_config('canonical_m1_default_acceptance_window_sec', 120)

    def _load_bool_config(self, key: str, fallback: bool) -> bool:
        try:
            conn = pymysql.connect(**self._db_config, autocommit=True, connect_timeout=5)
            with conn.cursor() as cur:
                cur.execute("SELECT config_value FROM hermes_config WHERE config_key=%s AND enabled=1", (key,))
                row = cur.fetchone()
            conn.close()
            if row:
                return row[0].lower() in ('true', '1', 'yes')
            raise ValueError(f"GOV-CFG-001: Config key '{key}' not found in hermes_config")
        except pymysql.Error:
            raise

    def _load_int_config(self, key: str, fallback: int) -> int:
        try:
            conn = pymysql.connect(**self._db_config, autocommit=True, connect_timeout=5)
            with conn.cursor() as cur:
                cur.execute("SELECT config_value FROM hermes_config WHERE config_key=%s AND enabled=1", (key,))
                row = cur.fetchone()
            conn.close()
            if row:
                return int(row[0])
            raise ValueError(f"GOV-CFG-001: Config key '{key}' not found in hermes_config")
        except pymysql.Error:
            raise

    def _get_conn(self):
        """Fresh connection per call. No shared state between instruments."""
        return pymysql.connect(**self._db_config, autocommit=True, connect_timeout=5)

    def submit_candidate(self, candidate: CandidateM1) -> AcceptResult:
        """
        Submit a candidate M1 candle for canonical acceptance.

        This method operates on ONE instrument. It opens its own
        connection and closes it. No shared state with other instruments.

        Returns AcceptResult with status and fault code.
        """
        now = datetime.now(timezone.utc)

        # --- Validate ---
        if not candidate.is_valid():
            result = AcceptResult(
                status=AcceptStatus.REJECTED_INVALID,
                instrument=candidate.instrument,
                minute_bucket_utc=candidate.minute_bucket_utc,
                source_id=candidate.source_id,
                fault_code=CanonicalFault.REJECTED_INVALID,
                detail="Candidate failed validity check",
            )
            self._log_rejection(candidate, result)
            return result

        # --- Check acceptance window ---
        policy = self._policy_reader.get_policy(candidate.instrument)
        acceptance_window = policy['acceptance_window_sec'] if policy else self._default_acceptance_window

        bucket_utc = candidate.minute_bucket_utc
        if bucket_utc.tzinfo is None:
            bucket_utc_aware = bucket_utc.replace(tzinfo=timezone.utc)
        else:
            bucket_utc_aware = bucket_utc

        age_seconds = (now - bucket_utc_aware).total_seconds()
        # Allow future buckets (clock skew) up to 60s
        if age_seconds > acceptance_window:
            result = AcceptResult(
                status=AcceptStatus.REJECTED_LATE,
                instrument=candidate.instrument,
                minute_bucket_utc=candidate.minute_bucket_utc,
                source_id=candidate.source_id,
                fault_code=CanonicalFault.REJECTED_LATE,
                detail=f"Candidate is {age_seconds:.0f}s old, window is {acceptance_window}s",
            )
            self._log_rejection(candidate, result)
            return result

        # --- Check for existing canonical row ---
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM canonical_m1 WHERE instrument=%s AND minute_bucket_utc=%s",
                    (candidate.instrument, candidate.minute_bucket_utc)
                )
                existing = cur.fetchone()

            if existing and self._reject_if_exists:
                result = AcceptResult(
                    status=AcceptStatus.REJECTED_DUPLICATE,
                    instrument=candidate.instrument,
                    minute_bucket_utc=candidate.minute_bucket_utc,
                    source_id=candidate.source_id,
                    fault_code=CanonicalFault.REJECTED_DUPLICATE,
                    detail="Canonical row already exists",
                )
                self._log_rejection(candidate, result)
                return result

            # --- Accept ---
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO canonical_m1
                    (instrument, minute_bucket_utc, open, high, low, close, volume,
                     source_id, ingest_mode, arrival_utc, source_timestamp_utc, complete,
                     description, llm_reasoning)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'LIVE_FIRST_ACCEPTED', %s, %s, %s, %s, %s)""",
                    (
                        candidate.instrument, candidate.minute_bucket_utc,
                        candidate.open, candidate.high, candidate.low, candidate.close,
                        candidate.volume, candidate.source_id, now,
                        candidate.source_timestamp_utc, 1,
                        f"Canonical M1 accepted: {candidate.instrument} {candidate.minute_bucket_utc} from {candidate.source_id}",
                        json.dumps({"rationale": "First valid arrival within acceptance window", "source": "canonical_m1_engine"}),
                    )
                )

            # Log accepted candidate too (for audit completeness)
            if self._log_rejected:
                self._log_candidate(candidate, now, None)

            return AcceptResult(
                status=AcceptStatus.ACCEPTED,
                instrument=candidate.instrument,
                minute_bucket_utc=candidate.minute_bucket_utc,
                source_id=candidate.source_id,
            )

        except pymysql.IntegrityError:
            # Race condition: another thread inserted between SELECT and INSERT
            result = AcceptResult(
                status=AcceptStatus.REJECTED_DUPLICATE,
                instrument=candidate.instrument,
                minute_bucket_utc=candidate.minute_bucket_utc,
                source_id=candidate.source_id,
                fault_code=CanonicalFault.REJECTED_DUPLICATE,
                detail="Canonical row inserted by concurrent writer",
            )
            self._log_rejection(candidate, result)
            return result
        finally:
            conn.close()

    def submit_repair(self, candidate: CandidateM1, job_id: Optional[int] = None) -> AcceptResult:
        """
        Submit a repair/backfill M1 candle. Explicitly overwrites.

        This is the ONLY path that can replace an existing canonical row.
        It marks the row with ingest_mode=REPAIR and logs the overwrite.
        """
        now = datetime.now(timezone.utc)

        if not candidate.is_valid():
            return AcceptResult(
                status=AcceptStatus.REJECTED_INVALID,
                instrument=candidate.instrument,
                minute_bucket_utc=candidate.minute_bucket_utc,
                source_id=candidate.source_id,
                fault_code=CanonicalFault.REJECTED_INVALID,
                detail="Repair candidate failed validity check",
            )

        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO canonical_m1
                    (instrument, minute_bucket_utc, open, high, low, close, volume,
                     source_id, ingest_mode, arrival_utc, source_timestamp_utc, complete,
                     description, llm_reasoning)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'REPAIR', %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        open=VALUES(open), high=VALUES(high), low=VALUES(low),
                        close=VALUES(close), volume=VALUES(volume),
                        source_id=VALUES(source_id), ingest_mode='REPAIR',
                        arrival_utc=VALUES(arrival_utc),
                        source_timestamp_utc=VALUES(source_timestamp_utc),
                        description=VALUES(description), llm_reasoning=VALUES(llm_reasoning)""",
                    (
                        candidate.instrument, candidate.minute_bucket_utc,
                        candidate.open, candidate.high, candidate.low, candidate.close,
                        candidate.volume, candidate.source_id, now,
                        candidate.source_timestamp_utc, 1,
                        f"Canonical M1 repair: {candidate.instrument} {candidate.minute_bucket_utc} from {candidate.source_id} (job_id={job_id})",
                        json.dumps({"rationale": "Explicit repair overwrite", "job_id": job_id, "source": "canonical_m1_engine"}),
                    )
                )

            self._log.info(
                f"[{CanonicalFault.REPAIR_OVERWRITE}] Repair: {candidate.instrument} "
                f"{candidate.minute_bucket_utc} from {candidate.source_id}"
            )

            return AcceptResult(
                status=AcceptStatus.ACCEPTED,
                instrument=candidate.instrument,
                minute_bucket_utc=candidate.minute_bucket_utc,
                source_id=candidate.source_id,
            )
        finally:
            conn.close()

    def get_canonical(self, instrument: str, minute_bucket_utc: datetime) -> Optional[dict]:
        """Read a single canonical M1 row."""
        conn = self._get_conn()
        try:
            with conn.cursor(pymysql.cursors.DictCursor) as cur:
                cur.execute(
                    "SELECT * FROM canonical_m1 WHERE instrument=%s AND minute_bucket_utc=%s",
                    (instrument, minute_bucket_utc)
                )
                return cur.fetchone()
        finally:
            conn.close()

    def get_latest(self, instrument: str) -> Optional[dict]:
        """Get the most recent canonical M1 row for an instrument."""
        conn = self._get_conn()
        try:
            with conn.cursor(pymysql.cursors.DictCursor) as cur:
                cur.execute(
                    "SELECT * FROM canonical_m1 WHERE instrument=%s ORDER BY minute_bucket_utc DESC LIMIT 1",
                    (instrument,)
                )
                return cur.fetchone()
        finally:
            conn.close()

    def _log_rejection(self, candidate: CandidateM1, result: AcceptResult):
        """Log rejected candidate and emit warning."""
        self._log.warning(
            f"[{result.fault_code}] {candidate.instrument} {candidate.minute_bucket_utc} "
            f"from {candidate.source_id}: {result.detail}"
        )
        if self._log_rejected:
            self._log_candidate(candidate, datetime.now(timezone.utc), result.fault_code)

    def _log_candidate(self, candidate: CandidateM1, arrival_utc: datetime, rejection_reason: Optional[str]):
        """Write to hermes_candidate_log."""
        try:
            conn = self._get_conn()
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO hermes_candidate_log
                    (instrument, minute_bucket_utc, source_id, ingest_mode,
                     rejection_reason, open, high, low, close, arrival_utc)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        candidate.instrument, candidate.minute_bucket_utc,
                        candidate.source_id, 'LIVE_FIRST_ACCEPTED',
                        rejection_reason, candidate.open, candidate.high,
                        candidate.low, candidate.close, arrival_utc,
                    )
                )
            conn.close()
        except Exception as e:
            self._log.error(f"Failed to log candidate: {e}")
