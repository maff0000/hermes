"""HERMES PH2 recovery-planner PLAN-ONLY runtime wiring v1.
WO-HELM-HERMES-PH2-RECOVERY-PLANNER-WIRING-IMPLEMENTATION-0001.
Hardened by WO-HELM-HERMES-PH2-RECOVERY-PLANNER-DIGEST-HARDENING-0001.

This wiring invokes a deterministic planner only. It does not publish, authorise or execute recovery.

Thin, plan-only integration layer around the pure planner utils.hermes_recovery_planner_v1. It evaluates governed gates
(component-level 105, never whole-container), loads a READ-ONLY mounted JSON policy from /app/config/recovery_planner_policy.v1.json,
acquires the live gaps contract + a RETENTION-BOUNDED coverage snapshot through injected read-only adapters, builds only the
HERMES-owned regular weekend closure, enforces input staleness + snapshot consistency, computes a semantic-digest idempotency
tuple, invokes the pure planner, retains the single result IN-MEMORY only, and reports through structured UTC logs + supervisor
runner state. It performs NO Redis write/delete, NO SQL, NO vendor call, NO file write, NO proposal/health publication, NO
recovery execution/backfill/repair, and has NO import/call path to utils.recovery_planner / utils.recovery_executor /
RecoveryLibrary / D1 seed-backfill / Falcon / ARES / HELIOS / NEO / SOLO. The nine frozen blueprint decisions are honoured.

DIGEST-HARDENING (0001): the invocation-idempotency tuple's coverage element is now the RECOVERY-RELEVANT coverage digest
(recovery_relevant_coverage_digest) rather than the whole-window presence digest. Live candle advancement OUTSIDE recovery-relevant
gap scope MUST NOT trigger a new planner invocation. Only coverage facts that can change clipping/overlap/segment bounds/
recoverability/cost/status for the current gaps are digested: retained gap intervals, coverage intersecting-or-adjacent to those
retained gaps, and the coverage provenance/version authority for those timeframes. The pure planner STILL receives the FULL
coverage snapshots (its output is unchanged); only the recompute trigger is scoped. Policy and recovery-relevant coverage are
verified pre/post (bounded retry; material change -> BLOCKED_INPUT_INCONSISTENCY). One injected UTC cycle clock drives freshness,
retention, closures, relevant-scope and the digest. Structured counters distinguish runner cycles from planner invocations.
The pure planner's recovery logic, the proposal/policy schema, retention doctrine, caller model, gate behaviour, output
disposition, publication boundary and executor boundary are UNCHANGED.
"""
from __future__ import annotations

import datetime
import json
import threading
from dataclasses import dataclass, field
from typing import Optional, Protocol

from utils.hermes_recovery_planner_v1 import (
    CANONICAL_INSTRUMENT, CONTRACT_VERSION as PLANNER_CONTRACT_VERSION, PLANNER_VERSION, SUPPORTED_TIMEFRAMES,
    PERIOD_SECONDS, Interval, GapRecord, GapSurfaceSnapshot, ExistingCoverageSnapshot, MarketClosureSnapshot,
    CostModel, RecoveryPlanningPolicy, build_recovery_proposal, validate_proposed_workload, _sha,
    ENABLED_ENV, AUTHORISED_ENV,
)

try:
    from hermes_logging import get_logger
    _LOG = get_logger("hermes.recovery_planner_runtime")
except Exception:  # noqa: BLE001 - logging optional at import
    import logging
    _LOG = logging.getLogger("hermes.recovery_planner_runtime")

UTC = datetime.timezone.utc

# --- governed constants (env NAMES only; material policy comes from the mounted JSON) ---
POLICY_PATH = "/app/config/recovery_planner_policy.v1.json"       # the ONLY policy transport (frozen decision 5)
GAPS_KEY = f"hermes:gaps:{CANONICAL_INSTRUMENT}:v1"
POLICY_CONTRACT_VERSION = "1"
# retention boundary (frozen decision 3) — used only to BOUND the read; policy must agree or planning is blocked
RETENTION_DAYS = {"M1": 35, "M5": 35, "M15": 35, "H1": 35, "H4": 35, "D1": 120}
# gaps freshness = 2x the governed 60s gaps cadence (frozen blueprint); policy may override via staleness_policy
DEFAULT_MAX_GAPS_AGE_SECONDS = 120
# runner lifecycle bounds
INITIAL_DELAY_SECONDS = 5
CHECK_CADENCE_SECONDS = 60
MAX_BACKOFF_SECONDS = 600
INVOCATION_TIMEOUT_SECONDS = 30
SNAPSHOT_MAX_SKEW_SECONDS = 5
SNAPSHOT_MAX_RETRIES = 2
COMPONENT_105 = 105

# component states + fault codes
STATE_DISABLED = "DISABLED"
STATE_PLAN_ONLY = "PLAN_ONLY_ENABLED"
STATE_BLOCKED_105 = "GATE_FAILCLOSED_105"
POLICY_FAULTS = ("POLICY_FILE_MISSING", "POLICY_JSON_INVALID", "POLICY_SCHEMA_INVALID", "POLICY_VERSION_UNSUPPORTED",
                 "POLICY_INSTRUMENT_INVALID", "POLICY_DIGEST_FAILED")
GAPS_FAULTS = ("GAPS_MISSING", "GAPS_JSON_INVALID", "GAPS_CONTRACT_INVALID", "GAPS_INSTRUMENT_INVALID", "GAPS_STALE",
               "GAPS_REDIS_UNAVAILABLE", "GAPS_INCONSISTENT")
COVERAGE_FAULTS = ("COVERAGE_INCOMPLETE_SOURCE", "COVERAGE_REDIS_UNAVAILABLE")
RUNTIME_STATUSES = ("PLAN_ONLY_ENABLED", "DISABLED", "GATE_FAILCLOSED_105", "BLOCKED_POLICY", "BLOCKED_STALE_GAPS",
                    "BLOCKED_INPUT_INCONSISTENCY", "BLOCKED_UNCLASSIFIED_MARKET_STATE", "PLANNER_INVOCATION_FAILED",
                    "PROPOSAL_HELD", "NO_RECOVERY_REQUIRED")


