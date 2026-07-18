"""HERMES shared-stream recovery Phase-2 — COMPARISON TAXONOMY + classifier (PRODUCTION-OWNED, INERT).

WO-HELM-HERMES-SHARED-STREAM-RECOVERY-PHASE2-SHADOW-ADAPTER-IMPLEMENTATION-0001.
Authority: HELM (HERMES market-data lane). Created (UTC): 2026-07-17. Owner: HERMES (Helm).
Contract: docs/design/shared_stream_recovery/architecture_phase2_v1.md §15 (binding). Contract version "1".
Promotes the audited inert comparator prototype under design/ into a production-owned module.

STATUS: INERT / NOT WIRED. Imported by NO live runtime path; only tests + sibling utils/hermes_sss_* modules.

The comparator maps (shadow DecisionEnvelope, current-authority observation, evidence completeness/conflict) to
exactly ONE ComparisonClass + zero-or-more divergence reasons. PURE + total + deterministic. It EXECUTES nothing.

Precedence (fail-closed first): ADAPTER_ERROR > EVIDENCE_INCOMPLETE > EVIDENCE_CONFLICT >
CURRENT_AUTHORITY_NOT_EVALUATED > agreement/divergence logic > SHADOW_INDETERMINATE. Standard-library only + the
Phase-1 core.
"""
from __future__ import annotations

import enum
from typing import List, Optional, Tuple

import utils.hermes_shared_stream_recovery_v1 as core
from utils.hermes_sss_shadow_record_v1 import CurrentAuthorityObservation


class ComparisonClass(str, enum.Enum):
    """The 10-class shadow-vs-current comparison taxonomy (architecture_phase2_v1 §15)."""
    AGREE_NO_ACTION = "AGREE_NO_ACTION"
    AGREE_RECONNECT = "AGREE_RECONNECT"
    SHADOW_DENIES_CURRENT_RECONNECT = "SHADOW_DENIES_CURRENT_RECONNECT"
    SHADOW_AUTHORIZES_CURRENT_NO_RECONNECT = "SHADOW_AUTHORIZES_CURRENT_NO_RECONNECT"
    SHADOW_PROPOSAL_CURRENT_RECONNECT = "SHADOW_PROPOSAL_CURRENT_RECONNECT"
    CURRENT_AUTHORITY_NOT_EVALUATED = "CURRENT_AUTHORITY_NOT_EVALUATED"
    SHADOW_INDETERMINATE = "SHADOW_INDETERMINATE"
    EVIDENCE_INCOMPLETE = "EVIDENCE_INCOMPLETE"
    EVIDENCE_CONFLICT = "EVIDENCE_CONFLICT"
    ADAPTER_ERROR = "ADAPTER_ERROR"


ALL_COMPARISON_CLASSES: frozenset = frozenset(c.value for c in ComparisonClass)

# Severity + phase-3 contribution metadata (architecture_phase2_v1 §15). "investigation_required" gates alerts.
COMPARISON_META = {
    ComparisonClass.AGREE_NO_ACTION: {
        "severity": "info", "investigation_required": False,
        "meaning": "Neither current authority nor shadow would reconnect.",
        "phase3": "Baseline agreement — builds confidence for cutover."},
    ComparisonClass.AGREE_RECONNECT: {
        "severity": "info", "investigation_required": False,
        "meaning": "Both current authority and shadow authorise a reconnect (genuine fault).",
        "phase3": "Confirms shadow preserves genuine-fault reconnects."},
    ComparisonClass.SHADOW_DENIES_CURRENT_RECONNECT: {
        "severity": "high", "investigation_required": True,
        "meaning": "Current authority reconnected; shadow would NOT (no transport authority). The July-16 defect class.",
        "phase3": "Primary target divergence — proves the correction prevents spurious reconnects."},
    ComparisonClass.SHADOW_AUTHORIZES_CURRENT_NO_RECONNECT: {
        "severity": "high", "investigation_required": True,
        "meaning": "Shadow authorises a reconnect the current authority did NOT trigger (a genuine fault current missed).",
        "phase3": "Safety divergence — must be understood before cutover (shadow may be catching real faults)."},
    ComparisonClass.SHADOW_PROPOSAL_CURRENT_RECONNECT: {
        "severity": "high", "investigation_required": True,
        "meaning": "Granular label of SHADOW_DENIES_CURRENT_RECONNECT where shadow emitted RECOVERY_PROPOSAL_ONLY.",
        "phase3": "Refines the denial divergence with the proposal-only detail."},
    ComparisonClass.CURRENT_AUTHORITY_NOT_EVALUATED: {
        "severity": "info", "investigation_required": False,
        "meaning": "Current authority did not run a recovery evaluation this cycle; nothing to compare.",
        "phase3": "Excluded from divergence accounting."},
    ComparisonClass.SHADOW_INDETERMINATE: {
        "severity": "warn", "investigation_required": True,
        "meaning": "Shadow fail-closed to OPERATOR_ESCALATION on ambiguous transport truth; current did not reconnect.",
        "phase3": "Indicates horizon/quorum tuning needed; not a green signal."},
    ComparisonClass.EVIDENCE_INCOMPLETE: {
        "severity": "warn", "investigation_required": True,
        "meaning": "An authority-bearing evidence field was UNAVAILABLE; shadow could not decide truthfully.",
        "phase3": "Coverage gap — excluded from the evidence-complete percentage."},
    ComparisonClass.EVIDENCE_CONFLICT: {
        "severity": "warn", "investigation_required": True,
        "meaning": "Contradictory transport evidence; shadow fails closed. Comparison withheld.",
        "phase3": "Signals adapter/source disagreement to resolve before cutover."},
    ComparisonClass.ADAPTER_ERROR: {
        "severity": "error", "investigation_required": True,
        "meaning": "The shadow adapter itself errored; NO shadow decision produced. Current authority unaffected.",
        "phase3": "Reliability gap — excluded; must be near-zero before Phase 3."},
}

