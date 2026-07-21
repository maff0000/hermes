#!/usr/bin/env python3
"""HERMES FW-08 F2-R1 authorised-registry-manifest contract v1 (PURE, fixture-backed).

WO-HELM-HERMES-FW08-F2-R1-EXTERNAL-PRODUCER-REGISTRY-ROOT-OF-TRUST-IMPLEMENTATION-0001 (§8).
Owner: HERMES (Helm). Created (UTC): 2026-07-21. Contract version: 1.

WHY THIS EXISTS (F2-R1). The manifest is the governed statement, made under an AUTHORITY ROOT, that a SPECIFIC
registry (bound by registry_digest + producer_set_digest, NOT by inlining the registry object) is authorised
for a scope + window, backed by a quorum of approvals. It binds authenticity to a registry's INTEGRITY digest
so a caller cannot swap the registry after approval.

PURE: no I/O, no subprocess, no network, no secrets, stdlib only, deterministic. NOT imported by runtime.
The manifest does NOT inline the registry object — it binds by registry_digest + producer_set_digest only.
"""
from __future__ import annotations

import dataclasses
import datetime
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

CONTRACT_VERSION = "1"
_UTC_ISO_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?(\+00:00|Z)$")


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
class AuthorisedRegistryManifest:
    """A governed authorisation of ONE registry under ONE authority root. Binds the registry by digest, not by
    value; carries the approval quorum records + result; scoped by application + gate/evidence + window."""

    manifest_contract_version: str
    authority_root_id: str
    registry_id: str
    registry_contract_version: str
    registry_policy_version: str
    registry_digest: str
    producer_set_digest: str
    application: str
    allowed_gate_ids: Tuple[str, ...]
    allowed_evidence_types: Tuple[str, ...]
    valid_from_utc: str
    valid_until_utc: str
    manifest_generation_utc: str
    approval_records: Tuple[object, ...]              # tuple of ApprovalRecord
    approval_threshold_result: bool
    revocation_state: str
    provenance_reference: str
    manifest_digest: str
    authority_root_digest: str

    def identity_fields(self) -> Dict[str, object]:
        """Canonical fields the digest covers (EXCLUDES manifest_digest itself). Approvals are folded in by
        their per-record digests so a swapped approval changes the manifest digest."""
        return {
            "manifest_contract_version": self.manifest_contract_version,
            "authority_root_id": self.authority_root_id,
            "registry_id": self.registry_id,
            "registry_contract_version": self.registry_contract_version,
            "registry_policy_version": self.registry_policy_version,
            "registry_digest": self.registry_digest,
            "producer_set_digest": self.producer_set_digest,
            "application": self.application,
            "allowed_gate_ids": list(self.allowed_gate_ids),
            "allowed_evidence_types": list(self.allowed_evidence_types),
            "valid_from_utc": self.valid_from_utc,
            "valid_until_utc": self.valid_until_utc,
            "manifest_generation_utc": self.manifest_generation_utc,
            "approval_records": [self._rec_digest(r) for r in self.approval_records],
            "approval_threshold_result": bool(self.approval_threshold_result),
            "revocation_state": self.revocation_state,
            "provenance_reference": self.provenance_reference,
            "authority_root_digest": self.authority_root_digest,
        }

    @staticmethod
    def _rec_digest(rec: object) -> str:
        return str(getattr(rec, "approval_record_digest", ""))

    def to_dict(self) -> Dict[str, object]:
        d = self.identity_fields()
        d["approval_records"] = [
            r.to_dict() if hasattr(r, "to_dict") else str(r) for r in self.approval_records
        ]
        d["manifest_digest"] = self.manifest_digest
        return d

    @staticmethod
    def compute_digest(identity_fields: Mapping[str, object]) -> str:
        return hashlib.sha256(_canonical(dict(identity_fields)).encode("utf-8")).hexdigest()

    def recompute_digest(self) -> str:
        return AuthorisedRegistryManifest.compute_digest(self.identity_fields())