# --------------------------------------------------------------------------- typed exceptions (NON-SystemExit)
class RecoveryPlannerRuntimeError(Exception):
    """Base runtime-wiring error carrying a stable code. Never a SystemExit (component-level containment)."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


class RecoveryPlannerGate105Error(RecoveryPlannerRuntimeError):
    """enabled-without-authorised — component-level fail-closed. Code 105. NOT a whole-process SystemExit."""

    def __init__(self, message: str = "planner enabled without authorisation"):
        super().__init__(STATE_BLOCKED_105, message)
        self.numeric_code = COMPONENT_105


class PolicyError(RecoveryPlannerRuntimeError):
    pass


class GapsError(RecoveryPlannerRuntimeError):
    pass


class CoverageError(RecoveryPlannerRuntimeError):
    pass


class SnapshotInconsistencyError(RecoveryPlannerRuntimeError):
    def __init__(self, message: str = "input changed mid-cycle"):
        super().__init__("BLOCKED_INPUT_INCONSISTENCY", message)


# --------------------------------------------------------------------------- clock + injected protocols
def _default_clock() -> datetime.datetime:
    return datetime.datetime.now(UTC)


class PolicyReader(Protocol):
    def read(self) -> Optional[str]:
        """Return the raw policy file text, or None if the file is absent. Read-only; never writes."""


class GapsReader(Protocol):
    def get(self, key: str):
        """Redis GET only."""


class CoverageReader(Protocol):
    def exists(self, key: str) -> int: ...
    def zrange(self, key: str, start: int, end: int): ...


class _FilePolicyReader:
    """Default read-only file reader for the mounted policy. NEVER creates or writes the file."""

    def __init__(self, path: str = POLICY_PATH):
        self.path = path

    def read(self) -> Optional[str]:
        import os
        if not os.path.isfile(self.path):
            return None
        with open(self.path, "r", encoding="utf-8") as fh:      # read-only mode
            return fh.read()


# --------------------------------------------------------------------------- component gate (frozen decision 2)
def evaluate_recovery_planner_component_gate(*, enabled: bool, authorised: bool) -> str:
    """Component-level gate. F/F->DISABLED; F/T->DISABLED; T/F->raise RecoveryPlannerGate105Error (component, code 105,
    NEVER SystemExit); T/T->PLAN_ONLY_ENABLED. The planner is NOT invoked on the 105 path."""
    if not enabled:
        return STATE_DISABLED
    if not authorised:
        raise RecoveryPlannerGate105Error()
    return STATE_PLAN_ONLY


def recovery_planner_append_enabled() -> bool:
    """Env-ONLY, NON-RAISING append decision for the supervisor assembly. True iff ENABLED gate is set. Appending on
    ENABLED (not AUTHORISED) keeps the 105 fault COMPONENT-level (surfaced in the runner step, never at boot assembly)."""
    from env_config import get_env_bool
    return get_env_bool(ENABLED_ENV, False)


# --------------------------------------------------------------------------- mounted policy loader (frozen decision 5)
def _content_sha(text: Optional[str]) -> Optional[str]:
    """Raw-content identity of the mounted policy text (atomic-replacement detector). None iff the file is absent."""
    if text is None:
        return None
    return _sha({"raw": text})


def load_policy_from_reader(reader: PolicyReader) -> tuple[RecoveryPlanningPolicy, str]:
    """Read + validate the mounted JSON policy via the reader, then delegate to load_policy_from_text. Returns (policy, digest)."""
    return load_policy_from_text(reader.read())


def load_policy_from_text(raw: Optional[str]) -> tuple[RecoveryPlanningPolicy, str]:
    """Validate already-read policy text; map to the pure RecoveryPlanningPolicy (+CostModel) WITHOUT changing its
    semantics. Default-deny: any failure raises PolicyError with a POLICY_* code. Returns (policy, digest). Reading the
    text once and validating it here lets the runner recheck the same bytes' identity without a re-read race."""
    if raw is None:
        raise PolicyError("POLICY_FILE_MISSING", f"policy file absent at {POLICY_PATH}")
    try:
        d = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        raise PolicyError("POLICY_JSON_INVALID", f"policy JSON invalid: {exc!r}")
    if not isinstance(d, dict):
        raise PolicyError("POLICY_JSON_INVALID", "policy root is not an object")
    if d.get("contract_version") != POLICY_CONTRACT_VERSION:
        raise PolicyError("POLICY_VERSION_UNSUPPORTED", f"contract_version {d.get('contract_version')!r} != 1")
    if d.get("instrument") != CANONICAL_INSTRUMENT:
        raise PolicyError("POLICY_INSTRUMENT_INVALID", f"instrument {d.get('instrument')!r} != {CANONICAL_INSTRUMENT}")
    if "XAUUSD" in raw:
        raise PolicyError("POLICY_INSTRUMENT_INVALID", "forbidden alias XAUUSD present in policy")
    try:
        _schema_validate(d)
        pol = _map_policy(d)
    except PolicyError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise PolicyError("POLICY_SCHEMA_INVALID", f"policy schema mapping failed: {exc!r}")
    try:
        digest = pol.digest()
    except Exception as exc:  # noqa: BLE001
        raise PolicyError("POLICY_DIGEST_FAILED", f"digest failed: {exc!r}")
    return pol, digest


def _req(d: dict, key: str):
    if key not in d:
        raise PolicyError("POLICY_SCHEMA_INVALID", f"missing required field {key!r}")
    return d[key]


def _pos_int(v, name: str) -> int:
    if not isinstance(v, int) or isinstance(v, bool) or v <= 0:
        raise PolicyError("POLICY_SCHEMA_INVALID", f"{name} must be a positive integer")
    return v


