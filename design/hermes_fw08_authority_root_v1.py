#!/usr/bin/env python3
"""HERMES FW-08 F2-R1 external authority-root contract v1 (PURE, fixture-backed).

WO-HELM-HERMES-FW08-F2-R1-EXTERNAL-PRODUCER-REGISTRY-ROOT-OF-TRUST-IMPLEMENTATION-0001 (§7).
Owner: HERMES (Helm). Created (UTC): 2026-07-21. Contract version: 1.

WHY THIS EXISTS (F2-R1). FW-08 F-2's `ProducerRegistry` is caller-mintable: a checksum proves internal
INTEGRITY, not external AUTHENTICITY. An `AuthorityRoot` is the EXTERNAL root of trust that authorises a
registry: it names the trust domain, the approvers, the versions it may authorise, and its validity window.

CRITICAL — AUTHENTICITY IS CONFERRED BY THE RESOLVER, NOT BY A STRING. `authority_root_classification` is a
FIELD, but trust is conferred by a governed RESOLVER returning the root through the external authority
boundary, NEVER by a caller setting the string. A plain-constructed root with
classification="GOVERNED_EXTERNAL_AUTHORITY" is NOT trusted — activation enforces that a governed classification
may only arrive via a GOVERNED_EXTERNAL_RESOLVER (see hermes_fw08_authority_resolver_v1 /
hermes_fw08_registry_activation_v1). In THIS WO only SYNTHETIC_TEST_AUTHORITY roots may be validly
instantiated by tests; the digest must recompute or the root is rejected downstream.

PURE: no I/O, no subprocess, no network, no secrets, stdlib only, deterministic. NOT imported by runtime.
This module holds NO key material — `approver_key_references` are REFERENCES only, never keys.
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

AUTHORITY_CLASSIFICATIONS = frozenset({
    "SYNTHETIC_TEST_AUTHORITY",
    "GOVERNED_EXTERNAL_AUTHORITY",
    "REVOKED_AUTHORITY",
    "UNTRUSTED_AUTHORITY",
})
_WILDCARD_APPROVERS = ("*", "")


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
class AuthorityRoot:
    """The external root of trust that authorises a producer registry. `authority_root_digest` proves the
    root's bytes were not altered; AUTHENTICITY (that this is a governed external authority) is conferred by a
    governed RESOLVER returning it — never by the classification string a caller sets."""

    contract_version: str
    application: str
    authority_root_id: str
    authority_type: str
    trust_domain_id: str
    policy_version: str
    allowed_registry_contract_versions: Tuple[str, ...]
    allowed_registry_policy_versions: Tuple[str, ...]
    allowed_registry_ids: Tuple[str, ...]           # may be empty if a derivation rule is set
    registry_id_derivation_rule: str                # "" == none
    allowed_producer_applications: Tuple[str, ...]
    approval_threshold: int
    authorised_approver_identities: Tuple[str, ...]
    approver_key_references: Tuple[str, ...]         # REFERENCES only — never key material
    authority_valid_from_utc: str
    authority_valid_until_utc: str                  # "" + permanence_rationale allowed
    permanence_rationale: str
    revocation_set_reference: str
    source_provenance: str
    source_version: str
    audit_reference: str
    authority_root_digest: str
    authority_root_classification: str
    activation_policy: str

    def identity_fields(self) -> Dict[str, object]:
        """Canonical fields the digest covers (EXCLUDES authority_root_digest itself)."""
        return {
            "contract_version": self.contract_version,
            "application": self.application,
            "authority_root_id": self.authority_root_id,
            "authority_type": self.authority_type,
            "trust_domain_id": self.trust_domain_id,
            "policy_version": self.policy_version,
            "allowed_registry_contract_versions": list(self.allowed_registry_contract_versions),
            "allowed_registry_policy_versions": list(self.allowed_registry_policy_versions),
            "allowed_registry_ids": list(self.allowed_registry_ids),
            "registry_id_derivation_rule": self.registry_id_derivation_rule,
            "allowed_producer_applications": list(self.allowed_producer_applications),
            "approval_threshold": int(self.approval_threshold),
            "authorised_approver_identities": list(self.authorised_approver_identities),
            "approver_key_references": list(self.approver_key_references),
            "authority_valid_from_utc": self.authority_valid_from_utc,
            "authority_valid_until_utc": self.authority_valid_until_utc,
            "permanence_rationale": self.permanence_rationale,
            "revocation_set_reference": self.revocation_set_reference,
            "source_provenance": self.source_provenance,
            "source_version": self.source_version,
            "audit_reference": self.audit_reference,
            "authority_root_classification": self.authority_root_classification,
            "activation_policy": self.activation_policy,
        }

    def to_dict(self) -> Dict[str, object]:
        d = self.identity_fields()
        d["authority_root_digest"] = self.authority_root_digest
        return d

    @staticmethod
    def compute_digest(identity_fields: Mapping[str, object]) -> str:
        return hashlib.sha256(_canonical(dict(identity_fields)).encode("utf-8")).hexdigest()

    def recompute_digest(self) -> str:
        return AuthorityRoot.compute_digest(self.identity_fields())


def new_authority_root(**kwargs: object) -> AuthorityRoot:
    """Construct an AuthorityRoot with its `authority_root_digest` computed over its identity fields (mirrors
    `new_registration`). This blesses the bytes' INTEGRITY; it does NOT confer external authenticity — a
    governed resolver must return the root for it to be trusted at activation."""
    kwargs.setdefault("contract_version", CONTRACT_VERSION)
    kwargs["authority_root_digest"] = ""
    root0 = AuthorityRoot(**kwargs)  # type: ignore[arg-type]
    return dataclasses.replace(root0, authority_root_digest=root0.recompute_digest())


def validate_authority_root(
    root: object,
    *,
    now_utc: str,
    application: str,
    trust_domain_id: str,
) -> Tuple[str, ...]:
    """Fail-closed validation of an authority root's bytes + scope + window. Returns () iff the root is a
    well-formed AuthorityRoot whose digest recomputes, whose classification is known and not
    revoked/untrusted, matching application + trust domain, within its validity window, with no wildcard
    approver and a threshold >= 1. Otherwise a sorted tuple of reason codes.

    NOTE: this validates the root's own SELF-CONSISTENCY + scope. It does NOT (and cannot) prove the root is a
    genuine external authority — that is enforced structurally by the resolver at activation."""
    reasons: List[str] = []
    if not isinstance(root, AuthorityRoot):
        return ("AR-WRONG-TYPE",)
    if root.authority_root_digest != root.recompute_digest():
        reasons.append("AR-DIGEST-TAMPER")
    if root.authority_root_classification not in AUTHORITY_CLASSIFICATIONS:
        reasons.append("AR-BAD-CLASSIFICATION")
    if root.authority_root_classification == "REVOKED_AUTHORITY":
        reasons.append("AR-REVOKED")
    if root.authority_root_classification == "UNTRUSTED_AUTHORITY":
        reasons.append("AR-UNTRUSTED")
    if root.application != application:
        reasons.append("AR-APPLICATION-MISMATCH")
    if root.trust_domain_id != trust_domain_id:
        reasons.append("AR-TRUST-DOMAIN-MISMATCH")

    now = _parse_utc(now_utc)
    frm = _parse_utc(root.authority_valid_from_utc)
    if now is None or frm is None:
        reasons.append("AR-NOT-YET-VALID")
    elif now < frm:
        reasons.append("AR-NOT-YET-VALID")
    if str(root.authority_valid_until_utc).strip() == "":
        if not str(root.permanence_rationale).strip():
            reasons.append("AR-EXPIRED")
    else:
        until = _parse_utc(root.authority_valid_until_utc)
        if until is None or now is None or now >= until:
            reasons.append("AR-EXPIRED")

    if any(str(a) in _WILDCARD_APPROVERS for a in root.authorised_approver_identities):
        reasons.append("AR-WILDCARD-APPROVER")
    if int(root.approval_threshold) < 1:
        reasons.append("AR-THRESHOLD-INVALID")

    return tuple(sorted(set(reasons)))
