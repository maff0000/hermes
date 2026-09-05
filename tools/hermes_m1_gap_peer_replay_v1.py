"""HERMES M1/M5 gap peer-replay engine v1 — deterministic, idempotent SQL candle-history repair.
WO-HELM-HERMES-DEV-DETERMINISTIC-CANDLE-GAP-RECONSTRUCTION-0001,
WO-HELM-HERMES-DEV-M5-GAP-REPAIR-AND-SIGNAL-COLLAPSE-RCA-0001.

Repairs a bounded, explicit window of missing SQL candle history for ONE instrument by:
  1. Copying missing M1 rows from a peer HERMES source database's candles_M1 into the target
     database's candles_M1 (the target is PROD in a real repair, a disposable proof table on DEV
     during this WO's proof).
  2. Deriving M15 / H1 / D1 from the now-complete target M1 via the EXISTING, UNMODIFIED
     utils.m1_deriver.M1DerivationEngine (pointed at candles_M1 via its new source_table param —
     see utils/m1_deriver.py — because canonical_m1, the engine's historical default source, has
     been stale since 2026-06-17 and holds zero rows for any recent gap).

Deliberately narrower than the generic hermes_recovery_library/RecoveryExecutor machinery it sits
beside: this tool is READ-then-CLASSIFY-then-INSERT-ONLY-WHAT-IS-NEW. It never uses
"ON DUPLICATE KEY UPDATE" (the pattern used elsewhere in recovery_executor.py /
backfill_candles_h4_m30.py for a live/derived refresh) because a peer-sourced PROD repair must
NEVER silently overwrite a row that is already there for an unexplained reason — an existing row
that disagrees with the peer source is a CONFLICT requiring architect review, not an autorepair.

Row classification (per timestamp, per timeframe):
  NEW               — present in source, absent in target -> eligible to write.
  MATCH             — present in both, OHLCV agree (within FLOAT_TOL) -> no write (idempotent).
  CONFLICT          — present in both, OHLCV disagree -> BLOCKED, never written, never overwritten.
  SOURCE_DATA_MISSING — absent in target AND absent in source -> cannot be reconstructed from this
                        source; carried forward for a governed decision (OANDA re-fetch / accept-loss).

Guarantees:
  - Dry-run by construction: plan_replay() never writes. execute_replay() is the only write path
    and only ever performs plain INSERTs of rows classified NEW by the immediately-preceding plan.
  - Idempotent: a second execute_replay() over the same window reclassifies the just-written rows
    as MATCH and writes zero further rows (proven in tests/proof, not merely asserted).
  - No look-ahead: a higher-timeframe bucket is only derived when ALL its constituent M1 rows are
    present in TARGET and the bucket has fully closed relative to `now_utc` (mirrors
    scripts/backfill_candles_h4_m30.py's last_closed_bucket_end pattern) — a partially-repaired
    bucket is never derived from a subset of its true constituents.
  - Bounded: every read/write is scoped to the explicit [window_start_utc, window_end_utc) the
    caller supplies. No unbounded scans.
  - UTC only.

M5 (added WO-HELM-HERMES-DEV-M5-GAP-REPAIR-AND-SIGNAL-COLLAPSE-RCA-0001): plan_m5_replay() /
execute_m5_replay() are a SEPARATE, manifest-driven pair — NOT a generalisation of plan_replay()'s
full-window scan. M5's real PROD gap is Swiss-cheese (~150-190 missing timestamps per instrument out
of ~840 in a representative 3-day window), unlike M1's total target-absence across its entire gap.
Scanning a whole M5 window and comparing source-vs-target the way plan_replay() does would compare
DEV against PROD's own already-present, never-intended-to-be-touched rows for the overwhelming
majority of timestamps — and this WO proved that comparison is NOT a proxy for "same data, different
copy": DEV and PROD are two independent LIVE OANDA streaming connections to the same account
(001-004-20020670-001, environment=live on both), and even on a normal control day only 1.8%-17% of
overlapping M5 rows match byte-exact (FX pairs sub-pip and mostly exact; metals/index show a real
tail up to ~$1.43/~0.5pts on fast ticks; volume differs on most rows by a small amount). A
full-window scan would therefore misclassify nearly every untouched row as CONFLICT and permanently
block execution. plan_m5_replay() instead classifies EXACTLY the caller-supplied missing-timestamp
manifest (Section 8: no date-range bulk copy) — those rows are expected to be target-absent by
construction, so MATCH/CONFLICT there only ever fires on a genuine surprise (stale manifest or a
race), never on ordinary inter-stream noise on rows nobody asked to repair. There is no M5-derived
higher-timeframe step: M5 is confirmed a fully independent primary write path (Section-9 evidence),
not M1-derived, so plan_m5_replay()/execute_m5_replay() never touch M15/H1/D1.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pymysql
import pymysql.cursors

from utils.m1_deriver import (
    M1DerivationEngine, TIMEFRAME_SECONDS, get_bucket_start, get_forex_day_start,
    SUPPORTED_SOURCE_TABLES,
)

SOURCE_MARKER = 'M1_GAP_REPLAY_V1'  # <=20 chars — candles_D1.source is varchar(20), the tightest of the four target tables
FLOAT_TOL = 0.00001  # tighter than m1_deriver's compare_with_legacy (0.001) — this is same-broker-stream comparison, not cross-derivation-method comparison.
M1_TABLE = 'candles_M1'
DERIVED_TIMEFRAMES = ('M15', 'H1', 'D1')  # M5 excluded from M1-derivation: it is its own independent
                                           # primary write path, not M1-derived (see plan_m5_replay
                                           # below for its Swiss-cheese PROD gap and separate,
                                           # manifest-driven repair path — NOT "ungapped", corrects a
                                           # prior WO's mistaken assumption). M30/H4 excluded: SQL
                                           # sinks confirmed dead (no writer) since 2026-06-15,
                                           # unrelated to this incident (see WO evidence).
TARGET_TABLES = {'M1': M1_TABLE, 'M15': 'candles_M15', 'H1': 'candles_H1', 'D1': 'candles_D1'}

NEW = 'NEW'
MATCH = 'MATCH'
CONFLICT = 'CONFLICT'
SOURCE_DATA_MISSING = 'SOURCE_DATA_MISSING'


@dataclass
class RowClassification:
    timeframe: str
    timestamp: datetime
    status: str
    source_row: Optional[dict] = None
    target_row: Optional[dict] = None


@dataclass
class ReplayPlan:
    instrument: str
    window_start_utc: datetime
    window_end_utc: datetime
    now_utc: datetime
    classifications: List[RowClassification] = field(default_factory=list)

    def by_status(self, timeframe: str, status: str) -> List[RowClassification]:
        return [c for c in self.classifications if c.timeframe == timeframe and c.status == status]

    def counts(self) -> Dict[str, Dict[str, int]]:
        out = {}
        for tf in ('M1',) + DERIVED_TIMEFRAMES:
            out[tf] = {s: len(self.by_status(tf, s)) for s in (NEW, MATCH, CONFLICT, SOURCE_DATA_MISSING)}
        return out

    def has_conflicts(self) -> bool:
        return any(c.status == CONFLICT for c in self.classifications)

    def fingerprint(self) -> str:
        """Stable digest over the plan's decisions — used to prove a second plan (post-execute)
        collapses to all-MATCH (idempotence), and to record provenance in diagnostic_json."""
        payload = json.dumps(
            sorted((c.timeframe, c.timestamp.isoformat(), c.status) for c in self.classifications),
            separators=(',', ':'),
        )
        return hashlib.sha256(payload.encode()).hexdigest()


def _ohlcv_match(a: dict, b: dict) -> bool:
    for k in ('open', 'high', 'low', 'close'):
        if abs(float(a[k]) - float(b[k])) > FLOAT_TOL:
            return False
    return True  # volume deliberately excluded — same known semantic note as m1_deriver.compare_with_legacy


def _fetch_rows(conn, table: str, instrument: str, start: datetime, end: datetime) -> Dict[datetime, dict]:
    with conn.cursor(pymysql.cursors.DictCursor) as cur:
        cur.execute(
            f"SELECT timestamp, open, high, low, close, volume, complete FROM {table} "
            f"WHERE instrument = %s AND timestamp >= %s AND timestamp < %s ORDER BY timestamp",
            (instrument, start, end),
        )
        return {r['timestamp']: r for r in cur.fetchall()}


def _storage_ts(timeframe: str, bucket_start: datetime) -> datetime:
    """candles_D1 stores each row keyed at 00:00 UTC of the calendar date the forex trading day is
    conventionally labeled by (the day it opens INTO) — TWO HOURS AFTER get_forex_day_start's 22:00
    UTC boundary, which is what M1DerivationEngine/get_m1_buckets_for_tf correctly use for the actual
    M1 windowing. Discovered during this WO's golden-comparison proof: a genuine, previously-latent
    key-convention mismatch in the shared D1 recovery path (CANDLE_D1 / DERIVE_FROM_CANONICAL_M1 in
    hermes_recovery_library), never caught because that path's only prior source, canonical_m1, has
    been stale since 2026-06-17 and this D1 path has apparently never actually executed against real
    data. bucket_start here MUST remain the true 22:00 boundary everywhere else in this module (it is
    what M1DerivationEngine.derive_candle expects) — only the final storage/lookup key is shifted."""
    return bucket_start + timedelta(hours=2) if timeframe == 'D1' else bucket_start


def _last_closed_bucket_end(now_utc: datetime, timeframe: str) -> datetime:
    if timeframe == 'D1':
        return get_forex_day_start(now_utc)
    return get_bucket_start(now_utc, TIMEFRAME_SECONDS[timeframe])


def _expected_m1_timestamps(bucket_start: datetime, timeframe: str) -> List[datetime]:
    if timeframe == 'D1':
        return [bucket_start + timedelta(minutes=i) for i in range(1440)]
    n = TIMEFRAME_SECONDS[timeframe] // 60
    return [bucket_start + timedelta(minutes=i) for i in range(n)]


def _bucket_starts_in_window(start: datetime, end: datetime, timeframe: str) -> List[datetime]:
    if timeframe == 'D1':
        cur = get_forex_day_start(start)
        step = timedelta(hours=24)
    else:
        cur = get_bucket_start(start, TIMEFRAME_SECONDS[timeframe])
        step = timedelta(seconds=TIMEFRAME_SECONDS[timeframe])
    out = []
    while cur < end:
        out.append(cur)
        cur += step
    return out


def plan_replay(*, source_db_config: dict, target_db_config: dict, instrument: str,
                window_start_utc: datetime, window_end_utc: datetime,
                now_utc: Optional[datetime] = None,
                target_tables: Optional[Dict[str, str]] = None) -> ReplayPlan:
    """Read-only. Never writes. Classifies every M1 timestamp in [start, end) and every fully-closed
    higher-timeframe bucket overlapping the window, for ONE instrument.

    target_tables overrides TARGET_TABLES (default) — used ONLY to point a proof/rehearsal run at
    disposable tables (e.g. wo13_proof_candles_*) instead of the real candles_* tables. The peer
    SOURCE is always the real candles_M1 on the source host; only the TARGET side is ever redirected."""
    # UTC_AUDIT_METADATA_OK: deliberately naive, matching m1_deriver.py's naive-UTC epoch bucket math.
    now_utc = now_utc or datetime.utcnow()
    tables = target_tables or TARGET_TABLES
    caller_start, caller_end = window_start_utc, window_end_utc  # the actual repair scope — never widened
    plan = ReplayPlan(instrument=instrument, window_start_utc=caller_start,
                      window_end_utc=caller_end, now_utc=now_utc)

    # READ range is snapped out to whole forex-day (D1, 22:00 UTC) boundaries so a bucket straddling
    # the caller's raw window edge can still see ALL its constituent M1 minutes — the minutes outside
    # [caller_start, caller_end) are read-only CONTEXT (target's own pre-existing, un-repaired truth),
    # never classified or written. Without this a boundary bucket would derive a wrong OHLCV from a
    # silently truncated constituent set.
    read_start = get_forex_day_start(caller_start)
    read_end = caller_end if caller_end == get_forex_day_start(caller_end) \
        else get_forex_day_start(caller_end) + timedelta(hours=24)

    src = pymysql.connect(**source_db_config, autocommit=True, connect_timeout=5)
    tgt = pymysql.connect(**target_db_config, autocommit=True, connect_timeout=5)
    try:
        source_m1 = _fetch_rows(src, M1_TABLE, instrument, read_start, read_end)
        target_m1 = _fetch_rows(tgt, tables['M1'], instrument, read_start, read_end)

        cursor = caller_start
        while cursor < caller_end:
            s, t = source_m1.get(cursor), target_m1.get(cursor)
            if t is not None and s is not None:
                status = MATCH if _ohlcv_match(s, t) else CONFLICT
            elif t is not None and s is None:
                status = CONFLICT  # target already has an unexplained row the source can't corroborate
            elif s is not None and t is None:
                status = NEW
            else:
                status = SOURCE_DATA_MISSING
            plan.classifications.append(RowClassification('M1', cursor, status, s, t))
            cursor += timedelta(minutes=1)

        # Derived timeframes: reuses the SAME partial-candle convention already governed by
        # scripts/backfill_candles_h4_m30.py — a bucket is derived from WHATEVER constituent M1
        # minutes are resolvable, with complete=1 only when every constituent minute in the true
        # (unwindowed) bucket was available. Only a CONFLICT minute inside [caller_start, caller_end)
        # blocks the whole bucket — a disputed value is a governance question, never derived around.
        # A minute genuinely absent from both sides (SOURCE_DATA_MISSING) does not block: DEV's own
        # live aggregator would have produced exactly the same partial candle. NOTE: the `complete`
        # flag is written for provenance/observability but deliberately NOT part of MATCH/CONFLICT
        # comparison below — this repair's M1-row-count notion of completeness is a different, and
        # sometimes disagreeing, concept from the live tick-based aggregator's completeness signal
        # (proven empirically during this WO's DEV proof: identical OHLCV, occasionally disagreeing
        # `complete`, on buckets never touched by the incident) — treating that disagreement as a
        # blocking CONFLICT would be a false positive, not a real data defect.
        resolvable_m1 = {
            c.timestamp for c in plan.classifications
            if c.status in (NEW, MATCH)
        }
        conflict_m1 = {
            c.timestamp for c in plan.classifications
            if c.status == CONFLICT
        }

        # A derived-timeframe bucket is only ever a candidate for CREATION, never for re-derivation
        # or overwrite-checking of a row that already exists. On the real incident, every affected
        # M15/H1/D1 bucket is confirmed completely ABSENT (Phase 1 evidence: rows_strictly_between=0
        # for M15/H1; both missing D1 dates entirely absent) — there is no "partially there" case to
        # reconcile. Re-deriving an EXISTING row from raw M1 and comparing it would be unsound anyway:
        # this WO's own proof caught a genuine, pre-existing (non-incident) OHLC divergence between
        # M1-row aggregation and the true tick-based value at a daily-rollover-adjacent bucket — an
        # M1-based reconstruction is not always byte-identical to the original tick-based candle even
        # when nothing is wrong. So: leave every already-present row untouched, unconditionally.
        for tf in DERIVED_TIMEFRAMES:
            table = tables[tf]
            closed_end = _last_closed_bucket_end(now_utc, tf)
            # D1 fetches one extra day of history before read_start so the prior-close carry-forward
            # lookup (below) can find the immediately-preceding D1 row even when it falls just outside
            # the caller's declared window.
            existing_fetch_start = read_start - timedelta(hours=24) if tf == 'D1' else read_start
            existing = _fetch_rows(tgt, table, instrument, existing_fetch_start, read_end)
            d1_prev_close = None  # only meaningful/used when tf == 'D1' — see note below
            for bucket_start in _bucket_starts_in_window(read_start, read_end, tf):
                bucket_end = (bucket_start + timedelta(hours=24)) if tf == 'D1' else \
                    (bucket_start + timedelta(seconds=TIMEFRAME_SECONDS[tf]))
                if bucket_end > closed_end:
                    continue  # forming/not-yet-closed — never derive it (no look-ahead)
                if not (bucket_end > caller_start and bucket_start < caller_end):
                    continue  # bucket does not overlap the caller's actual repair window — not in scope
                if existing.get(_storage_ts(tf, bucket_start)) is not None:
                    continue  # already present — never re-derived, never compared, never touched
                expected = _expected_m1_timestamps(bucket_start, tf)
                if any(ts in conflict_m1 for ts in expected):
                    plan.classifications.append(RowClassification(tf, bucket_start, CONFLICT))
                    continue
                available = {}
                for ts in expected:
                    if ts in resolvable_m1:
                        available[ts] = source_m1.get(ts) or target_m1.get(ts)
                    elif not (caller_start <= ts < caller_end):
                        ctx = target_m1.get(ts)  # read-only context outside repair scope
                        if ctx is not None:
                            available[ts] = ctx
                if not available:
                    plan.classifications.append(RowClassification(tf, bucket_start, SOURCE_DATA_MISSING))
                    continue
                derived = _derive_bucket_from_rows(instrument, tf, bucket_start, available,
                                                   expected_count=len(expected))
                if tf == 'D1':
                    # WO discovery: candles_D1's `open` is NOT "the first constituent M1's open" — it
                    # is the PRIOR forex day's close carried forward (proven during this WO's proof:
                    # EUR_USD matched to 5dp exactly; XAU_USD/SPX500_USD matched within a small
                    # rollover/swap adjustment on a metals/index instrument — consistent with a
                    # continuous-price-feed daily-bucketing convention, not a fresh session open).
                    # M1DerivationEngine's own D1 open (first-row-open) does NOT reproduce this and
                    # would silently write a wrong `open` for every M1-derived D1 candle — never used
                    # for the actual write; only high/low/close/volume come from the M1 aggregation.
                    if d1_prev_close is None:
                        prev_bucket_key = _storage_ts('D1', bucket_start - timedelta(hours=24))
                        prev_existing = existing.get(prev_bucket_key)
                        if prev_existing is not None:
                            d1_prev_close = float(prev_existing['close'])
                    if d1_prev_close is not None:
                        derived['open'] = d1_prev_close
                    d1_prev_close = derived['close']
                plan.classifications.append(RowClassification(tf, bucket_start, NEW, derived, None))
    finally:
        src.close()
        tgt.close()
    return plan


def _derive_bucket_from_rows(instrument: str, timeframe: str, bucket_start: datetime,
                             m1_rows_by_ts: Dict[datetime, dict], *, expected_count: int) -> dict:
    """Mirrors backfill_candles_h4_m30.py's aggregate_ohlcv exactly: first open, max high, min low,
    last close, sum volume — over whatever constituent minutes are available. complete=1 only when
    every constituent minute in the full (unwindowed) bucket was available."""
    ordered = [m1_rows_by_ts[ts] for ts in sorted(m1_rows_by_ts)]
    return {
        'instrument': instrument, 'timeframe': timeframe, 'timestamp': bucket_start,
        'complete': 1 if len(ordered) == expected_count else 0,
        'open': float(ordered[0]['open']),
        'high': max(float(r['high']) for r in ordered),
        'low': min(float(r['low']) for r in ordered),
        'close': float(ordered[-1]['close']),
        'volume': sum(int(r['volume'] or 0) for r in ordered),
    }


def _insert_row(conn, table: str, instrument: str, ts: datetime, row: dict, race_conflicts: list, tf: str) -> int:
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {table} "
                f"(instrument, timestamp, open, high, low, close, volume, complete, source) "
                f"VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (instrument, ts, row['open'], row['high'], row['low'], row['close'],
                 row.get('volume', 0), int(row.get('complete', 1)), SOURCE_MARKER),
            )
        return 1
    except pymysql.err.IntegrityError:
        race_conflicts.append({'timeframe': tf, 'timestamp': ts.isoformat()})
        return 0


def execute_replay(*, target_db_config: dict, plan: ReplayPlan,
                   target_tables: Optional[Dict[str, str]] = None) -> dict:
    """The only write path. INSERTs (never ON DUPLICATE KEY UPDATE) exactly the rows plan_replay
    classified NEW. A concurrent-insert race is caught as a fresh conflict, never silently absorbed.
    Raises GOV-M1-REPLAY-CONFLICT if the plan contains any CONFLICT (refuses to execute a plan with
    unresolved conflicts — conflicts require a separate architect-reviewed WO, never auto-resolved).

    M1 NEW rows are written first, from the plan's peer-sourced payload. Derived-timeframe (M15/H1/D1)
    NEW rows are then RE-DERIVED from scratch via the canonical, unmodified
    utils.m1_deriver.M1DerivationEngine reading the now-repaired target M1 table — the plan's own
    precomputed derived value (an independently-authored mirror, used only so dry-run planning never
    has to write) is cross-checked against this canonical re-derivation and a mismatch is fail-loud
    (GOV-M1-REPLAY-DERIVE-MISMATCH), never silently reconciled either way.

    target_tables must be the SAME mapping (or None -> real candles_*) passed to plan_replay()."""
    if plan.has_conflicts():
        conflicts = [c for c in plan.classifications if c.status == CONFLICT]
        raise ValueError(
            f"GOV-M1-REPLAY-CONFLICT: {len(conflicts)} conflicting row(s) for {plan.instrument} "
            f"in [{plan.window_start_utc}, {plan.window_end_utc}) — refusing to execute; "
            "requires architect review, never auto-resolved."
        )
    tables = target_tables or TARGET_TABLES
    written = {'M1': 0, 'M15': 0, 'H1': 0, 'D1': 0}
    race_conflicts = []
    conn = pymysql.connect(**target_db_config, autocommit=True, connect_timeout=5)
    try:
        for c in plan.by_status('M1', NEW):
            written['M1'] += _insert_row(conn, tables['M1'], plan.instrument, c.timestamp, c.source_row,
                                          race_conflicts, 'M1')

        m1_ts_col = SUPPORTED_SOURCE_TABLES.get(tables['M1'], 'timestamp')
        deriver = M1DerivationEngine(target_db_config, source_table=tables['M1'],
                                     source_timestamp_column=m1_ts_col)
        for tf in DERIVED_TIMEFRAMES:
            table = tables[tf]
            d1_prev_close = None
            for c in plan.by_status(tf, NEW):
                fresh = deriver.derive_candle(plan.instrument, tf, c.timestamp)
                if fresh is None:
                    raise ValueError(f"GOV-M1-REPLAY-DERIVE-INCOMPLETE: {tf} bucket {c.timestamp} "
                                     f"was planned NEW but re-derivation against the repaired target "
                                     f"found zero constituent M1 rows — the M1 write above did not "
                                     f"land as expected")
                fresh_row = {'open': fresh.open, 'high': fresh.high, 'low': fresh.low,
                            'close': fresh.close, 'volume': fresh.volume, 'complete': int(fresh.complete)}
                if tf == 'D1':
                    # Mirror plan_replay's open-carry-forward correction exactly (see its comment) so
                    # this cross-check compares like-for-like rather than raising a spurious mismatch
                    # against M1DerivationEngine's uncorrected first-row open.
                    if d1_prev_close is None:
                        with conn.cursor(pymysql.cursors.DictCursor) as cur:
                            cur.execute(f"SELECT close FROM {table} WHERE instrument=%s AND timestamp=%s",
                                       (plan.instrument, c.timestamp - timedelta(hours=22)))
                            row = cur.fetchone()
                        if row is not None:
                            d1_prev_close = float(row['close'])
                    if d1_prev_close is not None:
                        fresh_row['open'] = d1_prev_close
                    d1_prev_close = fresh_row['close']
                if not (_ohlcv_match(fresh_row, c.source_row)
                        and fresh_row['complete'] == int(c.source_row['complete'])):
                    raise ValueError(f"GOV-M1-REPLAY-DERIVE-MISMATCH: {tf} bucket {c.timestamp} — plan's "
                                     f"preview {c.source_row} disagrees with canonical M1DerivationEngine "
                                     f"re-derivation {fresh_row}")
                written[tf] += _insert_row(conn, table, plan.instrument, _storage_ts(tf, c.timestamp),
                                            fresh_row, race_conflicts, tf)
    finally:
        conn.close()
    if race_conflicts:
        raise ValueError(f"GOV-M1-REPLAY-RACE: {len(race_conflicts)} row(s) were written by another "
                         f"process between plan and execute — re-plan required: {race_conflicts[:5]}")
    return {'written': written, 'plan_fingerprint': plan.fingerprint()}


M5_TABLE = 'candles_M5'


@dataclass
class M5ReplayPlan:
    """Manifest-driven plan for M5 repair — see plan_m5_replay for why this deliberately does NOT
    reuse plan_replay's full-window scan."""
    instrument: str
    missing_timestamps: List[datetime]
    now_utc: datetime
    classifications: List[RowClassification] = field(default_factory=list)

    def by_status(self, status: str) -> List[RowClassification]:
        return [c for c in self.classifications if c.status == status]

    def counts(self) -> Dict[str, int]:
        return {s: len(self.by_status(s)) for s in (NEW, MATCH, CONFLICT, SOURCE_DATA_MISSING)}

    def has_conflicts(self) -> bool:
        return any(c.status == CONFLICT for c in self.classifications)

    def fingerprint(self) -> str:
        payload = json.dumps(
            sorted((c.timestamp.isoformat(), c.status) for c in self.classifications),
            separators=(',', ':'),
        )
        return hashlib.sha256(payload.encode()).hexdigest()