def _schema_validate(d: dict) -> None:
    for f in ("enabled_timeframes", "timeframe_priority", "d1_history_floor", "retention_policy", "staleness_policy",
              "workload_bounds", "cost_model", "regular_market_schedule", "partial_proposals_allowed"):
        _req(d, f)
    for tf in list(d["enabled_timeframes"]) + list(d["timeframe_priority"]):
        if tf not in SUPPORTED_TIMEFRAMES:
            raise PolicyError("POLICY_SCHEMA_INVALID", f"timeframe {tf!r} not permitted")
    fl = d["d1_history_floor"]
    if fl.get("anchor_hour_utc") != 22:
        raise PolicyError("POLICY_SCHEMA_INVALID", "d1 anchor_hour_utc must be 22 (NY5PM)")
    _pos_int(fl.get("seconds"), "d1_history_floor.seconds")
    rp_ = d["retention_policy"]
    if rp_.get("m1_h4_days") != 35 or rp_.get("d1_days") != 120:
        raise PolicyError("POLICY_SCHEMA_INVALID", "retention must match frozen boundary (M1-H4 35d, D1 120d)")
    st = d["staleness_policy"]
    _pos_int(st.get("gaps_max_age_seconds"), "gaps_max_age_seconds")
    _pos_int(st.get("coverage_max_age_seconds"), "coverage_max_age_seconds")
    wb = d["workload_bounds"]
    for k in ("max_segments", "max_candles_per_segment", "max_candles_per_proposal", "max_lookback_seconds", "max_request_units"):
        _pos_int(wb.get(k), f"workload_bounds.{k}")
    cm = d["cost_model"]
    _pos_int(cm.get("candles_per_request"), "candles_per_request")
    _pos_int(cm.get("request_units_per_call"), "request_units_per_call")
    for k in ("cost_tokens_per_request", "fixed_overhead_units"):
        if not isinstance(cm.get(k), int) or cm.get(k) < 0:
            raise PolicyError("POLICY_SCHEMA_INVALID", f"cost_model.{k} must be >= 0")
    if not isinstance(cm.get("timeframe_multipliers"), dict):
        raise PolicyError("POLICY_SCHEMA_INVALID", "cost_model.timeframe_multipliers must be an object")
    sch = d["regular_market_schedule"]
    if (sch.get("weekend_close_weekday"), sch.get("weekend_close_hour_utc"), sch.get("weekend_reopen_weekday"),
            sch.get("weekend_reopen_hour_utc")) != (4, 22, 6, 22):
        raise PolicyError("POLICY_SCHEMA_INVALID", "regular_market_schedule must be Fri22->Sun22 UTC")
    if not isinstance(d["partial_proposals_allowed"], bool):
        raise PolicyError("POLICY_SCHEMA_INVALID", "partial_proposals_allowed must be boolean")


def _map_policy(d: dict) -> RecoveryPlanningPolicy:
    cm = d["cost_model"]
    cost = CostModel(candles_per_request=cm["candles_per_request"], request_units_per_call=cm["request_units_per_call"],
                     cost_tokens_per_request=cm["cost_tokens_per_request"], fixed_overhead_units=cm["fixed_overhead_units"],
                     timeframe_multipliers=tuple(sorted((k, float(v)) for k, v in cm["timeframe_multipliers"].items())))
    wb = d["workload_bounds"]
    return RecoveryPlanningPolicy(
        contract_version=PLANNER_CONTRACT_VERSION, instrument=CANONICAL_INSTRUMENT,
        allowed_timeframes=tuple(d["enabled_timeframes"]), timeframe_priority=tuple(d["timeframe_priority"]),
        priority_tiers=tuple((tf, 1 if tf == "D1" else (2 if tf in ("H4", "H1") else 3)) for tf in SUPPORTED_TIMEFRAMES),
        max_segments=wb["max_segments"], max_candles_per_segment=wb["max_candles_per_segment"],
        max_candles_per_proposal=wb["max_candles_per_proposal"], max_lookback_seconds=wb["max_lookback_seconds"],
        max_request_units=wb["max_request_units"], merge_adjacent_threshold_seconds=int(d.get("merge_adjacent_threshold_seconds", 0)),
        d1_history_floor_seconds=d["d1_history_floor"]["seconds"], regular_weekend_closure_utc=(4, 22, 6, 22),
        accepted_closure_authorities=("GOVERNED",), uncertain_to_unclassified=True,
        stale_gaps_max_age_seconds=d["staleness_policy"]["gaps_max_age_seconds"],
        allow_partial=bool(d["partial_proposals_allowed"]), cost_model=cost)


# --------------------------------------------------------------------------- gaps adapter (read-only, frozen decision 8/D)
def _parse_gaps_interval(s: str) -> datetime.datetime:
    from utils.hermes_recovery_planner_v1 import _parse_utc
    return _parse_utc(s)


