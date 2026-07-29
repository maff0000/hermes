#!/usr/bin/env python3
"""HERMES FW-08 F2-R3-FUT-3 immutable evidence-store + resolver readiness contract v1 (PURE, INERT).

WO-HELM-HERMES-FW08-F2-R3-FUT-3-IMMUTABLE-EVIDENCE-STORE-READINESS-CONTRACT-IMPLEMENTATION-0001
(§A-§G + main validator + §6 reference boundary). Owner: HERMES (Helm). Created (UTC): 2026-07-29.
Contract version: 1.

WHAT THIS IS. Lifecycle STAGE 6 = GATE 6 (immutable evidence-store readiness) of the merged FW-08 real-proof
lifecycle — the THIRD future gate (FUT-3). It produces an EV-06 readiness record that READIES the ALREADY
IMPLEMENTED §9 content-addressed immutable resolver (`design.hermes_fw08_content_resolution_v1`) and BINDS
readiness to EV-04 (build-producer registration) and EV-05 (OCI-inspection-producer registration) from FUT-2.
Storage is TECHNOLOGY-NEUTRAL: no filesystem / SQL / Redis / Git / OCI registry / object store is selected,
opened, or written. The contract carries an `immutable_storage_id` + `storage_version_scheme` REFERENCE only.

WHAT THIS IS NOT. This authorises NO store mutation. It does NOT create a real evidence store, write any
evidence object, open a socket, read a file, or touch runtime. Real evidence resolution stays UNAVAILABLE and
FAILS CLOSED: `production_evidence_store_configured()` is False, `configured_store_authority_identity()` is
None, and REAL readiness ALWAYS fails closed. The §9 ProductionContentResolver ALWAYS raises
ContentResolverUnavailable, so a real resolve fails closed unconditionally. Content-level separation is
PREPARED, NOT operationally proven — see `content_level_separation_status()`.

CRITICAL — CONSUMES §9, does NOT reimplement it. This module DELEGATES every content-resolution / immutability
/ alias / near-copy check to `design.hermes_fw08_content_resolution_v1` and surfaces its `CR-*` codes verbatim
(`require_content_resolver`, `resolve_and_verify`, `detect_alias_or_nearcopy`). There is exactly ONE resolver
in the codebase; this module does not add a second. Upstream EV-04/EV-05 are consumed as REAL
`ProducerRegistrationRecord` objects via `design.hermes_fw08_producer_registration_v1` — NOT as raw strings.

CRITICAL LESSON FROM FUT-1 (baked in from the start, mirroring FUT-2). Real acceptance must NOT root in a
single primitive — not a caller-set classification, not a monkeypatchable Boolean, not a reflectively-forgeable
local seal. The DECISIVE real-readiness root here is a MODULE-CONTROLLED configured store authority that is
UNAVAILABLE, anchored by a NON-Boolean identity (`configured_store_authority_identity()` returns None),
INDEPENDENT of any local seal and of any single Boolean, and NOT caller-suppliable. The real-readiness gate
FIRES (appends a reason, guaranteeing rejection) BEFORE any valid seal could cause acceptance, so no real
readiness record ever validates even when `production_evidence_store_configured` is monkeypatched True. The
local module seal (`_MODULE_ISSUER`) is object-integrity metadata ONLY — NEVER a production trust root.

DISCLOSED LIMITATIONS (honest).
  * Opaque-reference: a governed reference is validated as a well-formed pointer (§6 `validate_evidence_reference`),
    NOT resolved to real content here — real resolution is §9's job and is unavailable in this WO.
  * Co-resident wholesale module replacement: a hostile in-process actor that REPLACES the whole module (or the
    §9 module) is out of scope; supported + lightly-reflective paths fail closed. This mirrors FUT-1/FUT-2.

PURE except the ONE ephemeral in-process seal key (`secrets.token_bytes(32)`), mirroring FUT-1/FUT-2: no I/O,
no subprocess, no network, no filesystem/env/registry/store lookup, stdlib + design siblings only, deterministic
given a fixed key. NOT imported by runtime. Imports two sibling DESIGN modules (§9 content resolution, FUT-2
producer registration) — both pure, no cycle.
"""
from __future__ import annotations

import dataclasses
import datetime
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import design.hermes_fw08_content_resolution_v1 as cres
import design.hermes_fw08_producer_registration_v1 as pr

CONTRACT_VERSION = "1"

# §A lifecycle coordinates.
LIFECYCLE_STAGE = 6
FUT = "FUT-3"
GATE = 6
EV06_RECORD_ID = "EV-06-EVIDENCE-STORE-AND-RESOLVER-READINESS"

# §A storage semantics — reuse §9 semantics; do NOT invent a competing digest scheme. sha256 is the digest
# algorithm §9's ContentResolvedEvidence.compute_digest uses.
DIGEST_ALGORITHM = "sha256"

# §A the two evidence classes this readiness supports (from FUT-2 role -> class mapping).
SUPPORTED_EVIDENCE_CLASSES = frozenset({"BUILD_EVIDENCE", "OCI_INSPECTION_EVIDENCE"})

# §A readiness states. Only SYNTHETIC_UNREADY is mintable here — a real ready state is UNREACHABLE (real
# evidence store not configured).
READINESS_STATES = frozenset({
    "SYNTHETIC_UNREADY",
    "REAL_READINESS_UNAVAILABLE",
    "REVOKED_READINESS",
    "INVALID_READINESS",
})

# Readiness modes reused conceptually from the lifecycle. REAL_CANDIDATE always fails closed.
READINESS_MODES = frozenset({"TEST_ONLY", "INERT_SIMULATION", "REAL_CANDIDATE"})

# Reference-confusion markers: a reference value carrying any of these is an INLINE PAYLOAD, not a reference.
_INLINE_PAYLOAD_MARKERS = ("\n", "\r", "\t", "{", "}", "BEGIN", "-----BEGIN", "\x00")
_MAX_REFERENCE_LEN = 200
# Secret-looking / inline-encoding patterns a reference must never contain.
_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"-----BEGIN [A-Z ]*KEY-----"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}"),
    re.compile(r"(?i)\b(secret|token|password|passwd|api[_-]?key|private[_-]?key)\s*[:=]\s*\S+"),
    re.compile(r"://[^/@\s]+:[^/@\s]+@"),  # embedded userinfo credential in a URI
)

