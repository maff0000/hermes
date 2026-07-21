#!/usr/bin/env python3
"""HERMES FW-08 F-2 independent governed producer registry v1 (PURE, fixture-backed).

WO-HELM-HERMES-FW08-F2-INDEPENDENT-ACTIVE-IMPORT-ANCHORS-IMPLEMENTATION-0001 (§9).
Owner: HERMES (Helm). Created (UTC): 2026-07-21. Contract version: 1.

WHY THIS EXISTS (F-2). The active-import evidence validator is a PURE comparator: it compares the evidence's
declared anchors (image id / filesystem digest / producer) against caller-supplied EXPECTED values. The F-2
defect was that the Stage-B wrapper sourced those expected values FROM THE EVIDENCE RECORD ITSELF — a
tautology (a record proves its own truth by repeating a value). The fix requires the expected anchors to come
from INDEPENDENT GOVERNED PRODUCERS: a governed build-result producer, a dedicated OCI/image-inspection
producer, and a separate active-import-analysis producer, each REGISTERED here by an EXTERNAL approver and
scoped to an exact evidence type + gate. Trust (authority) is conferred ONLY by a registration in this
registry, never by a value repeated inside the evidence.

STRUCTURAL GUARANTEES:
  * `resolve()` consults ONLY the in-memory registry object, NEVER any evidence record.
  * A frozen registry cannot be mutated (evidence cannot register a producer at validation time).
  * A producer that approves itself, or is inlined "from evidence", or carries wildcard scope, is rejected.
  * `real_evidence_authority` is a registry-governed flag; a synthetic producer MUST be False, and the
    evidence's OWN declared classification cannot make it real.

PURE: no I/O, no subprocess, no network, no secrets, stdlib only, deterministic. NOT imported by runtime.
This module imports NOTHING from the active-import evidence module (avoids an import cycle).
"""
from __future__ import annotations

import dataclasses
import datetime
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Tuple

CONTRACT_VERSION = "1"
_UTC_ISO_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?(\+00:00|Z)$")

# The exact producer classes F-2 recognises. Each governed anchor derives from EXACTLY ONE class.
PRODUCER_TYPES = frozenset({
    "GOVERNED_BUILD_RESULT",     # authoritative image id / candidate / source boundary (build result)
    "OCI_IMAGE_INSPECTION",      # authoritative manifest / config / filesystem digests (image inspection)
    "ACTIVE_IMPORT_ANALYSIS",    # non-running import-graph analysis over the exported filesystem
})
# A blank / wildcard scope grants everything — structurally rejected.
_WILDCARDS = ("*", "?", "")

# Origin that is forbidden: a registration synthesised inline from the evidence being validated.
INLINE_ORIGIN = "INLINE_FROM_EVIDENCE"


