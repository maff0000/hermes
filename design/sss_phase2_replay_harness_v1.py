"""HERMES shared-stream recovery Phase-2 — OFFLINE REPLAY HARNESS (INERT / DESIGN-ONLY).

WO-HELM-HERMES-SHARED-STREAM-RECOVERY-PHASE2-SHADOW-ADAPTER-AND-EVIDENCE-CONTRACT-DESIGN-0001.
Authority: HELM (HERMES market-data lane). Created (UTC): 2026-07-17.

STATUS: INERT / NOT WIRED. Imported by NO runtime path. DESIGN artefact only.

A PURE, deterministic, OFFLINE replay: it reads a recorded EvidenceSnapshot (dict/JSON), maps it to the UNCHANGED
Phase-1 core input, calls the merged utils/hermes_shared_stream_recovery_v1.py decide(), classifies the resulting
shadow decision against a RECORDED current-authority observation, and folds everything into an immutable
ShadowDecisionRecord. It touches NO live surface, opens NO socket/DB/Redis, executes NO reconnect, reads NO
wall-clock beyond a supplied `decided_at_utc`. Same snapshot in -> byte-identical record out (determinism proof).

This is the mechanism that lets us replay the July-16 metals transition and PROVE the shadow would have said
SHADOW_DENIES_CURRENT_RECONNECT on the exact SPX500/WTICO stale, offline, before any Phase-3 cutover.
"""
from __future__ import annotations

from typing import Mapping, Optional

import utils.hermes_shared_stream_recovery_v1 as core
from design.sss_phase2_comparator_v1 import classify
from design.sss_phase2_evidence_snapshot_v1 import EvidenceSnapshot, snapshot_from_dict
from design.sss_phase2_shadow_record_v1 import (
    CurrentAuthorityObservation, ShadowDecisionRecord, build_record, current_authority_from_dict,
)


def replay_snapshot(
    snapshot: EvidenceSnapshot,
    current: CurrentAuthorityObservation,
    *,
    decided_at_utc: str,
    adapter_version: str = "sss-phase2-replay-v1",
    source_sha: str = "OFFLINE_REPLAY",
    runtime_identity: Optional[Mapping[str, object]] = None,
    granular: bool = False,
    simulate_adapter_error: bool = False,
) -> ShadowDecisionRecord:
    """Replay ONE snapshot through the pure core + comparator, producing an immutable record. PURE.

    Fail-closed handling mirrors the live adapter's contract:
      * simulate_adapter_error / a core exception -> ADAPTER_ERROR record, NO shadow decision, current unaffected;
      * snapshot.evidence_completeness == INCOMPLETE -> EVIDENCE_INCOMPLETE (shadow decision withheld);
      * contradictory_evidence present -> EVIDENCE_CONFLICT.
    """
    completeness = snapshot.evidence_completeness.value
    contradictory = len(snapshot.contradictory_evidence) > 0
    runtime_identity = dict(runtime_identity or {"mode": "offline_replay"})

    envelope: Optional[core.DecisionEnvelope]
    adapter_error = simulate_adapter_error
    if adapter_error:
        envelope = None
    else:
        try:
            decision_input = snapshot.to_recovery_input()
            envelope = core.decide(decision_input)
        except Exception:
            # The pure core raised at the construction boundary (structurally-impossible input). The adapter
            # treats this as ADAPTER_ERROR — it does NOT fabricate a decision and does NOT reconnect.
            envelope = None
            adapter_error = True

    comparison_class, divergence = classify(
        envelope, current,
        evidence_completeness=completeness,
        contradictory_evidence=contradictory,
        adapter_error=adapter_error,
        granular=granular,
    )

    if envelope is None:
        # No shadow decision — synthesise a sentinel envelope-view so the record is still offline-replayable and
        # UNMISTAKABLY not an authorisation (action NO_ACTION-equivalent, reconnect_authorised False).
        envelope = _sentinel_envelope(snapshot, comparison_class.value)

    return build_record(
        snapshot_id=snapshot.snapshot_id,
        envelope=envelope,
        current_authority=current,
        comparison_class=comparison_class.value,
        divergence_reasons=divergence,
        evidence_completeness=completeness,
        adapter_version=adapter_version,
        source_sha=source_sha,
        runtime_identity=runtime_identity,
        decided_at_utc=decided_at_utc,
    )


def replay_from_dicts(
    snapshot_payload: Mapping[str, object],
    current_payload: Mapping[str, object],
    *,
    decided_at_utc: str,
    **kwargs,
) -> ShadowDecisionRecord:
    """Convenience: decode a snapshot dict + current-authority dict (e.g. loaded from a fixture JSON) and replay."""
    snapshot = snapshot_from_dict(snapshot_payload)
    current = current_authority_from_dict(current_payload)
    return replay_snapshot(snapshot, current, decided_at_utc=decided_at_utc, **kwargs)


def _sentinel_envelope(snapshot: EvidenceSnapshot, cause: str) -> core.DecisionEnvelope:
    """A NON-AUTHORISING sentinel envelope for adapter-error / incomplete cases. reconnect_authorised is FALSE —
    it can never be consumed as an authorisation."""
    return core.DecisionEnvelope(
        contract_version=snapshot.contract_version,
        envelope_version=core.ENVELOPE_VERSION,
        decision_id=f"SENTINEL_{cause}",
        evaluated_at_utc=snapshot.evaluated_at_utc,
        provider=snapshot.provider,
        transport_state=core.TransportState.SILENT_UNCONFIRMED.value,
        socket_connected=False,
        heartbeat_state=core.HeartbeatState.UNKNOWN.value,
        heartbeat_age_s=None,
        shared_progress_state=core.HeartbeatState.UNKNOWN.value,
        shared_progress_age_s=None,
        expected_flow_instruments=(),
        stale_governed_instruments=(),
        stale_unvalidated_instruments=(),
        authority_status=core.AuthorityStatus.FAIL_CLOSED.value,
        action=core.Action.NO_ACTION.value,
        reason_codes=(core.ReasonCode.NO_TRANSPORT_TRUTH_FAILCLOSED.value,),
        reconnect_authorised=False,
        emergency_bypass_eligible=False,
        emergency_bypass_reason=None,
        adapter_bounding_required=False,
        limiter_state={},
        evidence_summary={"sentinel": True, "cause": cause, "snapshot_id": snapshot.snapshot_id},
        config_version=snapshot.config_version,
    )
