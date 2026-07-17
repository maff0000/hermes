"""HERMES shared-stream recovery Phase-2 — TEST DOUBLES (INERT / DESIGN-ONLY).

WO-HELM-HERMES-SHARED-STREAM-RECOVERY-PHASE2-SHADOW-ADAPTER-AND-EVIDENCE-CONTRACT-DESIGN-0001.
Authority: HELM (HERMES market-data lane). Created (UTC): 2026-07-17.

STATUS: INERT / NOT WIRED. Imported by NO runtime path (tests only). DESIGN artefact only.

Pure in-memory fakes that satisfy the design.sss_phase2_interfaces_v1 seams WITHOUT touching any live surface:
  * FakeCurrentAuthorityObserver  — replays a recorded CurrentAuthorityObservation (no watchdog contact)
  * InMemoryShadowEmitter         — appends records to a list (no JSONL/Redis/SQL); can simulate a write failure
  * RefusingShadowExecutor        — the NO-OP boundary: raises ShadowExecutionForbidden if ever invoked

These let the Phase-2 offline tests exercise every seam and PROVE the shadow path is mechanically incapable of
executing a reconnect — all with zero I/O.
"""
from __future__ import annotations

from typing import List

from design.sss_phase2_interfaces_v1 import ShadowExecutionForbidden
from design.sss_phase2_shadow_record_v1 import CurrentAuthorityObservation, ShadowDecisionRecord


class FakeCurrentAuthorityObserver:
    """Replays a fixed observation. Never contacts the watchdog — pure double."""

    def __init__(self, observation: CurrentAuthorityObservation):
        self._observation = observation

    def observe(self, *, now_utc: str) -> CurrentAuthorityObservation:
        return self._observation


class InMemoryShadowEmitter:
    """Appends records to an in-memory list. No I/O. `fail_writes=True` simulates a (visible) durable-write failure
    that must NOT raise and must NOT affect authority."""

    def __init__(self, fail_writes: bool = False):
        self.records: List[ShadowDecisionRecord] = []
        self.write_failures = 0
        self._fail_writes = fail_writes

    def emit(self, record: ShadowDecisionRecord) -> bool:
        if self._fail_writes:
            self.write_failures += 1
            return False
        self.records.append(record)
        return True


class RefusingShadowExecutor:
    """The NO-OP shadow-executor boundary. It has NO disconnect()/connect() handle and refuses to act. Proves the
    shadow path can never become a reconnect — the impossibility is testable, not merely documented."""

    def __init__(self):
        self.invocations = 0

    def refuse(self, record: ShadowDecisionRecord) -> None:
        self.invocations += 1
        raise ShadowExecutionForbidden(
            "Shadow path is read-only: a ShadowDecisionRecord can never be executed as a reconnect "
            f"(record snapshot_id={record.snapshot_id}, action={record.shadow_action})."
        )

    # Deliberately NO connect / disconnect / recovery_request methods exist on this class.
