#!/usr/bin/env python3
"""HERMES FW-08 bare-Boolean readiness evaluator — REMOVED (fail-closed tombstone).

WO-HELM-HERMES-PR114-FW08-FINAL-PREBUILD-TRUST-BOUNDARY-CORRECTIONS-0001 (R-1 §6/§7/§8).
Owner: HERMES (Helm). Contract version: 1.

WHY THIS FILE IS A TOMBSTONE. The prior public readiness API here (`ReadinessInputs` + `evaluate(...)`)
accepted CALLER-SUPPLIED pass/fail booleans. R2D2 R-1: a bare-Boolean readiness model is a reachable path by
which a public caller can fabricate readiness by handing it `True`s. Per R-1 the bare-Boolean readiness model
and its evaluator are REMOVED as a reachable readiness path, and any compatibility constructor that recreates
readiness from bools is FAIL-CLOSED.

THE SOLE PUBLIC CANDIDATE-READINESS CONTRACT is now evidence-based and lives in:
  * design/hermes_fw08_readiness_evidence_v1.py   — immutable typed GateEvidence + evaluate_evidence
  * design/hermes_fw08_producer_trust_v1.py        — producer-trust + unforgeable in-process seal
  * design/hermes_fw08_evidence_chain_v1.py        — Merkle-bound validated evidence chain

Readiness is reachable ONLY through a VALIDATED, PRODUCER-TRUSTED, SEALED evidence chain. There is no way to
obtain a ready verdict from bare booleans. Importing this module is safe; USING its removed API raises.
"""
from __future__ import annotations

from typing import Any

CONTRACT_VERSION = "1"

_REMOVED_MSG = (
    "bare-Boolean readiness (ReadinessInputs/evaluate) is REMOVED (R-1 §6-§8): readiness is reachable ONLY "
    "through a validated, producer-trusted, sealed evidence chain "
    "(design.hermes_fw08_readiness_evidence_v1 + hermes_fw08_producer_trust_v1 + hermes_fw08_evidence_chain_v1)"
)


class BareBooleanReadinessRemovedError(RuntimeError):
    """Raised on any attempt to use the removed bare-Boolean readiness API."""


def evaluate(*_args: Any, **_kwargs: Any) -> "None":
    """REMOVED. Fail closed — no bare-Boolean path may reconstruct readiness."""
    raise BareBooleanReadinessRemovedError(_REMOVED_MSG)


def __getattr__(name: str) -> Any:
    # Any access to the removed public symbols (ReadinessInputs, ReadinessVerdict, ...) fails closed.
    if name in ("ReadinessInputs", "ReadinessVerdict"):
        raise BareBooleanReadinessRemovedError(_REMOVED_MSG)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
