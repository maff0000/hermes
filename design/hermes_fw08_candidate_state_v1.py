#!/usr/bin/env python3
"""HERMES FW-08 candidate-readiness state machine v1 (PURE, machine-readable model + code).

WO-HELM-HERMES-FW08-GOVERNED-STAGE-B-IMAGE-BUILD-ENFORCEMENT-IMPLEMENTATION-0001.
Owner: HERMES (Helm). Created (UTC): 2026-07-19. Contract version: 1.

The Stage-B candidate build advances through an ORDERED, non-skippable sequence of states. There is NO
`PUBLISHED` and NO `DEPLOYED` state anywhere in the machine — candidate readiness is mechanically
incapable of expressing publication or deployment (§18, §20). Any failure forces the terminal `REJECTED`
state; a REJECTED machine can NEVER become `CANDIDATE_READY`, cannot auto-retry, and cannot publish or
deploy. Transition records are immutable and carry UTC + reason code + source SHA + optional image id +
evidence ref only (no secrets).

PURE: no I/O, no subprocess, no network, no secrets, stdlib only, deterministic. NOT imported by runtime.
"""
from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

CONTRACT_VERSION = "1"
UTC = datetime.timezone.utc
_UTC_ISO_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?(\+00:00|Z)$")


class State(str, Enum):
    NOT_STARTED = "NOT_STARTED"
    SOURCE_VERIFIED = "SOURCE_VERIFIED"
    CONTEXT_EXPORTED = "CONTEXT_EXPORTED"
    CONTEXT_VERIFIED = "CONTEXT_VERIFIED"
    BUILD_READY = "BUILD_READY"
    BUILD_COMPLETED = "BUILD_COMPLETED"
    IMAGE_INSPECTED = "IMAGE_INSPECTED"
    SBOM_COMPLETED = "SBOM_COMPLETED"
    VULNERABILITY_SCAN_COMPLETED = "VULNERABILITY_SCAN_COMPLETED"
    CANDIDATE_READY = "CANDIDATE_READY"
    REJECTED = "REJECTED"


# The single authorised forward path. Each state may ONLY advance to its immediate successor (no skip),
# or transition to REJECTED. REJECTED is terminal.
_ORDER: Tuple[State, ...] = (
    State.NOT_STARTED,
    State.SOURCE_VERIFIED,
    State.CONTEXT_EXPORTED,
    State.CONTEXT_VERIFIED,
    State.BUILD_READY,
    State.BUILD_COMPLETED,
    State.IMAGE_INSPECTED,
    State.SBOM_COMPLETED,
    State.VULNERABILITY_SCAN_COMPLETED,
    State.CANDIDATE_READY,
)
_ORDER_INDEX: Dict[State, int] = {s: i for i, s in enumerate(_ORDER)}

# Machine-readable allowed transitions (successor + REJECTED). No PUBLISHED/DEPLOYED target exists.
ALLOWED_TRANSITIONS: Dict[State, Tuple[State, ...]] = {}
for _i, _s in enumerate(_ORDER):
    _succ: List[State] = []
    if _i + 1 < len(_ORDER):
        _succ.append(_ORDER[_i + 1])
    _succ.append(State.REJECTED)
    ALLOWED_TRANSITIONS[_s] = tuple(_succ)
ALLOWED_TRANSITIONS[State.REJECTED] = ()  # terminal — no exit


class StateMachineError(Exception):
    """Fail-closed error for an illegal / skipped / post-terminal transition or malformed record."""


@dataclass(frozen=True)
class TransitionRecord:
    """Immutable record of one state transition. Carries safe fields only — never secret values."""

    from_state: State
    to_state: State
    at_utc: str
    reason_code: str
    source_sha: str
    image_id: Optional[str] = None
    evidence_ref: Optional[str] = None

    def to_dict(self) -> Dict[str, object]:
        return {
            "from_state": self.from_state.value,
            "to_state": self.to_state.value,
            "at_utc": self.at_utc,
            "reason_code": self.reason_code,
            "source_sha": self.source_sha,
            "image_id": self.image_id,
            "evidence_ref": self.evidence_ref,
        }


def _require_utc(value: str) -> str:
    if not isinstance(value, str) or not _UTC_ISO_RE.match(value):
        raise StateMachineError(f"transition timestamp must be tz-aware UTC ISO-8601, got {value!r}")
    return value


@dataclass
class CandidateStateMachine:
    """Drives one Stage-B candidate through the ordered states with immutable transition records."""

    source_sha: str
    state: State = State.NOT_STARTED
    _records: Tuple[TransitionRecord, ...] = field(default_factory=tuple)

    @property
    def records(self) -> Tuple[TransitionRecord, ...]:
        return self._records

    @property
    def is_rejected(self) -> bool:
        return self.state is State.REJECTED

    @property
    def is_ready(self) -> bool:
        return self.state is State.CANDIDATE_READY

    def _append(self, to_state: State, at_utc: str, reason_code: str, *,
                image_id: Optional[str], evidence_ref: Optional[str]) -> None:
        rec = TransitionRecord(
            from_state=self.state, to_state=to_state, at_utc=_require_utc(at_utc),
            reason_code=reason_code, source_sha=self.source_sha,
            image_id=image_id, evidence_ref=evidence_ref,
        )
        self._records = self._records + (rec,)
        self.state = to_state

    def advance(self, to_state: State, at_utc: str, reason_code: str, *,
                image_id: Optional[str] = None, evidence_ref: Optional[str] = None) -> TransitionRecord:
        """Advance to the immediate successor state. Fail closed on skip / post-terminal / wrong target."""
        if self.state is State.REJECTED:
            raise StateMachineError("machine is REJECTED (terminal); no further transition permitted")
        if to_state is State.REJECTED:
            raise StateMachineError("use reject() to move to REJECTED, not advance()")
        allowed = ALLOWED_TRANSITIONS[self.state]
        if to_state not in allowed:
            raise StateMachineError(
                f"illegal transition {self.state.value} -> {to_state.value} "
                f"(allowed: {[s.value for s in allowed]}); no state may be skipped"
            )
        self._append(to_state, at_utc, reason_code, image_id=image_id, evidence_ref=evidence_ref)
        return self._records[-1]

    def reject(self, at_utc: str, reason_code: str, *,
               image_id: Optional[str] = None, evidence_ref: Optional[str] = None) -> TransitionRecord:
        """Force the terminal REJECTED state. Legal from any non-terminal state. Idempotent-safe: a
        second reject after REJECTED fails closed (terminal)."""
        if self.state is State.REJECTED:
            raise StateMachineError("machine already REJECTED (terminal)")
        self._append(State.REJECTED, at_utc, reason_code, image_id=image_id, evidence_ref=evidence_ref)
        return self._records[-1]

    def to_dict(self) -> Dict[str, object]:
        return {
            "contract_version": CONTRACT_VERSION,
            "source_sha": self.source_sha,
            "state": self.state.value,
            "is_ready": self.is_ready,
            "is_rejected": self.is_rejected,
            "transitions": [r.to_dict() for r in self._records],
        }


def state_order() -> Tuple[str, ...]:
    """The ordered non-terminal state names (machine-readable)."""
    return tuple(s.value for s in _ORDER)
