#!/usr/bin/env python3
"""HERMES FW-08 F2-R3-FUT-1 external-authority + issuer-attestation readiness contract v1 (PURE, INERT).

WO-HELM-HERMES-FW08-F2-R3-FUT-1-EXTERNAL-AUTHORITY-AND-ISSUER-ATTESTATION-READINESS-CONTRACT-IMPLEMENTATION-0001
(§4A,§4B,§4C,§4D,§4E). Owner: HERMES (Helm). Created (UTC): 2026-07-27. Contract version: 1.

WHAT THIS IS. Lifecycle STAGE 3 (external-authority + issuer-attestation readiness) of the merged FW-08
real-proof lifecycle — the first FUTURE gate (FUT-1). It defines HOW HERMES will LATER reference and verify a
governed external authority + issuer-attestation boundary WITHOUT the HERMES app, a caller, or any co-resident
Python code ever possessing or inventing production signing authority. It closes the `L-F2R3-REFLECTIVE`
limitation ONLY at the CONTRACT boundary.

WHAT THIS IS NOT. This does NOT claim a real HSM / KMS / external signer / production isolation exists. Real
external authority is UNAVAILABLE and FAILS CLOSED: `production_external_authority_available()` is False and
REAL_CANDIDATE resolution ALWAYS returns fail-closed. There is NO signing/private material anywhere — the
readiness model, the issuer-attestation reference, and the verifier interface carry REFERENCES ONLY. The
verifier-only boundary EXPOSES no sign/issue/mint/key method. A caller can NEVER supply both an asserted
authority AND its verifier (no caller-owned trust loop). Classification (SYNTHETIC vs REAL) is set by the
MODULE issuer, never by the caller — a relabel breaks the seal.

MIRRORS F2-R1 (`hermes_fw08_trust_anchor_provider_v1._ModuleVerifier` / `hermes_fw08_authority_resolver_v1`):
  * `_ReadinessIssuer` — ephemeral in-process HMAC, name-mangled key, NO getter, NEVER serialised;
  * `_MODULE_ISSUER` singleton + `_READINESS_TOKEN` module-private sentinel;
  * `production_external_authority_available() -> False` (no fs/env/network lookup);
  * `resolve_authority_readiness` real mode fails closed, mirroring `ResolverNotConfigured` / `require_resolver`.

PURE except the ONE ephemeral in-process seal key (`secrets.token_bytes(32)`), mirroring `_ModuleVerifier` /
`_ProofIssuer`: no I/O, no subprocess, no network, no filesystem/env/registry lookup, stdlib only, deterministic
given a fixed key. NOT imported by runtime. No cycle: imports nothing from any FW-08 module.
"""
from __future__ import annotations

import abc
import dataclasses
import datetime
import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Tuple

CONTRACT_VERSION = "1"

LIFECYCLE_STAGE = 3

# §4A readiness states. Only AUTHORITY_READY is mintable here (synthetic, module-sealed); a real ready state is
# UNREACHABLE (external authority unavailable).
READINESS_STATES = frozenset({
    "AUTHORITY_READY",
    "AUTHORITY_UNAVAILABLE",
    "AUTHORITY_REVOKED",
    "AUTHORITY_INVALID",
})

# §4A authority classes. Only SYNTHETIC_TEST_AUTHORITY_READINESS is mintable here — a governed external class is
# never conferred in this WO (real external authority not configured).
AUTHORITY_CLASSES = frozenset({
    "SYNTHETIC_TEST_AUTHORITY_READINESS",
    "GOVERNED_EXTERNAL_AUTHORITY_READINESS",
    "REVOKED_AUTHORITY_READINESS",
    "UNTRUSTED_AUTHORITY_READINESS",
})

# Candidate modes reused conceptually from the lifecycle (stage-1/2 idiom). REAL_CANDIDATE always fails closed.
CANDIDATE_MODES = frozenset({"TEST_ONLY", "INERT_SIMULATION", "REAL_CANDIDATE"})

# Forbidden capability names on the verifier-only boundary — NONE of these may be exposed by a verifier or bound
# on a readiness object. A single one present = a trust loop / signing capability leak.
FORBIDDEN_CAPABILITY_NAMES = (
    "sign", "issue", "mint", "generate_key", "import_key", "import_private_key",
    "export_key", "export_private_key", "rotate_key", "approve", "approve_self",
    "self_register", "private_key", "trust_root",
)

