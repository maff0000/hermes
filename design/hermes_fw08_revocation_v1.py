#!/usr/bin/env python3
"""HERMES FW-08 F2-R1 revocation contract v1 (PURE, fixture-backed).

WO-HELM-HERMES-FW08-F2-R1-EXTERNAL-PRODUCER-REGISTRY-ROOT-OF-TRUST-IMPLEMENTATION-0001 (§15).
Owner: HERMES (Helm). Created (UTC): 2026-07-21. Contract version: 1.

WHY THIS EXISTS (F2-R1). Trust must be REVOCABLE. A revocation set names authority roots, registry manifests,
producer registrations, or approvers that are no longer trusted from an effective time onward. Activation
consults the set at `now` and fails closed on ANY match.

PURE: no I/O, no subprocess, no network, no secrets, stdlib only, deterministic. NOT imported by runtime.
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

REVOCATION_TYPES = frozenset({
    "AUTHORITY_ROOT",
    "REGISTRY_MANIFEST",
    "PRODUCER_REGISTRATION",
    "APPROVER",
})


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
class RevocationEntry:
    revocation_type: str
    target_id: str
    effective_utc: str
    reason_code: str
    source: str
    audit_reference: str
    revocation_digest: str

    def identity_fields(self) -> Dict[str, object]:
        return {
            "revocation_type": self.revocation_type,
            "target_id": self.target_id,
            "effective_utc": self.effective_utc,
            "reason_code": self.reason_code,
            "source": self.source,
            "audit_reference": self.audit_reference,
        }

    def to_dict(self) -> Dict[str, object]:
        d = self.identity_fields()
        d["revocation_digest"] = self.revocation_digest
        return d

    @staticmethod
    def compute_digest(identity_fields: Mapping[str, object]) -> str:
        return hashlib.sha256(_canonical(dict(identity_fields)).encode("utf-8")).hexdigest()

    def recompute_digest(self) -> str:
        return RevocationEntry.compute_digest(self.identity_fields())


def new_revocation(**kwargs: object) -> RevocationEntry:
    kwargs["revocation_digest"] = ""
    e0 = RevocationEntry(**kwargs)  # type: ignore[arg-type]
    return dataclasses.replace(e0, revocation_digest=e0.recompute_digest())


def _set_digest(entries: Sequence[RevocationEntry]) -> str:
    payload = [e.to_dict() for e in sorted(
        entries, key=lambda x: (x.revocation_type, x.target_id, x.effective_utc))]
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RevocationSet:
    entries: Tuple[RevocationEntry, ...]
    set_digest: str

    def is_revoked(self, *, revocation_type: str, target_id: str, at_utc: str) -> bool:
        """True iff a matching entry exists whose effective_utc <= at_utc (a well-formed entry with a
        recomputing digest). Fail-closed: a tampered entry is ignored (not counted as revoked here — the
        set_digest guards the whole set's integrity)."""
        at = _parse_utc(at_utc)
        if at is None:
            return False
        for e in self.entries:
            if e.revocation_type != revocation_type or e.target_id != str(target_id):
                continue
            if e.revocation_digest != e.recompute_digest():
                continue
            eff = _parse_utc(e.effective_utc)
            if eff is not None and eff <= at:
                return True
        return False

    def integrity_ok(self) -> bool:
        """True iff the whole set is untampered: the set_digest recomputes AND every entry's own digest
        recomputes. Activation MUST require this so a governance revocation cannot be silently neutralised by
        tampering an entry (which `is_revoked` would otherwise skip). Fail-closed: any tamper means the set is
        not trustworthy to WITHHOLD trust, so activation rejects rather than proceeding."""
        if self.set_digest != _set_digest(self.entries):
            return False
        return all(e.revocation_digest == e.recompute_digest() for e in self.entries)

    def revoked_reasons(self, *, revocation_type: str, target_id: str, at_utc: str) -> Tuple[str, ...]:
        """The reason codes of matching effective revocations at `at_utc` (sorted)."""
        at = _parse_utc(at_utc)
        out: List[str] = []
        if at is None:
            return tuple()
        for e in self.entries:
            if e.revocation_type != revocation_type or e.target_id != str(target_id):
                continue
            if e.revocation_digest != e.recompute_digest():
                continue
            eff = _parse_utc(e.effective_utc)
            if eff is not None and eff <= at:
                out.append(str(e.reason_code))
        return tuple(sorted(set(out)))


def new_revocation_set(entries: Sequence[RevocationEntry]) -> RevocationSet:
    """Build a RevocationSet with its `set_digest` computed over the sorted entries."""
    es = tuple(entries)
    return RevocationSet(entries=es, set_digest=_set_digest(es))
