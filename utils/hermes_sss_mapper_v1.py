"""HERMES shared-stream recovery Phase-2 — DETERMINISTIC SNAPSHOT -> CORE-INPUT MAPPER (PRODUCTION-OWNED, INERT).

WO-HELM-HERMES-SHARED-STREAM-RECOVERY-PHASE2-SHADOW-ADAPTER-IMPLEMENTATION-0001.
Authority: HELM (HERMES market-data lane). Created (UTC): 2026-07-17. Owner: HERMES (Helm).
Contract: docs/design/shared_stream_recovery/architecture_phase2_v1.md §5/§6/§8 (binding). Contract version "1".

STATUS: INERT / NOT WIRED. Imported by NO live runtime path; only tests + sibling utils/hermes_sss_* modules.

PURPOSE (architecture_phase2_v1 §12 part 3): a DETERMINISTIC, SIDE-EFFECT-FREE mapping from an immutable
EvidenceSnapshot to the UNCHANGED Phase-1 core input (RecoveryDecisionInput). It preserves availability and
provenance and enforces the load-bearing honesty invariants:

  * DOUBLE-COUNT GUARD (§8): the adapter bumps AdapterHealth.last_tick_at on EVERY provider message, so the
    heartbeat signal (#8/#9) and the shared-progress signal (#13/#14) are read from the SAME physical surface.
    They MUST NOT be handed to the core as two INDEPENDENT transport confirmations. When both fields cite the same
    provenance surface_id, this mapper COLLAPSES shared-progress to the mirror form (shared_progress_available=None)
    so the Phase-1 core mirrors the heartbeat and cannot count it a second time. An independent shared-progress
    signal (e.g. the asyncio.TimeoutError silent-stall seam) with a DISTINCT provenance surface_id is passed through
    unchanged.
  * SOCKET AMBIGUITY: socket CONNECTED is passed through verbatim; the core already treats it as leaning on the
    heartbeat (never affirmative). The mapper does NOT elevate CONNECTED to a health assertion.
  * PROVENANCE HONESTY: the mapper never rewrites an Observation — a JUSTIFIED_INFERENCE stays inferred; it can
    never emerge as DIRECTLY_OBSERVED. The mapper carries NO caller-controlled emergency/force input.
  * FAIL CLOSED: a structurally-malformed snapshot (rejected by the core's construction boundary) raises
    MapperError; the adapter routes that to ADAPTER_ERROR (never a fabricated decision, never a reconnect).

Standard-library only + the Phase-1 core + the evidence-snapshot model. Pure.
"""
from __future__ import annotations

import datetime
from dataclasses import replace
from typing import Optional

import utils.hermes_shared_stream_recovery_v1 as core
import utils.hermes_sss_evidence_snapshot_v1 as snap_mod
from utils.hermes_sss_evidence_snapshot_v1 import EvidenceSnapshot, Observation


class MapperError(Exception):
    """Raised when a snapshot cannot be mapped to a valid core input (malformed / contradictory)."""


def shares_transport_provenance(snapshot: EvidenceSnapshot) -> bool:
    """True when the heartbeat field and the shared-progress field read the SAME physical surface (identical
    provenance surface_id). In that case they are NOT independent corroboration and the mapper must collapse
    shared-progress to the mirror form so the Phase-1 core cannot double-count them (§8)."""
    hb = snapshot.provenance_for("heartbeat_available") or snapshot.provenance_for("heartbeat_age_s")
    sp = snapshot.provenance_for("shared_progress_available") or snapshot.provenance_for("shared_progress_age_s")
    if hb is None or sp is None:
        # Absent shared-progress provenance means it is NOT an independently-sourced signal -> treat as shared.
        return sp is None and hb is not None
    return hb.surface_id == sp.surface_id


def _guarded_snapshot(snapshot: EvidenceSnapshot) -> EvidenceSnapshot:
    """Return a snapshot in which a same-surface shared-progress signal is collapsed to the mirror form. Immutable
    copy — never mutates the caller's snapshot. When the shared-progress signal is independently sourced (distinct
    surface_id) the snapshot is returned unchanged."""
    if shares_transport_provenance(snapshot) and snapshot.shared_progress_available is not None:
        # Collapse to mirror: the core's effective_shared_progress_* then mirrors the heartbeat instead of counting
        # the same last_tick_at surface as a second, independent transport confirmation.
        return replace(snapshot, shared_progress_available=None, shared_progress_age_s=None)
    return snapshot


