#!/usr/bin/env python3
"""HERMES FW-08 producer-trust contract + in-process unforgeable evidence seal v1 (PURE).

WO-HELM-HERMES-PR114-FW08-FINAL-PREBUILD-TRUST-BOUNDARY-CORRECTIONS-0001 (R-1 §7).
Owner: HERMES (Helm). Created (UTC): 2026-07-19. Contract version: 1.

WHY THIS EXISTS (R2D2 R-1 §7). An `evidence_checksum` proves INTEGRITY (the bytes were not altered) — it
does NOT prove AUTHORITY (that a TRUSTED producer wrote them). A public caller can recompute a checksum over
fabricated `result=True` fields. This module binds every gate-evidence record to a TYPED PRODUCER-TRUST
CONTRACT and seals it with an UNFORGEABLE in-process capability so that only the governed wrapper — the
holder of a fresh, per-run, never-persisted seal key — can produce records that reach readiness.

TRUST MECHANISM (Option B of the WO — in-process unforgeable record path). The governed wrapper constructs a
`GovernedEvidenceSealer` bound to an EPHEMERAL per-run key (`secrets.token_bytes`, 32 bytes). The key lives
only in memory for the duration of one build, is NEVER written to code / evidence / logs / disk, and is NOT a
credential (it grants access to nothing — it is a structural integrity nonce). The sealer HMAC-SHA256-signs
the canonical safe fields of each `GateEvidence` together with the producer identity. The evaluator verifies
the seal with the SAME in-process verifier the wrapper holds. A public caller who lacks the key CANNOT forge a
valid seal, and a caller who mints their OWN sealer produces a `producer_id` that is not in the governed
registry — either way readiness is unreachable. (A cryptographically-signed / attested producer contract is
the documented ALTERNATIVE for a future governed signing key; this WO ships NO live key and uses the
in-process path so that NO secret value is ever generated into code, evidence, or logs.)

PURE: no I/O, no subprocess, no network, no PERSISTED secrets, stdlib only, deterministic given its key.
NOT imported by runtime.
"""
from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass, field
from typing import Dict, Mapping, Optional, Sequence, Tuple

import design.hermes_fw08_readiness_evidence_v1 as ee

CONTRACT_VERSION = "1"
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_UTC_ISO_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?(\+00:00|Z)$")
_WILDCARDS = ("*", "?", "")

# The only sealing method this WO ships. A future governed signing key would add a
# "CRYPTOGRAPHIC_SIGNATURE_V1" method — NOT generated here (no live secret).
SEALING_METHOD_IN_PROCESS = "IN_PROCESS_HMAC_SHA256"
ADMISSIBLE_SEALING_METHODS = frozenset({SEALING_METHOD_IN_PROCESS, "CRYPTOGRAPHIC_SIGNATURE_V1"})

# Producer types recognised by the trust policy.
ADMISSIBLE_PRODUCER_TYPES = frozenset({"IN_PROCESS_GOVERNED_WRAPPER", "ATTESTED_EXTERNAL_TOOL"})


class ProducerTrustError(Exception):
    """Fail-closed error for a malformed / under-specified producer-trust contract."""


