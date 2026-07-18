"""HERMES shared-stream recovery Phase-2 — SHADOW ADAPTER (orchestrator + refusing executor) (PRODUCTION-OWNED, INERT).

WO-HELM-HERMES-SHARED-STREAM-RECOVERY-PHASE2-SHADOW-ADAPTER-IMPLEMENTATION-0001.
Authority: HELM (HERMES market-data lane). Created (UTC): 2026-07-17. Owner: HERMES (Helm).
Contract: docs/design/shared_stream_recovery/architecture_phase2_v1.md §11-§16/§19-§21 (binding). Version "1".

STATUS: INERT / NOT WIRED / DISABLED-BY-DEFAULT / MECHANICALLY INCAPABLE OF RECONNECT. This module is imported by
NO live runtime path (main.py / utils/watchdog.py / adapters/oanda.py / adapters/base.py / provider client / any
runner / docker-compose / Dockerfile / cron / systemd). It is imported ONLY by tests. Proven by the static-guard
+ dependency-scan tests in tests/test_hermes_sss_shadow_adapter_v1.py.

WHAT IT DOES: given an already-built immutable EvidenceSnapshot and a passive CurrentAuthorityObservation, it maps
the snapshot to the UNCHANGED Phase-1 core input, calls core.decide(), classifies the shadow decision against the
current authority, folds the result into an immutable ShadowDecisionRecord, and (optionally) hands the record to a
read-only emitter. It RETURNS a distinct ShadowEvaluationResult and touches NO transport.

MECHANICAL SAFETY BOUNDARY (§16):
  * NO executor dependency: this module imports NO connect()/disconnect()/reconnect object and holds none. The only
    object standing where an executor would be is `RefusingShadowExecutor`, which RAISES `ShadowExecutionForbidden`.
  * NO recovery-request producer: it never sets a watchdog recovery flag and never consumes the one-shot request.
  * NO DI path for an executor: `run_shadow_evaluation` has NO executor/force/emergency parameter; a real executor
    cannot be substituted in.
  * distinct result type: the terminal artefact is a ShadowDecisionRecord / ShadowEvaluationResult, never a command.
  * disabled-by-default: when `config.enabled` is False the adapter does nothing and produces no record.
  * failure isolation: any exception in mapping/decide/classify is caught -> ADAPTER_ERROR record; the current
    authority is never affected and nothing is ever re-raised into a market-data or reconnect path.

Standard-library only + the Phase-1 core + sibling utils/hermes_sss_* modules. No wall-clock in the pure decision
path (decided_at_utc supplied); monotonic used ONLY for callback dedup pacing, never for evidence timestamps.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Mapping, Optional, Tuple

import utils.hermes_shared_stream_recovery_v1 as core
from utils.hermes_sss_comparator_v1 import ComparisonClass, classify
from utils.hermes_sss_config_v1 import ShadowAdapterConfig, disabled_config
from utils.hermes_sss_evidence_snapshot_v1 import EvidenceSnapshot
from utils.hermes_sss_mapper_v1 import MapperError, map_snapshot_to_recovery_input
from utils.hermes_sss_shadow_record_v1 import (
    CurrentAuthorityObservation, ShadowDecisionRecord, build_record,
)

ADAPTER_VERSION = "sss-phase2-shadow-adapter-v1"


class ShadowExecutionForbidden(Exception):
    """Raised by RefusingShadowExecutor if anything ever asks the shadow path to execute a reconnect. This is the
    MECHANICAL guarantee (§16) that a shadow record can NEVER become an action."""


class RefusingShadowExecutor:
    """The NO-OP shadow-executor boundary. It has NO connect()/disconnect()/reconnect()/recovery_request handle and
    REFUSES to act. Its mere presence documents that the shadow path terminates at a record, never an action; the
    impossibility is testable, not merely commented."""

    def __init__(self) -> None:
        self.invocations = 0

    def refuse(self, record: ShadowDecisionRecord) -> None:
        """MUST raise ShadowExecutionForbidden. Exists so the impossibility is testable."""
        self.invocations += 1
        raise ShadowExecutionForbidden(
            "Shadow path is read-only: a ShadowDecisionRecord can never be executed as a reconnect "
            f"(snapshot_id={record.snapshot_id}, action={record.shadow_action})."
        )

    # Deliberately NO connect / disconnect / reconnect / recovery_request methods exist on this class.


@dataclass(frozen=True)
class ShadowEvaluationResult:
    """Distinct shadow-evaluation result type. NOT a command. `record` is None only when the adapter is disabled or
    skipped; `reconnect_authorised_shadow` is the SHADOW opinion and is NEVER acted upon by anything here."""
    enabled: bool
    produced: bool
    comparison_class: Optional[str]
    record: Optional[ShadowDecisionRecord]
    reason: Optional[str] = None

    @property
    def reconnect_authorised_shadow(self) -> bool:
        return bool(self.record is not None and self.record.shadow_reconnect_authorised)


class BoundedCallbackDeduplicator:
    """Bounds provider-disconnect callback storms (§4/§12). Keyed by (provider, connection_generation, event_type);
    a repeated identical key within `window_sec` collapses to one. A genuinely NEW connection_generation or a
    distinct event_type is NEVER deduped away. Bounded LRU (no unbounded memory). The clock is INJECTED (monotonic
    seconds) — no wall-clock read, no randomness. No lock, no retry loop, no recursion."""

    def __init__(self, *, window_sec: float = 2.0, max_entries: int = 1024) -> None:
        if window_sec <= 0:
            raise ValueError("window_sec must be > 0")
        if max_entries < 1:
            raise ValueError("max_entries must be >= 1")
        self._window = float(window_sec)
        self._max = int(max_entries)
        self._seen: "OrderedDict[Tuple[str, int, str], float]" = OrderedDict()
        self.suppressed = 0

    def should_process(self, provider: str, connection_generation: int, event_type: str, *, now_monotonic: float) -> bool:
        key = (provider, int(connection_generation), event_type)
        last = self._seen.get(key)
        if last is not None and (now_monotonic - last) < self._window:
            self.suppressed += 1  # suppression is COUNTED (never silent — §4)
            self._seen.move_to_end(key)
            return False
        self._seen[key] = now_monotonic
        self._seen.move_to_end(key)
        while len(self._seen) > self._max:
            self._seen.popitem(last=False)  # evict oldest -> bounded memory
        return True


def run_shadow_evaluation(
    snapshot: EvidenceSnapshot,
    current_authority: CurrentAuthorityObservation,
    *,
    config: ShadowAdapterConfig,
    decided_at_utc: str,
    source_sha: str,
    runtime_identity: Mapping[str, object],
    adapter_version: str = ADAPTER_VERSION,
    emitter: Optional[object] = None,
    granular: bool = False,
    simulate_adapter_error: bool = False,
) -> ShadowEvaluationResult:
    """Run ONE shadow evaluation. PURE with respect to transport: it NEVER reconnects, NEVER sets a recovery flag,
    NEVER holds an executor. Deterministic given its inputs. There is DELIBERATELY no executor / force / emergency
    parameter — a real executor cannot be injected.

    Disabled-by-default: when `config.enabled` is False the adapter produces NOTHING (returns a skipped result).
    Fail-closed: INCOMPLETE evidence -> EVIDENCE_INCOMPLETE; contradictory evidence -> EVIDENCE_CONFLICT; any mapping
    or core exception -> ADAPTER_ERROR (no fabricated decision). If an `emitter` is supplied its `emit(record)` is
    called read-only; an emit failure is isolated and NEVER affects this result's correctness or the current
    authority."""
    if config is None or not config.enabled:
        return ShadowEvaluationResult(
            enabled=False, produced=False, comparison_class=None, record=None,
            reason=(config.disabled_reason if config is not None else "no config") or "shadow disabled",
        )

    completeness = snapshot.evidence_completeness.value
    contradictory = len(snapshot.contradictory_evidence) > 0

    envelope: Optional[core.DecisionEnvelope]
    adapter_error = bool(simulate_adapter_error)
    if adapter_error:
        envelope = None
    else:
        try:
            decision_input = map_snapshot_to_recovery_input(snapshot)
            envelope = core.decide(decision_input)
        except (MapperError, core.RecoveryContractError, Exception):  # noqa: BLE001 - isolation boundary
            # A structurally-impossible / contradictory snapshot. Treated as ADAPTER_ERROR: NO decision, NO
            # reconnect, current authority untouched. Never re-raised into a live path.
            envelope = None
            adapter_error = True

    comparison_class, divergence = classify(
        envelope, current_authority,
        evidence_completeness=completeness,
        contradictory_evidence=contradictory,
        adapter_error=adapter_error,
        granular=granular,
    )

    if envelope is None:
        envelope = _sentinel_envelope(snapshot, comparison_class.value)

    record = build_record(
        snapshot_id=snapshot.snapshot_id,
        envelope=envelope,
        current_authority=current_authority,
        comparison_class=comparison_class.value,
        divergence_reasons=divergence,
        evidence_completeness=completeness,
        adapter_version=adapter_version,
        source_sha=source_sha,
        runtime_identity=runtime_identity,
        decided_at_utc=decided_at_utc,
        connection_generation=snapshot.connection_generation,
    )

    if emitter is not None:
        _safe_emit(emitter, record)

    return ShadowEvaluationResult(
        enabled=True, produced=True, comparison_class=comparison_class.value, record=record,
    )


def _safe_emit(emitter: object, record: ShadowDecisionRecord) -> None:
    """Call the emitter read-only. An emit exception is isolated — it can never affect the shadow result or the
    current authority (§21)."""
    emit = getattr(emitter, "emit", None) or getattr(emitter, "append", None)
    if emit is None:
        return
    try:
        emit(record if getattr(emitter, "emit", None) else record.to_dict())
    except Exception:  # noqa: BLE001 - the emitter is off the hot path; a failure must never escape.
        return


def _sentinel_envelope(snapshot: EvidenceSnapshot, cause: str) -> core.DecisionEnvelope:
    """A NON-AUTHORISING sentinel envelope for adapter-error / incomplete cases. reconnect_authorised is FALSE — it
    can NEVER be consumed as an authorisation."""
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
