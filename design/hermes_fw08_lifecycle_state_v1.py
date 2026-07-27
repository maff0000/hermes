#!/usr/bin/env python3
"""HERMES FW-08 real-proof lifecycle state machine v1 (PURE, INERT).

WO-HELM-HERMES-FW08-REAL-PROOF-LIFECYCLE-CANONICAL-SOURCE-FREEZE-AND-CANDIDATE-IDENTITY-CONTRACT-
IMPLEMENTATION-0001 (§8). Owner: HERMES (Helm). Created (UTC): 2026-07-27. Contract version: 1.

WHAT THIS IS. The §8 lifecycle state contract for the F2-R3 real-proof execution lifecycle. In THIS WO the
ONLY permitted transition is the CONTRACT-readiness transition

    LIFECYCLE_DESIGNED -> SOURCE_FREEZE_CONTRACT_READY

and it is permitted ONLY when the freeze request AND candidate contract are valid AND execution_authorised
is False. This is NOT a real freeze — no source is frozen, no candidate is allocated. Any transition to a
REAL state (SOURCE_FROZEN, CANDIDATE_ALLOCATED, or beyond) ALWAYS fails closed with
(LS-REAL-STATE-NOT-AUTHORISED, EXECUTION_NOT_AUTHORISED) because execution is not authorised in this WO.

NO caller Boolean may force an advance. There is DELIBERATELY no `force` / `advance` parameter. Independence
of the transition is DERIVED from validity + the execution flag — a caller cannot assert progress. A request
carrying execution_authorised=True is rejected outright (LS-EXECUTION-AUTHORISED-FORBIDDEN).

A REAL state transition is IMPOSSIBLE in this WO: real freeze and real allocation are unavailable
(design.hermes_fw08_source_freeze_v1.production_real_freeze_available() is False;
design.hermes_fw08_candidate_identity_v1.allocate_candidate_identity(REAL_CANDIDATE) always fails closed).

PURE: no I/O, no subprocess, no network, no secrets, stdlib only, deterministic. NOT imported by runtime.
No cycle: imports nothing from any FW-08 module.
"""
from __future__ import annotations

from typing import Optional, Tuple

CONTRACT_VERSION = "1"

# §8 the declared lifecycle states. States after SOURCE_FREEZE_CONTRACT_READY are DECLARED for the design but
# are UNREACHABLE in this WO (real execution is unauthorised).
LIFECYCLE_STATES = (
    "LIFECYCLE_DESIGNED",
    "SOURCE_FREEZE_CONTRACT_READY",
    "SOURCE_FROZEN",
    "CANDIDATE_ALLOCATED",
    "AUTHORITY_READY",
    "PRODUCERS_REGISTERED",
    "EVIDENCE_STORE_READY",
    "IMAGE_BUILT",
    "OCI_INSPECTED",
    "PROOF_ASSEMBLED",
    "AUDIT_GREEN",
    "PUBLICATION_AUTHORISED",
    "DEPLOYMENT_AUTHORISED",
    "RUN_CLOSED",
)

# The single CONTRACT-readiness transition permitted in this WO.
_READINESS_FROM = "LIFECYCLE_DESIGNED"
_READINESS_TO = "SOURCE_FREEZE_CONTRACT_READY"

# States that represent REAL execution progress — NEVER reachable in this WO.
_REAL_STATES = frozenset({
    "SOURCE_FROZEN", "CANDIDATE_ALLOCATED", "AUTHORITY_READY", "PRODUCERS_REGISTERED",
    "EVIDENCE_STORE_READY", "IMAGE_BUILT", "OCI_INSPECTED", "PROOF_ASSEMBLED", "AUDIT_GREEN",
    "PUBLICATION_AUTHORISED", "DEPLOYMENT_AUTHORISED", "RUN_CLOSED",
})


def advance_lifecycle_state(
    *,
    current_state: str,
    target_state: str,
    source_freeze_request_valid: bool,
    candidate_contract_valid: bool,
    execution_authorised: bool,
) -> Tuple[Optional[str], Tuple[str, ...]]:
    """§8 attempt a lifecycle transition, returning (new_state, ()) on success or (None, sorted reasons) on a
    fail-closed rejection. There is NO `force` / `advance` boolean: progress is DERIVED, never asserted.

    Rules:
      * execution_authorised is not exactly False              -> LS-EXECUTION-AUTHORISED-FORBIDDEN
      * target is a REAL state (SOURCE_FROZEN/CANDIDATE_ALLOCATED/...) ->
            (None, ('EXECUTION_NOT_AUTHORISED', 'LS-REAL-STATE-NOT-AUTHORISED')) ALWAYS
      * ONLY LIFECYCLE_DESIGNED -> SOURCE_FREEZE_CONTRACT_READY is permitted, and ONLY when the freeze
        request AND candidate contract are valid                -> otherwise LS-INVALID-TRANSITION /
            LS-FREEZE-REQUEST-INVALID / LS-CANDIDATE-CONTRACT-INVALID
    """
    reasons = []

    # No caller may assert execution authority to force progress.
    if execution_authorised is not False:
        reasons.append("LS-EXECUTION-AUTHORISED-FORBIDDEN")

    # A REAL state target ALWAYS fails closed — real execution is unauthorised in this WO.
    if target_state in _REAL_STATES:
        return (None, ("EXECUTION_NOT_AUTHORISED", "LS-REAL-STATE-NOT-AUTHORISED"))

    # The ONLY permitted transition is the CONTRACT-readiness one.
    if not (current_state == _READINESS_FROM and target_state == _READINESS_TO):
        reasons.append("LS-INVALID-TRANSITION")
        return (None, tuple(sorted(set(reasons))))

    # Contract-readiness requires BOTH contracts valid.
    if not source_freeze_request_valid:
        reasons.append("LS-FREEZE-REQUEST-INVALID")
    if not candidate_contract_valid:
        reasons.append("LS-CANDIDATE-CONTRACT-INVALID")

    if reasons:
        return (None, tuple(sorted(set(reasons))))

    return (_READINESS_TO, tuple())


def real_state_transition_possible() -> bool:
    """A real state transition is IMPOSSIBLE in this WO (execution unauthorised; real freeze + real allocation
    unavailable). Unconditionally False — documents the fail-closed posture."""
    return False
