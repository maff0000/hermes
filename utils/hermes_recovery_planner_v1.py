"""HERMES PH2 recovery PLANNER — deterministic DRY-RUN-ONLY recovery-workload proposer v1.
WO-HELM-HERMES-PH2-RECOVERY-PLANNER-DESIGN-0001.

Pure, deterministic planning ONLY. Given validated in-memory inputs (a gaps snapshot, an existing-coverage snapshot, a
governed market-closure snapshot, and an explicitly supplied planning policy) this module produces a bounded, prioritised,
serialisable ProposedWorkload describing WHAT MAY require recovery. It is structurally incapable of performing recovery:
there is NO Redis/SQL/HTTP/vendor/broker/subprocess/filesystem client anywhere, NO .set/.delete, NO seed/backfill invocation,
NO job/queue/publish path. Every output carries planning_mode=DRY_RUN_ONLY and execution_enabled/backfill_executed/
repair_executed/consumer_live = HARD false. It reasons only about candle/history coverage, market-closure exclusions, existing
coverage, timeframe priority, bounded segments and workload estimates — NEVER regime/risk/strategy/signal/trade/Falcon/ARES/HELIOS/NEO/SOLO.

Interval convention: HALF-OPEN [start_utc, end_utc). All timestamps are timezone-aware UTC; naive datetimes are rejected.
Clock and policy are dependency-injected (no local time, no hidden defaults). Identical inputs+policy -> identical output
(the only volatile field is the injected generated_at_utc). Gate env names are constants; material policy is caller-supplied.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Optional

UTC = timezone.utc
CANONICAL_INSTRUMENT = "XAU_USD"
_ALIAS_DENY = ("XAUUSD",)
PLANNER_VERSION = "v1"
CONTRACT_VERSION = "v1"
PLANNING_MODE = "DRY_RUN_ONLY"
INTERVAL_CONVENTION = "HALF_OPEN_[start,end)"

# --- future runtime gate names (CONSTANTS only; this WO never wires them into boot) ---
ENABLED_ENV = "HERMES_RECOVERY_PLANNER_ENABLED"
AUTHORISED_ENV = "HERMES_RECOVERY_PLANNER_AUTHORISED"
HALT_CODE = 105

PERIOD_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600, "H4": 14400, "D1": 86400}
D1_ANCHOR_HOUR_UTC = 22            # 22:00 NY-5PM; 00:00 must never pass
SUPPORTED_TIMEFRAMES = ("M1", "M5", "M15", "H1", "H4", "D1")

# High-level planning statuses (never OK).
PLAN_STATUSES = ("NO_RECOVERY_REQUIRED", "PROPOSAL_READY", "PARTIAL_BOUNDED_PROPOSAL", "BLOCKED_INVALID_INPUT",
                 "BLOCKED_D1_BOUNDARY", "BLOCKED_STALE_GAPS", "BLOCKED_POLICY", "BLOCKED_UNCLASSIFIED_MARKET_STATE")

# Stable fault codes.
FAULT_CODES = (
    "INVALID_CONTRACT_VERSION", "INVALID_INSTRUMENT", "XAUUSD_REJECTED", "NAIVE_TIMESTAMP", "INVALID_INTERVAL",
    "STALE_GAPS_SNAPSHOT", "MISSING_GAPS_PAYLOAD", "INVALID_GAPS_STATE", "D1_BOUNDARY_FAILURE", "UNSUPPORTED_TIMEFRAME",
    "MISSING_CLOSURE_PROVENANCE", "UNCERTAIN_MARKET_STATE", "EXISTING_COVERAGE_MISMATCH", "WORKLOAD_BOUND_EXCEEDED",
    "INVALID_COST_MODEL", "NON_DETERMINISTIC_SEGMENT_IDENTITY", "SOURCE_POLICY_INSTRUMENT_MISMATCH",
)

SEGMENT_CLASSES = ("RECOVERY_CANDIDATE", "INTENTIONALLY_UNAVAILABLE", "EXISTING_COVERAGE_EXCLUDED",
                   "UNCLASSIFIED_MARKET_STATE", "DEFERRED_BOUND", "REJECTED")


class PlannerError(Exception):
    """Explicit, non-swallowed planner error carrying a stable fault code."""

    def __init__(self, code: str, message: str):
        if code not in FAULT_CODES:
            raise ValueError(f"unknown fault code {code!r}")
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


# --------------------------------------------------------------------------- time + interval primitives
def _assert_utc(dt: datetime, ctx: str) -> datetime:
    if not isinstance(dt, datetime):
        raise PlannerError("NAIVE_TIMESTAMP", f"{ctx}: not a datetime")
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise PlannerError("NAIVE_TIMESTAMP", f"{ctx}: naive datetime rejected")
    return dt.astimezone(UTC)


def _fmt_utc(dt: datetime) -> str:
    dt = _assert_utc(dt, "serialise")
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _parse_utc(s: str) -> datetime:
    if not isinstance(s, str) or not (s.endswith("Z") or s.endswith("+00:00")):
        raise PlannerError("NAIVE_TIMESTAMP", f"timestamp {s!r} must be explicit UTC (Z or +00:00)")
    raw = s[:-1] + "+0000" if s.endswith("Z") else s.replace("+00:00", "+0000")
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(raw, fmt).astimezone(UTC)
        except ValueError:
            continue
    raise PlannerError("INVALID_INTERVAL", f"unparseable UTC timestamp {s!r}")


@dataclass(frozen=True)
class Interval:
    """Half-open [start_utc, end_utc); both bounds tz-aware UTC; start strictly before end."""
    start_utc: datetime
    end_utc: datetime

    def __post_init__(self):
        s = _assert_utc(self.start_utc, "interval.start")
        e = _assert_utc(self.end_utc, "interval.end")
        if s >= e:
            raise PlannerError("INVALID_INTERVAL", f"non-positive interval [{_fmt_utc(s)},{_fmt_utc(e)})")
        object.__setattr__(self, "start_utc", s)
        object.__setattr__(self, "end_utc", e)

    def overlaps(self, other: "Interval") -> bool:
        return self.start_utc < other.end_utc and other.start_utc < self.end_utc

    def to_dict(self) -> dict:
        return {"start_utc": _fmt_utc(self.start_utc), "end_utc": _fmt_utc(self.end_utc)}


def _merge_intervals(intervals: tuple[Interval, ...]) -> tuple[Interval, ...]:
    """Sort + merge overlapping/adjacent intervals (adjacent: next.start <= prev.end). Does not mutate input."""
    if not intervals:
        return ()
    ordered = sorted(intervals, key=lambda iv: (iv.start_utc, iv.end_utc))
    out: list[Interval] = [ordered[0]]
    for iv in ordered[1:]:
        last = out[-1]
        if iv.start_utc <= last.end_utc:                     # overlap or adjacency
            if iv.end_utc > last.end_utc:
                out[-1] = Interval(last.start_utc, iv.end_utc)
        else:
            out.append(iv)
    return tuple(out)


def _subtract(gap: Interval, covers: tuple[Interval, ...]) -> tuple[Interval, ...]:
    """Residual of `gap` after removing merged `covers` (overlap shielding / idempotency). Half-open semantics."""
    merged = _merge_intervals(covers)
    residuals: list[Interval] = []
    cursor = gap.start_utc
    for c in merged:
        if c.end_utc <= gap.start_utc or c.start_utc >= gap.end_utc:
            continue
        cs = max(c.start_utc, gap.start_utc)
        if cs > cursor:
            residuals.append(Interval(cursor, cs))
        cursor = max(cursor, min(c.end_utc, gap.end_utc))
    if cursor < gap.end_utc:
        residuals.append(Interval(cursor, gap.end_utc))
    return tuple(residuals)


def _candles(iv: Interval, tf: str) -> int:
    p = PERIOD_SECONDS[tf]
    return int((iv.end_utc - iv.start_utc).total_seconds() // p)


# --------------------------------------------------------------------------- input contracts (frozen, validated)
@dataclass(frozen=True)
class GapRecord:
    timeframe: str
    missing_intervals: tuple[Interval, ...]
    gap_state: str

    def __post_init__(self):
        if self.timeframe not in SUPPORTED_TIMEFRAMES:
            raise PlannerError("UNSUPPORTED_TIMEFRAME", f"timeframe {self.timeframe!r} not supported")


@dataclass(frozen=True)
class GapSurfaceSnapshot:
    contract_version: str
    instrument: str
    source_key: str
    source_generated_at_utc: datetime
    overall_gap_state: str
    gap_records: tuple[GapRecord, ...]
    d1_boundary_state: str
    d1_anchor_hour_utc: int
    source_observed_at_utc: Optional[datetime] = None

    def __post_init__(self):
        _reject_alias(self.instrument, "gaps.instrument")
        if self.instrument != CANONICAL_INSTRUMENT:
            raise PlannerError("INVALID_INSTRUMENT", f"gaps instrument {self.instrument!r} != {CANONICAL_INSTRUMENT}")
        _assert_utc(self.source_generated_at_utc, "gaps.source_generated_at_utc")
        if self.source_observed_at_utc is not None:
            _assert_utc(self.source_observed_at_utc, "gaps.source_observed_at_utc")


@dataclass(frozen=True)
class ExistingCoverageSnapshot:
    contract_version: str
    instrument: str
    timeframe: str
    covered_intervals: tuple[Interval, ...]
    snapshot_at_utc: datetime
    provenance: str

    def __post_init__(self):
        _reject_alias(self.instrument, "coverage.instrument")
        if self.instrument != CANONICAL_INSTRUMENT:
            raise PlannerError("INVALID_INSTRUMENT", f"coverage instrument {self.instrument!r}")
        if self.timeframe not in SUPPORTED_TIMEFRAMES:
            raise PlannerError("UNSUPPORTED_TIMEFRAME", f"coverage timeframe {self.timeframe!r}")
        _assert_utc(self.snapshot_at_utc, "coverage.snapshot_at_utc")


@dataclass(frozen=True)
class MarketClosureSnapshot:
    contract_version: str
    instrument: str
    closure_interval: Interval
    classification: str           # e.g. WEEKEND | HOLIDAY | SESSION_BREAK
    provenance: str
    authority: str                # e.g. GOVERNED | UNVERIFIED
    effective_at_utc: datetime

    def __post_init__(self):
        _reject_alias(self.instrument, "closure.instrument")
        if self.instrument != CANONICAL_INSTRUMENT:
            raise PlannerError("INVALID_INSTRUMENT", f"closure instrument {self.instrument!r}")
        _assert_utc(self.effective_at_utc, "closure.effective_at_utc")


@dataclass(frozen=True)
class CostModel:
    candles_per_request: int
    request_units_per_call: int
    cost_tokens_per_request: int
    fixed_overhead_units: int
    timeframe_multipliers: tuple[tuple[str, float], ...]      # deterministic ordered pairs

    def __post_init__(self):
        if self.candles_per_request <= 0 or self.request_units_per_call <= 0:
            raise PlannerError("INVALID_COST_MODEL", "candles_per_request and request_units_per_call must be > 0")
        if self.cost_tokens_per_request < 0 or self.fixed_overhead_units < 0:
            raise PlannerError("INVALID_COST_MODEL", "cost/overhead must be >= 0")

    def multiplier(self, tf: str) -> float:
        for k, v in self.timeframe_multipliers:
            if k == tf:
                return v
        return 1.0


@dataclass(frozen=True)
class RecoveryPlanningPolicy:
    contract_version: str
    instrument: str
    allowed_timeframes: tuple[str, ...]
    timeframe_priority: tuple[str, ...]                       # ordered; index = rank
    priority_tiers: tuple[tuple[str, int], ...]              # (timeframe, tier) explicit
    max_segments: int
    max_candles_per_segment: int
    max_candles_per_proposal: int
    max_lookback_seconds: int
    max_request_units: int
    merge_adjacent_threshold_seconds: int
    d1_history_floor_seconds: int
    regular_weekend_closure_utc: tuple[int, int, int, int]    # (start_weekday, start_hour, end_weekday, end_hour)
    accepted_closure_authorities: tuple[str, ...]
    uncertain_to_unclassified: bool
    stale_gaps_max_age_seconds: int
    allow_partial: bool
    cost_model: CostModel

    def __post_init__(self):
        _reject_alias(self.instrument, "policy.instrument")
        if self.instrument != CANONICAL_INSTRUMENT:
            raise PlannerError("INVALID_INSTRUMENT", f"policy instrument {self.instrument!r}")
        if self.contract_version != CONTRACT_VERSION:
            raise PlannerError("INVALID_CONTRACT_VERSION", f"policy contract_version {self.contract_version!r}")
        for tf in self.allowed_timeframes + self.timeframe_priority:
            if tf not in SUPPORTED_TIMEFRAMES:
                raise PlannerError("UNSUPPORTED_TIMEFRAME", f"policy timeframe {tf!r}")
        for lim in (self.max_segments, self.max_candles_per_segment, self.max_candles_per_proposal,
                    self.max_lookback_seconds, self.max_request_units):
            if lim <= 0:
                _raise_policy("policy limits must be > 0")

    def rank(self, tf: str) -> int:
        return self.timeframe_priority.index(tf) if tf in self.timeframe_priority else len(self.timeframe_priority)

    def tier(self, tf: str) -> int:
        for k, v in self.priority_tiers:
            if k == tf:
                return v
        return 99

    def digest(self) -> str:
        return _sha(_canonical_policy_dict(self))


def _raise_policy(msg: str):
    raise PlannerError("WORKLOAD_BOUND_EXCEEDED", msg)


def _reject_alias(value: str, ctx: str) -> None:
    if any(a in str(value) for a in _ALIAS_DENY):
        raise PlannerError("XAUUSD_REJECTED", f"{ctx}: forbidden alias in {value!r}")


def _sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _canonical_policy_dict(p: RecoveryPlanningPolicy) -> dict:
    cm = p.cost_model
    return {"contract_version": p.contract_version, "instrument": p.instrument,
            "allowed_timeframes": list(p.allowed_timeframes), "timeframe_priority": list(p.timeframe_priority),
            "priority_tiers": [list(t) for t in p.priority_tiers], "max_segments": p.max_segments,
            "max_candles_per_segment": p.max_candles_per_segment, "max_candles_per_proposal": p.max_candles_per_proposal,
            "max_lookback_seconds": p.max_lookback_seconds, "max_request_units": p.max_request_units,
            "merge_adjacent_threshold_seconds": p.merge_adjacent_threshold_seconds,
            "d1_history_floor_seconds": p.d1_history_floor_seconds,
            "regular_weekend_closure_utc": list(p.regular_weekend_closure_utc),
            "accepted_closure_authorities": list(p.accepted_closure_authorities),
            "uncertain_to_unclassified": p.uncertain_to_unclassified,
            "stale_gaps_max_age_seconds": p.stale_gaps_max_age_seconds, "allow_partial": p.allow_partial,
            "cost_model": {"candles_per_request": cm.candles_per_request, "request_units_per_call": cm.request_units_per_call,
                           "cost_tokens_per_request": cm.cost_tokens_per_request, "fixed_overhead_units": cm.fixed_overhead_units,
                           "timeframe_multipliers": [list(t) for t in cm.timeframe_multipliers]}}


# --------------------------------------------------------------------------- segment + output contracts
@dataclass(frozen=True)
class RecoverySegment:
    timeframe: str
    interval: Interval
    candle_count: int
    priority_rank: int
    priority_tier: int
    segment_class: str
    required_source: str
    reason: str
    segment_id: str

    def to_dict(self) -> dict:
        return {"segment_id": self.segment_id, "timeframe": self.timeframe, **self.interval.to_dict(),
                "candle_count": self.candle_count, "priority_rank": self.priority_rank, "priority_tier": self.priority_tier,
                "segment_class": self.segment_class, "required_source": self.required_source, "reason": self.reason}


def _segment_id(instrument: str, tf: str, iv: Interval, seg_class: str) -> str:
    return "seg_" + _sha([instrument, tf, _fmt_utc(iv.start_utc), _fmt_utc(iv.end_utc), seg_class])[:20]


@dataclass(frozen=True)
class ProposedWorkload:
    contract_version: str
    proposal_id: str
    planner_version: str
    target_instrument: str
    generated_at_utc: str
    source_gaps_generated_at_utc: str
    planning_mode: str
    execution_enabled: bool
    backfill_executed: bool
    repair_executed: bool
    consumer_live: bool
    overall_plan_status: str
    estimated_request_units: int
    estimated_api_cost_tokens: int
    prioritized_segments_list: tuple[RecoverySegment, ...]
    intentionally_unavailable_segments: tuple[RecoverySegment, ...]
    excluded_existing_coverage_segments: tuple[RecoverySegment, ...]
    unclassified_segments: tuple[RecoverySegment, ...]
    deferred_segments: tuple[RecoverySegment, ...]
    faults: tuple[dict, ...]
    warnings: tuple[str, ...]
    input_provenance: dict
    policy_digest: str

    def to_dict(self) -> dict:
        return {
            "contract_version": self.contract_version, "proposal_id": self.proposal_id,
            "planner_version": self.planner_version, "target_instrument": self.target_instrument,
            "generated_at_utc": self.generated_at_utc, "source_gaps_generated_at_utc": self.source_gaps_generated_at_utc,
            "planning_mode": self.planning_mode, "execution_enabled": self.execution_enabled,
            "backfill_executed": self.backfill_executed, "repair_executed": self.repair_executed,
            "consumer_live": self.consumer_live, "overall_plan_status": self.overall_plan_status,
            "estimated_request_units": self.estimated_request_units, "estimated_api_cost_tokens": self.estimated_api_cost_tokens,
            "prioritized_segments_list": [s.to_dict() for s in self.prioritized_segments_list],
            "intentionally_unavailable_segments": [s.to_dict() for s in self.intentionally_unavailable_segments],
            "excluded_existing_coverage_segments": [s.to_dict() for s in self.excluded_existing_coverage_segments],
            "unclassified_segments": [s.to_dict() for s in self.unclassified_segments],
            "deferred_segments": [s.to_dict() for s in self.deferred_segments],
            "faults": list(self.faults), "warnings": list(self.warnings),
            "input_provenance": self.input_provenance, "policy_digest": self.policy_digest,
        }

    def to_json(self, *, indent: Optional[int] = None) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=True, allow_nan=False)


def validate_proposed_workload(d: dict) -> bool:
    """Validate a serialised ProposedWorkload dict. Fail-closed on invariant / alias / OK-status violations."""
    if d.get("target_instrument") != CANONICAL_INSTRUMENT:
        raise PlannerError("INVALID_INSTRUMENT", "target_instrument must be XAU_USD")
    if "XAUUSD" in json.dumps(d):
        raise PlannerError("XAUUSD_REJECTED", "XAUUSD alias present in proposal")
    for f in ("execution_enabled", "backfill_executed", "repair_executed", "consumer_live"):
        if d.get(f) is not False:
            raise PlannerError("INVALID_GAPS_STATE", f"{f} must be hard false")
    if d.get("planning_mode") != PLANNING_MODE:
        raise PlannerError("INVALID_GAPS_STATE", "planning_mode must be DRY_RUN_ONLY")
    if d.get("overall_plan_status") not in PLAN_STATUSES or d.get("overall_plan_status") == "OK":
        raise PlannerError("INVALID_GAPS_STATE", "overall_plan_status not in fail-closed vocab / OK forbidden")
    return True


# --------------------------------------------------------------------------- gate evaluation (no I/O, no boot wiring)
def evaluate_recovery_planner_gate(*, enabled: bool, authorised: bool) -> dict:
    """Gate truth table. Enabled+authorised only GRANTS a future caller permission to PLAN — never to execute/publish.
    enabled=False -> DISABLED (regardless of authorised). enabled=True & authorised=False -> SystemExit(105) (fail-closed)."""
    if not enabled:
        return {"enabled": False, "authorised": bool(authorised), "state": "DISABLED", "planning_permitted": False,
                "execution_permitted": False}
    if not authorised:
        raise SystemExit(HALT_CODE)
    return {"enabled": True, "authorised": True, "state": "ENABLED_AUTHORISED_PLAN_ONLY", "planning_permitted": True,
            "execution_permitted": False}


def evaluate_recovery_planner_gate_from_env() -> dict:
    """Env-only gate read (no client, no boot wiring in this WO)."""
    from env_config import get_env_bool
    return evaluate_recovery_planner_gate(enabled=get_env_bool(ENABLED_ENV, False),
                                          authorised=get_env_bool(AUTHORISED_ENV, False))


# --------------------------------------------------------------------------- closure classification
def _governed_closures(policy: RecoveryPlanningPolicy, closures: tuple[MarketClosureSnapshot, ...],
                       horizon: Interval, warnings: list[str]) -> tuple[tuple[Interval, ...], tuple[Interval, ...]]:
    """Return (governed_closure_intervals, uncertain_closure_intervals). Regular weekend closure comes from policy;
    holiday/other closures require an accepted authority, else they are NOT silently excluded (warned; optionally
    routed to UNCLASSIFIED by caller). Ungoverned holidays are never guessed from date alone."""
    governed = list(_weekend_intervals(policy, horizon))
    uncertain = []
    for c in closures:
        if c.authority in policy.accepted_closure_authorities:
            governed.append(c.closure_interval)
        else:
            warnings.append(f"closure {c.classification}@{_fmt_utc(c.closure_interval.start_utc)} authority={c.authority} "
                            f"not accepted -> not excluded (uncertain)")
            uncertain.append(c.closure_interval)
    return _merge_intervals(tuple(governed)), _merge_intervals(tuple(uncertain))


def _weekend_intervals(policy: RecoveryPlanningPolicy, horizon: Interval) -> tuple[Interval, ...]:
    """Deterministically enumerate regular weekend closure intervals intersecting the horizon from policy rule
    (start_weekday, start_hour, end_weekday, end_hour) in UTC. weekday: Monday=0..Sunday=6."""
    sw, sh, ew, eh = policy.regular_weekend_closure_utc
    out = []
    day = datetime(horizon.start_utc.year, horizon.start_utc.month, horizon.start_utc.day, tzinfo=UTC)
    day = day.replace(hour=0, minute=0, second=0, microsecond=0)
    # walk back a week to catch a closure straddling the horizon start
    from datetime import timedelta
    cur = day - timedelta(days=8)
    end_scan = horizon.end_utc + timedelta(days=1)
    while cur <= end_scan:
        if cur.weekday() == sw:
            start = cur.replace(hour=sh)
            # end at the next occurrence of end weekday/hour after start
            delta_days = (ew - sw) % 7
            end = (start + timedelta(days=delta_days)).replace(hour=eh)
            if end <= start:
                end = end + timedelta(days=7)
            iv = Interval(start, end)
            if iv.overlaps(horizon):
                out.append(Interval(max(iv.start_utc, horizon.start_utc), min(iv.end_utc, horizon.end_utc)))
        cur = cur + timedelta(days=1)
    return _merge_intervals(tuple(out))


# --------------------------------------------------------------------------- main planner (PURE)
def build_recovery_proposal(*, gaps: GapSurfaceSnapshot, coverage: tuple[ExistingCoverageSnapshot, ...],
                            closures: tuple[MarketClosureSnapshot, ...], policy: RecoveryPlanningPolicy,
                            now_utc: datetime) -> ProposedWorkload:
    """Convert validated inputs into a bounded, prioritised, deterministic ProposedWorkload. PURE: no I/O, no execution.
    `now_utc` is the injected clock (tests use a fixed clock). Blocking conditions return a BLOCKED_* status with NO
    executable-looking segment list."""
    now = _assert_utc(now_utc, "now_utc")
    faults: list[dict] = []
    warnings: list[str] = []

    def _blocked(status: str, code: str, msg: str) -> ProposedWorkload:
        return _empty(gaps, policy, now, status, ({"code": code, "message": msg},), tuple(warnings))

    # ---- input governance ----
    if gaps.contract_version != CONTRACT_VERSION:
        return _blocked("BLOCKED_INVALID_INPUT", "INVALID_CONTRACT_VERSION", f"gaps contract {gaps.contract_version!r}")
    if gaps.instrument != policy.instrument:
        return _blocked("BLOCKED_INVALID_INPUT", "SOURCE_POLICY_INSTRUMENT_MISMATCH", "gaps vs policy instrument")
    age = (now - gaps.source_generated_at_utc).total_seconds()
    if age > policy.stale_gaps_max_age_seconds:
        return _blocked("BLOCKED_STALE_GAPS", "STALE_GAPS_SNAPSHOT", f"gaps age {int(age)}s > {policy.stale_gaps_max_age_seconds}s")
    if not gaps.gap_records:
        return _blocked("BLOCKED_INVALID_INPUT", "MISSING_GAPS_PAYLOAD", "no gap records")
    for cov in coverage:
        if cov.instrument != policy.instrument:
            return _blocked("BLOCKED_INVALID_INPUT", "EXISTING_COVERAGE_MISMATCH", "coverage instrument mismatch")
    # ---- D1 boundary doctrine: a boundary defect / wrong anchor blocks, never becomes a segment ----
    if any(r.timeframe == "D1" for r in gaps.gap_records):
        if gaps.d1_anchor_hour_utc != D1_ANCHOR_HOUR_UTC:
            return _blocked("BLOCKED_D1_BOUNDARY", "D1_BOUNDARY_FAILURE",
                            f"D1 anchor {gaps.d1_anchor_hour_utc}:00 != {D1_ANCHOR_HOUR_UTC}:00")
        if gaps.d1_boundary_state != "OK":
            return _blocked("BLOCKED_D1_BOUNDARY", "D1_BOUNDARY_FAILURE", f"d1_boundary_state={gaps.d1_boundary_state}")

    horizon_start = now.fromtimestamp(now.timestamp() - policy.max_lookback_seconds, UTC)
    horizon = Interval(horizon_start, now)
    governed_cl, uncertain_cl = _governed_closures(policy, closures, horizon, warnings)
    cov_by_tf: dict[str, tuple[Interval, ...]] = {}
    for cov in coverage:
        cov_by_tf.setdefault(cov.timeframe, tuple())
        cov_by_tf[cov.timeframe] = cov_by_tf[cov.timeframe] + cov.covered_intervals

    candidates: list[RecoverySegment] = []
    unavailable: list[RecoverySegment] = []
    excluded_cov: list[RecoverySegment] = []
    unclassified: list[RecoverySegment] = []

    for rec in gaps.gap_records:
        tf = rec.timeframe
        if tf not in policy.allowed_timeframes:
            faults.append({"code": "UNSUPPORTED_TIMEFRAME", "message": f"{tf} not in policy.allowed_timeframes"})
            continue
        for miss in rec.missing_intervals:
            miss = _assert_interval(miss)
            # clip to horizon (respect max_lookback); older -> not a recovery target
            if not miss.overlaps(horizon):
                continue
            miss = Interval(max(miss.start_utc, horizon.start_utc), min(miss.end_utc, now))
            # D1 history-floor: only within-floor D1 gaps are recovery targets
            if tf == "D1":
                floor_start = now.fromtimestamp(now.timestamp() - policy.d1_history_floor_seconds, UTC)
                if miss.end_utc <= floor_start:
                    continue                                    # beyond history floor -> not required
                miss = Interval(max(miss.start_utc, floor_start), miss.end_utc)
            # overlap shielding: remove existing coverage
            covered_here = _intersections(miss, cov_by_tf.get(tf, ()))
            for cvd in covered_here:
                excluded_cov.append(_mk(policy, tf, cvd, "EXISTING_COVERAGE_EXCLUDED", "already covered locally"))
            for residual in _subtract(miss, cov_by_tf.get(tf, ())):
                # governed closures -> intentionally unavailable; clip them out of the recovery residual
                for cl in _intersections(residual, governed_cl):
                    unavailable.append(_mk(policy, tf, cl, "INTENTIONALLY_UNAVAILABLE", "governed market closure"))
                for rem in _subtract(residual, governed_cl):
                    # uncertain closure overlap -> unclassified (honest), per policy
                    unc = _intersections(rem, uncertain_cl) if policy.uncertain_to_unclassified else ()
                    for u in unc:
                        unclassified.append(_mk(policy, tf, u, "UNCLASSIFIED_MARKET_STATE", "uncertain closure evidence"))
                    for cand in (_subtract(rem, uncertain_cl) if policy.uncertain_to_unclassified else (rem,)):
                        candidates.extend(_split_by_candle_bound(policy, tf, cand))

    # ---- coalesce adjacent same-tf/same-class candidates (never across closures/limits) ----
    candidates = _coalesce(policy, candidates, governed_cl)
    # ---- deterministic priority ordering ----
    candidates.sort(key=lambda s: (s.priority_rank, s.interval.start_utc, s.timeframe, s.segment_id))
    # ---- bounded workload ----
    accepted, deferred, bound_status = _apply_bounds(policy, candidates, faults)

    req_units, cost_tokens = _estimate(policy, accepted)
    if req_units > policy.max_request_units:
        # request-unit bound: defer from the tail until within budget (or block if partial disallowed)
        accepted, deferred2 = _trim_to_units(policy, accepted)
        deferred = deferred + deferred2
        bound_status = "PARTIAL_BOUNDED_PROPOSAL"
        req_units, cost_tokens = _estimate(policy, accepted)
        if not policy.allow_partial:
            return _blocked("BLOCKED_POLICY", "WORKLOAD_BOUND_EXCEEDED", "request units exceed max and partials disallowed")

    if not candidates and not unavailable and not excluded_cov and not unclassified:
        status = "NO_RECOVERY_REQUIRED"
    elif not accepted and deferred:
        status = "BLOCKED_POLICY" if not policy.allow_partial else "PARTIAL_BOUNDED_PROPOSAL"
    elif deferred or bound_status == "PARTIAL_BOUNDED_PROPOSAL":
        if not policy.allow_partial:
            return _blocked("BLOCKED_POLICY", "WORKLOAD_BOUND_EXCEEDED", "bounds exceeded; partials disallowed")
        status = "PARTIAL_BOUNDED_PROPOSAL"
        warnings.append(f"{len(deferred)} segment(s) deferred due to workload bounds")
    else:
        status = "PROPOSAL_READY"
    if uncertain_cl and unclassified and status in ("PROPOSAL_READY", "NO_RECOVERY_REQUIRED"):
        warnings.append("unclassified market-state segments present; review closure provenance")

    provenance = {"gaps_source_key": gaps.source_key, "gaps_generated_at_utc": _fmt_utc(gaps.source_generated_at_utc),
                  "coverage_provenances": sorted({c.provenance for c in coverage}),
                  "closure_provenances": sorted({c.provenance for c in closures}),
                  "interval_convention": INTERVAL_CONVENTION}
    proposal_id = "prop_" + _sha({"instrument": policy.instrument, "policy_digest": policy.digest(),
                                  "status": status, "accepted": [s.segment_id for s in accepted],
                                  "deferred": [s.segment_id for s in deferred],
                                  "unavailable": [s.segment_id for s in unavailable],
                                  "excluded": [s.segment_id for s in excluded_cov],
                                  "unclassified": [s.segment_id for s in unclassified]})[:24]

    wl = ProposedWorkload(
        contract_version=CONTRACT_VERSION, proposal_id=proposal_id, planner_version=PLANNER_VERSION,
        target_instrument=CANONICAL_INSTRUMENT, generated_at_utc=_fmt_utc(now),
        source_gaps_generated_at_utc=_fmt_utc(gaps.source_generated_at_utc), planning_mode=PLANNING_MODE,
        execution_enabled=False, backfill_executed=False, repair_executed=False, consumer_live=False,
        overall_plan_status=status, estimated_request_units=req_units, estimated_api_cost_tokens=cost_tokens,
        prioritized_segments_list=tuple(accepted), intentionally_unavailable_segments=tuple(unavailable),
        excluded_existing_coverage_segments=tuple(excluded_cov), unclassified_segments=tuple(unclassified),
        deferred_segments=tuple(deferred), faults=tuple(faults), warnings=tuple(warnings),
        input_provenance=provenance, policy_digest=policy.digest())
    validate_proposed_workload(wl.to_dict())
    return wl


# --------------------------------------------------------------------------- helpers
def _assert_interval(iv: Interval) -> Interval:
    if not isinstance(iv, Interval):
        raise PlannerError("INVALID_INTERVAL", "expected Interval")
    return iv


def _intersections(base: Interval, others: tuple[Interval, ...]) -> tuple[Interval, ...]:
    out = []
    for o in _merge_intervals(others):
        if base.overlaps(o):
            out.append(Interval(max(base.start_utc, o.start_utc), min(base.end_utc, o.end_utc)))
    return tuple(out)


def _mk(policy: RecoveryPlanningPolicy, tf: str, iv: Interval, seg_class: str, reason: str) -> RecoverySegment:
    return RecoverySegment(timeframe=tf, interval=iv, candle_count=_candles(iv, tf), priority_rank=policy.rank(tf),
                           priority_tier=policy.tier(tf), segment_class=seg_class,
                           required_source=f"governed_candle_history:{tf}", reason=reason,
                           segment_id=_segment_id(policy.instrument, tf, iv, seg_class))


def _split_by_candle_bound(policy: RecoveryPlanningPolicy, tf: str, iv: Interval) -> list[RecoverySegment]:
    """Split a residual exceeding max_candles_per_segment into aligned bounded chunks (never silent truncation)."""
    p = PERIOD_SECONDS[tf]
    total = _candles(iv, tf)
    if total <= 0:
        return []
    cap = policy.max_candles_per_segment
    segs = []
    cur = iv.start_utc
    from datetime import timedelta
    while cur < iv.end_utc:
        chunk_end = min(iv.end_utc, cur.fromtimestamp(cur.timestamp() + cap * p, UTC))
        segs.append(_mk(policy, tf, Interval(cur, chunk_end), "RECOVERY_CANDIDATE", "uncovered open-market gap"))
        cur = chunk_end
    return segs


def _coalesce(policy: RecoveryPlanningPolicy, segs: list[RecoverySegment],
              governed_cl: tuple[Interval, ...]) -> list[RecoverySegment]:
    """Merge adjacent same-tf/same-class/same-source candidates when the gap between them <= threshold AND no governed
    closure lies between them AND the merged candle count stays within max_candles_per_segment."""
    by_key: dict[tuple, list[RecoverySegment]] = {}
    for s in segs:
        by_key.setdefault((s.timeframe, s.segment_class, s.required_source), []).append(s)
    out: list[RecoverySegment] = []
    from datetime import timedelta
    for key, group in by_key.items():
        group.sort(key=lambda s: s.interval.start_utc)
        cur = group[0]
        for nxt in group[1:]:
            gap_between = (nxt.interval.start_utc - cur.interval.end_utc).total_seconds()
            closure_between = any(cl.start_utc < nxt.interval.start_utc and cl.end_utc > cur.interval.end_utc
                                  for cl in governed_cl)
            merged_iv = Interval(cur.interval.start_utc, nxt.interval.end_utc)
            if (0 <= gap_between <= policy.merge_adjacent_threshold_seconds and not closure_between
                    and _candles(merged_iv, cur.timeframe) <= policy.max_candles_per_segment):
                cur = _mk(policy, cur.timeframe, merged_iv, cur.segment_class, cur.reason)
            else:
                out.append(cur)
                cur = nxt
        out.append(cur)
    return out


def _apply_bounds(policy: RecoveryPlanningPolicy, segs: list[RecoverySegment],
                  faults: list[dict]) -> tuple[list[RecoverySegment], list[RecoverySegment], str]:
    accepted: list[RecoverySegment] = []
    deferred: list[RecoverySegment] = []
    total_candles = 0
    status = "OK_BOUNDS"
    for s in segs:
        if (len(accepted) >= policy.max_segments or total_candles + s.candle_count > policy.max_candles_per_proposal):
            deferred.append(replace(s, segment_class="DEFERRED_BOUND", reason="workload bound: " + s.reason))
            status = "PARTIAL_BOUNDED_PROPOSAL"
            continue
        accepted.append(s)
        total_candles += s.candle_count
    if deferred:
        faults.append({"code": "WORKLOAD_BOUND_EXCEEDED",
                       "message": f"{len(deferred)} segment(s) exceed max_segments/max_candles_per_proposal"})
    return accepted, deferred, status


def _trim_to_units(policy: RecoveryPlanningPolicy,
                   accepted: list[RecoverySegment]) -> tuple[list[RecoverySegment], list[RecoverySegment]]:
    keep, drop = [], []
    for s in accepted:
        u, _ = _estimate(policy, keep + [s])
        if u <= policy.max_request_units:
            keep.append(s)
        else:
            drop.append(replace(s, segment_class="DEFERRED_BOUND", reason="request-unit bound: " + s.reason))
    return keep, drop


def _estimate(policy: RecoveryPlanningPolicy, segs: list[RecoverySegment]) -> tuple[int, int]:
    """Deterministic, vendor-neutral estimate from the explicit cost model. Estimates only — never actual charges."""
    cm = policy.cost_model
    units = 0
    tokens = 0
    for s in segs:
        calls = math.ceil(s.candle_count / cm.candles_per_request) if s.candle_count > 0 else 0
        units += int(round(calls * cm.request_units_per_call * cm.multiplier(s.timeframe))) + cm.fixed_overhead_units
        tokens += calls * cm.cost_tokens_per_request
    return units, tokens


def _empty(gaps: GapSurfaceSnapshot, policy: RecoveryPlanningPolicy, now: datetime, status: str,
           faults: tuple[dict, ...], warnings: tuple[str, ...]) -> ProposedWorkload:
    prov = {"gaps_source_key": gaps.source_key, "gaps_generated_at_utc": _fmt_utc(gaps.source_generated_at_utc),
            "interval_convention": INTERVAL_CONVENTION}
    pid = "prop_" + _sha({"instrument": policy.instrument, "policy_digest": policy.digest(), "status": status,
                          "faults": list(faults)})[:24]
    wl = ProposedWorkload(
        contract_version=CONTRACT_VERSION, proposal_id=pid, planner_version=PLANNER_VERSION,
        target_instrument=CANONICAL_INSTRUMENT, generated_at_utc=_fmt_utc(now),
        source_gaps_generated_at_utc=_fmt_utc(gaps.source_generated_at_utc), planning_mode=PLANNING_MODE,
        execution_enabled=False, backfill_executed=False, repair_executed=False, consumer_live=False,
        overall_plan_status=status, estimated_request_units=0, estimated_api_cost_tokens=0,
        prioritized_segments_list=(), intentionally_unavailable_segments=(), excluded_existing_coverage_segments=(),
        unclassified_segments=(), deferred_segments=(), faults=faults, warnings=warnings,
        input_provenance=prov, policy_digest=policy.digest())
    validate_proposed_workload(wl.to_dict())
    return wl