@dataclass(frozen=True)
class ProducerTrustContract:
    """§7 typed producer-trust binding. `checksum proves integrity NOT authority`: trust is conferred ONLY by
    this contract (producer identity + policy + candidate/source binding + lifecycle), never by a checksum."""

    contract_version: str
    producer_id: str
    producer_type: str
    trust_contract_version: str          # the producer's own declared contract version
    allowed_gate_ids: Tuple[str, ...]
    application: str
    module_identity: str                 # tool/module that produces the evidence
    content_digest: str                  # executable/content digest of the producer (where practical)
    source_version: str                  # source SHA the producer was built from
    trust_policy_version: str
    sealing_method: str                  # evidence signing / attestation method
    candidate_binding: str               # candidate_id the producer is authorised for
    source_binding: str                  # source SHA the evidence must bind to
    created_utc: str
    expiry_utc: str
    audit_reference: str

    def identity_fields(self) -> Dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "producer_id": self.producer_id,
            "producer_type": self.producer_type,
            "trust_contract_version": self.trust_contract_version,
            "allowed_gate_ids": list(self.allowed_gate_ids),
            "application": self.application,
            "module_identity": self.module_identity,
            "content_digest": self.content_digest,
            "source_version": self.source_version,
            "trust_policy_version": self.trust_policy_version,
            "sealing_method": self.sealing_method,
            "candidate_binding": self.candidate_binding,
            "source_binding": self.source_binding,
            "created_utc": self.created_utc,
            "expiry_utc": self.expiry_utc,
            "audit_reference": self.audit_reference,
        }

    def to_dict(self) -> Dict[str, object]:
        return self.identity_fields()

    def reference(self) -> str:
        """A stable, non-secret reference to this contract (sha256 of its identity fields)."""
        blob = json.dumps(self.identity_fields(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _no_wildcard(value: str, name: str) -> None:
    if not isinstance(value, str) or value.strip() in _WILDCARDS or "*" in value or "?" in value:
        raise ProducerTrustError(f"producer-trust {name} must be an exact value (no wildcard/blank)")


def validate_producer_trust(c: "ProducerTrustContract") -> None:
    """Fail closed on any malformed / wildcarded / under-specified / unknown-method contract."""
    if not isinstance(c, ProducerTrustContract):
        raise ProducerTrustError("not a ProducerTrustContract")
    if c.contract_version != CONTRACT_VERSION:
        raise ProducerTrustError("contract_version mismatch")
    for name in ("producer_id", "trust_contract_version", "application", "module_identity",
                 "content_digest", "source_version", "trust_policy_version", "candidate_binding",
                 "source_binding", "audit_reference"):
        _no_wildcard(getattr(c, name), name)
    if c.producer_type not in ADMISSIBLE_PRODUCER_TYPES:
        raise ProducerTrustError(f"unknown producer_type {c.producer_type!r}")
    if c.sealing_method not in ADMISSIBLE_SEALING_METHODS:
        raise ProducerTrustError(f"unknown sealing_method {c.sealing_method!r}")
    if not c.allowed_gate_ids:
        raise ProducerTrustError("allowed_gate_ids must be non-empty")
    unknown = [g for g in c.allowed_gate_ids if g not in ee._GATE_INDEX]
    if unknown:
        raise ProducerTrustError(f"allowed_gate_ids contains unknown gate(s): {unknown}")
    for name in ("created_utc", "expiry_utc"):
        if not _UTC_ISO_RE.match(getattr(c, name)):
            raise ProducerTrustError(f"{name} must be tz-aware UTC ISO-8601")


def _parse(value: str) -> Optional[datetime.datetime]:
    if not isinstance(value, str) or not _UTC_ISO_RE.match(value):
        return None
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass
class ProducerTrustRegistry:
    """The governed set of trusted producers. A producer not present here is UNTRUSTED even if its evidence
    is checksum-consistent and seal-consistent under some foreign key."""

    _by_id: Dict[str, ProducerTrustContract] = field(default_factory=dict)

    def register(self, c: "ProducerTrustContract") -> None:
        validate_producer_trust(c)
        self._by_id[c.producer_id] = c

    def get(self, producer_id: str) -> Optional["ProducerTrustContract"]:
        return self._by_id.get(producer_id)

    def is_trusted(self, producer_id: str, gate_id: str, *, now_utc: str,
                   candidate_id: str, source_sha: str) -> Tuple[bool, str]:
        """Return (True, "TRUSTED") iff `producer_id` is registered, well-formed, unexpired, authorised for
        `gate_id`, and bound to the expected candidate + source. Otherwise (False, reason)."""
        c = self._by_id.get(producer_id)
        if c is None:
            return (False, "PT-UNKNOWN-PRODUCER")
        try:
            validate_producer_trust(c)
        except ProducerTrustError as exc:
            return (False, f"PT-MALFORMED:{exc}")
        if gate_id not in c.allowed_gate_ids:
            return (False, "PT-GATE-NOT-ALLOWED")
        if c.candidate_binding != candidate_id:
            return (False, "PT-CANDIDATE-MISMATCH")
        if c.source_binding != source_sha:
            return (False, "PT-SOURCE-MISMATCH")
        exp = _parse(c.expiry_utc)
        now = _parse(now_utc)
        if exp is None or now is None:
            return (False, "PT-TIMESTAMP-UNPARSEABLE")
        if now >= exp:
            return (False, "PT-EXPIRED")
        return (True, "TRUSTED")


@dataclass(frozen=True)
class SealedGateEvidence:
    """A `GateEvidence` bound to a producer identity and an unforgeable seal. `evidence` alone is integrity
    proof; `seal` is the authority proof (only the key holder can compute it)."""

    evidence: "ee.GateEvidence"
    producer_id: str
    sealing_method: str
    seal: str                            # HMAC-SHA256 hex (in-process) or signature (attested)

    def to_dict(self) -> Dict[str, object]:
        return {
            "producer_id": self.producer_id,
            "sealing_method": self.sealing_method,
            "seal": self.seal,
            "evidence": self.evidence.to_dict(),
        }


def _seal_message(rec: "ee.GateEvidence", producer_id: str) -> bytes:
    """Canonical bytes the seal covers: the record's safe bound fields + its checksum + the producer id."""
    payload = {
        "producer_id": producer_id,
        "evidence_checksum": rec.evidence_checksum,
        "bound_fields": rec.bound_fields(),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


class GovernedEvidenceSealer:
    """In-process capability that seals gate evidence with an EPHEMERAL, never-persisted key.

    The key is generated fresh per instance via `secrets.token_bytes(32)` and is held ONLY in a name-mangled
    private attribute. There is NO getter, NO serialisation, and it never enters `to_dict`, evidence, or logs.
    A public caller cannot obtain the key, therefore cannot compute a valid seal, therefore cannot fabricate a
    trusted evidence bundle. This is the structural guarantee behind R-1 §7."""

    def __init__(self, *, producer_id: str, sealing_method: str = SEALING_METHOD_IN_PROCESS,
                 _key: Optional[bytes] = None) -> None:
        if sealing_method != SEALING_METHOD_IN_PROCESS:
            raise ProducerTrustError("GovernedEvidenceSealer only implements the in-process HMAC method")
        self.producer_id = producer_id
        self.sealing_method = sealing_method
        # EPHEMERAL integrity nonce — not a credential, never persisted/logged/serialised.
        self.__key = _key if _key is not None else secrets.token_bytes(32)

    def seal(self, rec: "ee.GateEvidence") -> SealedGateEvidence:
        mac = hmac.new(self.__key, _seal_message(rec, self.producer_id), hashlib.sha256).hexdigest()
        return SealedGateEvidence(evidence=rec, producer_id=self.producer_id,
                                  sealing_method=self.sealing_method, seal=mac)

    def verify(self, sealed: "SealedGateEvidence") -> bool:
        """Constant-time verification that `sealed` was produced by THIS sealer (same key + producer id)."""
        if not isinstance(sealed, SealedGateEvidence):
            return False
        if sealed.producer_id != self.producer_id or sealed.sealing_method != self.sealing_method:
            return False
        if not isinstance(sealed.seal, str) or not _HEX64_RE.match(sealed.seal):
            return False
        expected = hmac.new(self.__key, _seal_message(sealed.evidence, sealed.producer_id),
                            hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, sealed.seal)


@dataclass(frozen=True)
class TrustedReadinessVerdict:
    ready: bool
    reason_codes: Tuple[str, ...]

    def to_dict(self) -> Dict[str, object]:
        return {"contract_version": CONTRACT_VERSION, "ready": self.ready,
                "reason_codes": list(self.reason_codes)}


def evaluate_trusted_readiness(
    sealed_records: Sequence["SealedGateEvidence"],
    *,
    verifier: "GovernedEvidenceSealer",
    registry: "ProducerTrustRegistry",
    expected_source_sha: str,
    expected_image_id: str,
    expected_candidate_id: str,
    now_utc: str,
    max_evidence_age_hours: int = 24,
) -> TrustedReadinessVerdict:
    """The SOLE trusted-evidence readiness path. `ready` is True ONLY if EVERY record is (a) a sealed record
    from a TRUSTED, unexpired, gate-authorised, candidate/source-bound producer, (b) carries a seal that
    verifies under the in-process verifier the governed wrapper holds, AND (c) the underlying typed evidence
    passes the full structural / cross-gate / freshness evaluation. Bare booleans, checksum-only fabrications,
    and foreign-key seals are all unreachable."""
    fail = []
    if not sealed_records:
        return TrustedReadinessVerdict(False, ("PT-NO-EVIDENCE",))

    for sealed in sealed_records:
        if not isinstance(sealed, SealedGateEvidence):
            fail.append("PT-NOT-SEALED")
            continue
        gate_id = getattr(sealed.evidence, "gate_id", "")
        trusted, reason = registry.is_trusted(
            sealed.producer_id, gate_id, now_utc=now_utc,
            candidate_id=expected_candidate_id, source_sha=expected_source_sha,
        )
        if not trusted:
            fail.append(f"{reason}:{gate_id}")
            continue
        if not verifier.verify(sealed):
            fail.append(f"PT-SEAL-INVALID:{gate_id}")

    if fail:
        return TrustedReadinessVerdict(False, tuple(sorted(set(fail))))

    # Authority proven — now prove the typed evidence itself (structural, cross-gate, freshness).
    verdict = ee.evaluate_evidence(
        [s.evidence for s in sealed_records],
        expected_source_sha=expected_source_sha, expected_image_id=expected_image_id,
        now_utc=now_utc, max_evidence_age_hours=max_evidence_age_hours,
    )
    if not verdict.ready:
        return TrustedReadinessVerdict(False, tuple(sorted(set(verdict.reason_codes))))
    return TrustedReadinessVerdict(True, tuple())
