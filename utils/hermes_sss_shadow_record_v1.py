"""HERMES shared-stream recovery Phase-2 — SHADOW DECISION RECORD (PRODUCTION-OWNED, INERT).

WO-HELM-HERMES-SHARED-STREAM-RECOVERY-PHASE2-SHADOW-ADAPTER-IMPLEMENTATION-0001.
Authority: HELM (HERMES market-data lane). Created (UTC): 2026-07-17. Owner: HERMES (Helm).
Contract: docs/design/shared_stream_recovery/architecture_phase2_v1.md §10/§14 (binding). Contract version "1".
Promotes the audited inert shadow-record prototype under design/ into a production-owned module.

STATUS: INERT / NOT WIRED. Imported by NO live runtime path; only tests + sibling utils/hermes_sss_* modules.

A ShadowDecisionRecord is the immutable, versioned, offline-replayable record of ONE shadow evaluation:
  (evidence snapshot) -> mapper -> core.decide() -> DecisionEnvelope -> comparison-vs-current-authority.

It is MECHANICALLY impossible to mistake for a reconnect command:
  * shadow_only is ALWAYS true and consumer_live is ALWAYS false (constants, validated at construction);
  * it carries NO executor handle, NO reconnect callback, NO recovery-request flag;
  * it is a pure data record — it has no method that performs a side effect.

The record embeds the FULL DecisionEnvelope so the shadow decision can be reproduced and audited offline from the
snapshot alone. No secrets, UTC only. Standard-library only + the Phase-1 core.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Mapping, Optional, Tuple

import utils.hermes_shared_stream_recovery_v1 as core

RECORD_VERSION = "1"
SUPPORTED_RECORD_VERSIONS = frozenset({"1"})


class ShadowRecordError(Exception):
    """Structurally-impossible record (bad version, shadow_only!=True). Construction-boundary only."""


@dataclass(frozen=True)
class CurrentAuthorityObservation:
    """PASSIVE, read-only observation of what the EXISTING sustained-red authority did (architecture_phase2 §10).

    Captured by observing existing events/state (watchdog `_recovery_request_pending` / `_recovery_request_reason`
    / `_recovery_attempts_window`, the main.py reconnect outcome) — NEVER by calling or wrapping the authority in a
    way that changes its ordering or timing, and NEVER by consuming the one-shot recovery request. `evaluated=False`
    means the current authority did not run a recovery evaluation for this cycle, yielding
    CURRENT_AUTHORITY_NOT_EVALUATED.
    """
    evaluated: bool
    triggering_instrument: Optional[str]
    recovery_requested: bool
    reconnect_executed: bool
    reconnect_suppressed: bool = False       # limiter/cooldown suppressed the request
    reason: Optional[str] = None
    observed_at_utc: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "evaluated": self.evaluated, "triggering_instrument": self.triggering_instrument,
            "recovery_requested": self.recovery_requested, "reconnect_executed": self.reconnect_executed,
            "reconnect_suppressed": self.reconnect_suppressed, "reason": self.reason,
            "observed_at_utc": self.observed_at_utc,
        }

    @property
    def reconnected(self) -> bool:
        """The current authority caused (or requested) a shared reconnect this cycle."""
        return bool(self.recovery_requested or self.reconnect_executed)


def current_authority_from_dict(p: Mapping[str, object]) -> CurrentAuthorityObservation:
    return CurrentAuthorityObservation(
        evaluated=bool(p["evaluated"]),
        triggering_instrument=p.get("triggering_instrument"),
        recovery_requested=bool(p.get("recovery_requested", False)),
        reconnect_executed=bool(p.get("reconnect_executed", False)),
        reconnect_suppressed=bool(p.get("reconnect_suppressed", False)),
        reason=p.get("reason"),
        observed_at_utc=p.get("observed_at_utc"),
    )


@dataclass(frozen=True)
class ShadowDecisionRecord:
    """Immutable, versioned shadow-decision record. Offline-replayable; impossible to mistake for authority."""
    # ---- identity / lineage ----
    snapshot_id: str
    decided_at_utc: str
    adapter_version: str
    source_sha: str
    runtime_identity: Mapping[str, object]          # sanitised — never secrets
    config_version: Optional[str]
    contract_version: str
    # ---- shadow decision (from the Phase-1 DecisionEnvelope) ----
    shadow_action: str
    shadow_authority_status: str
    shadow_transport_state: str
    shadow_reason_codes: Tuple[str, ...]
    shadow_reconnect_authorised: bool
    shadow_emergency_bypass_eligible: bool
    shadow_decision_id: str
    shadow_limiter_state: Mapping[str, object]
    # ---- current-authority observation ----
    current_authority: CurrentAuthorityObservation
    # ---- comparison ----
    comparison_class: str
    divergence_reasons: Tuple[str, ...]
    evidence_completeness: str
    # ---- full envelope for offline reproduction ----
    envelope: Mapping[str, object]
    # ---- provenance / bounding context ----
    connection_generation: Optional[int] = None
    # ---- MECHANICAL safety constants (validated) ----
    shadow_only: bool = True
    consumer_live: bool = False
    record_version: str = RECORD_VERSION

    def __post_init__(self) -> None:
        if self.record_version not in SUPPORTED_RECORD_VERSIONS:
            raise ShadowRecordError(f"unsupported record_version {self.record_version!r}")
        if self.shadow_only is not True:
            raise ShadowRecordError("shadow_only MUST be True — a shadow record can never be a live command")
        if self.consumer_live is not False:
            raise ShadowRecordError("consumer_live MUST be False in Phase 2")

    def canonical_dict(self) -> dict:
        return {
            "record_version": self.record_version,
            "contract_version": self.contract_version,
            "snapshot_id": self.snapshot_id,
            "decided_at_utc": self.decided_at_utc,
            "adapter_version": self.adapter_version,
            "source_sha": self.source_sha,
            "runtime_identity": dict(self.runtime_identity),
            "config_version": self.config_version,
            "connection_generation": self.connection_generation,
            "shadow_action": self.shadow_action,
            "shadow_authority_status": self.shadow_authority_status,
            "shadow_transport_state": self.shadow_transport_state,
            "shadow_reason_codes": list(self.shadow_reason_codes),
            "shadow_reconnect_authorised": self.shadow_reconnect_authorised,
            "shadow_emergency_bypass_eligible": self.shadow_emergency_bypass_eligible,
            "shadow_decision_id": self.shadow_decision_id,
            "shadow_limiter_state": dict(self.shadow_limiter_state),
            "current_authority": self.current_authority.to_dict(),
            "comparison_class": self.comparison_class,
            "divergence_reasons": list(self.divergence_reasons),
            "evidence_completeness": self.evidence_completeness,
            "envelope": dict(self.envelope),
            "shadow_only": self.shadow_only,
            "consumer_live": self.consumer_live,
        }

    @property
    def record_id(self) -> str:
        """Deterministic sha256[:16] over the canonical record. No randomness, no wall-clock."""
        return hashlib.sha256(
            json.dumps(self.canonical_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:16]

    def to_dict(self) -> dict:
        d = self.canonical_dict()
        d["record_id"] = self.record_id
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))


def build_record(
    *,
    snapshot_id: str,
    envelope: core.DecisionEnvelope,
    current_authority: CurrentAuthorityObservation,
    comparison_class: str,
    divergence_reasons: Tuple[str, ...],
    evidence_completeness: str,
    adapter_version: str,
    source_sha: str,
    runtime_identity: Mapping[str, object],
    decided_at_utc: str,
    connection_generation: Optional[int] = None,
) -> ShadowDecisionRecord:
    """PURE constructor: fold a DecisionEnvelope + current-authority observation + comparison into an immutable
    record. No side effect. `decided_at_utc` is supplied (no wall-clock read)."""
    return ShadowDecisionRecord(
        snapshot_id=snapshot_id,
        decided_at_utc=decided_at_utc,
        adapter_version=adapter_version,
        source_sha=source_sha,
        runtime_identity=dict(runtime_identity),
        config_version=envelope.config_version,
        contract_version=envelope.contract_version,
        connection_generation=connection_generation,
        shadow_action=envelope.action,
        shadow_authority_status=envelope.authority_status,
        shadow_transport_state=envelope.transport_state,
        shadow_reason_codes=tuple(envelope.reason_codes),
        shadow_reconnect_authorised=envelope.reconnect_authorised,
        shadow_emergency_bypass_eligible=envelope.emergency_bypass_eligible,
        shadow_decision_id=envelope.decision_id,
        shadow_limiter_state=dict(envelope.limiter_state),
        current_authority=current_authority,
        comparison_class=comparison_class,
        divergence_reasons=tuple(divergence_reasons),
        evidence_completeness=evidence_completeness,
        envelope=envelope.to_dict(),
    )
