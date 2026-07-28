#!/usr/bin/env python3
"""HERMES FW-08 F2-R3-FUT-2 build + OCI-inspection producer-registration contract v1 (PURE, INERT).

WO-HELM-HERMES-FW08-F2-R3-FUT-2-BUILD-AND-OCI-INSPECTION-PRODUCER-REGISTRATION-CONTRACT-IMPLEMENTATION-0001
(§4A-§4G + distinctness + approval + evidence). Owner: HERMES (Helm). Created (UTC): 2026-07-28.
Contract version: 1.

WHAT THIS IS. Lifecycle STAGE 4 (build-producer registration) + STAGE 5 (OCI-inspection-producer
registration) of the merged FW-08 real-proof lifecycle — the SECOND future gate (FUT-2). It defines the inert
contract for registering TWO DISTINCT producer ROLES so that later (Stage-B) a build producer and an
OCI-inspection producer are STRUCTURALLY separated: a build producer emits BUILD_EVIDENCE only, an OCI
producer emits OCI_INSPECTION_EVIDENCE only, and the two are execution-distinct across all six F2-R3 dimensions
(execution identity, registration, runner, executable boundary, evidence reference, provenance).

WHAT THIS IS NOT. This does NOT register a real producer, activate a registry, build/inspect an image, or
touch runtime. Real producer registration is UNAVAILABLE and FAILS CLOSED:
`production_producer_registry_configured()` is False, `configured_registry_authority_identity()` is None, and
REAL_CANDIDATE registration ALWAYS returns fail-closed. FUT-2 is NOT external-verifier integration (FUT-7 /
stages 17-18), NOT the evidence store (FUT-3 / gate 6), and NOT Stage-B.

CRITICAL LESSON FROM FUT-1 (baked in from the start). Real acceptance must NOT root in a single primitive —
not a caller-set classification, not a monkeypatchable Boolean, not a reflectively-forgeable local seal. The
DECISIVE real-registration root here is a MODULE-CONTROLLED configured authority that is UNAVAILABLE, anchored
by a NON-Boolean identity (`configured_registry_authority_identity()` returns None), INDEPENDENT of any local
seal/receipt and of any single Boolean. A caller cannot supply that identity. The local module seal
(`_MODULE_ISSUER`) is object-integrity metadata ONLY — NEVER a production trust root. The real-registration
gate FIRES (appends a reason, guaranteeing rejection) BEFORE any valid seal could cause acceptance, so no real
record ever validates even when `production_producer_registry_configured` is monkeypatched True.

MIRRORS the FUT-1 pattern (`hermes_fw08_external_authority_readiness_v1`): `_ModuleRegistrationIssuer` /
ephemeral name-mangled HMAC, `_MODULE_ISSUER`, `_REGISTRATION_TOKEN`, `production_producer_registry_configured()
-> False`, `configured_registry_authority_identity() -> None`, token-gated synthetic mint, reject-not-repair
validators. REUSES `hermes_fw08_execution_identity_v1.evaluate_execution_independence` for distinctness and
`hermes_fw08_producer_independence_v1` codes/evidence-type constants for role separation.

PURE except the ONE ephemeral in-process seal key (`secrets.token_bytes(32)`): no I/O, no subprocess, no
network, no filesystem/env/registry lookup, stdlib only, deterministic given a fixed key. NOT imported by
runtime. Imports two sibling DESIGN modules (execution identity, producer independence) — both pure, no cycle.
"""
from __future__ import annotations

import dataclasses
import datetime
import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Tuple

import design.hermes_fw08_execution_identity_v1 as ei
import design.hermes_fw08_producer_independence_v1 as pind

CONTRACT_VERSION = "1"

# §4A the two producer ROLES this contract registers. A build producer and an OCI-inspection producer.
PRODUCER_ROLES = frozenset({"BUILD_PRODUCER", "OCI_INSPECTION_PRODUCER"})

# §4A the two evidence CLASSES. Each role is permitted EXACTLY ONE.
EVIDENCE_CLASSES = frozenset({"BUILD_EVIDENCE", "OCI_INSPECTION_EVIDENCE"})

# §4A role -> the single evidence class it is permitted to emit.
ROLE_PERMITTED_EVIDENCE_CLASS: Dict[str, str] = {
    "BUILD_PRODUCER": "BUILD_EVIDENCE",
    "OCI_INSPECTION_PRODUCER": "OCI_INSPECTION_EVIDENCE",
}

# §4A registration states. Only SYNTHETIC_UNREGISTERED is mintable here — a real/registered state is
# UNREACHABLE (real producer registration not configured).
REGISTRATION_STATES = frozenset({
    "SYNTHETIC_UNREGISTERED",
    "REAL_REGISTRATION_UNAVAILABLE",
    "REVOKED_REGISTRATION",
    "INVALID_REGISTRATION",
})

# Registration modes reused conceptually from the lifecycle. REAL_CANDIDATE always fails closed.
REGISTRATION_MODES = frozenset({"TEST_ONLY", "INERT_SIMULATION", "REAL_CANDIDATE"})

# Reference-confusion markers: a reference value carrying any of these is an INLINE PAYLOAD, not a reference.
_INLINE_PAYLOAD_MARKERS = ("\n", "{", "}", "BEGIN", "-----BEGIN")
_MAX_REFERENCE_LEN = 200
# Secret-looking patterns a reference must never contain.
_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"-----BEGIN [A-Z ]*KEY-----"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}"),
    re.compile(r"(?i)\b(secret|token|password|passwd|api[_-]?key|private[_-]?key)\s*[:=]\s*\S+"),
)

# Non-canonical reference markers (traversal / encoding / non-canonical path). A governed reference PATH (the
# part after an optional 'scheme://' prefix and before an optional '#...' binding suffix) must carry none of
# these.
_NONCANONICAL_MARKERS = ("%", "\\", "..", "//")
_REF_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.\-]*://")