# Non-canonical reference markers (traversal / encoding / non-canonical path). A governed reference PATH (the
# part after an optional 'scheme://' prefix and before an optional '#...' binding suffix) must carry none of
# these. `file://` and a bare local/absolute path are rejected separately (§6).
_NONCANONICAL_MARKERS = ("%", "\\", "..", "//")
_REF_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.\-]*://")
# A digest-only string masquerading as evidence (64 hex chars, or 'sha256:' + 64 hex) is not a reference.
_DIGEST_ONLY_RE = re.compile(r"^(sha256:)?[0-9a-fA-F]{64}$")


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
    """True iff `value` looks like an inline evidence/payload rather than a reference: contains a newline/brace/
    control char / 'BEGIN' block, exceeds >200 chars, or matches a private-key/secret pattern."""
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
    is a bare absolute path (leading /), is non-ascii, or carries an uppercase ascii char. A governed reference
    is a lowercase ascii canonical path under an optional 'scheme://' prefix, optionally suffixed with a
    '#...' binding. Mirrors the FUT-2 idiom."""
    if not isinstance(value, str) or not value.strip():
        return False
    s = value
    try:
        s.encode("ascii")
    except UnicodeEncodeError:
        return True
    body = _REF_SCHEME_RE.sub("", s)
    had_scheme = body != s
    path = body.split("#", 1)[0]
    if any(m in path for m in _NONCANONICAL_MARKERS):
        return True
    if not had_scheme and path.startswith("/"):
        return True
    if any(ch.isupper() for ch in path):
        return True
    return False


class EvidenceStoreReadinessForbidden(Exception):
    """C-FUT3-READINESS-PROMOTION — raised when a caller asks the token-gated mint path to confer a non-SYNTHETIC
    classification, or supplies no module token. Public construction is SYNTHETIC-ONLY in this inert stage; a
    real classification is not caller-conferrable and is NOT silently repaired — it is rejected explicitly
    (mirrors FUT-1's EARPromotionForbidden / FUT-2's ProducerRegistrationForbidden)."""


# ============================================================================ module-owned issuer
class _ModuleReadinessIssuer:
    """MODULE-OWNED in-process seal capability, mirroring FUT-1's `_ReadinessIssuer` / FUT-2's
    `_ModuleRegistrationIssuer`. Holds an EPHEMERAL, name-mangled `self.__key` (`secrets.token_bytes(32)`) — NO
    getter, NEVER serialised, NEVER in any to_dict/log. Only the module builds/holds this; a caller cannot
    supply/forge a valid seal, and cannot relabel a synthetic readiness into a real one (the seal binds the
    classification).

    IMPORTANT: this seal is OBJECT-INTEGRITY METADATA ONLY — it is NOT a production trust root, NOT store
    authority, NOT external attestation, and NEVER by itself grounds real-mode acceptance."""

    def __init__(self, *, _key: Optional[bytes] = None) -> None:
        self.__key = _key if _key is not None else secrets.token_bytes(32)

    def _seal(self, payload_bytes: bytes) -> str:
        return hmac.new(self.__key, payload_bytes, hashlib.sha256).hexdigest()

    def seal_readiness(self, record: "EvidenceStoreReadinessRecord") -> str:
        return self._seal(_seal_message(record))

    def verify(self, record: "EvidenceStoreReadinessRecord") -> bool:
        if not isinstance(record.readiness_seal, str):
            return False
        expected = self._seal(_seal_message(record))
        return hmac.compare_digest(expected, record.readiness_seal)


# The module's OWN issuer + private token sentinel. Neither is caller-constructible/substitutable.
_MODULE_ISSUER = _ModuleReadinessIssuer()
_READINESS_TOKEN = object()   # module-private: only the module helper supplies it


# ============================================================================ (A) EV-06 record
@dataclass(frozen=True)
class EvidenceStoreReadinessRecord:
    """§A the EV-06 evidence-store-and-resolver readiness record. Binds the full field set. Carries ONLY
    REFERENCES + policy tokens + status strings — NO endpoint/bucket/credential/host-path/raw-key fields.
    `synthetic_or_real_classification` is set by the ISSUER (never the caller). `readiness_digest` binds every
    field except itself + the seal; `readiness_seal` is the module issuer's HMAC over the digest + classification,
    so a relabel to 'REAL' breaks the seal.

    IMPORTANT: a frozen dataclass being immutable in-process is NOT the same as immutable STORAGE. This record
    READIES the §9 immutable resolver; it does not itself constitute a content-addressed immutable store. Real
    durability / operational immutability is NOT claimed here (see the module docstring)."""

    contract_version: str
    lifecycle_stage: int                       # 6
    fut: str                                   # "FUT-3"
    readiness_record_id: str
    ev04_reference: str
    ev05_reference: str
    resolver_contract_reference: str
    resolver_identity_reference: str
    store_policy_reference: str
    digest_algorithm: str
    immutable_storage_id: str
    storage_version_scheme: str
    supported_evidence_classes: Tuple[str, ...]
    canonical_serialisation_reference: str
    max_object_size_policy_reference: str
    fail_closed_policy_reference: str
    creation_utc: str
    validity_end_utc: str
    revocation_state: str
    synthetic_or_real_classification: str
    readiness_status: str
    fault_code: str
    provenance: str
    evidence_references: Tuple[str, ...]
    created_by: str
    readiness_digest: str
    readiness_seal: str

    def identity_fields(self) -> Dict[str, object]:
        """Canonical fields the readiness_digest covers (EXCLUDES readiness_digest AND readiness_seal)."""
        return {
            "contract_version": self.contract_version,
            "lifecycle_stage": self.lifecycle_stage,
            "fut": self.fut,
            "readiness_record_id": self.readiness_record_id,
            "ev04_reference": self.ev04_reference,
            "ev05_reference": self.ev05_reference,
            "resolver_contract_reference": self.resolver_contract_reference,
            "resolver_identity_reference": self.resolver_identity_reference,
            "store_policy_reference": self.store_policy_reference,
            "digest_algorithm": self.digest_algorithm,
            "immutable_storage_id": self.immutable_storage_id,
            "storage_version_scheme": self.storage_version_scheme,
            "supported_evidence_classes": list(self.supported_evidence_classes),
            "canonical_serialisation_reference": self.canonical_serialisation_reference,
            "max_object_size_policy_reference": self.max_object_size_policy_reference,
            "fail_closed_policy_reference": self.fail_closed_policy_reference,
            "creation_utc": self.creation_utc,
            "validity_end_utc": self.validity_end_utc,
            "revocation_state": self.revocation_state,
            "synthetic_or_real_classification": self.synthetic_or_real_classification,
            "readiness_status": self.readiness_status,
            "fault_code": self.fault_code,
            "provenance": self.provenance,
            "evidence_references": list(self.evidence_references),
            "created_by": self.created_by,
        }

    def to_dict(self) -> Dict[str, object]:
        d = self.identity_fields()
        d["readiness_digest"] = self.readiness_digest
        d["readiness_seal"] = self.readiness_seal
        return d

    @staticmethod
    def compute_digest(identity_fields: Dict[str, object]) -> str:
        # reuse §9's canonical-serialisation + sha256 digest scheme (do NOT invent a competing scheme).
        return cres.ContentResolvedEvidence.compute_digest(dict(identity_fields))

    def recompute_digest(self) -> str:
        return EvidenceStoreReadinessRecord.compute_digest(self.identity_fields())


def _seal_message(record: EvidenceStoreReadinessRecord) -> bytes:
    """Bytes the issuer HMAC covers: the readiness digest + the classification (so a relabelled classification
    breaks the seal)."""
    return (record.readiness_digest + "\x00" + record.synthetic_or_real_classification).encode("utf-8")


def new_synthetic_readiness(**kwargs: object) -> EvidenceStoreReadinessRecord:
    """MODULE-ONLY, token-gated SYNTHETIC mint: builds an EvidenceStoreReadinessRecord with its readiness_digest
    computed and its seal applied by the MODULE issuer. Blesses INTEGRITY only — validity is decided by
    `validate_evidence_store_readiness`.

    C-FUT3-READINESS-PROMOTION: requires `test_token is _READINESS_TOKEN` (only the module helper supplies it)
    else `EvidenceStoreReadinessForbidden`. The classification is forced to 'SYNTHETIC'; any caller-selected
    non-synthetic classification raises `EvidenceStoreReadinessForbidden` (not repaired). A real record can only
    ever arise from a genuinely-available governed production store (unavailable in this WO), never from
    caller-supplied construction."""
    if kwargs.get("test_token", None) is not _READINESS_TOKEN:
        raise EvidenceStoreReadinessForbidden(
            "synthetic evidence-store readiness mint is module-token gated; a caller cannot mint a readiness "
            "record (C-FUT3-READINESS-PROMOTION)")
    kwargs.pop("test_token", None)
    _cls = kwargs.get("synthetic_or_real_classification", "SYNTHETIC")
    if str(_cls) != "SYNTHETIC":
        raise EvidenceStoreReadinessForbidden(
            "public construction is synthetic-only; caller-selected classification "
            f"{_cls!r} is not permitted (C-FUT3-READINESS-PROMOTION)")
    kwargs["synthetic_or_real_classification"] = "SYNTHETIC"
    kwargs.setdefault("contract_version", CONTRACT_VERSION)
    kwargs.setdefault("lifecycle_stage", LIFECYCLE_STAGE)
    kwargs.setdefault("fut", FUT)
    kwargs.setdefault("readiness_record_id", EV06_RECORD_ID)
    kwargs.setdefault("readiness_status", "SYNTHETIC_UNREADY")
    kwargs.setdefault("revocation_state", "NOT_REVOKED")
    kwargs.setdefault("digest_algorithm", DIGEST_ALGORITHM)
    kwargs.setdefault("fault_code", "")
    kwargs.setdefault("created_by", "SYNTHETIC_TEST")
    kwargs.setdefault("canonical_serialisation_reference", "ref://policy/canonical-serialisation")
    kwargs.setdefault("max_object_size_policy_reference", "ref://policy/max-object-size")
    kwargs.setdefault("fail_closed_policy_reference", "ref://policy/fail-closed")
    sec = kwargs.get("supported_evidence_classes")
    if sec is None:
        kwargs["supported_evidence_classes"] = tuple(sorted(SUPPORTED_EVIDENCE_CLASSES))
    else:
        kwargs["supported_evidence_classes"] = tuple(sec)  # type: ignore[arg-type]
    evrefs = kwargs.get("evidence_references", ())
    kwargs["evidence_references"] = tuple(evrefs) if evrefs else tuple()
    kwargs["readiness_digest"] = ""
    kwargs["readiness_seal"] = ""
    r0 = EvidenceStoreReadinessRecord(**kwargs)  # type: ignore[arg-type]
    r1 = dataclasses.replace(r0, readiness_digest=r0.recompute_digest())
    return dataclasses.replace(r1, readiness_seal=_MODULE_ISSUER.seal_readiness(r1))


# ============================================================================ (G) production unavailable
def production_evidence_store_configured() -> bool:
    """The PRODUCTION immutable evidence store (a governed, externally-rooted content-addressed store) is NOT
    configured in this WO. Real readiness therefore has NO source here — REAL_CANDIDATE always fails closed.
    Performs NO filesystem / /etc / environment / network / store lookup: unconditionally False."""
    return False


def configured_store_authority_identity() -> Optional[str]:
    """C-FUT3-STORE-SOLE-ROOT: the module-controlled identity of the GOVERNED evidence-store authority that a
    real readiness must have been readied by. This is a NON-Boolean second anchor so that
    `production_evidence_store_configured()` (a Boolean) can NEVER become the sole trust root: even if that
    Boolean is monkeypatched True, a real readiness must ALSO resolve to THIS module-controlled identity — which
    is unconfigured (None) in this inert WO, so no real readiness can match. A caller cannot supply this
    identity; it is not derived from the record, the seal, the storage id, or the resolver.

    Stage-B must replace this inert None with a governed, externally-rooted store-authority identity +
    configuration contract. This WO does NOT supply it (no host value, no endpoint, no bucket, no secret)."""
    return None


def ready_evidence_store(
    *,
    mode: str,
    resolver: object = None,
    ev04: object = None,
    ev05: object = None,
    ev04_reference: str = "",
    ev05_reference: str = "",
    resolver_contract_reference: str = "ref://contract/hermes-fw08-content-resolution-v1",
    resolver_identity_reference: str = "",
    store_policy_reference: str = "",
    immutable_storage_id: str = "",
    storage_version_scheme: str = "",
    now_utc: str = "",
    validity_end_utc: str = "",
    provenance: str = "",
    created_by: str = "SYNTHETIC_TEST",
    evidence_references: Tuple[str, ...] = (),
    test_token: object = None,
) -> Tuple[Optional[EvidenceStoreReadinessRecord], Tuple[str, ...]]:
    """§G obtain an EV-06 readiness record, or (None, reasons). Fail-closed. The classification is set by THIS
    module, never the caller.

    REAL_CANDIDATE: ALWAYS (None, ('ES-REAL-READINESS-UNAVAILABLE',
        'F2R3-FUT3-REAL-EVIDENCE-STORE-NOT-CONFIGURED')) — no fallback, no synthetic substitution, no
        /etc/env/filesystem/network/store lookup (mirrors FUT-1/FUT-2 real-unavailable).

    TEST_ONLY / INERT_SIMULATION: require `test_token is _READINESS_TOKEN` (only the module helper supplies it)
        else (None, ('ES-SYNTHETIC-REQUIRES-MODULE-TOKEN',)); then mint a SYNTHETIC_UNREADY record with
        classification 'SYNTHETIC', sealed by _MODULE_ISSUER. Classification is set by the module, NEVER the
        caller. The `ev04`/`ev05` ProducerRegistrationRecords, `resolver`, and storage id are recorded ONLY as
        references / policy tokens here — no real store is readied and no evidence is written."""
    if mode == "REAL_CANDIDATE":
        return (None, (
            "ES-REAL-READINESS-UNAVAILABLE",
            "F2R3-FUT3-REAL-EVIDENCE-STORE-NOT-CONFIGURED",
        ))

    if mode not in ("TEST_ONLY", "INERT_SIMULATION"):
        return (None, ("ES-INVALID-MODE",))

    if test_token is not _READINESS_TOKEN:
        return (None, ("ES-SYNTHETIC-REQUIRES-MODULE-TOKEN",))

    # derive EV-04/EV-05 references from the real ProducerRegistrationRecords where supplied (never trust a raw
    # string over the actual record's registration_id).
    ev04_ref = str(ev04_reference)
    ev05_ref = str(ev05_reference)
    if isinstance(ev04, pr.ProducerRegistrationRecord):
        ev04_ref = str(ev04.registration_id)
    if isinstance(ev05, pr.ProducerRegistrationRecord):
        ev05_ref = str(ev05.registration_id)

    record = new_synthetic_readiness(
        ev04_reference=ev04_ref,
        ev05_reference=ev05_ref,
        resolver_contract_reference=str(resolver_contract_reference),
        resolver_identity_reference=str(resolver_identity_reference),
        store_policy_reference=str(store_policy_reference),
        immutable_storage_id=str(immutable_storage_id),
        storage_version_scheme=str(storage_version_scheme),
        creation_utc=str(now_utc),
        validity_end_utc=str(validity_end_utc),
        provenance=str(provenance),
        created_by=str(created_by),
        evidence_references=tuple(evidence_references or ()),
        synthetic_or_real_classification="SYNTHETIC",     # module sets it, never the caller.
        readiness_status="SYNTHETIC_UNREADY",             # module sets it, never the caller.
        test_token=_READINESS_TOKEN,
    )
    return (record, tuple())


def new_synthetic_evidence_store_readiness(
    **kwargs: object,
) -> Tuple[Optional[EvidenceStoreReadinessRecord], Tuple[str, ...]]:
    """Module helper: mint a synthetic SYNTHETIC_UNREADY EV-06 record by internally supplying `_READINESS_TOKEN`.
    Tests call this WITHOUT ever seeing the token; a caller calling `ready_evidence_store` with no token gets
    ES-SYNTHETIC-REQUIRES-MODULE-TOKEN. REAL_CANDIDATE is refused here too."""
    kwargs.setdefault("mode", "TEST_ONLY")
    kwargs["test_token"] = _READINESS_TOKEN
    return ready_evidence_store(**kwargs)  # type: ignore[arg-type]


# ============================================================================ real-readiness gate (FUT-1 lesson)
def _real_readiness_reasons(record: EvidenceStoreReadinessRecord, *, real_mode: bool) -> List[str]:
    """C-FUT3-STORE-SOLE-ROOT: the INDEPENDENT real-readiness gate, evaluated FIRST (before any valid seal could
    cause acceptance). A REAL/ready record requires a governed, module-controlled production store to be
    configured AND its exact configured authority identity to resolve AND match the record's store_policy_
    reference. Evaluated INDEPENDENTLY of seal validity / digest / classification field / caller / constructor /
    reflective issuer access / monkeypatching. Appends reasons (guaranteeing rejection); the module seal is
    NEVER the trust root. The identity anchor is NON-Boolean so `production_evidence_store_configured()` cannot
    become the new sole root: even monkeypatched True, the module-controlled configured identity (None here)
    blocks every real record."""
    reasons: List[str] = []
    _is_real = (real_mode
                or str(record.synthetic_or_real_classification).strip().upper() == "REAL"
                or str(record.readiness_status).strip().upper() not in ("SYNTHETIC_UNREADY", ""))
    if not _is_real:
        return reasons
    # (1) the Boolean gate — NECESSARY but NOT SUFFICIENT.
    if not production_evidence_store_configured():
        reasons.append("ES-STORE-NOT-CONFIGURED")
    # (2) the DECISIVE NON-Boolean anchor — evaluated INDEPENDENTLY of the Boolean, so it fires whether or not
    #     the Boolean is (mis)configured/monkeypatched True. The module-controlled configured store authority
    #     identity is None here -> no real record can ever resolve it. A caller cannot supply it. This is the
    #     primitive that closes the FUT-1 sole-root class: the Boolean is never the sole root, and neither is the
    #     local seal.
    _cfg_identity = configured_store_authority_identity()
    if not _cfg_identity:
        reasons.append("ES-STORE-AUTHORITY-NOT-CONFIGURED")
    elif str(record.store_policy_reference) != str(_cfg_identity):
        reasons.append("ES-STORE-AUTHORITY-MISMATCH")
    return reasons


# ============================================================================ (B) resolver readiness
def validate_resolver_readiness(
    record: object,
    *,
    resolver: object,
    real_mode: bool,
    now_utc: str,
) -> Tuple[str, ...]:
    """§B return () iff the readiness record's RESOLVER is ready for the mode, else a sorted tuple of reasons.
    Fail-closed, reject-not-repair.

    DELEGATES resolver acceptability to §9 `require_content_resolver(resolver, real_mode=real_mode)` and surfaces
    its CR-* codes VERBATIM (CR-RESOLVER-MISSING / CR-RESOLVER-UNTRUSTED / CR-SYNTHETIC-IN-REAL-MODE). A
    caller-provided (non-module-built) resolver is UNTRUSTED and rejected by §9 — there is NO raw-dict /
    local-file / caller-provided-evidence-and-resolver acceptance path (structurally impossible; asserted via
    tests). Additionally requires resolver_identity_reference present + canonical, digest_algorithm == 'sha256',
    and immutable_storage_id + storage_version_scheme present. The INDEPENDENT real-readiness gate runs FIRST."""
    if not isinstance(record, EvidenceStoreReadinessRecord):
        return ("ES-WRONG-TYPE",)

    reasons: List[str] = []

    # INDEPENDENT real-readiness gate FIRST (bake FUT-1 lesson) — appends before any seal could cause acceptance.
    reasons.extend(_real_readiness_reasons(record, real_mode=real_mode))

    # DELEGATE resolver acceptability to §9 — surface CR-* verbatim.
    reasons.extend(cres.require_content_resolver(resolver, real_mode=real_mode))

    # resolver identity must be a present, canonical reference.
    rid = str(record.resolver_identity_reference or "").strip()
    if not rid:
        reasons.append("ES-RESOLVER-IDENTITY-MISSING")
    elif _looks_like_inline_payload(record.resolver_identity_reference) or \
            _is_noncanonical_reference(record.resolver_identity_reference):
        reasons.append("ES-RESOLVER-IDENTITY-NONCANONICAL")

    # digest algorithm must be sha256 (reuse §9 semantics; no competing digest scheme).
    if str(record.digest_algorithm) != DIGEST_ALGORITHM:
        reasons.append("ES-DIGEST-ALGORITHM-UNSUPPORTED")

    # immutable storage id + storage version scheme must be present (a mutable/versionless store is unready).
    if not str(record.immutable_storage_id or "").strip() or \
            not str(record.storage_version_scheme or "").strip():
        reasons.append("ES-MUTABLE-STORAGE-UNREADY")

    return tuple(sorted(set(reasons)))


# ============================================================================ (C) immutability contract
def validate_immutability(
    record: object,
    resolved_evidence: object,
    *,
    now_utc: str,
    resolver: object = None,
    expected_content_digest: Optional[str] = None,
    expected_storage_version: Optional[str] = None,
) -> Tuple[str, ...]:
    """§C return () iff the record + resolved evidence honour immutable, content-addressed, versioned semantics,
    else a sorted tuple of reasons. Fail-closed, reject-not-repair.

    A frozen dataclass is NOT immutable STORAGE: in-process object-immutability does not prove that the backing
    store forbids update-in-place / delete / silent replacement. This validator therefore requires an
    immutable_storage_id + a resolved storage_version, and DELEGATES the resolution checks to §9
    `resolve_and_verify` (surfacing CR-MUTABLE-REFERENCE / CR-REFERENCE-CONTENT-MISMATCH / CR-CHANGED-CONTENT)
    where a resolver + expected digest are supplied — exact-digit content verification comes from §9, not from a
    name match here.

    Own codes: ES-STORAGE-ID-MISSING, ES-STORAGE-VERSION-MISSING, ES-UPDATE-IN-PLACE-FORBIDDEN,
    ES-DELETE-FORBIDDEN, ES-METADATA-MUTATED, ES-DIGEST-MUTATED (surfaced when the resolved evidence's own
    self-digest no longer recomputes — a mutated/replaced object)."""
    if not isinstance(record, EvidenceStoreReadinessRecord):
        return ("ES-WRONG-TYPE",)

    reasons: List[str] = []

    if not str(record.immutable_storage_id or "").strip():
        reasons.append("ES-STORAGE-ID-MISSING")

    if not isinstance(resolved_evidence, cres.ContentResolvedEvidence):
        # no resolvable content to verify immutability against; the resolution path below cannot run.
        reasons.append("ES-STORAGE-VERSION-MISSING")
        return tuple(sorted(set(reasons)))

    # resolved storage version must be present (a versionless resolved object is a mutable alias candidate).
    if not str(resolved_evidence.storage_version or "").strip():
        reasons.append("ES-STORAGE-VERSION-MISSING")

    # content-addressed identity: the resolved evidence's own self-digest must recompute (a metadata/digest
    # mutation or a silent object replacement breaks this).
    if resolved_evidence.evidence_digest != resolved_evidence.recompute_digest():
        reasons.append("ES-DIGEST-MUTATED")

    # DELEGATE the resolution checks to §9 where a resolver + expected digest are supplied. §9 owns
    # CR-MUTABLE-REFERENCE (update-in-place / mutable alias), CR-REFERENCE-CONTENT-MISMATCH (silent replacement),
    # CR-CHANGED-CONTENT (same ref, changed version). We surface those verbatim.
    if resolver is not None and expected_content_digest is not None:
        _ev, cr_reasons = cres.resolve_and_verify(
            resolved_evidence.reference, str(expected_content_digest),
            resolver=resolver, now_utc=now_utc, expected_storage_version=expected_storage_version)
        reasons.extend(cr_reasons)

    return tuple(sorted(set(reasons)))


# ============================================================================ (D) duplicate & alias policy
def evaluate_duplicate_policy(
    record_a: object,
    resolved_a: object,
    record_b: object,
    resolved_b: object,
    *,
    build_result: Dict[str, object],
    oci_inspection: Dict[str, object],
) -> Tuple[str, ...]:
    """§D return () iff the duplicate/alias relationship between two readied objects is permissible, else a
    sorted tuple of reasons. Fail-closed, reject-not-repair. Does NOT silently canonicalise / repair aliases.

    An identical object under the EXACT same canonical identity (same reference AND same content_digest) is
    idempotent ONLY where canonical permits; the SAME identity carrying DIFFERENT content is a conflict
    (ES-CONFLICTING-DUPLICATE). Same content under an unauthorised ALTERNATE reference, mutable references, and
    near-copy provenance are DELEGATED to §9 `detect_alias_or_nearcopy` (surfacing CR-ALIAS-SAME-CONTENT,
    CR-NEAR-COPY-FROM-BUILD, CR-FABRICATED-OCI-DIGEST, CR-OCI-FROM-ACTIVE-IMPORT, CR-EMBEDDED-EVIDENCE verbatim)."""
    if not isinstance(record_a, EvidenceStoreReadinessRecord) or \
            not isinstance(record_b, EvidenceStoreReadinessRecord):
        return ("ES-WRONG-TYPE",)

    reasons: List[str] = []

    ra = resolved_a if isinstance(resolved_a, cres.ContentResolvedEvidence) else None
    rb = resolved_b if isinstance(resolved_b, cres.ContentResolvedEvidence) else None

    # same canonical identity (same reference) but different content -> a conflicting duplicate (a store must
    # NOT silently replace or shadow content behind an existing identity).
    if ra is not None and rb is not None and ra.reference == rb.reference \
            and ra.content_digest != rb.content_digest:
        reasons.append("ES-CONFLICTING-DUPLICATE")

    # DELEGATE alias / near-copy detection to §9 — surface CR-* verbatim (same content under an alternate
    # reference = CR-ALIAS-SAME-CONTENT, etc.).
    reasons.extend(cres.detect_alias_or_nearcopy(
        build_content=ra, oci_content=rb, build_result=build_result, oci_inspection=oci_inspection))

    return tuple(sorted(set(reasons)))


# ============================================================================ (E) EV-04/EV-05 binding
def validate_upstream_binding(
    record: object,
    *,
    ev04_record: object,
    ev05_record: object,
    real_mode: bool,
    now_utc: str,
) -> Tuple[str, ...]:
    """§E return () iff the readiness record binds to the EXACT upstream EV-04 (build) + EV-05 (OCI)
    ProducerRegistrationRecords, else a sorted tuple of reasons. Fail-closed, reject-not-repair. A caller must
    supply the ACTUAL ProducerRegistrationRecords — a raw string cannot assert a binding where a stronger object
    exists.

    DELEGATES each upstream to `producer_registration.validate_producer_registration` (surfacing PR-FUT2-*
    verbatim) and to `evaluate_producer_registration_distinctness` (surfacing PR-FUT2-* / EI-* verbatim).

    Own codes: ES-EV04-MISSING / ES-EV05-MISSING, ES-UPSTREAM-LIFECYCLE-MISMATCH, ES-EV04-REFERENCE-MISMATCH /
    ES-EV05-REFERENCE-MISMATCH, ES-EVIDENCE-CLASS-MISMATCH, ES-REGISTRY-AUTHORITY-MISMATCH,
    ES-RESOLVER-CONTRACT-MISMATCH, ES-COPIED-UPSTREAM-REFERENCE, ES-UPSTREAM-EXPIRED, ES-UPSTREAM-REVOKED,
    ES-SYNTHETIC-UPSTREAM-IN-REAL-MODE."""
    if not isinstance(record, EvidenceStoreReadinessRecord):
        return ("ES-WRONG-TYPE",)

    reasons: List[str] = []

    ev04_ok = isinstance(ev04_record, pr.ProducerRegistrationRecord)
    ev05_ok = isinstance(ev05_record, pr.ProducerRegistrationRecord)
    if not ev04_ok:
        reasons.append("ES-EV04-MISSING")
    if not ev05_ok:
        reasons.append("ES-EV05-MISSING")
    if not ev04_ok or not ev05_ok:
        return tuple(sorted(set(reasons)))

    # DELEGATE upstream validity to FUT-2 — surface PR-FUT2-* verbatim.
    reasons.extend(pr.validate_producer_registration(ev04_record, now_utc=now_utc, real_mode=real_mode))
    reasons.extend(pr.validate_producer_registration(ev05_record, now_utc=now_utc, real_mode=real_mode))

    # lifecycle-stage coherence: EV-04 is stage 4 (build), EV-05 is stage 5 (OCI).
    if ev04_record.lifecycle_stage != 4 or ev05_record.lifecycle_stage != 5:
        reasons.append("ES-UPSTREAM-LIFECYCLE-MISMATCH")
    if ev04_record.producer_role != "BUILD_PRODUCER" or \
            ev05_record.producer_role != "OCI_INSPECTION_PRODUCER":
        reasons.append("ES-UPSTREAM-LIFECYCLE-MISMATCH")

    # the readiness record's stored EV-04/EV-05 references must match the actual registration ids.
    if str(record.ev04_reference) != str(ev04_record.registration_id):
        reasons.append("ES-EV04-REFERENCE-MISMATCH")
    if str(record.ev05_reference) != str(ev05_record.registration_id):
        reasons.append("ES-EV05-REFERENCE-MISMATCH")

    # the two supported evidence classes must be exactly the two upstream permitted classes.
    upstream_classes = {ev04_record.permitted_evidence_class, ev05_record.permitted_evidence_class}
    if upstream_classes != set(record.supported_evidence_classes) or \
            upstream_classes != SUPPORTED_EVIDENCE_CLASSES:
        reasons.append("ES-EVIDENCE-CLASS-MISMATCH")

    # registry authority coherence: EV-04 and EV-05 must be rooted at DISTINCT authorities (distinctness), and a
    # single copied authority-root across both is a mismatch. Delegate to distinctness for the deep check.
    dist = pr.evaluate_producer_registration_distinctness(
        ev04_record, ev05_record,
        build_execution=None, oci_execution=None)
    # surface only the registration-level distinctness codes (execution objects not supplied here); a shared
    # authority root is the registry-authority collision this gate cares about.
    if "PR-FUT2-SAME-AUTHORITY-ROOT" in dist:
        reasons.append("ES-REGISTRY-AUTHORITY-MISMATCH")

    # resolver contract must be the §9 contract, consistent across the readiness record.
    if "content-resolution" not in str(record.resolver_contract_reference).lower():
        reasons.append("ES-RESOLVER-CONTRACT-MISMATCH")

    # copied upstream reference: EV-04 and EV-05 references must not be the same string (a copied binding).
    if str(record.ev04_reference).strip() and \
            str(record.ev04_reference) == str(record.ev05_reference):
        reasons.append("ES-COPIED-UPSTREAM-REFERENCE")

    # expiry / revocation on either upstream (surfaced as an ES-level readiness fault too).
    now = _parse_utc(now_utc)
    for up in (ev04_record, ev05_record):
        validity = _parse_utc(up.validity_end_utc)
        if validity is not None and now is not None and now >= validity:
            reasons.append("ES-UPSTREAM-EXPIRED")
        if str(up.revocation_state).strip().upper() in ("REVOKED", "REVOKED_REGISTRATION"):
            reasons.append("ES-UPSTREAM-REVOKED")

    # synthetic upstream in real mode: a real readiness cannot bind synthetic producer registrations.
    if real_mode:
        for up in (ev04_record, ev05_record):
            if str(up.synthetic_or_real_classification).strip().upper() != "REAL":
                reasons.append("ES-SYNTHETIC-UPSTREAM-IN-REAL-MODE")

    return tuple(sorted(set(reasons)))


# ============================================================================ (F) producer & evidence binding
def validate_producer_binding(
    record: object,
    *,
    ev04_record: object,
    ev05_record: object,
) -> Tuple[str, ...]:
    """§F return () iff the readiness record preserves the producer/evidence binding to the two upstream
    registrations (producer-registration id, producer role, execution identity, registry authority, approval
    record, evidence class, contract version, lifecycle stage, creation UTC, content digest, immutable storage
    id, storage version) AND the build vs OCI evidence stay distinct. Fail-closed, reject-not-repair.

    Build vs OCI distinctness is DELEGATED to `producer_registration.evaluate_producer_registration_distinctness`.

    Own codes: ES-PRODUCER-BINDING-BROKEN, ES-BUILD-OCI-NOT-DISTINCT. Does NOT claim content-level independence
    operationally proven (see `content_level_separation_status`)."""
    if not isinstance(record, EvidenceStoreReadinessRecord):
        return ("ES-WRONG-TYPE",)

    reasons: List[str] = []

    if not isinstance(ev04_record, pr.ProducerRegistrationRecord) or \
            not isinstance(ev05_record, pr.ProducerRegistrationRecord):
        reasons.append("ES-PRODUCER-BINDING-BROKEN")
        return tuple(sorted(set(reasons)))

    # the readiness must reference the exact registration ids + the two distinct evidence classes.
    if str(record.ev04_reference) != str(ev04_record.registration_id) or \
            str(record.ev05_reference) != str(ev05_record.registration_id):
        reasons.append("ES-PRODUCER-BINDING-BROKEN")
    if ev04_record.permitted_evidence_class == ev05_record.permitted_evidence_class:
        reasons.append("ES-BUILD-OCI-NOT-DISTINCT")
    if set(record.supported_evidence_classes) != {
            ev04_record.permitted_evidence_class, ev05_record.permitted_evidence_class}:
        reasons.append("ES-PRODUCER-BINDING-BROKEN")

    # DELEGATE build/OCI distinctness (execution objects not supplied here — the registration-level same-* and
    # role checks still fire). A shared producer/execution/executable/authority/registration id collapses the
    # two producers -> not distinct.
    dist = pr.evaluate_producer_registration_distinctness(
        ev04_record, ev05_record, build_execution=None, oci_execution=None)
    for code in ("PR-FUT2-SAME-REGISTRATION-ID", "PR-FUT2-SAME-PRODUCER-IDENTITY",
                 "PR-FUT2-SAME-EXECUTION-IDENTITY", "PR-FUT2-SAME-EXECUTABLE-IDENTITY",
                 "PR-FUT2-SAME-AUTHORITY-ROOT", "PR-FUT2-ROLE-MISMATCH", "PR-FUT2-ALIASED-IDENTITY",
                 "PR-FUT2-DUAL-ROLE"):
        if code in dist:
            reasons.append("ES-BUILD-OCI-NOT-DISTINCT")
            break

    return tuple(sorted(set(reasons)))


def content_level_separation_status() -> str:
    """The content-level separation between build and OCI evidence is PREPARED by this contract (distinct
    producer roles, disjoint evidence classes, §9 alias/near-copy resolver readied) but is NOT operationally
    proven here — no real image is built or inspected, no real content is resolved. Honest, not over-claiming."""
    return "CONTENT_LEVEL_SEPARATION_PREPARED_NOT_OPERATIONALLY_PROVEN"


# ============================================================================ (§6) evidence-reference boundary
def validate_evidence_reference(ref: object) -> Tuple[str, ...]:
    """§6 return () iff `ref` is a well-formed governed OPAQUE reference (a pointer §9 resolves), else a sorted
    tuple of reasons. A reference is a POINTER governed by §9, NOT evidence itself.

    LIMITATION (honest): this validates the reference is a well-formed pointer; it does NOT resolve it to real
    content (that is §9's job, unavailable here). Reuses the §9/FUT-2 reference idiom.

    Rejects: inline JSON / Base64 blob / PEM / compressed / control chars / path traversal / encoded traversal /
    local path / file:// / shell fragment / embedded credential / raw dict / digest-only-string-as-evidence /
    mutable query param / alias / non-canonical encoding.

    Codes: ES-REFERENCE-IS-INLINE-PAYLOAD / ES-REFERENCE-NONCANONICAL / ES-REFERENCE-MUTABLE-QUERY /
    ES-REFERENCE-DIGEST-ONLY."""
    reasons: List[str] = []

    if isinstance(ref, (dict, list, tuple, bytes)) or not isinstance(ref, str):
        return ("ES-REFERENCE-IS-INLINE-PAYLOAD",)

    s = ref.strip()
    if not s:
        return ("ES-REFERENCE-IS-INLINE-PAYLOAD",)

    # a digest-only string is content identity, not a governed reference.
    if _DIGEST_ONLY_RE.match(s):
        reasons.append("ES-REFERENCE-DIGEST-ONLY")

    # inline payload: JSON/PEM/control chars/base64-ish/oversized/secret/shell fragment.
    if _looks_like_inline_payload(s):
        reasons.append("ES-REFERENCE-IS-INLINE-PAYLOAD")
    # a shell fragment or obvious base64/compressed blob marker.
    if any(tok in s for tok in ("$(", "`", "|", ";", "&&", "==")):
        reasons.append("ES-REFERENCE-IS-INLINE-PAYLOAD")

    # a bare local path or a file:// URI is not a governed content reference.
    low = s.lower()
    if low.startswith("file://") or low.startswith("/") or low.startswith("./") or low.startswith("../") \
            or re.match(r"^[a-z]:\\", low):
        reasons.append("ES-REFERENCE-NONCANONICAL")

    # non-canonical: traversal / encoding / uppercase / non-ascii in the path.
    if _is_noncanonical_reference(s):
        reasons.append("ES-REFERENCE-NONCANONICAL")

    # a mutable query parameter (e.g. '?tag=latest' / '?version=mutable') behind a reference makes it repointable.
    if "?" in s:
        reasons.append("ES-REFERENCE-MUTABLE-QUERY")

    return tuple(sorted(set(reasons)))


# ============================================================================ main validator
def validate_evidence_store_readiness(
    record: object,
    *,
    resolver: object,
    ev04_record: object,
    ev05_record: object,
    real_mode: bool,
    now_utc: str,
) -> Tuple[str, ...]:
    """MAIN validator: return () iff `record` is a valid SYNTHETIC EV-06 evidence-store readiness record for the
    mode, else a sorted tuple of reasons. Composes §A/§B/§E/§F + the INDEPENDENT real-readiness gate + seal /
    digest / type / UTC / version / stage / revocation / provenance checks. Reject-not-repair, fail-closed; () iff
    a valid SYNTHETIC readiness record.

    The INDEPENDENT real-readiness gate runs FIRST (before any valid seal could cause acceptance): for real_mode
    OR a real-classified / real-status record it requires production_evidence_store_configured() == True AND the
    record to resolve the module-controlled configured_store_authority_identity() (None here ->
    ES-STORE-AUTHORITY-NOT-CONFIGURED). No real record ever validates. The local seal is integrity metadata
    ONLY."""
    if not isinstance(record, EvidenceStoreReadinessRecord):
        return ("ES-WRONG-TYPE",)

    reasons: List[str] = []

    # (1) INDEPENDENT real-readiness gate — FIRST, appends (guaranteeing rejection).
    reasons.extend(_real_readiness_reasons(record, real_mode=real_mode))

    # (2) digest tamper.
    if record.readiness_digest != record.recompute_digest():
        reasons.append("ES-DIGEST-TAMPER")

    # (3) seal — caller-forged / copied / relabelled record fails (ephemeral module key). Integrity metadata ONLY.
    if not _MODULE_ISSUER.verify(record):
        reasons.append("ES-SEAL-INVALID")

    # (4) real mode: a synthetic (or any non-'REAL') classification never passes; a real record cannot exist here.
    if real_mode and str(record.synthetic_or_real_classification).strip().upper() != "REAL":
        reasons.append("ES-SYNTHETIC-IN-REAL-MODE")

    # (5) status: only SYNTHETIC_UNREADY is permitted; any real/ready status is forbidden.
    if str(record.readiness_status) != "SYNTHETIC_UNREADY":
        reasons.append("ES-REAL-STATUS-FORBIDDEN")

    # (6) contract version / fut / stage.
    if str(record.contract_version) != CONTRACT_VERSION:
        reasons.append("ES-CONTRACT-VERSION")
    if record.lifecycle_stage != LIFECYCLE_STAGE:
        reasons.append("ES-WRONG-STAGE")
    if str(record.fut) != FUT:
        reasons.append("ES-WRONG-STAGE")

    # (7) readiness record id must be the canonical EV-06 id.
    if str(record.readiness_record_id) != EV06_RECORD_ID:
        reasons.append("ES-READINESS-ID-MISMATCH")

    # (8) revocation.
    if str(record.revocation_state).strip().upper() in ("REVOKED", "REVOKED_READINESS"):
        reasons.append("ES-REVOKED")

    # (9) UTC discipline + expiry.
    created = _parse_utc(record.creation_utc)
    validity = _parse_utc(record.validity_end_utc)
    now = _parse_utc(now_utc)
    if created is None or validity is None:
        reasons.append("ES-UTC-INVALID")
    elif now is not None and now >= validity:
        reasons.append("ES-EXPIRED")

    # (10) provenance.
    if not str(record.provenance or "").strip():
        reasons.append("ES-PROVENANCE-MISSING")

    # (11) required references present.
    ref_checks = (
        (record.ev04_reference, "ES-EV04-REF-MISSING"),
        (record.ev05_reference, "ES-EV05-REF-MISSING"),
        (record.resolver_contract_reference, "ES-RESOLVER-CONTRACT-REF-MISSING"),
        (record.resolver_identity_reference, "ES-RESOLVER-IDENTITY-REF-MISSING"),
        (record.store_policy_reference, "ES-STORE-POLICY-REF-MISSING"),
        (record.immutable_storage_id, "ES-STORAGE-ID-REF-MISSING"),
        (record.storage_version_scheme, "ES-STORAGE-VERSION-REF-MISSING"),
    )
    for value, code in ref_checks:
        if not str(value or "").strip():
            reasons.append(code)

    # (12) §B resolver readiness (delegates §9 require_content_resolver + digest/storage checks; real gate too).
    reasons.extend(validate_resolver_readiness(record, resolver=resolver, real_mode=real_mode, now_utc=now_utc))

    # (13) §E upstream EV-04/EV-05 binding (delegates FUT-2 validate + distinctness).
    reasons.extend(validate_upstream_binding(
        record, ev04_record=ev04_record, ev05_record=ev05_record, real_mode=real_mode, now_utc=now_utc))

    # (14) §F producer & evidence binding.
    reasons.extend(validate_producer_binding(record, ev04_record=ev04_record, ev05_record=ev05_record))

    return tuple(sorted(set(reasons)))


# ============================================================================ D-PR120-EV-TAIL (stage 6)
def eventual_evidence_record_ids() -> Dict[str, str]:
    """D-PR120-EV-TAIL (stage 6). The downstream evidence record identifier this FUT-3 contract DEFINES,
    extending the EV-04/EV-05 tail so later gates can reference the evidence-store-and-resolver readiness record
    unambiguously. Stable, deterministic, no I/O."""
    return {
        "evidence_store_readiness": EV06_RECORD_ID,
    }