def _canonical(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _parse_utc(value: str) -> Optional[datetime.datetime]:
    if not isinstance(value, str) or not _UTC_ISO_RE.match(value):
        return None
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass(frozen=True)
class ProducerRegistration:
    """A typed, checksummed registration of ONE governed producer. `registry_record_checksum` proves the
    registration bytes were not altered; AUTHORITY is conferred by presence in a governed `ProducerRegistry`
    plus a recorded external approval, never by the checksum alone."""

    registry_contract_version: str
    registry_policy_version: str
    producer_id: str
    producer_type: str
    application: str
    allowed_evidence_types: Tuple[str, ...]
    allowed_gate_ids: Tuple[str, ...]
    tool_identity: str
    tool_version: str
    tool_digest: str
    source_version: str
    candidate_binding_policy: str
    image_binding_policy: str
    authority_scope: str
    creation_utc: str
    expiry_utc: str                      # "" == permanent (requires permanent_policy_rationale)
    permanent_policy_rationale: str
    approval_authority: str
    audit_reference: str
    real_evidence_authority: bool        # synthetic producers MUST be False
    created_by: str
    origin: str
    registry_record_checksum: str

    def identity_fields(self) -> Dict[str, object]:
        """Canonical fields the checksum covers (EXCLUDES registry_record_checksum itself)."""
        return {
            "registry_contract_version": self.registry_contract_version,
            "registry_policy_version": self.registry_policy_version,
            "producer_id": self.producer_id,
            "producer_type": self.producer_type,
            "application": self.application,
            "allowed_evidence_types": list(self.allowed_evidence_types),
            "allowed_gate_ids": list(self.allowed_gate_ids),
            "tool_identity": self.tool_identity,
            "tool_version": self.tool_version,
            "tool_digest": self.tool_digest,
            "source_version": self.source_version,
            "candidate_binding_policy": self.candidate_binding_policy,
            "image_binding_policy": self.image_binding_policy,
            "authority_scope": self.authority_scope,
            "creation_utc": self.creation_utc,
            "expiry_utc": self.expiry_utc,
            "permanent_policy_rationale": self.permanent_policy_rationale,
            "approval_authority": self.approval_authority,
            "audit_reference": self.audit_reference,
            "real_evidence_authority": bool(self.real_evidence_authority),
            "created_by": self.created_by,
            "origin": self.origin,
        }

    def to_dict(self) -> Dict[str, object]:
        d = self.identity_fields()
        d["registry_record_checksum"] = self.registry_record_checksum
        return d

    @staticmethod
    def compute_checksum(identity_fields: Mapping[str, object]) -> str:
        """sha256 over the canonical identity fields (the record's checksum-excluded projection)."""
        return hashlib.sha256(_canonical(dict(identity_fields)).encode("utf-8")).hexdigest()

    def recompute_checksum(self) -> str:
        return ProducerRegistration.compute_checksum(self.identity_fields())


def new_registration(**kwargs: object) -> ProducerRegistration:
    """Construct a ProducerRegistration with its `registry_record_checksum` computed over its own identity
    fields. This is the ONLY blessed constructor for a well-formed registration (used by governed set-up and
    by tests). Any registration whose checksum does not recompute exactly is rejected on register/resolve."""
    kwargs.setdefault("registry_contract_version", CONTRACT_VERSION)
    kwargs["registry_record_checksum"] = ""
    reg0 = ProducerRegistration(**kwargs)  # type: ignore[arg-type]
    return dataclasses.replace(reg0, registry_record_checksum=reg0.recompute_checksum())


class ProducerRegistry:
    """The governed set of trusted producers. Constructed EMPTY; producers are added ONLY via `register()`
    with an EXTERNAL approver authority, and the set is `freeze()`-d before it backs any anchor. `resolve()`
    consults ONLY this object — it never reads an evidence record — so no evidence can confer authority on
    itself."""

    def __init__(self, *, registry_policy_version: str) -> None:
        self.registry_policy_version = str(registry_policy_version)
        self._by_id: Dict[str, ProducerRegistration] = {}
        self._approvals: Dict[str, str] = {}
        self._frozen = False

    @property
    def registry_reference(self) -> str:
        """The version an anchor records as its `producer_registry_ref` (matched by the bundle)."""
        return self.registry_policy_version

    @property
    def frozen(self) -> bool:
        return self._frozen

    def freeze(self) -> None:
        self._frozen = True

    def register(self, reg: ProducerRegistration, *, approver_authority: str) -> Tuple[str, ...]:
        """Append `reg` to the registry (only while not frozen). Returns () on success, else a sorted tuple of
        reason codes and does NOT register. Rejects self-registration, inline-from-evidence origin, wildcard
        scope, checksum tamper, and a missing approval authority."""
        reasons: List[str] = []
        if self._frozen:
            reasons.append("PR-REGISTRY-FROZEN")
        if not isinstance(reg, ProducerRegistration):
            return ("PR-NOT-A-REGISTRATION",)
        if reg.registry_record_checksum != reg.recompute_checksum():
            reasons.append("PR-REGISTRY-CHECKSUM-TAMPER")
        if not str(approver_authority).strip() or not str(reg.approval_authority).strip():
            reasons.append("PR-APPROVAL-MISSING")
        # self-registration: a producer cannot be its own approver / its own creator.
        if reg.approval_authority == reg.producer_id or str(approver_authority) == reg.producer_id:
            reasons.append("PR-SELF-REGISTRATION")
        if reg.producer_type == "ACTIVE_IMPORT_ANALYSIS" and reg.created_by == reg.producer_id:
            reasons.append("PR-SELF-REGISTRATION")
        if reg.origin == INLINE_ORIGIN:
            reasons.append("PR-INLINE-REGISTRY")
        if any(str(e) in _WILDCARDS for e in reg.allowed_evidence_types) or not reg.allowed_evidence_types:
            reasons.append("PR-WILDCARD-EVIDENCE-TYPE")
        if any(str(g) in _WILDCARDS for g in reg.allowed_gate_ids) or not reg.allowed_gate_ids:
            reasons.append("PR-WILDCARD-GATE")
        if reg.producer_type not in PRODUCER_TYPES:
            reasons.append("PR-UNKNOWN-PRODUCER-TYPE")
        if reasons:
            return tuple(sorted(set(reasons)))
        self._by_id[reg.producer_id] = reg
        self._approvals[reg.producer_id] = str(approver_authority)
        return tuple()

    def resolve(
        self,
        *,
        producer_id: str,
        evidence_type: str,
        gate_id: str,
        application: str,
        now_utc: str,
        expected_tool_identity: Optional[str] = None,
        expected_tool_version: Optional[str] = None,
        expected_tool_digest: Optional[str] = None,
        expected_source_version: Optional[str] = None,
    ) -> Tuple[Optional[ProducerRegistration], Tuple[str, ...]]:
        """Resolve `producer_id` for an exact evidence type + gate + application. Returns (registration, ())
        iff the producer is registered, well-formed, unexpired, approved, scoped, and (when the caller pins
        expected tool identity / version / digest / source) matches them. Otherwise (None, reasons).

        CONSULTS ONLY the registry — never an evidence record."""
        reg = self._by_id.get(str(producer_id))
        if reg is None:
            return (None, ("PR-UNKNOWN-PRODUCER",))
        reasons: List[str] = []
        if reg.registry_contract_version != CONTRACT_VERSION:
            reasons.append("PR-CONTRACT-VERSION")
        if reg.registry_record_checksum != reg.recompute_checksum():
            reasons.append("PR-REGISTRY-CHECKSUM-TAMPER")
        if not self._approvals.get(str(producer_id)) or not str(reg.approval_authority).strip():
            reasons.append("PR-APPROVAL-MISSING")
        if evidence_type not in reg.allowed_evidence_types:
            reasons.append("PR-WRONG-EVIDENCE-TYPE")
        if gate_id not in reg.allowed_gate_ids:
            reasons.append("PR-WRONG-GATE")
        if reg.application != application:
            reasons.append("PR-APPLICATION-MISMATCH")
        # expiry (a permanent producer must carry a rationale).
        if str(reg.expiry_utc).strip() == "":
            if not str(reg.permanent_policy_rationale).strip():
                reasons.append("PR-PRODUCER-EXPIRED")
        else:
            exp = _parse_utc(reg.expiry_utc)
            now = _parse_utc(now_utc)
            if exp is None or now is None or now >= exp:
                reasons.append("PR-PRODUCER-EXPIRED")
        if expected_tool_identity is not None and reg.tool_identity != expected_tool_identity:
            reasons.append("PR-TOOL-IDENTITY-MISMATCH")
        if expected_tool_version is not None and reg.tool_version != expected_tool_version:
            reasons.append("PR-TOOL-VERSION-MISMATCH")
        if expected_tool_digest is not None and reg.tool_digest != expected_tool_digest:
            reasons.append("PR-TOOL-DIGEST-MISMATCH")
        if expected_source_version is not None and reg.source_version != expected_source_version:
            reasons.append("PR-SOURCE-VERSION-MISMATCH")
        if reasons:
            return (None, tuple(sorted(set(reasons))))
        return (reg, tuple())