class GapsAdapter:
    """Read-only GET adapter for hermes:gaps:XAU_USD:v1 -> pure GapSurfaceSnapshot. NO SET/DELETE; never mutates source."""

    def __init__(self, reader: GapsReader):
        self.reader = reader

    def _raw(self):
        try:
            return self.reader.get(GAPS_KEY)
        except Exception as exc:  # noqa: BLE001
            raise GapsError("GAPS_REDIS_UNAVAILABLE", f"redis get failed: {exc!r}")

    def acquire(self, *, now: datetime.datetime, max_age_seconds: int) -> tuple[GapSurfaceSnapshot, str]:
        raw = self._raw()
        if raw is None:
            raise GapsError("GAPS_MISSING", "gaps key absent")
        text = raw.decode() if isinstance(raw, (bytes, bytearray)) else str(raw)
        try:
            payload = json.loads(text)
        except Exception as exc:  # noqa: BLE001
            raise GapsError("GAPS_JSON_INVALID", f"gaps json invalid: {exc!r}")
        if payload.get("contract_version") != "v1":
            raise GapsError("GAPS_CONTRACT_INVALID", "gaps contract_version != v1")
        if payload.get("instrument") != CANONICAL_INSTRUMENT or "XAUUSD" in text:
            raise GapsError("GAPS_INSTRUMENT_INVALID", "gaps instrument invalid / alias present")
        gen = _parse_gaps_interval(payload["generated_at_utc"])
        age = (now - gen).total_seconds()
        if age > max_age_seconds:
            raise GapsError("GAPS_STALE", f"gaps age {int(age)}s > {max_age_seconds}s")
        records = []
        for tf, blk in payload.get("timeframes", {}).items():
            if tf not in SUPPORTED_TIMEFRAMES:
                continue
            miss = []
            for ep in _missing_epochs_from_block(blk, tf):
                miss.append(Interval(datetime.datetime.fromtimestamp(ep, UTC),
                                     datetime.datetime.fromtimestamp(ep + PERIOD_SECONDS[tf], UTC)))
            records.append(GapRecord(tf, tuple(miss), blk.get("gap_state", "OK")))
        d1b = payload.get("d1_boundary", {})
        anchor = 22 if str(d1b.get("expected_anchor_utc", "22:00")).startswith("22") else 0
        snap = GapSurfaceSnapshot(contract_version="v1", instrument=CANONICAL_INSTRUMENT, source_key=GAPS_KEY,
                                  source_generated_at_utc=gen, overall_gap_state=payload.get("overall_gap_state", "OK"),
                                  gap_records=tuple(records), d1_boundary_state=d1b.get("d1_boundary_state", "OK"),
                                  d1_anchor_hour_utc=anchor, source_observed_at_utc=now)
        return snap, _gaps_semantic_digest(snap)


def _gaps_semantic_digest(snap: GapSurfaceSnapshot) -> str:
    """Canonical gaps digest (B): canonical timeframe keys (sort_keys), explicit gap_state + classification, deterministically
    sorted + DEDUPLICATED missing-open epochs, UTC epoch endpoints. Excludes generated_at/observed_at/TTL/refresh metadata and
    is independent of dictionary/retrieval order. Changes iff a material gap fact (state, boundary, or missing interval) changes."""
    return _sha({"overall": snap.overall_gap_state, "d1_boundary": snap.d1_boundary_state,
                 "tf": {r.timeframe: [r.gap_state, sorted({int(iv.start_utc.timestamp()) for iv in r.missing_intervals})]
                        for r in snap.gap_records}})


def _missing_epochs_from_block(blk: dict, tf: str) -> list[int]:
    """Read the gaps block's bounded missing-open sample (read-only). The gaps surface already caps this list."""
    out = []
    for s in blk.get("missing_open_epochs_sample", []) or []:
        try:
            out.append(int(s))
        except Exception:  # noqa: BLE001
            continue
    return out


# --------------------------------------------------------------------------- coverage adapter (retention-bounded, decision 3)
class CoverageAdapter:
    """Read-only, RETENTION-BOUNDED coverage from the governed candle history index. Authoritative for PRESENCE WITHIN
    retention only; beyond-retention is excluded (never a recovery segment). NO writes, NO vendor, NO SQL, NO completeness
    overclaim outside the retention window."""

    def __init__(self, reader: CoverageReader):
        self.reader = reader

    def _index_key(self, tf: str) -> str:
        return f"hermes:candles:{CANONICAL_INSTRUMENT}:{tf}:history:v1:index"

    def acquire(self, *, now: datetime.datetime) -> tuple[tuple[ExistingCoverageSnapshot, ...], str]:
        snaps = []
        digest_parts = {}
        for tf in SUPPORTED_TIMEFRAMES:
            key = self._index_key(tf)
            floor = now - datetime.timedelta(days=RETENTION_DAYS[tf])
            floor_ep = int(floor.timestamp())
            try:
                present = bool(self.reader.exists(key))
                opens = [int(e) for e in self.reader.zrange(key, 0, -1)] if present else []
            except Exception as exc:  # noqa: BLE001
                raise CoverageError("COVERAGE_REDIS_UNAVAILABLE", f"redis read failed for {tf}: {exc!r}")
            covered = []
            for o in sorted(set(opens)):
                if o < floor_ep:
                    continue                                    # beyond retention -> excluded, NOT covered/missing
                covered.append(Interval(datetime.datetime.fromtimestamp(o, UTC),
                                        datetime.datetime.fromtimestamp(o + PERIOD_SECONDS[tf], UTC)))
            snaps.append(ExistingCoverageSnapshot(contract_version="v1", instrument=CANONICAL_INSTRUMENT, timeframe=tf,
                                                  covered_intervals=tuple(covered), snapshot_at_utc=now,
                                                  provenance=f"governed_redis_history_index:RETENTION_BOUNDED:{RETENTION_DAYS[tf]}d"))
            digest_parts[tf] = [int(iv.start_utc.timestamp()) for iv in covered]
        return tuple(snaps), _sha(digest_parts)


# --------------------------------------------------------------------------- recovery-relevant coverage scope + digest (A/C/D)
def retained_gap_intervals(*, gaps: GapSurfaceSnapshot, now: datetime.datetime) -> dict:
    """Per-timeframe RETAINED gap intervals: each gap's missing intervals clipped to the timeframe retention floor
    (now - RETENTION_DAYS[tf]). Intervals wholly beyond retention are dropped (not recoverable -> irrelevant). Endpoints are
    UTC epoch seconds; results are DEDUPLICATED and deterministically sorted. Canonical timeframe order. This is the only
    scope in which coverage can materially change the current recovery proposal, so retention-boundary movement that
    reclassifies a gap DOES change this scope by design."""
    out: dict = {}
    for rec in gaps.gap_records:
        tf = rec.timeframe
        if tf not in RETENTION_DAYS:
            continue
        floor_ep = int((now - datetime.timedelta(days=RETENTION_DAYS[tf])).timestamp())
        ivs = set()
        for iv in rec.missing_intervals:
            s = int(iv.start_utc.timestamp())
            e = int(iv.end_utc.timestamp())
            if e <= floor_ep:
                continue                                        # entirely beyond retention -> excluded
            ivs.add((max(s, floor_ep), e))                      # retained portion only
        if ivs:
            out[tf] = sorted(ivs)
    return out