def _canonical(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _parse_utc(value: object) -> Optional[datetime.datetime]:
    """Parse an ISO-8601 UTC string. Returns None unless it parses AND carries an explicit UTC offset."""
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


def _is_noncanonical_reference(value: object) -> bool:
    """True iff `value` is a non-canonical reference: its PATH contains a traversal/encoding marker (% \\ .. //),
    is an absolute path (leading /), is non-ascii, or carries an uppercase ascii char. A genuine governed
    reference is a lowercase ascii canonical path under an optional 'scheme://' prefix, optionally suffixed with
    a '#reg=<id>' binding (e.g. 'ref://producer/build-01' or 'ref://evidence/build-evidence/ev-04#reg=reg-01').
    The scheme '//' and the binding suffix are stripped before the checks so they are not false positives."""
    if not isinstance(value, str) or not value.strip():
        return False
    s = value
    # non-ascii anywhere is non-canonical.
    try:
        s.encode("ascii")
    except UnicodeEncodeError:
        return True
    # strip an optional lowercase 'scheme://' prefix (the scheme's '//' is legitimate).
    body = _REF_SCHEME_RE.sub("", s)
    had_scheme = body != s
    # strip an optional '#...' binding suffix (the binding id is validated separately).
    path = body.split("#", 1)[0]
    if any(m in path for m in _NONCANONICAL_MARKERS):
        return True
    # an absolute path with NO scheme (leading '/') is non-canonical.
    if not had_scheme and path.startswith("/"):
        return True
    # uppercase-where-ascii: any ascii uppercase letter makes a lower-cased reference non-canonical.
    if any(ch.isupper() for ch in path):
        return True
    return False


class ProducerRegistrationForbidden(Exception):
    """C-FUT2-REGISTRATION-PROMOTION — raised when a caller asks the public/token-gated mint path to confer a
    non-SYNTHETIC classification, or supplies no module token. Public construction is SYNTHETIC-ONLY in this
    inert stage; a real classification is not caller-conferrable and is NOT silently repaired — it is rejected
    explicitly (mirrors FUT-1's EARPromotionForbidden)."""


# ============================================================================ §4G module-owned issuer
class _ModuleRegistrationIssuer:
    """MODULE-OWNED in-process seal capability, mirroring FUT-1's `_ReadinessIssuer` / F2-R1's `_ModuleVerifier`.
    Holds an EPHEMERAL, name-mangled `self.__key` (`secrets.token_bytes(32)`) — NO getter, NEVER serialised,
    NEVER in any to_dict/log. Only the module builds/holds this; a caller cannot supply/forge a valid seal, and
    cannot relabel a synthetic registration into a real one (the seal binds the classification).

    IMPORTANT: this seal is OBJECT-INTEGRITY METADATA ONLY — it is NOT a production trust root, NOT registry
    authority, NOT external attestation, and NEVER by itself grounds real-mode acceptance."""

    def __init__(self, *, _key: Optional[bytes] = None) -> None:
        self.__key = _key if _key is not None else secrets.token_bytes(32)

    def _seal(self, payload_bytes: bytes) -> str:
        return hmac.new(self.__key, payload_bytes, hashlib.sha256).hexdigest()

    def seal_registration(self, record: "ProducerRegistrationRecord") -> str:
        return self._seal(_seal_message(record))

    def verify(self, record: "ProducerRegistrationRecord") -> bool:
        if not isinstance(record.registration_seal, str):
            return False
        expected = self._seal(_seal_message(record))
        return hmac.compare_digest(expected, record.registration_seal)


# The module's OWN issuer + private token sentinel. Neither is caller-constructible/substitutable.
_MODULE_ISSUER = _ModuleRegistrationIssuer()
_REGISTRATION_TOKEN = object()   # module-private: only the module helper supplies it


# ============================================================================ §4A model
@dataclass(frozen=True)
class ProducerRegistrationRecord:
    """§4A the producer-registration record (EV-04 build / EV-05 OCI). Binds the full field set. Carries ONLY
    REFERENCES + status strings — NO private/secret/endpoint fields. `synthetic_or_real_classification` is set
    by the ISSUER (never the caller). `registration_digest` binds every field except itself + the seal;
    `registration_seal` is the module issuer's HMAC over the digest + classification, so a relabel to 'REAL'
    breaks the seal. A REAL/registered record is UNMINTABLE in this WO (real registration unavailable)."""

    contract_version: str
    lifecycle_stage: int                       # 4 for build, 5 for OCI
    registration_id: str
    producer_role: str
    producer_identity_reference: str
    execution_identity_reference: str
    executable_identity_reference: str
    authority_root_reference: str
    approval_record_reference: str
    activated_registry_handle_reference: str
    permitted_evidence_class: str
    forbidden_evidence_classes: Tuple[str, ...]
    registration_utc: str
    validity_end_utc: str
    revocation_state: str
    provenance: str
    synthetic_or_real_classification: str
    status: str
    fault_code: str
    evidence_references: Tuple[str, ...]
    created_by: str
    registration_digest: str
    registration_seal: str

    def identity_fields(self) -> Dict[str, object]:
        """Canonical fields the registration_digest covers (EXCLUDES registration_digest AND
        registration_seal)."""
        return {
            "contract_version": self.contract_version,
            "lifecycle_stage": self.lifecycle_stage,
            "registration_id": self.registration_id,
            "producer_role": self.producer_role,
            "producer_identity_reference": self.producer_identity_reference,
            "execution_identity_reference": self.execution_identity_reference,
            "executable_identity_reference": self.executable_identity_reference,
            "authority_root_reference": self.authority_root_reference,
            "approval_record_reference": self.approval_record_reference,
            "activated_registry_handle_reference": self.activated_registry_handle_reference,
            "permitted_evidence_class": self.permitted_evidence_class,
            "forbidden_evidence_classes": list(self.forbidden_evidence_classes),
            "registration_utc": self.registration_utc,
            "validity_end_utc": self.validity_end_utc,
            "revocation_state": self.revocation_state,
            "provenance": self.provenance,
            "synthetic_or_real_classification": self.synthetic_or_real_classification,
            "status": self.status,
            "fault_code": self.fault_code,
            "evidence_references": list(self.evidence_references),
            "created_by": self.created_by,
        }

    def to_dict(self) -> Dict[str, object]:
        d = self.identity_fields()
        d["registration_digest"] = self.registration_digest
        d["registration_seal"] = self.registration_seal
        return d

    @staticmethod
    def compute_digest(identity_fields: Mapping[str, object]) -> str:
        return hashlib.sha256(_canonical(dict(identity_fields)).encode("utf-8")).hexdigest()

    def recompute_digest(self) -> str:
        return ProducerRegistrationRecord.compute_digest(self.identity_fields())


def _seal_message(record: ProducerRegistrationRecord) -> bytes:
    """Bytes the issuer HMAC covers: the registration digest + the classification (so a relabelled
    classification breaks the seal)."""
    return (record.registration_digest + "\x00" + record.synthetic_or_real_classification).encode("utf-8")


def new_synthetic_producer_registration(**kwargs: object) -> ProducerRegistrationRecord:
    """MODULE-ONLY, token-gated SYNTHETIC mint: builds a ProducerRegistrationRecord with its
    registration_digest computed and its seal applied by the MODULE issuer. Blesses INTEGRITY only — validity
    is decided by `validate_producer_registration`.

    C-FUT2-REGISTRATION-PROMOTION: requires `test_token is _REGISTRATION_TOKEN` (only the module helper supplies
    it) else `ProducerRegistrationForbidden`. The classification is forced to 'SYNTHETIC'; any caller-selected
    non-synthetic classification raises `ProducerRegistrationForbidden` (not repaired). A real record can only
    ever arise from a genuinely-available governed production registry (unavailable in this WO), never from
    caller-supplied construction."""
    if kwargs.get("test_token", None) is not _REGISTRATION_TOKEN:
        raise ProducerRegistrationForbidden(
            "synthetic producer-registration mint is module-token gated; a caller cannot mint a registration "
            "record (C-FUT2-REGISTRATION-PROMOTION)")
    kwargs.pop("test_token", None)
    _cls = kwargs.get("synthetic_or_real_classification", "SYNTHETIC")
    if str(_cls) != "SYNTHETIC":
        raise ProducerRegistrationForbidden(
            "public construction is synthetic-only; caller-selected classification "
            f"{_cls!r} is not permitted (C-FUT2-REGISTRATION-PROMOTION)")
    kwargs["synthetic_or_real_classification"] = "SYNTHETIC"
    kwargs.setdefault("contract_version", CONTRACT_VERSION)
    kwargs.setdefault("status", "SYNTHETIC_UNREGISTERED")
    kwargs.setdefault("revocation_state", "NOT_REVOKED")
    kwargs.setdefault("fault_code", "")
    kwargs.setdefault("created_by", "SYNTHETIC_TEST")
    role = str(kwargs.get("producer_role", ""))
    # permitted / forbidden evidence classes derive from the role (module-set, never caller-asserted freely).
    if "permitted_evidence_class" not in kwargs:
        kwargs["permitted_evidence_class"] = ROLE_PERMITTED_EVIDENCE_CLASS.get(role, "")
    if "forbidden_evidence_classes" not in kwargs:
        permitted = str(kwargs["permitted_evidence_class"])
        kwargs["forbidden_evidence_classes"] = tuple(sorted(EVIDENCE_CLASSES - {permitted}))
    else:
        kwargs["forbidden_evidence_classes"] = tuple(kwargs["forbidden_evidence_classes"])  # type: ignore[arg-type]
    evrefs = kwargs.get("evidence_references", ())
    kwargs["evidence_references"] = tuple(evrefs) if evrefs else tuple()
    kwargs["registration_digest"] = ""
    kwargs["registration_seal"] = ""
    r0 = ProducerRegistrationRecord(**kwargs)  # type: ignore[arg-type]
    r1 = dataclasses.replace(r0, registration_digest=r0.recompute_digest())
    return dataclasses.replace(r1, registration_seal=_MODULE_ISSUER.seal_registration(r1))


# ============================================================================ §4G production unavailable
def production_producer_registry_configured() -> bool:
    """The PRODUCTION producer registry (a governed, externally-rooted registration authority) is NOT
    configured in this WO. Real producer registration therefore has NO source here — REAL_CANDIDATE always
    fails closed. Performs NO filesystem / /etc / environment / network lookup: unconditionally False."""
    return False


def configured_registry_authority_identity() -> Optional[str]:
    """C-FUT2-REGISTRY-SOLE-ROOT: the module-controlled identity of the GOVERNED registration authority that a
    real producer registration must have been registered by. This is a NON-Boolean second anchor so that
    `production_producer_registry_configured()` (a Boolean) can NEVER become the sole trust root: even if that
    Boolean is monkeypatched True, a real registration must ALSO resolve to THIS module-controlled identity —
    which is unconfigured (None) in this inert WO, so no real registration can match. A caller cannot supply
    this identity; it is not derived from the record, the seal, or the caller-supplied handle reference.

    Stage-B must replace this inert None with a governed, externally-rooted registration-authority identity +
    configuration contract. This WO does NOT supply it (no host value, no secret, no endpoint)."""
    return None


def register_producer(
    *,
    role: str,
    registration_id: str,
    producer_identity_reference: str,
    execution_identity_reference: str,
    executable_identity_reference: str,
    authority_root_reference: str,
    approval_record_reference: str,
    lifecycle_stage: int,
    now_utc: str,
    validity_end_utc: str,
    provenance: str,
    mode: str,
    activated_registry_handle: object = None,
    activation_authority: object = None,
    evidence_references: Tuple[str, ...] = (),
    created_by: str = "SYNTHETIC_TEST",
    test_token: object = None,
) -> Tuple[Optional[ProducerRegistrationRecord], Tuple[str, ...]]:
    """§4G obtain a producer-registration record, or (None, reasons). Fail-closed. The classification is set by
    THIS module, never the caller.

    REAL_CANDIDATE: ALWAYS (None, ('PR-FUT2-REAL-REGISTRATION-UNAVAILABLE',
        'F2R3-FUT2-REAL-PRODUCER-REGISTRATION-NOT-CONFIGURED')) — no fallback, no synthetic substitution, no
        /etc/env/filesystem/network lookup (mirrors FUT-1 EAR-REAL-AUTHORITY-UNAVAILABLE).

    TEST_ONLY / INERT_SIMULATION: require `test_token is _REGISTRATION_TOKEN` (only the module helper supplies
        it) else (None, ('PR-FUT2-SYNTHETIC-REQUIRES-MODULE-TOKEN',)); then mint a SYNTHETIC_UNREGISTERED
        record with classification 'SYNTHETIC', sealed by _MODULE_ISSUER. Classification is set by the module,
        NEVER the caller. The activated_registry_handle/activation_authority are recorded ONLY as references
        here — no real handle is activated."""
    if mode == "REAL_CANDIDATE":
        # No production producer registry in this WO. NO synthetic substitution, NO fallback, NO fs/env/network.
        return (None, (
            "PR-FUT2-REAL-REGISTRATION-UNAVAILABLE",
            "F2R3-FUT2-REAL-PRODUCER-REGISTRATION-NOT-CONFIGURED",
        ))

    if mode not in ("TEST_ONLY", "INERT_SIMULATION"):
        return (None, ("PR-FUT2-INVALID-MODE",))

    if test_token is not _REGISTRATION_TOKEN:
        return (None, ("PR-FUT2-SYNTHETIC-REQUIRES-MODULE-TOKEN",))

    handle_ref = _handle_reference(activated_registry_handle)

    record = new_synthetic_producer_registration(
        lifecycle_stage=int(lifecycle_stage),
        registration_id=str(registration_id),
        producer_role=str(role),                         # module records it; validated downstream.
        producer_identity_reference=str(producer_identity_reference),
        execution_identity_reference=str(execution_identity_reference),
        executable_identity_reference=str(executable_identity_reference),
        authority_root_reference=str(authority_root_reference),
        approval_record_reference=str(approval_record_reference),
        activated_registry_handle_reference=handle_ref,
        registration_utc=str(now_utc),
        validity_end_utc=str(validity_end_utc),
        provenance=str(provenance),
        evidence_references=tuple(evidence_references or ()),
        created_by=str(created_by),
        synthetic_or_real_classification="SYNTHETIC",    # module sets it, never the caller.
        status="SYNTHETIC_UNREGISTERED",                 # module sets it, never the caller.
        test_token=_REGISTRATION_TOKEN,
    )
    return (record, tuple())


def _handle_reference(activated_registry_handle: object) -> str:
    """Derive a REFERENCE string for the activated registry handle (never the live object). An
    ActivatedRegistryHandle-shaped object contributes its manifest_id/registry_id as a reference; a plain
    string is taken verbatim; None yields ''. NO trust is conferred here — the reference is validated, and a
    real record's handle authority is checked against the module-controlled configured identity."""
    if activated_registry_handle is None:
        return ""
    if isinstance(activated_registry_handle, str):
        return activated_registry_handle
    manifest_id = getattr(activated_registry_handle, "manifest_id", None)
    if isinstance(manifest_id, str) and manifest_id.strip():
        return "ref://activated-handle/" + manifest_id.replace("@", "-at-")
    return ""


def new_synthetic_registration(**kwargs: object) -> Tuple[Optional[ProducerRegistrationRecord], Tuple[str, ...]]:
    """Module helper: mint a synthetic SYNTHETIC_UNREGISTERED record by internally supplying
    `_REGISTRATION_TOKEN`. Tests call this WITHOUT ever seeing the token; a caller calling `register_producer`
    with no token gets PR-FUT2-SYNTHETIC-REQUIRES-MODULE-TOKEN (proved by a companion test). REAL_CANDIDATE is
    refused here too."""
    kwargs.setdefault("mode", "TEST_ONLY")
    kwargs["test_token"] = _REGISTRATION_TOKEN
    return register_producer(**kwargs)  # type: ignore[arg-type]


# ============================================================================ §4B role-permission validator
def validate_producer_registration(
    record: object,
    *,
    now_utc: str,
    real_mode: bool,
) -> Tuple[str, ...]:
    """§4B return () iff `record` is a valid SYNTHETIC producer-registration record, else a sorted tuple of
    PR-FUT2-* reason codes. Reject — never repair. Fail-closed.

    The INDEPENDENT real-registration gate (baking in the FUT-1 lesson) runs BEFORE the seal could cause
    acceptance: for real_mode OR a real-classified record it requires a MODULE-CONTROLLED configured registry
    authority (unavailable here) and APPENDS a reason, guaranteeing rejection — a valid local seal can NEVER
    overcome the missing production registry. The local seal is object-integrity metadata ONLY."""
    if not isinstance(record, ProducerRegistrationRecord):
        return ("PR-FUT2-WRONG-TYPE",)

    reasons: List[str] = []

    # --- C-FUT2-REGISTRY-SOLE-ROOT: INDEPENDENT real-registration gate, evaluated FIRST. A REAL/registered
    #     record requires a governed, module-controlled production registry to be configured AND its exact
    #     configured authority identity to resolve. This gate is INDEPENDENT of seal validity / digest /
    #     classification field / caller / constructor / reflective issuer access / monkeypatching. It appends a
    #     reason (guaranteeing rejection); the structural checks below still run as defence-in-depth. The
    #     module seal is NEVER the trust root. The identity anchor is NON-Boolean so
    #     `production_producer_registry_configured()` cannot become the new sole root: even monkeypatched True,
    #     the module-controlled configured identity (None here) blocks every real record. A caller cannot
    #     supply that identity via the handle reference.
    _is_real = (real_mode
                or str(record.synthetic_or_real_classification).strip().upper() == "REAL"
                or str(record.status).strip().upper() not in ("SYNTHETIC_UNREGISTERED", ""))
    if _is_real:
        # (1) the Boolean gate — NECESSARY but NOT SUFFICIENT.
        if not production_producer_registry_configured():
            reasons.append("PR-FUT2-REGISTRY-NOT-CONFIGURED")
        # (2) the DECISIVE NON-Boolean anchor — evaluated INDEPENDENTLY of the Boolean, so it fires whether or
        #     not the Boolean is (mis)configured/monkeypatched True. The module-controlled configured registry
        #     authority identity is None here -> no real record can ever resolve it. A caller cannot supply it
        #     (not via the handle reference). This is the primitive that closes the FUT-1 sole-root class: the
        #     Boolean is never the sole root, and neither is the local seal.
        _cfg_identity = configured_registry_authority_identity()
        if not _cfg_identity:
            reasons.append("PR-FUT2-REGISTRY-AUTHORITY-NOT-CONFIGURED")
        elif str(record.activated_registry_handle_reference) != str(_cfg_identity):
            reasons.append("PR-FUT2-REGISTRY-AUTHORITY-MISMATCH")

    # digest tamper (a body-field tamper breaks the digest recompute).
    if record.registration_digest != record.recompute_digest():
        reasons.append("PR-FUT2-DIGEST-TAMPER")

    # seal — a caller-forged / copied / relabelled record fails (the ephemeral key is module-owned). The seal
    # binds the classification, so relabelling synthetic->'REAL' invalidates the seal here. INTEGRITY metadata
    # ONLY — never a real trust root (see the real-registration gate above).
    if not _MODULE_ISSUER.verify(record):
        reasons.append("PR-FUT2-SEAL-INVALID")

    # real mode: a synthetic (or any non-'REAL') classification never passes real mode — and a real record
    # cannot exist here (real registration unavailable).
    if real_mode and str(record.synthetic_or_real_classification).strip().upper() != "REAL":
        reasons.append("PR-FUT2-SYNTHETIC-IN-REAL-MODE")

    # status: in this WO only SYNTHETIC_UNREGISTERED is permitted; any real/registered status is forbidden.
    if str(record.status) != "SYNTHETIC_UNREGISTERED":
        reasons.append("PR-FUT2-REAL-STATUS-FORBIDDEN")

    # role must be one of the two recognised roles.
    if record.producer_role not in PRODUCER_ROLES:
        reasons.append("PR-FUT2-BAD-ROLE")
    else:
        # permitted evidence class must equal the role's canonical class.
        canonical_class = ROLE_PERMITTED_EVIDENCE_CLASS[record.producer_role]
        if record.permitted_evidence_class != canonical_class:
            reasons.append("PR-FUT2-EVIDENCE-CLASS-MISMATCH")
        # dual-role: a role permitting BOTH classes, or the forbidden set failing to exclude the other class,
        # or a role that can also approve/inspect+build.
        forbidden = set(record.forbidden_evidence_classes)
        other = EVIDENCE_CLASSES - {canonical_class}
        if forbidden != other or record.permitted_evidence_class in forbidden:
            reasons.append("PR-FUT2-DUAL-ROLE")
        if canonical_class in forbidden:
            reasons.append("PR-FUT2-DUAL-ROLE")

    # evidence references: each must carry the permitted class marker; an inline payload is not a reference; a
    # non-canonical reference is rejected.
    permitted = str(record.permitted_evidence_class)
    for evref in record.evidence_references:
        if _looks_like_inline_payload(evref):
            reasons.append("PR-FUT2-REFERENCE-IS-INLINE-PAYLOAD")
            continue
        if _is_noncanonical_reference(evref):
            reasons.append("PR-FUT2-REFERENCE-NONCANONICAL")
            continue
        cls = _evidence_ref_class(evref)
        if cls is not None and cls != permitted:
            reasons.append("PR-FUT2-EVIDENCE-CLASS-VIOLATION")
        # an evidence reference bound to another registration id.
        bound = _evidence_ref_registration(evref)
        if bound is not None and bound != str(record.registration_id):
            reasons.append("PR-FUT2-EVIDENCE-BOUND-ELSEWHERE")

    # reference presence + non-canonical / inline checks on the structural references.
    ref_checks = (
        (record.producer_identity_reference, "PR-FUT2-PRODUCER-REF-MISSING"),
        (record.execution_identity_reference, "PR-FUT2-EXECUTION-REF-MISSING"),
        (record.executable_identity_reference, "PR-FUT2-EXECUTABLE-REF-MISSING"),
        (record.authority_root_reference, "PR-FUT2-AUTHORITY-REF-MISSING"),
        (record.approval_record_reference, "PR-FUT2-APPROVAL-REF-MISSING"),
    )
    for value, code in ref_checks:
        if not str(value or "").strip():
            reasons.append(code)

    structural_refs = [
        record.producer_identity_reference,
        record.execution_identity_reference,
        record.executable_identity_reference,
        record.authority_root_reference,
        record.approval_record_reference,
        record.activated_registry_handle_reference,
    ]
    if any(_looks_like_inline_payload(v) for v in structural_refs):
        reasons.append("PR-FUT2-REFERENCE-IS-INLINE-PAYLOAD")
    if any(_is_noncanonical_reference(v) for v in structural_refs):
        reasons.append("PR-FUT2-REFERENCE-NONCANONICAL")

    # self-registration: the producer cannot be its own approver / creator.
    prod = str(record.producer_identity_reference)
    if prod and str(record.created_by) == prod:
        reasons.append("PR-FUT2-SELF-REGISTRATION")
    if prod and str(record.approval_record_reference) == prod:
        reasons.append("PR-FUT2-SELF-APPROVAL")

    # revocation / expiry / UTC / provenance.
    if str(record.revocation_state).strip().upper() in ("REVOKED", "REVOKED_REGISTRATION"):
        reasons.append("PR-FUT2-REVOKED")
    issued = _parse_utc(record.registration_utc)
    validity = _parse_utc(record.validity_end_utc)
    now = _parse_utc(now_utc)
    if issued is None or validity is None:
        reasons.append("PR-FUT2-UTC-INVALID")
    elif now is not None and now >= validity:
        reasons.append("PR-FUT2-EXPIRED")
    if not str(record.provenance or "").strip():
        reasons.append("PR-FUT2-PROVENANCE-MISSING")

    # contract version / stage.
    if str(record.contract_version) != CONTRACT_VERSION:
        reasons.append("PR-FUT2-CONTRACT-VERSION")
    if record.lifecycle_stage not in (4, 5):
        reasons.append("PR-FUT2-WRONG-STAGE")
    # role<->stage coherence: build producer is stage 4, OCI is stage 5.
    if record.producer_role == "BUILD_PRODUCER" and record.lifecycle_stage != 4:
        reasons.append("PR-FUT2-WRONG-STAGE")
    if record.producer_role == "OCI_INSPECTION_PRODUCER" and record.lifecycle_stage != 5:
        reasons.append("PR-FUT2-WRONG-STAGE")

    # authority-governed: a real record whose authority derives ONLY from the local seal / a raw registry / an
    # unactivated handle / a caller assertion is not governed (real records only — synthetic records are
    # inert). The handle reference must not be empty for a real record.
    if _is_real and not str(record.activated_registry_handle_reference or "").strip():
        reasons.append("PR-FUT2-AUTHORITY-NOT-GOVERNED")

    return tuple(sorted(set(reasons)))


def _evidence_ref_class(evref: object) -> Optional[str]:
    """Infer the evidence class an evidence reference carries, or None if unmarked. A governed evidence
    reference embeds its class token (e.g. 'ref://evidence/build-evidence/...' or '.../oci-inspection-evidence/
    ...'). Case-insensitive on the token."""
    if not isinstance(evref, str):
        return None
    low = evref.lower()
    has_build = "build-evidence" in low or "build_evidence" in low
    has_oci = "oci-inspection-evidence" in low or "oci_inspection_evidence" in low
    if has_build and not has_oci:
        return "BUILD_EVIDENCE"
    if has_oci and not has_build:
        return "OCI_INSPECTION_EVIDENCE"
    return None


def _evidence_ref_registration(evref: object) -> Optional[str]:
    """Extract the registration id an evidence reference declares it is bound to, or None. Convention:
    '...#reg=<registration_id>' suffix."""
    if not isinstance(evref, str) or "#reg=" not in evref:
        return None
    return evref.split("#reg=", 1)[1]


# ============================================================================ §4C distinctness
def evaluate_producer_registration_distinctness(
    build_registration: object,
    oci_registration: object,
    *,
    build_execution: object,
    oci_execution: object,
) -> Tuple[str, ...]:
    """§4C return () iff the build registration + its execution and the OCI registration + its execution are
    GENUINELY DISTINCT across all six F2-R3 dimensions, else a sorted tuple of reason codes. Fail-closed.

    Distinctness is DERIVED — there is NO `distinct` parameter. Execution independence is DELEGATED to
    `execution_identity.evaluate_execution_independence`, whose EI-* reasons (incl. the decisive
    EI-COMMON-EXECUTABLE-AND-AUTHORITY when the two share executable_digest + authority + runner — labels
    differing is NOT sufficient) are surfaced verbatim.

    Rejects:
      * either not a ProducerRegistrationRecord                        -> PR-FUT2-WRONG-TYPE
      * same registration_id / producer_identity / execution / executable / authority reference -> PR-FUT2-SAME-*
      * build role != BUILD_PRODUCER or oci role != OCI_INSPECTION_PRODUCER -> PR-FUT2-ROLE-MISMATCH
      * one registration claiming both roles                           -> PR-FUT2-DUAL-ROLE
      * canonicalised identities resolving to the same value (case/whitespace/unicode/alias) -> PR-FUT2-ALIASED-IDENTITY
      * every EI-* reason from evaluate_execution_independence (delegated + propagated)
    """
    if not isinstance(build_registration, ProducerRegistrationRecord) or \
            not isinstance(oci_registration, ProducerRegistrationRecord):
        return ("PR-FUT2-WRONG-TYPE",)

    reasons: List[str] = []

    # same-* across the five registration reference dimensions + registration id.
    same_checks = (
        (build_registration.registration_id, oci_registration.registration_id, "PR-FUT2-SAME-REGISTRATION-ID"),
        (build_registration.producer_identity_reference, oci_registration.producer_identity_reference,
         "PR-FUT2-SAME-PRODUCER-IDENTITY"),
        (build_registration.execution_identity_reference, oci_registration.execution_identity_reference,
         "PR-FUT2-SAME-EXECUTION-IDENTITY"),
        (build_registration.executable_identity_reference, oci_registration.executable_identity_reference,
         "PR-FUT2-SAME-EXECUTABLE-IDENTITY"),
        (build_registration.authority_root_reference, oci_registration.authority_root_reference,
         "PR-FUT2-SAME-AUTHORITY-ROOT"),
    )
    for a, b, code in same_checks:
        if str(a).strip() and str(a) == str(b):
            reasons.append(code)

    # roles must be exactly build vs oci.
    if build_registration.producer_role != "BUILD_PRODUCER" or \
            oci_registration.producer_role != "OCI_INSPECTION_PRODUCER":
        reasons.append("PR-FUT2-ROLE-MISMATCH")

    # one registration claiming both roles (its permitted class not exclusive to its role).
    for reg in (build_registration, oci_registration):
        if reg.producer_role in ROLE_PERMITTED_EVIDENCE_CLASS:
            other = EVIDENCE_CLASSES - {ROLE_PERMITTED_EVIDENCE_CLASS[reg.producer_role]}
            if set(reg.forbidden_evidence_classes) != other:
                reasons.append("PR-FUT2-DUAL-ROLE")

    # aliased identity: canonicalising away case/whitespace/unicode collapses the two producer identities to
    # the same value (a proxy alias that differs only in label).
    for a, b in (
        (build_registration.producer_identity_reference, oci_registration.producer_identity_reference),
        (build_registration.execution_identity_reference, oci_registration.execution_identity_reference),
        (build_registration.executable_identity_reference, oci_registration.executable_identity_reference),
        (build_registration.authority_root_reference, oci_registration.authority_root_reference),
    ):
        if str(a) != str(b) and _canonical_identity(a) == _canonical_identity(b) and _canonical_identity(a):
            reasons.append("PR-FUT2-ALIASED-IDENTITY")

    # DELEGATE execution independence — surface EI-* reasons verbatim (incl. EI-COMMON-EXECUTABLE-AND-AUTHORITY).
    ei_reasons = ei.evaluate_execution_independence(
        build_execution=build_execution, oci_execution=oci_execution, candidate_mode="TEST_ONLY")
    reasons.extend(ei_reasons)

    return tuple(sorted(set(reasons)))


def _canonical_identity(value: object) -> str:
    """Canonicalise an identity reference for alias detection: strip, casefold, drop internal whitespace and
    common unicode confusables/zero-width, and normalise a leading 'proxy:' / 'alias:' prefix. Two references
    that collapse to the same canonical value are the SAME identity wearing different labels."""
    if not isinstance(value, str):
        return ""
    s = value.strip().casefold()
    for zw in ("​", "‌", "‍", "﻿"):
        s = s.replace(zw, "")
    s = "".join(ch for ch in s if not ch.isspace())
    for prefix in ("proxy:", "alias:", "proxy-", "alias-"):
        if s.startswith(prefix):
            s = s[len(prefix):]
    # fold a couple of common confusables to their ascii equivalent.
    s = s.replace("а", "a").replace("о", "o").replace("е", "e")
    return s


# ============================================================================ §4E approval boundary
def validate_registration_approval(
    record: object,
    *,
    approval_record: object,
    now_utc: str,
) -> Tuple[str, ...]:
    """§4E return () iff the approval attached to `record` is a genuine, independent, current, role-bound
    external approval, else a sorted tuple of PR-FUT2-* reason codes. Fail-closed.

    DELEGATES to `approval_record.evaluate_approvals` semantics for self-approval rejection where an
    ApprovalRecord is supplied. Rejects producer self-approval, an approver sharing execution/executable/
    authority with the producer, an approval bound to another producer/role, and a copied/relabelled/expired/
    revoked approval.

    The `approval_record` may be an approval_record.ApprovalRecord OR a Mapping carrying approver_id /
    bound_producer_identity / bound_role / approver_execution / approver_executable / approver_authority /
    approval_decision / expiry_utc / revoked."""
    if not isinstance(record, ProducerRegistrationRecord):
        return ("PR-FUT2-WRONG-TYPE",)

    reasons: List[str] = []

    ap = _approval_view(approval_record)
    if ap is None:
        return ("PR-FUT2-APPROVAL-WRONG-TYPE",)

    approver = ap.get("approver_id", "")
    producer = str(record.producer_identity_reference)

    # self-approval: approver == producer.
    if approver and str(approver) == producer:
        reasons.append("PR-FUT2-SELF-APPROVAL")
    if approver and _canonical_identity(approver) == _canonical_identity(producer) and _canonical_identity(producer):
        reasons.append("PR-FUT2-SELF-APPROVAL")

    # approver not independent: shares execution / executable / authority with the producer.
    if str(ap.get("approver_execution", "")) and \
            str(ap.get("approver_execution", "")) == str(record.execution_identity_reference):
        reasons.append("PR-FUT2-APPROVER-NOT-INDEPENDENT")
    if str(ap.get("approver_executable", "")) and \
            str(ap.get("approver_executable", "")) == str(record.executable_identity_reference):
        reasons.append("PR-FUT2-APPROVER-NOT-INDEPENDENT")
    if str(ap.get("approver_authority", "")) and \
            str(ap.get("approver_authority", "")) == str(record.authority_root_reference):
        reasons.append("PR-FUT2-APPROVER-NOT-INDEPENDENT")

    # approval binding: an approval bound to a different producer/role than this record.
    bound_prod = str(ap.get("bound_producer_identity", "") or "")
    if bound_prod and bound_prod != producer:
        reasons.append("PR-FUT2-APPROVAL-BINDING")
    bound_role = str(ap.get("bound_role", "") or "")
    if bound_role and bound_role != str(record.producer_role):
        reasons.append("PR-FUT2-APPROVAL-BINDING")
    # the approval must reference THIS registration id if it names one.
    bound_reg = str(ap.get("bound_registration_id", "") or "")
    if bound_reg and bound_reg != str(record.registration_id):
        reasons.append("PR-FUT2-APPROVAL-BINDING")

    # decision must be APPROVE.
    if str(ap.get("approval_decision", "APPROVE")).upper() != "APPROVE":
        reasons.append("PR-FUT2-APPROVAL-NOT-APPROVED")

    # copied / relabelled: digest tamper on an ApprovalRecord.
    if ap.get("_digest_tamper"):
        reasons.append("PR-FUT2-APPROVAL-COPIED")

    # revoked / expired.
    if str(ap.get("revoked", "")).strip().upper() in ("TRUE", "REVOKED", "1", "YES"):
        reasons.append("PR-FUT2-APPROVAL-REVOKED")
    exp = _parse_utc(ap.get("expiry_utc", ""))
    now = _parse_utc(now_utc)
    if str(ap.get("expiry_utc", "")).strip():
        if exp is None:
            reasons.append("PR-FUT2-APPROVAL-UTC-INVALID")
        elif now is not None and now >= exp:
            reasons.append("PR-FUT2-APPROVAL-EXPIRED")

    return tuple(sorted(set(reasons)))


def _approval_view(approval_record: object) -> Optional[Dict[str, object]]:
    """Project an approval object into a uniform mapping. Supports approval_record.ApprovalRecord (delegating
    to its digest recompute for tamper detection) and a plain Mapping. Returns None on an unknown type."""
    try:
        import design.hermes_fw08_approval_record_v1 as apr
    except Exception:
        apr = None  # type: ignore[assignment]

    if apr is not None and isinstance(approval_record, apr.ApprovalRecord):
        tamper = approval_record.approval_record_digest != approval_record.recompute_digest()
        return {
            "approver_id": approval_record.approver_id,
            "approval_decision": approval_record.approval_decision,
            "expiry_utc": approval_record.expiry_utc,
            "bound_registration_id": approval_record.registry_id,
            "_digest_tamper": tamper,
        }
    if isinstance(approval_record, Mapping):
        d = dict(approval_record)
        d.setdefault("_digest_tamper", False)
        return d
    return None


# ============================================================================ §4F evidence-class separation
def validate_evidence_class(
    record: object,
    evidence_ref_class: str,
    *,
    source_execution_ref: str = "",
) -> Tuple[str, ...]:
    """§4F return () iff `record`'s role may legitimately emit an evidence reference of class
    `evidence_ref_class` produced from `source_execution_ref`, else a sorted tuple of PR-FUT2-* codes.
    Fail-closed. A build producer's output is limited to BUILD_EVIDENCE; an OCI producer's to
    OCI_INSPECTION_EVIDENCE.

    Rejects:
      * not a ProducerRegistrationRecord                              -> PR-FUT2-WRONG-TYPE
      * unknown role                                                  -> PR-FUT2-BAD-ROLE
      * build emitting OCI / OCI emitting build / any wrong class     -> PR-FUT2-EVIDENCE-CLASS-VIOLATION
      * an inline payload passed as an evidence reference class       -> PR-FUT2-REFERENCE-IS-INLINE-PAYLOAD
      * a non-canonical / traversal-encoded class token               -> PR-FUT2-REFERENCE-NONCANONICAL
      * OCI evidence produced FROM the build producer's execution     -> PR-FUT2-OCI-REPACKAGES-BUILD
        (source_execution_ref == the build producer's execution reference == this OCI record's own or a bound
        build execution) — the OCI producer must inspect the image independently by digest, never repackage
        the build producer's execution output.
    """
    if not isinstance(record, ProducerRegistrationRecord):
        return ("PR-FUT2-WRONG-TYPE",)

    reasons: List[str] = []

    if record.producer_role not in ROLE_PERMITTED_EVIDENCE_CLASS:
        return ("PR-FUT2-BAD-ROLE",)

    permitted = ROLE_PERMITTED_EVIDENCE_CLASS[record.producer_role]

    cls = str(evidence_ref_class)
    if _looks_like_inline_payload(cls):
        reasons.append("PR-FUT2-REFERENCE-IS-INLINE-PAYLOAD")
    elif _is_noncanonical_reference(cls) and cls not in EVIDENCE_CLASSES:
        # an evidence class TOKEN (BUILD_EVIDENCE/OCI_INSPECTION_EVIDENCE) is a recognised uppercase constant;
        # a *reference-shaped* class carrying traversal/encoding is non-canonical.
        reasons.append("PR-FUT2-REFERENCE-NONCANONICAL")

    # normalise a reference-shaped class into its class constant for the comparison.
    norm = _evidence_ref_class(cls) or (cls if cls in EVIDENCE_CLASSES else None)
    if norm is None or norm != permitted:
        reasons.append("PR-FUT2-EVIDENCE-CLASS-VIOLATION")

    # OCI repackages build: an OCI producer whose evidence was produced FROM the build producer's execution.
    if record.producer_role == "OCI_INSPECTION_PRODUCER" and str(source_execution_ref).strip():
        # the OCI evidence must come from an INDEPENDENT execution, not the build producer's execution. If the
        # source execution reference equals this OCI record's OWN execution reference it is fine; if it carries
        # a build-producer marker OR equals a declared build execution it is a repackage.
        src = str(source_execution_ref)
        if _references_build_execution(src) or src == str(record.execution_identity_reference) and \
                _references_build_execution(record.execution_identity_reference):
            reasons.append("PR-FUT2-OCI-REPACKAGES-BUILD")
        if _references_build_execution(src):
            reasons.append("PR-FUT2-OCI-REPACKAGES-BUILD")

    return tuple(sorted(set(reasons)))


def _references_build_execution(ref: object) -> bool:
    """True iff a reference names the build producer's execution (carries a 'build' execution marker). Used to
    catch an OCI producer repackaging the build producer's execution output as its own inspection."""
    if not isinstance(ref, str):
        return False
    low = ref.lower()
    return "build-producer/execution" in low or "build_execution" in low or "/execution/build" in low


# ============================================================================ D-PR120-EV-TAIL (stages 4-5)
def eventual_evidence_record_ids() -> Dict[str, str]:
    """D-PR120-EV-TAIL (stages 4-5). The downstream evidence record identifiers this FUT-2 contract DEFINES,
    extending the EV-01/EV-02/EV-03 tail so later gates can reference the two producer-registration records
    unambiguously. Stable, deterministic, no I/O."""
    return {
        "build_producer_registration": "EV-04-BUILD-PRODUCER-REGISTRATION",
        "oci_inspection_producer_registration": "EV-05-OCI-INSPECTION-PRODUCER-REGISTRATION",
    }
