#!/usr/bin/env python3
"""HERMES FW-08 F2-R1 approval-record contract v1 (PURE, fixture-backed).

WO-HELM-HERMES-FW08-F2-R1-EXTERNAL-PRODUCER-REGISTRY-ROOT-OF-TRUST-IMPLEMENTATION-0001 (§14).
Owner: HERMES (Helm). Created (UTC): 2026-07-21. Contract version: 1.

WHY THIS EXISTS (F2-R1). An authorised-registry manifest binds a registry to an authority root ONLY if a
QUORUM of authorised, distinct, non-self approvers have signed off on the exact (registry_id + registry_digest
+ policy_version). `evaluate_approvals` enforces the quorum, distinctness, expiry, digest-match, and the
anti-self-approval rules (a producer or the registry author cannot approve their own registry).

PURE: no I/O, no subprocess, no network, no secrets, stdlib only, deterministic. NOT imported by runtime.
`approval_evidence_reference` is a REFERENCE to external approval evidence — never key material.
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
class ApprovalRecord:
    """One approver's signed decision over an exact (registry_id + registry_digest + policy_version). The
    digest proves the record bytes were not altered; the approver's AUTHORITY is conferred by membership in
    the authority root's authorised approver set, never by the record itself."""

    approver_id: str
    approver_role: str
    authority_root_id: str
    registry_id: str
    registry_digest: str
    policy_version: str
    approval_decision: str            # e.g. "APPROVE" / "REJECT"
    approval_utc: str
    expiry_utc: str
    approval_evidence_reference: str
    approval_record_digest: str

    def identity_fields(self) -> Dict[str, object]:
        return {
            "approver_id": self.approver_id,
            "approver_role": self.approver_role,
            "authority_root_id": self.authority_root_id,
            "registry_id": self.registry_id,
            "registry_digest": self.registry_digest,
            "policy_version": self.policy_version,
            "approval_decision": self.approval_decision,
            "approval_utc": self.approval_utc,
            "expiry_utc": self.expiry_utc,
            "approval_evidence_reference": self.approval_evidence_reference,
        }

    def to_dict(self) -> Dict[str, object]:
        d = self.identity_fields()
        d["approval_record_digest"] = self.approval_record_digest
        return d

    @staticmethod
    def compute_digest(identity_fields: Mapping[str, object]) -> str:
        return hashlib.sha256(_canonical(dict(identity_fields)).encode("utf-8")).hexdigest()

    def recompute_digest(self) -> str:
        return ApprovalRecord.compute_digest(self.identity_fields())


def new_approval(**kwargs: object) -> ApprovalRecord:
    """Construct an ApprovalRecord with its digest computed over its identity fields (mirrors
    `new_registration`)."""
    kwargs["approval_record_digest"] = ""
    rec0 = ApprovalRecord(**kwargs)  # type: ignore[arg-type]
    return dataclasses.replace(rec0, approval_record_digest=rec0.recompute_digest())


def evaluate_approvals(
    records: Sequence[object],
    *,
    authority_root: object,
    registry_id: str,
    registry_digest: str,
    policy_version: str,
    registry_author_id: str,
    producer_ids: Sequence[str],
    now_utc: str,
) -> Tuple[bool, Tuple[str, ...]]:
    """Fail-closed quorum evaluation. Returns (ok, sorted reasons). ok iff:
      * every record's digest recomputes (else AP-DIGEST-TAMPER);
      * each APPROVE approver is in `authority_root.authorised_approver_identities` (else AP-APPROVER-UNAUTHORISED);
      * each APPROVE record matches registry_id + registry_digest + policy_version (else AP-WRONG-DIGEST);
      * no APPROVE record is expired vs now (else AP-EXPIRED);
      * no wildcard approver ('*'/'' → AP-WILDCARD-APPROVER);
      * no producer self-approval (approver in producer_ids → AP-PRODUCER-SELF-APPROVAL);
      * no registry-author self-approval (approver == registry_author_id → AP-AUTHOR-SELF-APPROVAL);
      * a duplicate distinct-approver id → AP-DUPLICATE-APPROVER (counted once);
      * distinct APPROVE-approver count >= authority_root.approval_threshold (else AP-THRESHOLD-UNMET).
    Non-APPROVE decisions are ignored (counted as not-approve)."""
    from design.hermes_fw08_authority_root_v1 import AuthorityRoot

    reasons: List[str] = []
    if not isinstance(authority_root, AuthorityRoot):
        return (False, ("AP-AUTHORITY-WRONG-TYPE",))

    authorised = set(str(a) for a in authority_root.authorised_approver_identities)
    producers = set(str(p) for p in producer_ids)
    now = _parse_utc(now_utc)

    distinct_approvers: List[str] = []
    seen_approvers: set = set()
    for rec in records:
        if not isinstance(rec, ApprovalRecord):
            reasons.append("AP-WRONG-TYPE")
            continue
        if rec.approval_record_digest != rec.recompute_digest():
            reasons.append("AP-DIGEST-TAMPER")
            continue
        if rec.approval_decision != "APPROVE":
            # Non-approve decisions are simply not counted toward the quorum.
            continue
        aid = str(rec.approver_id)
        if aid in _WILDCARD_APPROVERS:
            reasons.append("AP-WILDCARD-APPROVER")
            continue
        if rec.registry_id != registry_id or rec.registry_digest != registry_digest \
                or rec.policy_version != policy_version:
            reasons.append("AP-WRONG-DIGEST")
            continue
        if aid not in authorised:
            reasons.append("AP-APPROVER-UNAUTHORISED")
            continue
        # expiry.
        exp = _parse_utc(rec.expiry_utc)
        if str(rec.expiry_utc).strip() != "":
            if exp is None or now is None or now >= exp:
                reasons.append("AP-EXPIRED")
                continue
        if aid in producers:
            reasons.append("AP-PRODUCER-SELF-APPROVAL")
            continue
        if aid == str(registry_author_id):
            reasons.append("AP-AUTHOR-SELF-APPROVAL")
            continue
        if aid in seen_approvers:
            reasons.append("AP-DUPLICATE-APPROVER")
            continue
        seen_approvers.add(aid)
        distinct_approvers.append(aid)

    if len(distinct_approvers) < int(authority_root.approval_threshold):
        reasons.append("AP-THRESHOLD-UNMET")

    ok = len(reasons) == 0
    return (ok, tuple(sorted(set(reasons))))
