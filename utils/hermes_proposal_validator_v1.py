"""HERMES PH2 — pure recovery-proposal PUBLICATION-ELIGIBILITY validator v1.
WO-HELM-HERMES-PH2-RECOVERY-PROPOSAL-PUBLICATION-ELIGIBILITY-VALIDATOR-0001.
Created (UTC): 2026-07-15. Owner: HERMES (Helm). Implements the merged PR #97 fail-closed publication contract design.

>>> This module determines ELIGIBILITY ONLY. It does not publish, persist, revoke, execute or mutate runtime state. <<<

PURE BOUNDARY (enforced by tests + static scan): standard library only; no Redis/SQL/HTTP/socket/subprocess/shell client;
no filesystem write; no environment/policy-file read; no logging side effect at import; no module singleton; starts no
thread/task/timer; imports NO other application and NO legacy recovery / recovery_executor / publisher-runner assembly.

The caller supplies a fully-loaded CandidateProposal + ValidationContext + PublicationConfig + an explicit now_utc. The
validator NEVER fetches inputs and NEVER infers missing truth: a missing/unknown fact fails closed. It returns an immutable
EligibilityDecision carrying eligible/decision_code, deterministically-ordered refusal codes, a neutralisation recommendation,
retry/alert classification, and (only when eligible) a logical expires_at_utc computed from now_utc + config. It performs the
merged AND-predicate across layers G(gates) H(currency) P(policy) S(source freshness+digest) I(integrity) C(envelope/generation).
Reusable later by a separately-audited publisher adapter which alone performs Redis compare-and-set / TTL / revocation.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field, replace
from typing import Mapping, Optional, Sequence, Tuple

UTC = datetime.timezone.utc
DECISION_VERSION = "1"
CONTRACT_VERSION = "1"
CANONICAL_INSTRUMENT = "XAU_USD"
FORBIDDEN_ALIAS = "XAUUSD"

# Deterministic timeframe -> period seconds (matches the pure planner; local constant, non-operational).
TIMEFRAME_PERIOD_SECONDS: Mapping[str, int] = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600, "H4": 14400, "D1": 86400}
RETENTION_DAYS_DEFAULT: Mapping[str, int] = {"M1": 35, "M5": 35, "M15": 35, "H1": 35, "H4": 35, "D1": 120}

# Gate states (not refusal codes).
GATE_DISABLED = "PUB_DISABLED"
GATE_PLAN_PUBLISH_ENABLED = "PUB_PLAN_PUBLISH_ENABLED"

# --- the 33 governed PUB_* refusal codes (exact reconciliation to merged PR #97 fault_code_catalogue.md) ---
PUB_GATES_DISABLED = "PUB_GATES_DISABLED"
PUB_GATE_MISMATCH = "PUB_GATE_MISMATCH"
PUB_UNSUPPORTED_CONTRACT_VERSION = "PUB_UNSUPPORTED_CONTRACT_VERSION"
PUB_UNSUPPORTED_PLANNER_VERSION = "PUB_UNSUPPORTED_PLANNER_VERSION"
PUB_NO_CURRENT_PROPOSAL = "PUB_NO_CURRENT_PROPOSAL"
PUB_PROPOSAL_STATUS_INELIGIBLE = "PUB_PROPOSAL_STATUS_INELIGIBLE"
PUB_PROPOSAL_STALE = "PUB_PROPOSAL_STALE"
PUB_PROPOSAL_EXPIRED = "PUB_PROPOSAL_EXPIRED"
PUB_SUPERSEDED = "PUB_SUPERSEDED"
PUB_REVOKED = "PUB_REVOKED"
PUB_BLOCKED = "PUB_BLOCKED"
PUB_POLICY_ABSENT = "PUB_POLICY_ABSENT"
PUB_POLICY_INVALID = "PUB_POLICY_INVALID"
PUB_POLICY_CHANGED = "PUB_POLICY_CHANGED"
PUB_GAPS_MISSING = "PUB_GAPS_MISSING"
PUB_GAPS_STALE = "PUB_GAPS_STALE"
PUB_GAPS_DIGEST_MISMATCH = "PUB_GAPS_DIGEST_MISMATCH"
PUB_COVERAGE_MISSING = "PUB_COVERAGE_MISSING"
PUB_COVERAGE_STALE = "PUB_COVERAGE_STALE"
PUB_COVERAGE_DIGEST_MISMATCH = "PUB_COVERAGE_DIGEST_MISMATCH"
PUB_CLOSURE_INCOMPLETE = "PUB_CLOSURE_INCOMPLETE"
PUB_UNRESOLVED_EXCEPTIONAL_CLOSURE = "PUB_UNRESOLVED_EXCEPTIONAL_CLOSURE"
PUB_INCONSISTENT_SNAPSHOT = "PUB_INCONSISTENT_SNAPSHOT"
PUB_NON_CANONICAL_INSTRUMENT = "PUB_NON_CANONICAL_INSTRUMENT"
PUB_OUT_OF_RETENTION = "PUB_OUT_OF_RETENTION"
PUB_UNCLASSIFIED_PRESENT = "PUB_UNCLASSIFIED_PRESENT"
PUB_GAP_INVARIANT_VIOLATION = "PUB_GAP_INVARIANT_VIOLATION"
PUB_DUPLICATE_PUBLISHER = "PUB_DUPLICATE_PUBLISHER"
PUB_STALE_WRITER = "PUB_STALE_WRITER"
PUB_REDIS_UNAVAILABLE = "PUB_REDIS_UNAVAILABLE"
PUB_ATOMIC_PUBLICATION_FAILED = "PUB_ATOMIC_PUBLICATION_FAILED"
PUB_PAYLOAD_TOO_LARGE = "PUB_PAYLOAD_TOO_LARGE"
PUB_SCHEMA_VALIDATION_FAILED = "PUB_SCHEMA_VALIDATION_FAILED"

# Canonical deterministic refusal ordering (§10). Index = evaluation/priority rank; primary = lowest rank present.
CODE_ORDER: Tuple[str, ...] = (
    # 1 malformed/unsupported contract + envelope structure
    PUB_UNSUPPORTED_CONTRACT_VERSION, PUB_SCHEMA_VALIDATION_FAILED,
    # 2 publication gates
    PUB_GATES_DISABLED, PUB_GATE_MISMATCH,
    # 3 canonical identity
    PUB_NON_CANONICAL_INSTRUMENT,
    # 4 holder / proposal state
    PUB_NO_CURRENT_PROPOSAL, PUB_PROPOSAL_STATUS_INELIGIBLE, PUB_BLOCKED, PUB_REVOKED, PUB_SUPERSEDED,
    PUB_PROPOSAL_EXPIRED, PUB_PROPOSAL_STALE,
    # 5 policy
    PUB_POLICY_ABSENT, PUB_POLICY_INVALID, PUB_POLICY_CHANGED, PUB_UNSUPPORTED_PLANNER_VERSION,
    # 6 source presence + freshness
    PUB_GAPS_MISSING, PUB_GAPS_STALE, PUB_COVERAGE_MISSING, PUB_COVERAGE_STALE,
    # 7 digest identity
    PUB_GAPS_DIGEST_MISMATCH, PUB_COVERAGE_DIGEST_MISMATCH,
    # 8 closure + exceptional closure
    PUB_CLOSURE_INCOMPLETE, PUB_UNRESOLVED_EXCEPTIONAL_CLOSURE,
    # 9 snapshot / integrity
    PUB_INCONSISTENT_SNAPSHOT,
    # 10 retention
    PUB_OUT_OF_RETENTION,
    # 11 payload + envelope content (unclassified, gap invariant, oversize)
    PUB_UNCLASSIFIED_PRESENT, PUB_GAP_INVARIANT_VIOLATION, PUB_PAYLOAD_TOO_LARGE,
    # 12 generation / fencing / atomic preconditions
    PUB_STALE_WRITER, PUB_DUPLICATE_PUBLISHER, PUB_REDIS_UNAVAILABLE, PUB_ATOMIC_PUBLICATION_FAILED,
)
ALL_PUB_CODES = frozenset(CODE_ORDER)
_ORDER_INDEX = {c: i for i, c in enumerate(CODE_ORDER)}

# Per-code metadata: retryable, alertable, neutralise-prior-current-if-was-current (effects from PR #97 catalogue).
_RETRYABLE = frozenset({PUB_INCONSISTENT_SNAPSHOT, PUB_REDIS_UNAVAILABLE, PUB_ATOMIC_PUBLICATION_FAILED})
_ALERTABLE = frozenset({
    PUB_GATE_MISMATCH, PUB_UNSUPPORTED_CONTRACT_VERSION, PUB_UNSUPPORTED_PLANNER_VERSION, PUB_NON_CANONICAL_INSTRUMENT,
    PUB_POLICY_ABSENT, PUB_POLICY_INVALID, PUB_GAP_INVARIANT_VIOLATION, PUB_UNRESOLVED_EXCEPTIONAL_CLOSURE,
    PUB_DUPLICATE_PUBLISHER, PUB_PAYLOAD_TOO_LARGE, PUB_SCHEMA_VALIDATION_FAILED,
})
# Codes that, when the candidate WAS the current holder, require neutralising the prior current pointer (input drift / lost eligibility).
_NEUTRALISE_IF_CURRENT = frozenset({
    PUB_PROPOSAL_STATUS_INELIGIBLE, PUB_BLOCKED, PUB_REVOKED, PUB_SUPERSEDED, PUB_PROPOSAL_EXPIRED, PUB_PROPOSAL_STALE,
    PUB_POLICY_ABSENT, PUB_POLICY_INVALID, PUB_POLICY_CHANGED, PUB_GAPS_MISSING, PUB_GAPS_STALE, PUB_GAPS_DIGEST_MISMATCH,
    PUB_COVERAGE_MISSING, PUB_COVERAGE_STALE, PUB_COVERAGE_DIGEST_MISMATCH, PUB_CLOSURE_INCOMPLETE,
    PUB_UNRESOLVED_EXCEPTIONAL_CLOSURE, PUB_INCONSISTENT_SNAPSHOT, PUB_OUT_OF_RETENTION, PUB_UNCLASSIFIED_PRESENT,
    PUB_GAP_INVARIANT_VIOLATION, PUB_NON_CANONICAL_INSTRUMENT,
})

# Recommendation values (pure; the adapter decides how to apply).
REC_ELIGIBLE = "ELIGIBLE"
REC_REFUSE_NO_CURRENT_KEY = "REFUSE_NO_CURRENT_KEY"
REC_REFUSE_AND_NEUTRALISE = "REFUSE_AND_NEUTRALISE_PRIOR_CURRENT"

PERMITTED_PLANNER_STATUSES_DEFAULT = ("READY", "PROPOSAL_HELD", "HELD_CURRENT", "NO_RECOVERY_REQUIRED")
EXCEPTIONAL_OK = frozenset({"NONE_IN_SCOPE", "RESOLVED"})
EXCEPTIONAL_ALL = frozenset({"NONE_IN_SCOPE", "RESOLVED", "UNRESOLVED", "UNKNOWN", "CONFLICTING"})


class ModelConstructionError(Exception):
    """Structurally impossible model input (naive/non-UTC datetime, malformed ordering). Raised at construction boundary
    ONLY — never used for governed refusals (those return an EligibilityDecision)."""


def _require_utc(value: datetime.datetime, name: str) -> datetime.datetime:
    if not isinstance(value, datetime.datetime):
        raise ModelConstructionError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ModelConstructionError(f"{name} must be timezone-aware")
    if value.utcoffset() != datetime.timedelta(0):
        raise ModelConstructionError(f"{name} must be UTC (offset 0)")
    return value


def _opt_utc(value: Optional[datetime.datetime], name: str) -> Optional[datetime.datetime]:
    return None if value is None else _require_utc(value, name)


# --------------------------------------------------------------------------- immutable models
@dataclass(frozen=True)
class GapInterval:
    timeframe: str
    start_utc: datetime.datetime
    end_utc: datetime.datetime

    def __post_init__(self):
        _require_utc(self.start_utc, "GapInterval.start_utc")
        _require_utc(self.end_utc, "GapInterval.end_utc")
        if self.timeframe not in TIMEFRAME_PERIOD_SECONDS:
            raise ModelConstructionError(f"unknown timeframe {self.timeframe!r}")


@dataclass(frozen=True)
class PublicationConfig:
    supported_contract_versions: Tuple[str, ...] = (CONTRACT_VERSION,)
    supported_planner_versions: Tuple[str, ...] = ("v1",)
    supported_policy_versions: Tuple[str, ...] = ("1",)
    supported_gaps_versions: Tuple[str, ...] = ("v1",)
    supported_coverage_versions: Tuple[str, ...] = ("v1",)
    canonical_instrument: str = CANONICAL_INSTRUMENT
    permitted_planner_statuses: Tuple[str, ...] = PERMITTED_PLANNER_STATUSES_DEFAULT
    proposal_ttl_seconds: int = 180
    revalidation_max_age_seconds: int = 120
    max_gaps_age_seconds: int = 120
    max_coverage_age_seconds: int = 900
    max_source_future_skew_seconds: int = 2
    max_payload_bytes: int = 32768
    max_published_segments: int = 200
    max_intervals: int = 4096
    retention_days: Mapping[str, int] = field(default_factory=lambda: dict(RETENTION_DAYS_DEFAULT))
    required_safety_flags: Mapping[str, bool] = field(default_factory=lambda: {
        "execution_authorised": False, "execution_started": False, "publication_only": True,
        "executor_bound": False, "consumer_live": False, "backfill_executed": False, "repair_executed": False})

    def __post_init__(self):
        for n in ("proposal_ttl_seconds", "revalidation_max_age_seconds", "max_gaps_age_seconds",
                  "max_coverage_age_seconds", "max_payload_bytes", "max_published_segments", "max_intervals"):
            v = getattr(self, n)
            if not isinstance(v, int) or isinstance(v, bool) or v <= 0:
                raise ModelConstructionError(f"config.{n} must be a positive int")


@dataclass(frozen=True)
class CandidateProposal:
    proposal_id: str
    proposal_generation: int
    instrument: str
    planner_status: str
    planner_version: str
    policy_version: str
    policy_digest: str
    gaps_contract_version: str
    gaps_semantic_digest: str
    coverage_contract_version: str
    coverage_semantic_digest: str
    closure_model_version: str
    closure_digest: str
    snapshot_consistency_status: str
    generated_at_utc: datetime.datetime
    source_as_of_utc: datetime.datetime
    expires_at_utc: Optional[datetime.datetime] = None
    segment_count: int = 0
    deferred_count: int = 0
    unclassified_count: int = 0
    intentionally_unavailable_count: int = 0
    estimated_request_units: int = 0
    payload_bytes: int = 0
    # safety flags (must be safe values)
    execution_authorised: bool = False
    execution_started: bool = False
    publication_only: bool = True
    executor_bound: bool = False
    consumer_live: bool = False
    backfill_executed: bool = False
    repair_executed: bool = False
    # currency
    is_current_holder: bool = True
    superseded: bool = False
    revoked: bool = False
    # optional raw scoped gap intervals for the invariant check (else caller supplies gap_invariant_ok in context)
    scoped_intervals: Tuple[GapInterval, ...] = ()

    def __post_init__(self):
        _require_utc(self.generated_at_utc, "candidate.generated_at_utc")
        _require_utc(self.source_as_of_utc, "candidate.source_as_of_utc")
        _opt_utc(self.expires_at_utc, "candidate.expires_at_utc")
        if not isinstance(self.proposal_generation, int) or isinstance(self.proposal_generation, bool) or self.proposal_generation < 1:
            raise ModelConstructionError("candidate.proposal_generation must be a positive int")
        if self.expires_at_utc is not None and self.expires_at_utc <= self.generated_at_utc:
            raise ModelConstructionError("candidate.expires_at_utc must be after generated_at_utc")
        if len(self.scoped_intervals) > 4096:
            raise ModelConstructionError("candidate.scoped_intervals exceeds max")


@dataclass(frozen=True)
class ValidationContext:
    publisher_enabled: bool
    publisher_authorised: bool
    gate_malformed: bool = False
    # holder / currency
    current_holder_proposal_id: Optional[str] = None
    current_generation: int = 0
    latest_published_generation: int = 0
    newer_proposal_exists: bool = False
    # policy
    policy_present: bool = True
    policy_schema_valid: bool = True
    current_policy_version: Optional[str] = None
    current_policy_digest: Optional[str] = None
    policy_valid_at_now: bool = True
    # gaps
    gaps_present: bool = True
    current_gaps_version: Optional[str] = None
    gaps_generated_at_utc: Optional[datetime.datetime] = None
    current_gaps_digest: Optional[str] = None
    # coverage
    coverage_present: bool = True
    current_coverage_version: Optional[str] = None
    coverage_snapshot_at_utc: Optional[datetime.datetime] = None
    current_coverage_digest: Optional[str] = None
    # closure
    closure_complete: bool = True
    current_closure_model_version: Optional[str] = None
    current_closure_digest: Optional[str] = None
    exceptional_closure_status: str = "NONE_IN_SCOPE"
    # integrity
    snapshot_consistent: bool = True
    within_retention: bool = True
    gap_invariant_ok: Optional[bool] = None          # used only if candidate.scoped_intervals is empty
    schema_valid: bool = True
    # generation / fencing / atomic preconditions (pure facts supplied by caller)
    duplicate_writer_detected: bool = False
    writer_fence_valid: bool = True
    redis_available: bool = True
    atomic_precondition_ok: bool = True
    # last successful validation of this holder (for HELD staleness); None for a fresh READY
    last_validated_at_utc: Optional[datetime.datetime] = None

    def __post_init__(self):
        for n in ("gaps_generated_at_utc", "coverage_snapshot_at_utc", "last_validated_at_utc"):
            _opt_utc(getattr(self, n), f"context.{n}")
        if self.exceptional_closure_status not in EXCEPTIONAL_ALL:
            raise ModelConstructionError(f"unknown exceptional_closure_status {self.exceptional_closure_status!r}")


@dataclass(frozen=True)
class EligibilityDecision:
    eligible: bool
    decision_code: str                                # REC_* recommendation
    refusal_codes: Tuple[str, ...]
    primary_refusal_code: Optional[str]
    evaluated_at_utc: datetime.datetime
    proposal_id: str
    proposal_generation: int
    contract_version: str
    decision_version: str
    validation_summary: Mapping[str, object]
    observed_digests: Mapping[str, Optional[str]]
    expires_at_utc: Optional[datetime.datetime]
    neutralise_prior_current: bool
    neutralisation_reason: Optional[str]
    retryable: bool
    alertable: bool
    execution_authority: bool = False                 # always False; the validator confers none


# --------------------------------------------------------------------------- the pure validator
def validate_publication_eligibility(candidate: CandidateProposal, context: ValidationContext,
                                     config: PublicationConfig, now_utc: datetime.datetime) -> EligibilityDecision:
    """Deterministic, side-effect-free. Returns an immutable EligibilityDecision. Fail-closed AND-predicate over G/H/P/S/I/C.
    Collects EVERY failing governed condition, orders them by CODE_ORDER, and reports the lowest-rank as primary. NEVER
    mutates inputs, NEVER performs I/O, NEVER confers execution authority."""
    _require_utc(now_utc, "now_utc")
    fails = set()

    def fail(code):
        fails.add(code)

    # ---- 1. contract + envelope structure ----
    if CONTRACT_VERSION not in config.supported_contract_versions:
        fail(PUB_UNSUPPORTED_CONTRACT_VERSION)
    if candidate.gaps_contract_version not in config.supported_gaps_versions:
        fail(PUB_UNSUPPORTED_CONTRACT_VERSION)
    if candidate.coverage_contract_version not in config.supported_coverage_versions:
        fail(PUB_UNSUPPORTED_CONTRACT_VERSION)
    if not context.schema_valid:
        fail(PUB_SCHEMA_VALIDATION_FAILED)
    # safety flags are schema-const-locked in PR#97: any unsafe value == a schema violation
    for name, want in config.required_safety_flags.items():
        if getattr(candidate, name) is not want:
            fail(PUB_SCHEMA_VALIDATION_FAILED)
            break

    # ---- 2. publication gates (fail-closed; separate from planner gates) ----
    if context.gate_malformed:
        fail(PUB_GATE_MISMATCH)
    elif not context.publisher_enabled:
        fail(PUB_GATES_DISABLED)
    elif not context.publisher_authorised:
        fail(PUB_GATE_MISMATCH)

    # ---- 3. canonical identity ----
    if candidate.instrument != config.canonical_instrument or FORBIDDEN_ALIAS in str(candidate.instrument):
        fail(PUB_NON_CANONICAL_INSTRUMENT)

    # ---- 4. holder / proposal state ----
    if context.current_holder_proposal_id is None:
        fail(PUB_NO_CURRENT_PROPOSAL)
    elif not candidate.is_current_holder or candidate.proposal_id != context.current_holder_proposal_id:
        fail(PUB_PROPOSAL_STATUS_INELIGIBLE)
    if candidate.planner_status not in config.permitted_planner_statuses:
        fail(PUB_PROPOSAL_STATUS_INELIGIBLE)
    if candidate.planner_status in ("BLOCKED", "BLOCKED_UNCLASSIFIED_MARKET_STATE", "PLANNER_INVOCATION_FAILED", "FAILED"):
        fail(PUB_BLOCKED)
    if candidate.revoked:
        fail(PUB_REVOKED)
    if candidate.superseded or context.newer_proposal_exists:
        fail(PUB_SUPERSEDED)
    if candidate.expires_at_utc is not None and now_utc >= candidate.expires_at_utc:
        fail(PUB_PROPOSAL_EXPIRED)
    if context.last_validated_at_utc is not None and \
            (now_utc - context.last_validated_at_utc).total_seconds() > config.revalidation_max_age_seconds:
        fail(PUB_PROPOSAL_STALE)

    # ---- 5. policy ----
    if not context.policy_present:
        fail(PUB_POLICY_ABSENT)
    else:
        if not context.policy_schema_valid:
            fail(PUB_POLICY_INVALID)
        if not context.policy_valid_at_now:
            fail(PUB_POLICY_CHANGED)
        if context.current_policy_version != candidate.policy_version or context.current_policy_digest != candidate.policy_digest:
            fail(PUB_POLICY_CHANGED)
    if candidate.planner_version not in config.supported_planner_versions:
        fail(PUB_UNSUPPORTED_PLANNER_VERSION)
    if context.current_policy_version is not None and candidate.policy_version not in config.supported_policy_versions:
        fail(PUB_POLICY_CHANGED)

    # ---- 6. source presence + freshness (independent of semantic sameness) ----
    if not context.gaps_present or context.gaps_generated_at_utc is None:
        fail(PUB_GAPS_MISSING)
    elif (now_utc - context.gaps_generated_at_utc).total_seconds() > config.max_gaps_age_seconds:
        fail(PUB_GAPS_STALE)
    if not context.coverage_present or context.coverage_snapshot_at_utc is None:
        fail(PUB_COVERAGE_MISSING)
    elif (now_utc - context.coverage_snapshot_at_utc).total_seconds() > config.max_coverage_age_seconds:
        fail(PUB_COVERAGE_STALE)

    # ---- 7. digest identity (only when the source is present) ----
    if context.gaps_present and context.current_gaps_digest is not None and \
            context.current_gaps_digest != candidate.gaps_semantic_digest:
        fail(PUB_GAPS_DIGEST_MISMATCH)
    if context.coverage_present and context.current_coverage_digest is not None and \
            context.current_coverage_digest != candidate.coverage_semantic_digest:
        fail(PUB_COVERAGE_DIGEST_MISMATCH)

    # ---- 8. closure + exceptional closure (fail-closed) ----
    if not context.closure_complete or context.current_closure_digest != candidate.closure_digest \
            or context.current_closure_model_version != candidate.closure_model_version:
        fail(PUB_CLOSURE_INCOMPLETE)
    if context.exceptional_closure_status not in EXCEPTIONAL_OK:
        fail(PUB_UNRESOLVED_EXCEPTIONAL_CLOSURE)

    # ---- 9. snapshot / integrity (temporal consistency) ----
    skew = config.max_source_future_skew_seconds
    if not context.snapshot_consistent or candidate.snapshot_consistency_status != "CONSISTENT" \
            or (candidate.generated_at_utc - now_utc).total_seconds() > skew \
            or (candidate.source_as_of_utc - now_utc).total_seconds() > skew \
            or (candidate.source_as_of_utc - candidate.generated_at_utc).total_seconds() > skew:
        fail(PUB_INCONSISTENT_SNAPSHOT)

    # ---- 10. retention ----
    if not context.within_retention:
        fail(PUB_OUT_OF_RETENTION)

    # ---- 11. payload + envelope content ----
    if candidate.unclassified_count > 0:
        fail(PUB_UNCLASSIFIED_PRESENT)
    if not _gap_invariant_ok(candidate, context):
        fail(PUB_GAP_INVARIANT_VIOLATION)
    if candidate.payload_bytes > config.max_payload_bytes or candidate.segment_count > config.max_published_segments:
        fail(PUB_PAYLOAD_TOO_LARGE)

    # ---- 12. generation / fencing / atomic preconditions ----
    if context.duplicate_writer_detected:
        fail(PUB_DUPLICATE_PUBLISHER)
    if not context.writer_fence_valid or candidate.proposal_generation <= context.current_generation \
            or candidate.proposal_generation <= context.latest_published_generation:
        fail(PUB_STALE_WRITER)
    if not context.redis_available:
        fail(PUB_REDIS_UNAVAILABLE)
    if not context.atomic_precondition_ok:
        fail(PUB_ATOMIC_PUBLICATION_FAILED)

    return _decide(candidate, context, config, now_utc, fails)


def _gap_invariant_ok(candidate: CandidateProposal, context: ValidationContext) -> bool:
    """§16/§21: assert end == start + timeframe period for every supplied scoped gap interval. If no raw intervals are
    supplied, fall back to the caller-supplied invariant status (which must be explicitly True — None fails closed)."""
    if candidate.scoped_intervals:
        for iv in candidate.scoped_intervals:
            period = TIMEFRAME_PERIOD_SECONDS[iv.timeframe]
            if (iv.end_utc - iv.start_utc).total_seconds() != period:
                return False
        return True
    return context.gap_invariant_ok is True          # None or False -> fail closed


def _decide(candidate, context, config, now_utc, fails) -> EligibilityDecision:
    ordered = tuple(sorted(fails, key=lambda c: _ORDER_INDEX[c]))
    eligible = not ordered
    primary = ordered[0] if ordered else None
    was_current = candidate.is_current_holder and context.current_holder_proposal_id == candidate.proposal_id

    if eligible:
        decision_code = REC_ELIGIBLE
        expires = now_utc + datetime.timedelta(seconds=config.proposal_ttl_seconds)
        neutralise, reason = False, None
    else:
        need_neu = was_current and any(c in _NEUTRALISE_IF_CURRENT for c in ordered)
        if need_neu:
            decision_code, neutralise = REC_REFUSE_AND_NEUTRALISE, True
            reason = next(c for c in ordered if c in _NEUTRALISE_IF_CURRENT)
        else:
            decision_code, neutralise, reason = REC_REFUSE_NO_CURRENT_KEY, False, None
        expires = None

    retryable = bool(ordered) and all(c in _RETRYABLE for c in ordered)
    alertable = any(c in _ALERTABLE for c in ordered)
    summary = {
        "layers_failed": len(ordered), "planner_status": candidate.planner_status, "is_current_holder": candidate.is_current_holder,
        "segment_count": candidate.segment_count, "deferred_count": candidate.deferred_count,
        "unclassified_count": candidate.unclassified_count, "intentionally_unavailable_count": candidate.intentionally_unavailable_count,
        "estimated_request_units": candidate.estimated_request_units,
    }
    digests = {"policy": candidate.policy_digest, "gaps": candidate.gaps_semantic_digest,
               "coverage": candidate.coverage_semantic_digest, "closure": candidate.closure_digest}
    return EligibilityDecision(
        eligible=eligible, decision_code=decision_code, refusal_codes=ordered, primary_refusal_code=primary,
        evaluated_at_utc=now_utc, proposal_id=candidate.proposal_id, proposal_generation=candidate.proposal_generation,
        contract_version=CONTRACT_VERSION, decision_version=DECISION_VERSION, validation_summary=summary,
        observed_digests=digests, expires_at_utc=expires, neutralise_prior_current=neutralise,
        neutralisation_reason=reason, retryable=retryable, alertable=alertable, execution_authority=False)