# Reference-confusion markers: a *_reference value carrying any of these is an INLINE PAYLOAD, not a reference.
_INLINE_PAYLOAD_MARKERS = ("\n", "{", "}", "BEGIN", "-----BEGIN")
_MAX_REFERENCE_LEN = 200
# Secret-looking patterns a reference must never contain (private key / raw credential / bearer token).
_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"-----BEGIN [A-Z ]*KEY-----"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}"),
    re.compile(r"(?i)\b(secret|token|password|passwd|api[_-]?key|private[_-]?key)\s*[:=]\s*\S+"),
)


def _canonical(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _parse_utc(value: object) -> Optional[datetime.datetime]:
    """Parse an ISO-8601 UTC string. Returns None unless it parses AND carries an explicit UTC offset (+00:00)."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None or dt.utcoffset() != datetime.timedelta(0):
        return None
    return dt


def _looks_like_inline_payload(value: object) -> bool:
    """True iff `value` looks like an inline evidence/payload rather than a reference: contains a newline, JSON
    braces, a 'BEGIN' block, exceeds >200 chars, or matches a private-key/secret pattern."""
    if not isinstance(value, str):
        return False
    if len(value) > _MAX_REFERENCE_LEN:
        return True
    for marker in _INLINE_PAYLOAD_MARKERS:
        if marker in value:
            return True
    for pat in _SECRET_PATTERNS:
        if pat.search(value):
            return True
    return False


# ============================================================================ §4E module-owned issuer
class _ReadinessIssuer:
    """MODULE-OWNED in-process seal capability, mirroring F2-R1's `_ModuleVerifier` / `_ProofIssuer`. Holds an
    EPHEMERAL, name-mangled `self.__key` (`secrets.token_bytes(32)`) — NO getter, NEVER serialised, NEVER in any
    to_dict/log. Only the module builds/holds this; a caller cannot supply/forge a valid seal, and cannot
    relabel a synthetic readiness into a real one (the seal binds the classification)."""

    def __init__(self, *, _key: Optional[bytes] = None) -> None:
        self.__key = _key if _key is not None else secrets.token_bytes(32)

    def _seal(self, payload_bytes: bytes) -> str:
        return hmac.new(self.__key, payload_bytes, hashlib.sha256).hexdigest()

    def seal_readiness(self, readiness: "ExternalAuthorityReadiness") -> str:
        return self._seal(_seal_message(readiness))

    def verify(self, readiness: "ExternalAuthorityReadiness") -> bool:
        if not isinstance(readiness.seal, str):
            return False
        expected = self._seal(_seal_message(readiness))
        return hmac.compare_digest(expected, readiness.seal)


# The module's OWN issuer + private token sentinel. Neither is caller-constructible/substitutable.
_MODULE_ISSUER = _ReadinessIssuer()
_READINESS_TOKEN = object()   # module-private: only the module helper supplies it


# ============================================================================ §4A readiness model
@dataclass(frozen=True)
class ExternalAuthorityReadiness:
    """§4A the authority-readiness record (EV-03). Binds the full field set. Carries ONLY REFERENCES — NO
    signing / private material fields. `synthetic_or_real_classification` is set by the ISSUER (never the
    caller). `readiness_digest` binds every field except itself + the seal; `seal` is the module issuer's HMAC
    over the digest + classification, so a relabel to 'REAL' breaks the seal. A GOVERNED_EXTERNAL / REAL
    readiness is UNMINTABLE in this WO (external authority unavailable)."""

    contract_version: str
    lifecycle_stage: int
    readiness_state: str
    authority_class: str
    external_authority_reference: str
    trust_anchor_reference: str
    resolver_reference: str
    issuer_identity_reference: str
    attestation_policy_reference: str
    evidence_references: Tuple[str, ...]
    issued_or_observed_utc: str
    validity_end_utc: str
    provenance: str
    fault_code: str
    fail_closed_reason: str
    synthetic_or_real_classification: str
    readiness_digest: str
    seal: str

    def identity_fields(self) -> Dict[str, object]:
        """Canonical fields the readiness_digest covers (EXCLUDES readiness_digest AND seal)."""
        return {
            "contract_version": self.contract_version,
            "lifecycle_stage": self.lifecycle_stage,
            "readiness_state": self.readiness_state,
            "authority_class": self.authority_class,
            "external_authority_reference": self.external_authority_reference,
            "trust_anchor_reference": self.trust_anchor_reference,
            "resolver_reference": self.resolver_reference,
            "issuer_identity_reference": self.issuer_identity_reference,
            "attestation_policy_reference": self.attestation_policy_reference,
            "evidence_references": list(self.evidence_references),
            "issued_or_observed_utc": self.issued_or_observed_utc,
            "validity_end_utc": self.validity_end_utc,
            "provenance": self.provenance,
            "fault_code": self.fault_code,
            "fail_closed_reason": self.fail_closed_reason,
            "synthetic_or_real_classification": self.synthetic_or_real_classification,
        }

    def to_dict(self) -> Dict[str, object]:
        d = self.identity_fields()
        d["readiness_digest"] = self.readiness_digest
        d["seal"] = self.seal
        return d

    @staticmethod
    def compute_digest(identity_fields: Mapping[str, object]) -> str:
        return hashlib.sha256(_canonical(dict(identity_fields)).encode("utf-8")).hexdigest()

    def recompute_digest(self) -> str:
        return ExternalAuthorityReadiness.compute_digest(self.identity_fields())


def _seal_message(readiness: ExternalAuthorityReadiness) -> bytes:
    """Bytes the issuer HMAC covers: the readiness digest + the classification (so a relabelled classification
    breaks the seal)."""
    return (readiness.readiness_digest + "\x00" + readiness.synthetic_or_real_classification).encode("utf-8")


class EARPromotionForbidden(Exception):
    """C-PR122-EAR-PROMOTION control B — raised when a caller asks the public constructor to confer a non-
    synthetic (real/governed) classification. Public construction is SYNTHETIC-ONLY in this inert stage; a real
    classification is not caller-conferrable and is not silently repaired — it is rejected explicitly."""


def new_external_authority_readiness(**kwargs: object) -> ExternalAuthorityReadiness:
    """Blessed SYNTHETIC-ONLY constructor: builds an ExternalAuthorityReadiness with its readiness_digest
    computed over the identity fields and its seal applied by the MODULE issuer. Blesses INTEGRITY only —
    validity is decided by `validate_authority_readiness`. Defaults fill inert/empty fault fields for a READY
    record.

    C-PR122-EAR-PROMOTION control B: the caller may NOT confer a real/governed classification through this
    public path. Any `synthetic_or_real_classification` other than 'SYNTHETIC' is REJECTED (not repaired) with
    `EARPromotionForbidden`. A real classification can only ever arise from a genuinely available governed
    production authority (unavailable in this WO), never from caller-supplied construction."""
    _cls = kwargs.get("synthetic_or_real_classification", "SYNTHETIC")
    if str(_cls) != "SYNTHETIC":
        raise EARPromotionForbidden(
            "public construction is synthetic-only; caller-selected classification "
            f"{_cls!r} is not permitted (C-PR122-EAR-PROMOTION)")
    kwargs.setdefault("synthetic_or_real_classification", "SYNTHETIC")
    kwargs.setdefault("contract_version", CONTRACT_VERSION)
    kwargs.setdefault("lifecycle_stage", LIFECYCLE_STAGE)
    kwargs.setdefault("fault_code", "")
    kwargs.setdefault("fail_closed_reason", "")
    evrefs = kwargs.get("evidence_references", ())
    kwargs["evidence_references"] = tuple(evrefs) if evrefs else tuple()
    kwargs["readiness_digest"] = ""
    kwargs["seal"] = ""
    r0 = ExternalAuthorityReadiness(**kwargs)  # type: ignore[arg-type]
    r1 = dataclasses.replace(r0, readiness_digest=r0.recompute_digest())
    return dataclasses.replace(r1, seal=_MODULE_ISSUER.seal_readiness(r1))


def production_external_authority_available() -> bool:
    """The PRODUCTION external authority (real HSM/KMS/external signer/governed root) is NOT configured in this
    WO. Real authority-readiness therefore has NO source here — REAL_CANDIDATE always fails closed. This
    performs NO filesystem / /etc / environment / network lookup: it is unconditionally False."""
    return False


def resolve_authority_readiness(
    *,
    mode: str,
    external_authority_reference: str,
    trust_anchor_reference: str,
    resolver_reference: str,
    issuer_identity_reference: str,
    attestation_policy_reference: str,
    evidence_references: Tuple[str, ...],
    now_utc: str,
    validity_end_utc: str,
    provenance: str,
    test_token: object = None,
) -> Tuple[Optional[ExternalAuthorityReadiness], Tuple[str, ...]]:
    """§4E obtain an authority-readiness record, or (None, reasons). Fail-closed. The classification is set by
    THIS module, never the caller.

    REAL_CANDIDATE: ALWAYS (None, ('EAR-REAL-AUTHORITY-UNAVAILABLE',
        'F2R3-FUT1-REAL-EXTERNAL-AUTHORITY-NOT-CONFIGURED')) — no fallback, no synthetic substitution, no
        /etc/env/filesystem/network lookup (mirrors F2-R1 ResolverNotConfigured / require_resolver).

    TEST_ONLY / INERT_SIMULATION: require `test_token is _READINESS_TOKEN` (only the module helper supplies it)
        else (None, ('EAR-SYNTHETIC-REQUIRES-MODULE-TOKEN',)); then mint readiness_state=AUTHORITY_READY,
        authority_class=SYNTHETIC_TEST_AUTHORITY_READINESS, synthetic_or_real_classification='SYNTHETIC', sealed
        by _MODULE_ISSUER. Classification is set by the module, NEVER the caller."""
    if mode == "REAL_CANDIDATE":
        # No production external authority in this WO. NO synthetic substitution, NO fallback, NO fs/env/network.
        return (None, (
            "EAR-REAL-AUTHORITY-UNAVAILABLE",
            "F2R3-FUT1-REAL-EXTERNAL-AUTHORITY-NOT-CONFIGURED",
        ))

    if mode not in ("TEST_ONLY", "INERT_SIMULATION"):
        return (None, ("EAR-INVALID-MODE",))

    if test_token is not _READINESS_TOKEN:
        return (None, ("EAR-SYNTHETIC-REQUIRES-MODULE-TOKEN",))

    readiness = new_external_authority_readiness(
        readiness_state="AUTHORITY_READY",                       # module sets it, never the caller.
        authority_class="SYNTHETIC_TEST_AUTHORITY_READINESS",    # module sets it, never the caller.
        external_authority_reference=str(external_authority_reference),
        trust_anchor_reference=str(trust_anchor_reference),
        resolver_reference=str(resolver_reference),
        issuer_identity_reference=str(issuer_identity_reference),
        attestation_policy_reference=str(attestation_policy_reference),
        evidence_references=tuple(evidence_references or ()),
        issued_or_observed_utc=str(now_utc),
        validity_end_utc=str(validity_end_utc),
        provenance=str(provenance),
        synthetic_or_real_classification="SYNTHETIC",            # module sets it, never the caller.
    )
    return (readiness, tuple())


def new_synthetic_test_readiness(**kwargs: object) -> Tuple[Optional[ExternalAuthorityReadiness], Tuple[str, ...]]:
    """Module helper: mint a synthetic AUTHORITY_READY record by internally supplying `_READINESS_TOKEN`. Tests
    call this WITHOUT ever seeing the token; a caller calling `resolve_authority_readiness` with no token gets
    EAR-SYNTHETIC-REQUIRES-MODULE-TOKEN (proved by a companion test). REAL_CANDIDATE is refused here too."""
    kwargs.setdefault("mode", "TEST_ONLY")
    kwargs["test_token"] = _READINESS_TOKEN
    return resolve_authority_readiness(**kwargs)  # type: ignore[arg-type]


# ============================================================================ §4B validator
def validate_authority_readiness(
    readiness: object,
    *,
    now_utc: str,
    real_mode: bool,
    application: str = "hermes",
) -> Tuple[str, ...]:
    """§4B return () iff the readiness record is valid, else a sorted tuple of EAR-* reason codes. Reject —
    never repair. Fail-closed.

    Rejects:
      * not an ExternalAuthorityReadiness                    -> EAR-WRONG-TYPE
      * readiness_digest does not recompute (tamper)         -> EAR-DIGEST-TAMPER
      * seal invalid under _MODULE_ISSUER (caller-forged/relabelled) -> EAR-SEAL-INVALID
      * real_mode and classification != 'REAL' (synthetic in real mode; a relabel to 'REAL' breaks the seal)
                                                             -> EAR-SYNTHETIC-IN-REAL-MODE
      * readiness_state not in READINESS_STATES              -> EAR-BAD-STATE
      * authority_class not in AUTHORITY_CLASSES             -> EAR-BAD-CLASS
      * readiness_state AUTHORITY_REVOKED                    -> EAR-REVOKED
      * readiness_state AUTHORITY_INVALID                    -> EAR-INVALID
      * missing external_authority_reference                 -> EAR-AUTHORITY-REF-MISSING
      * missing trust_anchor_reference                       -> EAR-TRUST-ANCHOR-REF-MISSING
      * missing issuer_identity_reference                    -> EAR-ISSUER-REF-MISSING
      * missing attestation_policy_reference                 -> EAR-ATTESTATION-POLICY-REF-MISSING
      * missing/empty evidence_references                    -> EAR-EVIDENCE-REF-MISSING
      * non-UTC issued/validity                              -> EAR-UTC-INVALID
      * expired vs now                                       -> EAR-EXPIRED
      * unsupported contract_version                         -> EAR-CONTRACT-VERSION
      * lifecycle_stage != 3                                 -> EAR-WRONG-STAGE
      * missing provenance                                   -> EAR-PROVENANCE-MISSING
      * a *_reference that is actually inline evidence/payload -> EAR-REFERENCE-IS-INLINE-PAYLOAD
      * caller-supplied signing material present              -> EAR-CALLER-SIGNING-MATERIAL
    """
    if not isinstance(readiness, ExternalAuthorityReadiness):
        return ("EAR-WRONG-TYPE",)

    # C-PR122-EAR-PROMOTION control A — UNCONDITIONAL real-mode fail-closed gate. Real-mode readiness cannot be
    #   accepted while a governed production external authority is not genuinely available. This gate runs
    #   FIRST and is INDEPENDENT of the seal, the classification value, the constructor used, the issuer
    #   identity, caller assertions, object provenance, reflective access, or monkey-patching of unrelated
    #   objects: a valid-looking seal (even one freshly minted through the public constructor or the reflective
    #   module issuer) NEVER substitutes for actual external-authority availability. A module seal is integrity
    #   metadata for synthetic contract objects — it is NOT external authority, a trust anchor, a production
    #   issuer, HSM/KMS evidence, or permission to enter real mode. Real mode is NOT downgraded to synthetic.
    if real_mode and not production_external_authority_available():
        return ("EAR-PRODUCTION-AUTHORITY-UNAVAILABLE",)

    reasons: List[str] = []

    # caller-supplied signing material: the object (or any field) exposing a forbidden capability.
    if _exposes_signing_material(readiness):
        reasons.append("EAR-CALLER-SIGNING-MATERIAL")

    # digest tamper (a body-field tamper breaks the digest recompute).
    if readiness.readiness_digest != readiness.recompute_digest():
        reasons.append("EAR-DIGEST-TAMPER")

    # seal — a caller-forged / copied / relabelled record fails (the ephemeral key is module-owned). The seal
    # binds the classification, so relabelling synthetic->'REAL' invalidates the seal here.
    if not _MODULE_ISSUER.verify(readiness):
        reasons.append("EAR-SEAL-INVALID")

    # real mode: a synthetic (or any non-'REAL') classification never passes — and a real record cannot exist
    # here (external authority unavailable).
    if real_mode and readiness.synthetic_or_real_classification != "REAL":
        reasons.append("EAR-SYNTHETIC-IN-REAL-MODE")

    if readiness.readiness_state not in READINESS_STATES:
        reasons.append("EAR-BAD-STATE")
    if readiness.authority_class not in AUTHORITY_CLASSES:
        reasons.append("EAR-BAD-CLASS")
    if readiness.readiness_state == "AUTHORITY_REVOKED":
        reasons.append("EAR-REVOKED")
    if readiness.readiness_state == "AUTHORITY_INVALID":
        reasons.append("EAR-INVALID")

    # reference presence (REFERENCES only — never inline payloads).
    ref_checks = (
        (readiness.external_authority_reference, "EAR-AUTHORITY-REF-MISSING"),
        (readiness.trust_anchor_reference, "EAR-TRUST-ANCHOR-REF-MISSING"),
        (readiness.issuer_identity_reference, "EAR-ISSUER-REF-MISSING"),
        (readiness.attestation_policy_reference, "EAR-ATTESTATION-POLICY-REF-MISSING"),
    )
    for value, code in ref_checks:
        if not str(value or "").strip():
            reasons.append(code)

    if not readiness.evidence_references or not any(
            str(x or "").strip() for x in readiness.evidence_references):
        reasons.append("EAR-EVIDENCE-REF-MISSING")

    # evidence/reference confusion: any reference (or an evidence ref) that is actually an inline payload.
    payload_candidates = [
        readiness.external_authority_reference,
        readiness.trust_anchor_reference,
        readiness.resolver_reference,
        readiness.issuer_identity_reference,
        readiness.attestation_policy_reference,
    ] + list(readiness.evidence_references)
    if any(_looks_like_inline_payload(v) for v in payload_candidates):
        reasons.append("EAR-REFERENCE-IS-INLINE-PAYLOAD")

    # UTC discipline.
    issued = _parse_utc(readiness.issued_or_observed_utc)
    validity = _parse_utc(readiness.validity_end_utc)
    now = _parse_utc(now_utc)
    if issued is None or validity is None:
        reasons.append("EAR-UTC-INVALID")
    elif now is not None and now >= validity:
        reasons.append("EAR-EXPIRED")

    if str(readiness.contract_version) != CONTRACT_VERSION:
        reasons.append("EAR-CONTRACT-VERSION")
    if readiness.lifecycle_stage != LIFECYCLE_STAGE:
        reasons.append("EAR-WRONG-STAGE")
    if not str(readiness.provenance or "").strip():
        reasons.append("EAR-PROVENANCE-MISSING")

    return tuple(sorted(set(reasons)))


def _exposes_signing_material(obj: object) -> bool:
    """True iff `obj` (or any of its non-callable field values, if it is a dataclass) exposes ANY forbidden
    capability name (sign/issue/mint/private_key/...). A frozen ExternalAuthorityReadiness carries only string
    references, so a genuine record returns False; a caller-forged shape that bolts on a `sign`/`private_key`
    attribute is caught here."""
    for name in FORBIDDEN_CAPABILITY_NAMES:
        if hasattr(obj, name):
            return True
    if dataclasses.is_dataclass(obj):
        for f in dataclasses.fields(obj):
            val = getattr(obj, f.name, None)
            if isinstance(val, (str, int, float, bool, tuple, type(None))):
                continue
            for name in FORBIDDEN_CAPABILITY_NAMES:
                if hasattr(val, name):
                    return True
    return False


# ============================================================================ §4C verifier-only boundary
class ExternalAuthorityUnavailable(Exception):
    """Raised when no production external verifier is configured — real F2-R3-FUT-1 integration outstanding."""


class ExternalAuthorityVerifier(abc.ABC):
    """§4C the VERIFIER-ONLY external boundary. It exposes ONLY `verify_authority_evidence` — it can VERIFY that
    a governed external authority attests a piece of evidence, and NOTHING ELSE. It has NO
    sign/issue/mint/generate_key/import_key/export_key/rotate_key/approve_self/self_register method: HERMES /
    a caller / co-resident code can never obtain or invent signing authority through this interface."""

    @abc.abstractmethod
    def verify_authority_evidence(
        self, *, readiness: object, evidence_reference: str, now_utc: str,
    ) -> Tuple[bool, Tuple[str, ...]]:
        raise NotImplementedError


class ProductionExternalAuthorityVerifier(ExternalAuthorityVerifier):
    """The GOVERNED external verifier. In this WO it is STRUCTURALLY non-functional — verification raises
    `ExternalAuthorityUnavailable`. Real verification therefore has NO source here (real external authority
    outstanding). Mirrors F2-R1's `ProductionAuthorityResolver` raising `ResolverNotConfigured`."""

    def __init__(self, *, verifier_identity: str = "hermes-fw08-production-external-authority-verifier",
                 verifier_version: str = "0-unconfigured") -> None:
        self._identity = verifier_identity
        self._version = verifier_version

    @property
    def verifier_identity(self) -> str:
        return self._identity

    @property
    def verifier_version(self) -> str:
        return self._version

    def verify_authority_evidence(
        self, *, readiness: object, evidence_reference: str, now_utc: str,
    ) -> Tuple[bool, Tuple[str, ...]]:
        raise ExternalAuthorityUnavailable(
            "no production external authority verifier configured — F2-R3-FUT-1 real integration outstanding")


class SyntheticExternalAuthorityVerifier(ExternalAuthorityVerifier):
    """The SYNTHETIC test verifier (module-token gated; deterministic). It verifies that an evidence reference
    is among the readiness record's governed evidence references — it does NOT sign, issue, or mint anything.
    NEVER usable in real mode (a caller cannot supply it as the production verifier)."""

    def __init__(self, *, _token: object = None,
                 verifier_identity: str = "hermes-fw08-synthetic-external-authority-verifier",
                 verifier_version: str = "1") -> None:
        if _token is not _READINESS_TOKEN:
            raise ExternalAuthorityUnavailable(
                "synthetic external verifier is module-token gated — a caller cannot construct it")
        self._identity = verifier_identity
        self._version = verifier_version

    @property
    def verifier_identity(self) -> str:
        return self._identity

    @property
    def verifier_version(self) -> str:
        return self._version

    def verify_authority_evidence(
        self, *, readiness: object, evidence_reference: str, now_utc: str,
    ) -> Tuple[bool, Tuple[str, ...]]:
        if not isinstance(readiness, ExternalAuthorityReadiness):
            return (False, ("EAR-VERIFY-WRONG-TYPE",))
        if not _MODULE_ISSUER.verify(readiness):
            return (False, ("EAR-VERIFY-SEAL-INVALID",))
        if str(evidence_reference) not in {str(x) for x in readiness.evidence_references}:
            return (False, ("EAR-VERIFY-EVIDENCE-NOT-GOVERNED",))
        return (True, tuple())


def new_synthetic_verifier() -> SyntheticExternalAuthorityVerifier:
    """Module helper: build a synthetic verifier by internally supplying `_READINESS_TOKEN`. Tests call this
    WITHOUT ever seeing the token; a caller cannot construct `SyntheticExternalAuthorityVerifier` directly."""
    return SyntheticExternalAuthorityVerifier(_token=_READINESS_TOKEN)


def require_verifier_only(candidate_obj: object) -> Tuple[str, ...]:
    """§4C fail-closed gate. Returns () iff `candidate_obj` is an ExternalAuthorityVerifier subclass instance
    that exposes NONE of the forbidden capability names. Rejects:
      * an object exposing ANY forbidden capability (sign/issue/mint/generate_key/import_private_key/
        export_private_key/rotate_key/approve/self_register/trust_root as a settable attr)
                                                             -> EAR-VERIFIER-EXPOSES-SIGNING
      * an object that is not an ExternalAuthorityVerifier instance -> EAR-NOT-VERIFIER-INTERFACE
    """
    reasons: List[str] = []
    for name in FORBIDDEN_CAPABILITY_NAMES:
        if hasattr(candidate_obj, name):
            reasons.append("EAR-VERIFIER-EXPOSES-SIGNING")
            break
    if not isinstance(candidate_obj, ExternalAuthorityVerifier):
        reasons.append("EAR-NOT-VERIFIER-INTERFACE")
    return tuple(sorted(set(reasons)))


def select_production_verifier() -> Tuple[Optional[ExternalAuthorityVerifier], Tuple[str, ...]]:
    """§4C the production verifier is obtained from the MODULE boundary, NOT from the caller. In this WO no
    production verifier is configured -> (None, ('EAR-PRODUCTION-VERIFIER-UNAVAILABLE',)): production
    verification cannot proceed (fail closed). NO fs/env/network lookup."""
    if not production_external_authority_available():
        return (None, ("EAR-PRODUCTION-VERIFIER-UNAVAILABLE",))
    # unreachable in this WO; defence in depth.
    return (None, ("EAR-PRODUCTION-VERIFIER-UNAVAILABLE",))


def verify_readiness_no_caller_trust_loop(
    *,
    readiness: object,
    caller_verifier: object,
    mode: str,
) -> Tuple[str, ...]:
    """§4C a caller CANNOT supply BOTH the asserted authority (the readiness) AND its verifier. In real mode a
    caller-supplied verifier is rejected -> ('EAR-CALLER-OWNED-TRUST-LOOP',): the verifier must come from the
    module/external boundary, which is UNAVAILABLE here -> fail closed. Returns () only when no trust loop is
    present AND (in real mode) the module boundary yields a verifier (it never does here)."""
    reasons: List[str] = []
    if mode == "REAL_CANDIDATE":
        if caller_verifier is not None:
            reasons.append("EAR-CALLER-OWNED-TRUST-LOOP")
        # the module boundary is the ONLY source of a real verifier — and it is unavailable in this WO.
        _verifier, sel_reasons = select_production_verifier()
        if _verifier is None:
            reasons.extend(sel_reasons)
    return tuple(sorted(set(reasons)))


# ============================================================================ §4D issuer-attestation boundary
@dataclass(frozen=True)
class IssuerAttestationReference:
    """§4D a REFERENCE to an issuer-attestation boundary. NO signed payload / private material / mock credential
    is embedded — every field is a reference or a status string. `reference_digest` binds every field except
    itself."""

    contract_version: str
    issuer_identity: str
    authority_root_reference: str
    attestation_evidence_reference: str
    evidence_location_reference: str
    verifier_result: str
    lifecycle_readiness: str
    reference_digest: str

    def identity_fields(self) -> Dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "issuer_identity": self.issuer_identity,
            "authority_root_reference": self.authority_root_reference,
            "attestation_evidence_reference": self.attestation_evidence_reference,
            "evidence_location_reference": self.evidence_location_reference,
            "verifier_result": self.verifier_result,
            "lifecycle_readiness": self.lifecycle_readiness,
        }

    def to_dict(self) -> Dict[str, object]:
        d = self.identity_fields()
        d["reference_digest"] = self.reference_digest
        return d

    @staticmethod
    def compute_digest(identity_fields: Mapping[str, object]) -> str:
        return hashlib.sha256(_canonical(dict(identity_fields)).encode("utf-8")).hexdigest()

    def recompute_digest(self) -> str:
        return IssuerAttestationReference.compute_digest(self.identity_fields())