def plan_m5_replay(*, source_db_config: dict, target_db_config: dict, instrument: str,
                   missing_timestamps: List[datetime], target_table: Optional[str] = None) -> M5ReplayPlan:
    """Read-only. Never writes. Classifies EXACTLY the caller-supplied missing_timestamps — an
    explicit manifest, not a date-range scan (Section 8: 'no date-range bulk copy').

    This is deliberately NOT plan_replay()'s full-window-scan shape. M1's real gap was total
    target-absence across its entire window, so scanning every minute and comparing source-vs-target
    was safe (a present target row only ever meant "context outside the repair scope", never "an
    already-covered row we must not misjudge"). M5's real gap is Swiss-cheese: the overwhelming
    majority of timestamps in any realistic window already carry a PROD-native row, and — proven
    during this WO on a normal, non-incident control day (2026-08-25) — that PROD-native row
    legitimately disagrees with DEV's row a large fraction of the time on at least one field, because
    DEV and PROD are TWO INDEPENDENT LIVE OANDA STREAMING CONNECTIONS to the SAME account
    (001-004-20020670-001, environment=live on both), not mirrors: FX pairs diverge by at most
    ~0.6-0.8 pip (negligible), but metals/index (XAU_USD, SPX500_USD, and by the same live-feed
    architecture presumably XAG/XPT/XCU/WTICO) show a real tail up to ~$1.43 / ~0.5pts during
    fast-moving ticks, and volume differs on most rows by a small absolute amount. A full-window scan
    would therefore misclassify the vast majority of already-present, never-intended-to-be-touched
    rows as CONFLICT and permanently block execute_m5_replay via has_conflicts() — a repair that can
    never run is not a safe repair, it is a dead tool. Restricting classification to the explicit
    missing-timestamp manifest sidesteps this entirely: those rows are, by construction, expected to
    be absent from target, so the MATCH/CONFLICT/SOURCE_DATA_MISSING branches below only ever fire on
    genuine surprises (a stale manifest or a concurrent write) — exactly the cases Section 7 requires
    to be caught, never silently absorbed."""
    table = target_table or M5_TABLE
    # recorded on the plan for provenance only, never used to gate M5 classification (M5 has no
    # closed-bucket/derivation concept) —
    # UTC_AUDIT_METADATA_OK: deliberately naive, matching plan_replay's now_utc convention above.
    now_utc = datetime.utcnow()
    plan = M5ReplayPlan(instrument=instrument, missing_timestamps=list(missing_timestamps), now_utc=now_utc)
    if not missing_timestamps:
        return plan
    lo, hi = min(missing_timestamps), max(missing_timestamps) + timedelta(minutes=5)
    src = pymysql.connect(**source_db_config, autocommit=True, connect_timeout=5)
    tgt = pymysql.connect(**target_db_config, autocommit=True, connect_timeout=5)
    try:
        source_rows = _fetch_rows(src, M5_TABLE, instrument, lo, hi)
        target_rows = _fetch_rows(tgt, table, instrument, lo, hi)
        for ts in sorted(set(missing_timestamps)):
            s, t = source_rows.get(ts), target_rows.get(ts)
            if t is not None and s is not None:
                status = MATCH if _ohlcv_match(s, t) else CONFLICT
            elif t is not None and s is None:
                status = CONFLICT  # manifest said "missing" but target already has an unexplained row
            elif s is not None and t is None:
                status = NEW
            else:
                status = SOURCE_DATA_MISSING
            plan.classifications.append(RowClassification('M5', ts, status, s, t))
    finally:
        src.close()
        tgt.close()
    return plan