def _detect_hard_contradiction(snapshot: EvidenceSnapshot) -> Optional[str]:
    """Detect a structurally-impossible provenance/value pairing the core cannot see (it only takes primitives).
    Returns a description if contradictory, else None. This is a mapper fail-closed guard, distinct from the
    snapshot-level advisory `contradictory_evidence` list (which the comparator surfaces as EVIDENCE_CONFLICT).

    Rule: a field whose provenance says DIRECTLY_OBSERVED-and-AVAILABLE must actually carry a value; if the
    provenance asserts an observed heartbeat measurement while heartbeat_available is False / age is None, the
    snapshot is internally inconsistent and cannot be mapped truthfully."""
    hb = snapshot.provenance_for("heartbeat_age_s")
    if hb is not None and hb.observation == Observation.DIRECTLY_OBSERVED \
            and hb.availability in (snap_mod.AvailabilityClass.AVAILABLE_AUTHORITATIVE,
                                    snap_mod.AvailabilityClass.AVAILABLE_DERIVED):
        if not snapshot.heartbeat_available or snapshot.heartbeat_age_s is None:
            return ("heartbeat_age_s provenance claims a directly-observed available measurement but "
                    "heartbeat_available is False / heartbeat_age_s is None")
    return None


def map_snapshot_to_recovery_input(snapshot: EvidenceSnapshot) -> core.RecoveryDecisionInput:
    """Map an EvidenceSnapshot to the UNCHANGED Phase-1 RecoveryDecisionInput. PURE, deterministic, no side effect,
    no caller-controlled emergency/force. Raises MapperError on a malformed / internally-contradictory snapshot.

    The mapping NEVER invents evidence: no per-instrument heartbeat, no manufactured connection-generation, no
    freshness-derived shared authority. Unknown/unavailable stays unknown/unavailable. socket CONNECTED stays
    ambiguous (the core leans on the heartbeat, not the socket flag)."""
    if not isinstance(snapshot, EvidenceSnapshot):
        raise MapperError("map_snapshot_to_recovery_input requires an EvidenceSnapshot")

    contradiction = _detect_hard_contradiction(snapshot)
    if contradiction is not None:
        raise MapperError(f"snapshot internally contradictory: {contradiction}")

    guarded = _guarded_snapshot(snapshot)

    try:
        evaluated = datetime.datetime.fromisoformat(guarded.evaluated_at_utc)
    except ValueError as exc:
        raise MapperError(f"evaluated_at_utc is not an ISO-8601 datetime: {exc}") from exc

    try:
        return core.RecoveryDecisionInput(
            evaluated_at_utc=evaluated,
            provider=guarded.provider,
            contract_version=guarded.contract_version,
            config_version=guarded.config_version,
            socket_state=core.SocketState(guarded.socket_state),
            provider_disconnect_event=guarded.provider_disconnect_event,
            auth_failure=guarded.auth_failure,
            heartbeat_available=guarded.heartbeat_available,
            heartbeat_age_s=guarded.heartbeat_age_s,
            shared_stream_silent=guarded.shared_stream_silent,
            parser_fatal=guarded.parser_fatal,
            reconnect_in_progress=guarded.reconnect_in_progress,
            shared_progress_available=guarded.shared_progress_available,
            shared_progress_age_s=guarded.shared_progress_age_s,
            provider_maintenance_indication=guarded.provider_maintenance_indication,
            error_count_delta=guarded.error_count_delta,
            heartbeat_soft_horizon_s=guarded.heartbeat_soft_horizon_s,
            heartbeat_hard_horizon_s=guarded.heartbeat_hard_horizon_s,
            instruments=tuple(i.to_core() for i in guarded.instruments),
            limiter=core.LimiterState(
                attempts_in_window=guarded.limiter_attempts_in_window,
                max_per_window=guarded.limiter_max_per_window,
                window_seconds=guarded.limiter_window_seconds,
                available=guarded.limiter_available,
            ),
            evidence_summary={
                "snapshot_id": guarded.snapshot_id,
                "source_sha": guarded.source_sha,
                "adapter_version": guarded.adapter_version,
                "connection_generation": guarded.connection_generation,
                "evidence_completeness": guarded.evidence_completeness.value,
                "shared_progress_collapsed_to_mirror": guarded.shared_progress_available is None
                and snapshot.shared_progress_available is not None,
            },
        )
    except core.RecoveryContractError as exc:
        # The pure core rejected a structurally-impossible input. The adapter treats this as a fail-closed mapper
        # error — it does NOT fabricate a decision and NEVER reconnects.
        raise MapperError(f"core rejected mapped input: {exc}") from exc