def recovery_relevant_scope(*, gaps: GapSurfaceSnapshot, coverage: tuple, now: datetime.datetime,
                            merge_threshold_seconds: int = 0) -> dict:
    """The canonical set of coverage facts that could change the CURRENT recovery proposal (A). For every timeframe with a
    retained gap: the retained gap intervals, the coverage intervals intersecting-or-adjacent to those gaps, and the coverage
    provenance+contract version (authority/interpretation) for that timeframe. The adjacency half-width is
    max(one period, merge_adjacent_threshold_seconds) each side — one period captures a boundary candle that changes clipping,
    and the merge threshold captures any covered candle that could change whether two gap segments merge; this keeps the scope
    provably COMPLETE (a wider window never HIDES a material change). Unrelated current-edge candles OUTSIDE every relevant
    window are excluded and cannot trigger a recompute. Deterministic and retrieval-order-independent."""
    retained = retained_gap_intervals(gaps=gaps, now=now)
    cov_by_tf = {c.timeframe: c for c in coverage}
    scope: dict = {}
    for tf, gap_ivs in retained.items():
        half = max(PERIOD_SECONDS[tf], int(merge_threshold_seconds))  # adjacency+merge window where output can change
        windows = [(s - half, e + half) for (s, e) in gap_ivs]
        cov = cov_by_tf.get(tf)
        rel = set()
        if cov is not None:
            for iv in cov.covered_intervals:
                cs = int(iv.start_utc.timestamp())
                ce = int(iv.end_utc.timestamp())
                if any(cs < we and ce > ws for (ws, we) in windows):
                    rel.add((cs, ce))
        scope[tf] = {"retained_gaps": [[s, e] for (s, e) in gap_ivs],
                     "relevant_coverage": [[s, e] for (s, e) in sorted(rel)],
                     "authority": (None if cov is None else [cov.provenance, cov.contract_version])}
    return scope


def recovery_relevant_coverage_digest(*, gaps: GapSurfaceSnapshot, coverage: tuple, now: datetime.datetime,
                                      merge_threshold_seconds: int = 0) -> str:
    """Digest of the recovery-relevant scope (A). Stable under unrelated candle advancement / sliding-window movement /
    volatile refresh metadata / retrieval order; changes iff a gap-relevant coverage fact, retained-gap set, retention
    reclassification, or coverage authority/version changes."""
    return _sha(recovery_relevant_scope(gaps=gaps, coverage=coverage, now=now, merge_threshold_seconds=merge_threshold_seconds))


# --------------------------------------------------------------------------- regular closure + exceptional detection (decision 4)
def build_regular_closures(*, policy: RecoveryPlanningPolicy, now: datetime.datetime) -> tuple[MarketClosureSnapshot, ...]:
    """The FIRST implementation consumes ONLY the HERMES-owned deterministic regular weekend schedule (Fri22->Sun22 UTC).
    No ARES, no vendor, no guessed holiday. The pure planner already excludes regular weekends via policy; this returns the
    governed MarketClosureSnapshots for explicit provenance/telemetry (authority GOVERNED)."""
    from utils.hermes_recovery_planner_v1 import _weekend_intervals, Interval as _Iv
    horizon = _Iv(now - datetime.timedelta(seconds=policy.max_lookback_seconds), now)
    out = []
    for iv in _weekend_intervals(policy, horizon):
        out.append(MarketClosureSnapshot(contract_version="v1", instrument=CANONICAL_INSTRUMENT, closure_interval=iv,
                                         classification="WEEKEND", provenance="hermes_regular_schedule",
                                         authority="GOVERNED", effective_at_utc=now))
    return tuple(out)


def detect_unresolved_exceptional(*, gaps: GapSurfaceSnapshot,
                                  regular_closures: tuple[MarketClosureSnapshot, ...]) -> bool:
    """Option A: any gaps timeframe block reporting MARKET_CLOSED for an interval OUTSIDE the deterministic governed weekend
    is a SUSPECTED exceptional closure with NO governed evidence -> unresolved. The runner blocks the affected scope
    (BLOCKED_UNCLASSIFIED_MARKET_STATE) rather than guessing a holiday or emitting PROPOSAL_READY."""
    closed = tuple(c.closure_interval for c in regular_closures)
    for rec in gaps.gap_records:
        if rec.gap_state == "MARKET_CLOSED":
            for miss in rec.missing_intervals:
                if not any(miss.overlaps(cl) for cl in closed):   # closed per gaps, but not a governed weekend -> unresolved
                    return True
    return False


# --------------------------------------------------------------------------- snapshot coordinator (consistency, decision G)
@dataclass(frozen=True)
class AcquiredSnapshots:
    gaps: GapSurfaceSnapshot
    gaps_digest: str
    coverage: tuple[ExistingCoverageSnapshot, ...]      # FULL retention-bounded snapshots -> passed to the pure planner
    coverage_digest: str                                # RECOVERY-RELEVANT digest -> used only for the idempotency tuple
    closures: tuple[MarketClosureSnapshot, ...]
    closure_digest: str
    policy: RecoveryPlanningPolicy
    policy_digest: str


