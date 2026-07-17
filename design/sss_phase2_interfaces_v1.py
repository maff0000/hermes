"""HERMES shared-stream recovery Phase-2 — ADAPTER INTERFACES / SEAMS (INERT / DESIGN-ONLY).

WO-HELM-HERMES-SHARED-STREAM-RECOVERY-PHASE2-SHADOW-ADAPTER-AND-EVIDENCE-CONTRACT-DESIGN-0001.
Authority: HELM (HERMES market-data lane). Created (UTC): 2026-07-17.

STATUS: INERT / NOT WIRED. Imported by NO runtime path. DESIGN artefact only — these are typing.Protocol seams
that a FUTURE Phase-2 implementation WO would satisfy. They define the 8 separated adapter parts + the MECHANICAL
NO-OP shadow-executor boundary. None of these are instantiated at import; nothing here performs a side effect.

The 8 separated parts (architecture_phase2_v1 §12):
  1. EvidenceCollector           — read a single live surface (socket / heartbeat / shared-progress / parser / auth / instrument / current-authority)
  2. SnapshotBuilder             — atomically assemble an immutable EvidenceSnapshot from all collectors
  3. Phase1InputMapper           — snapshot -> core.RecoveryDecisionInput (pure; here = EvidenceSnapshot.to_recovery_input)
  4. PureCoreInvoker             — call the UNCHANGED core.decide() (single-shot, pure)
  5. CurrentAuthorityObserver    — PASSIVELY read what the existing sustained-red authority did
  6. ShadowComparator            — classify shadow vs current (design.sss_phase2_comparator_v1.classify)
  7. ShadowEvidenceEmitter       — persist the ShadowDecisionRecord (append-only JSONL / structured log) — READ-ONLY re: transport
  8. NoOpShadowExecutorBoundary  — the boundary that MUST refuse to execute; proves the shadow cannot reconnect

The seam that a future impl must NOT cross: no part may hold or call an object that performs disconnect()/connect()
or sets the watchdog recovery-request flag. That capability lives ONLY in the (Phase-3+) real executor, which is
absent here by design.
"""
from __future__ import annotations

from typing import Optional, Protocol, Sequence, Tuple, runtime_checkable

import utils.hermes_shared_stream_recovery_v1 as core
from design.sss_phase2_comparator_v1 import ComparisonClass
from design.sss_phase2_evidence_snapshot_v1 import EvidenceSnapshot, FieldProvenance
from design.sss_phase2_shadow_record_v1 import CurrentAuthorityObservation, ShadowDecisionRecord


@runtime_checkable
class EvidenceCollector(Protocol):
    """Reads ONE live surface and returns a partial-evidence contribution + provenance. READ-ONLY: a collector
    MUST NOT mutate the surface it reads, must not reorder watchdog state, and must not block the market-data or
    recovery paths (architecture_phase2_v1 §19: never hold a lock across a market-data or reconnect operation)."""

    name: str

    def collect(self, *, now_utc: str) -> Tuple[dict, Sequence[FieldProvenance]]:
        """Return ({field: value, ...}, [FieldProvenance, ...]). `now_utc` is supplied (no wall-clock read).
        On its own source being unavailable, the collector returns the field marked UNAVAILABLE — never a
        favourable default."""
        ...


@runtime_checkable
class SnapshotBuilder(Protocol):
    """Atomically assembles an immutable EvidenceSnapshot from all collectors. Uses an immutable copy / generation
    check so a reconnect or instrument-state update mid-capture cannot produce a torn snapshot (§19)."""

    def build(self, *, now_utc: str, connection_generation: int) -> EvidenceSnapshot:
        ...


@runtime_checkable
class Phase1InputMapper(Protocol):
    """Maps a snapshot to the UNCHANGED Phase-1 core input. Pure. The canonical implementation is
    EvidenceSnapshot.to_recovery_input()."""

    def to_input(self, snapshot: EvidenceSnapshot) -> core.RecoveryDecisionInput:
        ...


@runtime_checkable
class PureCoreInvoker(Protocol):
    """Invokes the UNCHANGED core.decide(). The core is never modified; only called. Single-shot, pure."""

    def decide(self, decision_input: core.RecoveryDecisionInput) -> core.DecisionEnvelope:
        ...


@runtime_checkable
class CurrentAuthorityObserver(Protocol):
    """PASSIVELY observes the existing sustained-red authority (architecture_phase2_v1 §10). It reads existing
    events/state ONLY (watchdog `_recovery_request_pending` / `_recovery_request_reason` / `_recovery_attempts_window`,
    the main.py reconnect outcome). It MUST NOT call, wrap, gate, or re-order that authority — observation must not
    change the authority's timing or decision."""

    def observe(self, *, now_utc: str) -> CurrentAuthorityObservation:
        ...


@runtime_checkable
class ShadowComparator(Protocol):
    def classify(
        self, envelope: Optional[core.DecisionEnvelope], current: CurrentAuthorityObservation,
        *, evidence_completeness: str, contradictory_evidence: bool, adapter_error: bool,
    ) -> Tuple[ComparisonClass, Tuple[str, ...]]:
        ...


@runtime_checkable
class ShadowEvidenceEmitter(Protocol):
    """Persists a ShadowDecisionRecord to an append-only, offline-replayable sink (structured log / project-local
    JSONL). READ-ONLY with respect to transport: emitting evidence performs NO reconnect and sets NO flag. A write
    failure MUST be visible and MUST NOT affect the current authority or raise into the market-data path (§21)."""

    def emit(self, record: ShadowDecisionRecord) -> bool:
        """Return True on durable write, False on a (visible) write failure. Never raises into the caller."""
        ...


class ShadowExecutionForbidden(Exception):
    """Raised by NoOpShadowExecutorBoundary if anything ever asks the shadow path to execute a reconnect. This is
    the MECHANICAL guarantee (architecture_phase2_v1 §16) that a shadow record can never become an action."""


@runtime_checkable
class NoOpShadowExecutorBoundary(Protocol):
    """The boundary object standing where a real executor would be in Phase 3+. In Phase 2 it is a NO-OP that
    REFUSES: it has no disconnect()/connect() handle and MUST raise ShadowExecutionForbidden if invoked. Its mere
    presence documents that the shadow path terminates at a record, never at an action."""

    def refuse(self, record: ShadowDecisionRecord) -> None:
        """MUST raise ShadowExecutionForbidden. Exists so the impossibility is testable, not merely commented."""
        ...