# Shadow actions that WITHHOLD reconnect authority (i.e. shadow would NOT reconnect).
_SHADOW_NO_RECONNECT = frozenset({
    core.Action.NO_ACTION.value, core.Action.INCIDENT_ONLY.value,
    core.Action.RECOVERY_PROPOSAL_ONLY.value, core.Action.RECONNECT_RATE_LIMITED.value,
    core.Action.OPERATOR_ESCALATION.value,
})


def classify(
    envelope: Optional[core.DecisionEnvelope],
    current: CurrentAuthorityObservation,
    *,
    evidence_completeness: str = "COMPLETE",
    contradictory_evidence: bool = False,
    adapter_error: bool = False,
    granular: bool = False,
) -> Tuple[ComparisonClass, Tuple[str, ...]]:
    """Return (ComparisonClass, divergence_reasons). Total + deterministic.

    `granular=True` returns the finer SHADOW_PROPOSAL_CURRENT_RECONNECT label instead of the coarse
    SHADOW_DENIES_CURRENT_RECONNECT when the shadow action is RECOVERY_PROPOSAL_ONLY. The DEFAULT (coarse) is the
    binding class for the July-16 acceptance (SHADOW_DENIES_CURRENT_RECONNECT) — the proposal-only detail is always
    recorded in divergence_reasons regardless.
    """
    reasons: List[str] = []

    # ---- fail-closed precedence ----
    if adapter_error or envelope is None:
        return ComparisonClass.ADAPTER_ERROR, ("adapter_error",)
    if evidence_completeness == "INCOMPLETE":
        return ComparisonClass.EVIDENCE_INCOMPLETE, ("authority_bearing_field_unavailable",)
    if contradictory_evidence:
        return ComparisonClass.EVIDENCE_CONFLICT, ("contradictory_transport_evidence",)
    if not current.evaluated:
        return ComparisonClass.CURRENT_AUTHORITY_NOT_EVALUATED, ()

    shadow_reconnect = envelope.action == core.Action.RECONNECT_AUTHORISED.value
    shadow_withholds = envelope.action in _SHADOW_NO_RECONNECT
    current_reconnect = current.reconnected

    reasons.append(f"shadow_action={envelope.action}")
    reasons.append(f"shadow_transport_state={envelope.transport_state}")
    if current.triggering_instrument:
        reasons.append(f"current_trigger={current.triggering_instrument}")

    # ---- agreement / divergence ----
    if current_reconnect and shadow_reconnect:
        return ComparisonClass.AGREE_RECONNECT, tuple(reasons)

    if current_reconnect and shadow_withholds:
        reasons.append("current_reconnected_shadow_withheld_authority")
        if envelope.action == core.Action.RECOVERY_PROPOSAL_ONLY.value:
            reasons.append("shadow_emitted_recovery_proposal_only")
            if granular:
                return ComparisonClass.SHADOW_PROPOSAL_CURRENT_RECONNECT, tuple(reasons)
        return ComparisonClass.SHADOW_DENIES_CURRENT_RECONNECT, tuple(reasons)

    if not current_reconnect and shadow_reconnect:
        reasons.append("shadow_authorised_reconnect_current_did_not")
        return ComparisonClass.SHADOW_AUTHORIZES_CURRENT_NO_RECONNECT, tuple(reasons)

    # neither reconnected
    if envelope.action == core.Action.OPERATOR_ESCALATION.value:
        reasons.append("shadow_fail_closed_escalation")
        return ComparisonClass.SHADOW_INDETERMINATE, tuple(reasons)
    return ComparisonClass.AGREE_NO_ACTION, tuple(reasons)