def new_manifest(**kwargs: object) -> AuthorisedRegistryManifest:
    """Construct an AuthorisedRegistryManifest with its `manifest_digest` computed over identity fields
    (mirrors `new_registration`)."""
    kwargs.setdefault("manifest_contract_version", CONTRACT_VERSION)
    kwargs["manifest_digest"] = ""
    m0 = AuthorisedRegistryManifest(**kwargs)  # type: ignore[arg-type]
    return dataclasses.replace(m0, manifest_digest=m0.recompute_digest())


def validate_manifest_against_registry(
    manifest: object,
    *,
    registry: object,
    authority_root: object,
    now_utc: str,
    application: str,
) -> Tuple[str, ...]:
    """Fail-closed validation binding a manifest to a live registry + an authority root. Returns () iff every
    binding holds, else sorted reasons. Approvals are validated via `evaluate_approvals`; on failure the
    sub-reasons are appended alongside MF-APPROVAL-FAILED."""
    from design.hermes_fw08_authority_root_v1 import AuthorityRoot
    from design.hermes_fw08_approval_record_v1 import evaluate_approvals

    if not isinstance(manifest, AuthorisedRegistryManifest):
        return ("MF-WRONG-TYPE",)
    if not isinstance(authority_root, AuthorityRoot):
        return ("MF-AUTHORITY-WRONG-TYPE",)

    reasons: List[str] = []

    if manifest.manifest_digest != manifest.recompute_digest():
        reasons.append("MF-DIGEST-TAMPER")

    if manifest.authority_root_id != authority_root.authority_root_id \
            or manifest.authority_root_digest != authority_root.authority_root_digest:
        reasons.append("MF-AUTHORITY-MISMATCH")

    # Registry binding by digest — never by inlining the object.
    registry_id = getattr(registry, "registry_reference", None)
    # The manifest names the registry by its policy version / id; compare id.
    if manifest.registry_id != str(registry_id):
        reasons.append("MF-REGISTRY-ID-MISMATCH")

    try:
        canon = registry.canonical_digest()
        pset = registry.producer_set_digest()
    except AttributeError:
        return ("MF-REGISTRY-NOT-GOVERNED",)

    if manifest.registry_digest != canon:
        reasons.append("MF-REGISTRY-DIGEST-MISMATCH")
    if manifest.producer_set_digest != pset:
        reasons.append("MF-PRODUCER-SET-DIGEST-MISMATCH")

    if manifest.registry_policy_version != str(getattr(registry, "registry_policy_version", "")):
        reasons.append("MF-POLICY-MISMATCH")
    reg_cv = str(getattr(__import__("design.hermes_fw08_producer_registry_v1",
                                    fromlist=["CONTRACT_VERSION"]), "CONTRACT_VERSION", "1"))
    if manifest.registry_contract_version != reg_cv:
        reasons.append("MF-POLICY-MISMATCH")

    if manifest.application != application:
        reasons.append("MF-APPLICATION-MISMATCH")

    now = _parse_utc(now_utc)
    frm = _parse_utc(manifest.valid_from_utc)
    if now is None or frm is None or now < frm:
        reasons.append("MF-NOT-YET-VALID")
    if str(manifest.valid_until_utc).strip() == "":
        reasons.append("MF-EXPIRED")
    else:
        until = _parse_utc(manifest.valid_until_utc)
        if until is None or now is None or now >= until:
            reasons.append("MF-EXPIRED")

    if str(manifest.revocation_state) != "NONE":
        reasons.append("MF-REVOKED")

    # Approvals: the quorum must hold over the registry_id + registry_digest + policy_version.
    producer_ids: List[str] = []
    author_id = str(manifest.provenance_reference)
    active = getattr(registry, "_active_map", None)
    if callable(active):
        producer_ids = list(active().keys())
    ok, sub = evaluate_approvals(
        manifest.approval_records,
        authority_root=authority_root,
        registry_id=manifest.registry_id,
        registry_digest=manifest.registry_digest,
        policy_version=manifest.registry_policy_version,
        registry_author_id=author_id,
        producer_ids=producer_ids,
        now_utc=now_utc,
    )
    if not ok or not manifest.approval_threshold_result:
        reasons.append("MF-APPROVAL-FAILED")
        reasons.extend(sub)

    return tuple(sorted(set(reasons)))