def acquire_consistent_snapshot(*, gaps_adapter: GapsAdapter, coverage_adapter: CoverageAdapter,
                                policy: RecoveryPlanningPolicy, policy_digest: str, policy_reader: PolicyReader,
                                policy_raw_sha: Optional[str], now: datetime.datetime, max_gaps_age: int,
                                on_attempt=None) -> AcquiredSnapshots:
    """Acquire gaps + RECOVERY-RELEVANT coverage + closures with pre/post consistency in one exact order (H):
      1 gate (caller)  2 policy read+digest (caller, identity = policy_raw_sha)  3 gaps A  4 derive relevant gap scope
      5 recovery-relevant coverage A  6 closures  7 gaps B  8 policy recheck B  9 recovery-relevant coverage B
      10 compare all material identities  11 retry (bounded) or block  12 build tuple  (13 hold/invoke by caller).
    Gaps digest and the recovery-relevant coverage digest are each verified pre/post; an unrelated current-edge candle does
    NOT change the recovery-relevant digest and so does NOT force a retry. A material gap-relevant coverage change causes a
    bounded retry; a mid-cycle policy replacement/removal is rejected immediately. Exhausting retries or a policy change
    raises SnapshotInconsistencyError -> BLOCKED_INPUT_INCONSISTENCY. Closures are deterministic from policy + the one clock.
    The returned AcquiredSnapshots.coverage holds the FULL snapshots for the pure planner; .coverage_digest holds the
    recovery-relevant digest for the idempotency tuple."""
    merge_thr = getattr(policy, "merge_adjacent_threshold_seconds", 0) or 0
    last = None
    for _attempt in range(SNAPSHOT_MAX_RETRIES + 1):
        if on_attempt is not None:
            on_attempt()
        gaps_snap, gd_pre = gaps_adapter.acquire(now=now, max_age_seconds=max_gaps_age)   # 3 gaps A
        cov, _cov_presence = coverage_adapter.acquire(now=now)                            # 5 coverage A (full snapshots)
        rel_pre = recovery_relevant_coverage_digest(gaps=gaps_snap, coverage=cov, now=now,
                                                    merge_threshold_seconds=merge_thr)     # 4+5 relevant scope A
        closures = build_regular_closures(policy=policy, now=now)                          # 6 closures
        _g2, gd_post = gaps_adapter.acquire(now=now, max_age_seconds=max_gaps_age)         # 7 gaps B
        raw2 = policy_reader.read()                                                        # 8 policy recheck B
        if raw2 is None:
            raise SnapshotInconsistencyError("policy removed mid-cycle")
        if _content_sha(raw2) != policy_raw_sha:
            raise SnapshotInconsistencyError("policy changed mid-cycle")
        cov2, _ = coverage_adapter.acquire(now=now)                                        # 9 coverage B
        rel_post = recovery_relevant_coverage_digest(gaps=gaps_snap, coverage=cov2, now=now,
                                                     merge_threshold_seconds=merge_thr)
        if gd_pre == gd_post and rel_pre == rel_post:                                      # 10 all material identities stable
            closure_digest = _sha([[int(c.closure_interval.start_utc.timestamp()),
                                    int(c.closure_interval.end_utc.timestamp())] for c in closures])
            return AcquiredSnapshots(gaps_snap, gd_pre, cov, rel_pre, closures, closure_digest, policy, policy_digest)
        last = (gd_pre, gd_post, rel_pre, rel_post)                                        # 11 material change -> bounded retry
    raise SnapshotInconsistencyError(f"inputs changed mid-cycle across retries: {last}")


def semantic_digest(snap: AcquiredSnapshots) -> str:
    """Deterministic idempotency-tuple digest (D). Structure preserved: (semantic gaps digest, RECOVERY-RELEVANT coverage
    digest, closure digest, policy digest, planner version). INVARIANT: it changes when and only when a material input change
    can alter planner status, proposed/deferred/unclassified/intentionally-unavailable segments, workload cost, proposal
    identity, or a blocking fault. Volatile refresh timestamps and unrelated current-edge candles do NOT change it."""
    return _sha({"gaps": snap.gaps_digest, "coverage": snap.coverage_digest, "closure": snap.closure_digest,
                 "policy": snap.policy_digest, "planner_version": PLANNER_VERSION})


# --------------------------------------------------------------------------- in-memory output holder (decision 8/J)
class InMemoryProposalHolder:
    """Holds ONLY the current proposal in process memory. No Redis/SQL/file/API publication. Not trusted across restart."""

    def __init__(self):
        self._lock = threading.Lock()
        self._proposal_dict: Optional[dict] = None
        self._digest: Optional[str] = None

    def set(self, proposal_dict: dict, digest: str) -> None:
        # execution flags validated false before retention (fail-closed)
        for f in ("execution_enabled", "backfill_executed", "repair_executed", "consumer_live"):
            if proposal_dict.get(f) is not False:
                raise RecoveryPlannerRuntimeError("OUTPUT_INVARIANT", f"{f} must be false before retention")
        with self._lock:
            self._proposal_dict = proposal_dict
            self._digest = digest

    def clear(self) -> None:
        with self._lock:
            self._proposal_dict = None
            self._digest = None

    @property
    def digest(self) -> Optional[str]:
        with self._lock:
            return self._digest

    def snapshot(self) -> Optional[dict]:
        with self._lock:
            return dict(self._proposal_dict) if self._proposal_dict is not None else None