def new_issuer_attestation_reference(**kwargs: object) -> IssuerAttestationReference:
    """Blessed constructor: builds an IssuerAttestationReference with its reference_digest computed over the
    identity fields. References only — no signed payload / credential embedded."""
    kwargs.setdefault("contract_version", CONTRACT_VERSION)
    kwargs["reference_digest"] = ""
    r0 = IssuerAttestationReference(**kwargs)  # type: ignore[arg-type]
    return dataclasses.replace(r0, reference_digest=r0.recompute_digest())


def validate_issuer_attestation_reference(
    ref: object,
    *,
    now_utc: str,
) -> Tuple[str, ...]:
    """§4D return () iff the issuer-attestation reference is valid, else sorted EAR-IA-* reason codes. Reject —
    never repair. Fail-closed.

    Rejects:
      * not an IssuerAttestationReference                    -> EAR-IA-WRONG-TYPE
      * reference_digest does not recompute (tamper)         -> EAR-IA-DIGEST-TAMPER
      * missing issuer_identity                              -> EAR-IA-ISSUER-MISSING
      * missing authority_root_reference                     -> EAR-IA-AUTHORITY-ROOT-MISSING
      * missing attestation_evidence_reference               -> EAR-IA-ATTESTATION-EVIDENCE-MISSING
      * a reference that is actually an inline payload        -> EAR-IA-REFERENCE-IS-INLINE-PAYLOAD
      * verifier_result asserting self-issuance / 'SIGNED_BY_HERMES' -> EAR-IA-SELF-ISSUED
    """
    if not isinstance(ref, IssuerAttestationReference):
        return ("EAR-IA-WRONG-TYPE",)

    reasons: List[str] = []

    if ref.reference_digest != ref.recompute_digest():
        reasons.append("EAR-IA-DIGEST-TAMPER")

    if not str(ref.issuer_identity or "").strip():
        reasons.append("EAR-IA-ISSUER-MISSING")
    if not str(ref.authority_root_reference or "").strip():
        reasons.append("EAR-IA-AUTHORITY-ROOT-MISSING")
    if not str(ref.attestation_evidence_reference or "").strip():
        reasons.append("EAR-IA-ATTESTATION-EVIDENCE-MISSING")

    payload_candidates = [
        ref.issuer_identity,
        ref.authority_root_reference,
        ref.attestation_evidence_reference,
        ref.evidence_location_reference,
    ]
    if any(_looks_like_inline_payload(v) for v in payload_candidates):
        reasons.append("EAR-IA-REFERENCE-IS-INLINE-PAYLOAD")

    # self-issuance: HERMES / a caller must NEVER be the attesting authority. Any self-signed result is refused.
    vr = str(ref.verifier_result or "").strip().upper()
    if vr in ("SIGNED_BY_HERMES", "SELF_ISSUED", "SELF_SIGNED", "SIGNED_BY_CALLER") \
            or "SIGNED_BY_HERMES" in vr or "SELF_ISSUED" in vr or "SELF_SIGNED" in vr:
        reasons.append("EAR-IA-SELF-ISSUED")

    return tuple(sorted(set(reasons)))


# ============================================================================ D-PR120-EV-TAIL (stage 3)
def eventual_evidence_record_ids() -> Dict[str, str]:
    """D-PR120-EV-TAIL (stage 3). The downstream evidence record identifier this stage-3 contract DEFINES,
    extending the EV-01/EV-02 tail so later gates can reference the authority-readiness record unambiguously.
    Stable, deterministic, no I/O."""
    return {
        "authority_readiness": "EV-03-AUTHORITY-READINESS",
    }