def execute_m5_replay(*, target_db_config: dict, plan: M5ReplayPlan,
                      target_table: Optional[str] = None) -> dict:
    """The only write path for M5. Same fail-loud contract as execute_replay(): refuses to execute a
    plan with any CONFLICT, INSERTs only (never ON DUPLICATE KEY UPDATE), and treats a concurrent
    insert race as a fresh error rather than silently absorbing it."""
    if plan.has_conflicts():
        conflicts = plan.by_status(CONFLICT)
        raise ValueError(
            f"GOV-M5-REPLAY-CONFLICT: {len(conflicts)} conflicting row(s) for {plan.instrument} — "
            "refusing to execute; requires architect review, never auto-resolved."
        )
    table = target_table or M5_TABLE
    written = 0
    race_conflicts = []
    conn = pymysql.connect(**target_db_config, autocommit=True, connect_timeout=5)
    try:
        for c in plan.by_status(NEW):
            written += _insert_row(conn, table, plan.instrument, c.timestamp, c.source_row,
                                    race_conflicts, 'M5')
    finally:
        conn.close()
    if race_conflicts:
        raise ValueError(f"GOV-M5-REPLAY-RACE: {len(race_conflicts)} row(s) were written by another "
                         f"process between plan and execute — re-plan required: {race_conflicts[:5]}")
    return {'written': written, 'plan_fingerprint': plan.fingerprint()}