# --------------------------------------------------------------------------- component state
@dataclass
class ComponentState:
    component: str = "recovery_planner"
    planner_version: str = PLANNER_VERSION
    gate_state: str = STATE_DISABLED
    policy_version: Optional[str] = None
    policy_digest: Optional[str] = None
    last_invocation_utc: Optional[str] = None
    last_success_utc: Optional[str] = None
    source_digests: dict = field(default_factory=dict)
    status: str = STATE_DISABLED
    proposal_id: Optional[str] = None
    segment_count: int = 0
    deferred_count: int = 0
    unclassified_count: int = 0
    intentionally_unavailable_count: int = 0
    estimated_request_units: int = 0
    last_fault_code: Optional[str] = None
    consecutive_failures: int = 0
    invocation_duration_ms: Optional[int] = None
    backoff_state: int = 0
    execution_enabled: bool = False
    publication_enabled: bool = False
    consumer_live: bool = False
    # --- accounting counters (K): a runner cycle is one supervisor tick; a planner invocation is one pure-planner call.
    #     They are NOT the same number — held/deduplicated cycles run no planner call. Process-local; summary-logged only.
    runner_cycle_count: int = 0
    snapshot_attempt_count: int = 0
    planner_invocation_count: int = 0
    proposal_ready_count: int = 0
    proposal_held_count: int = 0
    blocked_count: int = 0
    failure_count: int = 0

    def summary(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


# --------------------------------------------------------------------------- the runner (plan-only)
class RecoveryPlannerRunner:
    """Plan-only runtime component. One step per supervisor tick. Component-isolated: faults never propagate to siblings.
    Digest-idempotent: recomputes only on semantic change. Retains one proposal IN-MEMORY; publishes NOTHING."""

    def __init__(self, *, gaps_reader: GapsReader, coverage_reader: CoverageReader,
                 policy_reader: Optional[PolicyReader] = None, clock=_default_clock):
        self.gaps_adapter = GapsAdapter(gaps_reader)
        self.coverage_adapter = CoverageAdapter(coverage_reader)
        self.policy_reader = policy_reader or _FilePolicyReader()
        self.clock = clock
        self.state = ComponentState()
        self.holder = InMemoryProposalHolder()
        self._lock = threading.Lock()                          # single active invocation

    def _gate(self) -> str:
        from env_config import get_env_bool
        return evaluate_recovery_planner_component_gate(enabled=get_env_bool(ENABLED_ENV, False),
                                                        authorised=get_env_bool(AUTHORISED_ENV, False))

    def run_once(self, *, enabled: Optional[bool] = None, authorised: Optional[bool] = None) -> dict:
        """One plan-only cycle. Returns a bounded result dict. NEVER raises to the caller (component containment) except
        for programmer errors; gate/policy/gaps/coverage/consistency faults become component state + a result dict."""
        if not self._lock.acquire(blocking=False):
            return {"published": 0, "status": "SKIPPED_CONCURRENT"}
        try:
            now = self.clock()                                  # ONE injected UTC cycle clock (I) — used everywhere below
            self.state.runner_cycle_count += 1                  # a runner cycle != a planner invocation (K)
            # ---- gate ----
            try:
                gs = (evaluate_recovery_planner_component_gate(enabled=enabled, authorised=authorised)
                      if enabled is not None else self._gate())
            except RecoveryPlannerGate105Error as e:
                self.state.gate_state = STATE_BLOCKED_105
                self.state.status = STATE_BLOCKED_105
                self.state.last_fault_code = STATE_BLOCKED_105
                self.state.consecutive_failures += 1
                self.state.blocked_count += 1
                _LOG.error("[RECOVERY_PLANNER] GATE_FAILCLOSED_105 component blocked (code=%d); planner NOT invoked; "
                           "critical runners unaffected", COMPONENT_105)
                return {"published": 0, "status": STATE_BLOCKED_105, "fault": STATE_BLOCKED_105, "code": COMPONENT_105}
            self.state.gate_state = gs
            if gs == STATE_DISABLED:
                self.state.status = STATE_DISABLED
                return {"published": 0, "status": STATE_DISABLED}     # NO reads, NO policy, NO planner
            # ---- enabled+authorised: plan-only ----
            self.state.last_invocation_utc = _fmt(now)
            # (H2) read the policy bytes ONCE; validate + digest from those exact bytes; capture their identity for the
            #      pre/post recheck so the recheck compares the SAME acquisition rather than racing a re-read.
            raw_policy = self.policy_reader.read()
            try:
                policy, pol_digest = load_policy_from_text(raw_policy)
            except PolicyError as e:
                return self._fault("BLOCKED_POLICY", e.code, now, kind="blocked")
            policy_raw_sha = _content_sha(raw_policy)
            self.state.policy_version = POLICY_CONTRACT_VERSION
            self.state.policy_digest = pol_digest
            max_gaps_age = policy.stale_gaps_max_age_seconds or DEFAULT_MAX_GAPS_AGE_SECONDS
            try:
                snap = acquire_consistent_snapshot(
                    gaps_adapter=self.gaps_adapter, coverage_adapter=self.coverage_adapter, policy=policy,
                    policy_digest=pol_digest, policy_reader=self.policy_reader, policy_raw_sha=policy_raw_sha,
                    now=now, max_gaps_age=max_gaps_age,
                    on_attempt=lambda: setattr(self.state, "snapshot_attempt_count", self.state.snapshot_attempt_count + 1))
            except GapsError as e:
                blocked = e.code == "GAPS_STALE"
                return self._fault("BLOCKED_STALE_GAPS" if blocked else e.code, e.code, now,
                                   kind="blocked" if blocked else "failure")
            except CoverageError as e:
                return self._fault(e.code, e.code, now, kind="failure")
            except SnapshotInconsistencyError as e:
                return self._fault("BLOCKED_INPUT_INCONSISTENCY", e.code, now, kind="blocked")
            self.state.source_digests = {"gaps": snap.gaps_digest, "coverage": snap.coverage_digest,
                                         "closure": snap.closure_digest, "policy": snap.policy_digest}
            # ---- exceptional-closure Option A: unresolved -> block affected scope ----
            if detect_unresolved_exceptional(gaps=snap.gaps, regular_closures=snap.closures):
                self.state.status = "BLOCKED_UNCLASSIFIED_MARKET_STATE"
                self.state.last_fault_code = "BLOCKED_UNCLASSIFIED_MARKET_STATE"
                self.state.blocked_count += 1
                self.holder.clear()
                _LOG.warning("[RECOVERY_PLANNER] BLOCKED_UNCLASSIFIED_MARKET_STATE — suspected exceptional closure without "
                             "governed evidence; scope not PROPOSAL_READY")
                return {"published": 0, "status": "BLOCKED_UNCLASSIFIED_MARKET_STATE"}
            # ---- idempotency (E): recompute ONLY on recovery-relevant semantic change; held cycles run NO planner call ----
            sd = semantic_digest(snap)
            if self.holder.digest == sd:
                self.state.status = "PROPOSAL_HELD"
                self.state.proposal_held_count += 1
                return {"published": 0, "status": "PROPOSAL_HELD", "idempotent": True}
            # ---- invoke the PURE planner (full coverage snapshots; output unchanged by the hardening) ----
            start = _monotonic_ms()
            try:
                self.state.planner_invocation_count += 1        # a planner invocation is one pure-planner call (K)
                wl = build_recovery_proposal(gaps=snap.gaps, coverage=snap.coverage, closures=snap.closures,
                                             policy=snap.policy, now_utc=now)
                wl_dict = wl.to_dict()
                validate_proposed_workload(wl_dict)            # defence-in-depth
                self.holder.set(wl_dict, sd)                   # IN-MEMORY only; execution flags validated false inside
            except Exception as exc:  # noqa: BLE001 — a failed invocation must NOT poison a prior good hold or mark success
                self.state.failure_count += 1
                self.state.consecutive_failures += 1
                self.state.backoff_state = min(MAX_BACKOFF_SECONDS, (self.state.backoff_state or CHECK_CADENCE_SECONDS) * 2)
                self.state.status = "PLANNER_INVOCATION_FAILED"
                self.state.last_fault_code = "PLANNER_INVOCATION_FAILED"
                _LOG.error("[RECOVERY_PLANNER] planner invocation failed (held tuple preserved, not marked successful): %r", exc)
                return {"published": 0, "status": "PLANNER_INVOCATION_FAILED", "fault": "PLANNER_INVOCATION_FAILED"}
            dur = _monotonic_ms() - start
            self.state.invocation_duration_ms = dur
            self.state.consecutive_failures = 0
            self.state.backoff_state = 0
            self.state.proposal_ready_count += 1
            self.state.last_success_utc = _fmt(now)
            self.state.proposal_id = wl_dict["proposal_id"]
            self.state.status = "NO_RECOVERY_REQUIRED" if wl_dict["overall_plan_status"] == "NO_RECOVERY_REQUIRED" else "PROPOSAL_HELD"
            self.state.segment_count = len(wl_dict["prioritized_segments_list"])
            self.state.deferred_count = len(wl_dict["deferred_segments"])
            self.state.unclassified_count = len(wl_dict["unclassified_segments"])
            self.state.intentionally_unavailable_count = len(wl_dict["intentionally_unavailable_segments"])
            self.state.estimated_request_units = wl_dict["estimated_request_units"]
            _LOG.info("[RECOVERY_PLANNER] plan-only cycle: status=%s proposal_id=%s segments=%d deferred=%d unclassified=%d "
                      "unavailable=%d req_units=%d duration_ms=%d runner_cycles=%d planner_invocations=%d held=%d "
                      "execution_enabled=false publication_enabled=false consumer_live=false",
                      wl_dict["overall_plan_status"], wl_dict["proposal_id"], self.state.segment_count, self.state.deferred_count,
                      self.state.unclassified_count, self.state.intentionally_unavailable_count, self.state.estimated_request_units,
                      dur, self.state.runner_cycle_count, self.state.planner_invocation_count, self.state.proposal_held_count)
            return {"published": 0, "status": wl_dict["overall_plan_status"], "proposal_id": wl_dict["proposal_id"], "held_in_memory": True}
        finally:
            self._lock.release()

    def _fault(self, status: str, code: str, now: datetime.datetime, *, kind: str = "failure") -> dict:
        """Governed non-plan outcome. kind='blocked' -> a deliberate governed block (stale/policy/inconsistency/unclassified);
        kind='failure' -> an infrastructure/parse fault. Both bump backoff; the holder is cleared because the inputs are not
        trustworthy for this cycle (a fresh recompute is required next cycle)."""
        self.state.status = status
        self.state.last_fault_code = code
        self.state.consecutive_failures += 1
        if kind == "blocked":
            self.state.blocked_count += 1
        else:
            self.state.failure_count += 1
        self.state.backoff_state = min(MAX_BACKOFF_SECONDS, (self.state.backoff_state or CHECK_CADENCE_SECONDS) * 2)
        self.holder.clear()
        _LOG.warning("[RECOVERY_PLANNER] plan-only %s: status=%s fault=%s consecutive=%d", kind, status, code,
                     self.state.consecutive_failures)
        return {"published": 0, "status": status, "fault": code}

    def restart_reset(self) -> None:
        """On restart: do NOT trust any prior proposal — clear in-memory result (recompute next cycle)."""
        self.holder.clear()
        self.state = ComponentState()


def _fmt(dt: datetime.datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _monotonic_ms() -> int:
    import time
    return int(time.monotonic() * 1000)


# --------------------------------------------------------------------------- supervisor step (module singleton)
_RUNNER_SINGLETON: Optional[RecoveryPlannerRunner] = None
_RUNNER_LOCK = threading.Lock()


def _get_runner(client) -> RecoveryPlannerRunner:
    global _RUNNER_SINGLETON
    with _RUNNER_LOCK:
        if _RUNNER_SINGLETON is None:
            _RUNNER_SINGLETON = RecoveryPlannerRunner(gaps_reader=client, coverage_reader=client)
        return _RUNNER_SINGLETON


def recovery_planner_step(client) -> dict:
    """Supervisor step (appended only when ENABLED). Plan-only; component-isolated. Delegates to the singleton runner.
    Returns a bounded result dict; the gate 105 / policy / input faults are contained as component state, never SystemExit."""
    return _get_runner(client).run_once()
