#!/usr/bin/env python3
"""HERMES FW-08 F2-R3 content-resolved evidence + near-copy detection v1 (PURE, fixture-backed).

WO-HELM-HERMES-FW08-F2-R3-EXECUTION-CONTENT-INDEPENDENCE-AND-REAL-IMAGE-PROOF-IMPLEMENTATION-0001 (§6,§7).
Owner: HERMES (Helm). Created (UTC): 2026-07-27. Contract version: 1.

WHY THIS EXISTS (F2-R3 §6/§7). Prior anchors compared REFERENCES (strings) and trusted that a reference named
some real content. A reference is not content: a mutable tag can be repointed, a fabricated digest can be
declared, and one evidence record can restate another's fields WITHOUT ever inspecting the image. §6 requires
evidence to be CONTENT-RESOLVED — the reference must resolve, through an IMMUTABLE storage boundary, to content
whose digest MATCHES an independently-expected digest, at the expected storage version. §7 requires near-copy /
alias detection BEYOND byte equality: the OCI evidence must carry fields that can ONLY come from an INDEPENDENT
OCI inspection, not values derived from (or embedded in) the build result.

FIELDS THAT MAY LEGITIMATELY AGREE (both evidence records describe the SAME image, so these SHOULD match):
  * image_id        — the content-addressed image identity;
  * candidate_id    — the candidate tag/binding;
  * source_sha      — the source commit.
FIELDS THAT MUST COME FROM INDEPENDENT OCI INSPECTION (absent-or-build-derived == a near-copy, not inspection):
  * manifest_digest, config_digest, filesystem_digest, layer_digests, and OCI inspection metadata
    (extraction_method / tool_identity / inspection_utc). If ALL of these are absent OR merely restate build
    metadata, the "OCI" record is a dressed-up copy of the build result -> CR-NEAR-COPY-FROM-BUILD.

The PRODUCTION content resolver is UNAVAILABLE in this WO (no live immutable storage) — it raises
ContentResolverUnavailable, so real-mode resolution ALWAYS fails closed. A module-token-gated SYNTHETIC
resolver serves TEST_ONLY (a caller cannot mint a TRUSTED one).

PURE: no I/O, no subprocess, no network, stdlib only, deterministic (module-token pattern mirrors F2-R1). NOT
imported by runtime; does NOT import the active-import module (avoids cycles).
"""
from __future__ import annotations

import abc
import dataclasses
import hashlib
import json
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Tuple

CONTRACT_VERSION = "1"

RESOLUTION_CLASSIFICATIONS = frozenset({
    "SYNTHETIC_TEST_CONTENT",
    "IMMUTABLE_RESOLVED_CONTENT",
    "REVOKED_CONTENT",
    "UNRESOLVED_CONTENT",
})

# §7 field partition (documented in the module docstring above).
MAY_AGREE_FIELDS = frozenset({"image_id", "candidate_id", "source_sha"})
OCI_INDEPENDENT_FIELDS = frozenset({
    "manifest_digest", "config_digest", "filesystem_digest", "layer_digests",
    "extraction_method", "tool_identity", "inspection_utc",
})
_ACTIVE_IMPORT_MARKERS = ("active_import", "ACTIVE_IMPORT")


def _canonical(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _get(m: object, key: str) -> str:
    return str(m.get(key, "")) if isinstance(m, Mapping) else ""


def _has(m: object, key: str) -> bool:
    if not isinstance(m, Mapping):
        return False
    v = m.get(key, "")
    if isinstance(v, (list, tuple)):
        return len(v) > 0
    return bool(str(v).strip())


class ContentResolverUnavailable(Exception):
    """Raised by the PRODUCTION content resolver — there is no live immutable storage in this WO, so real-mode
    resolution fails closed."""


# ============================================================================ §6 content-resolved evidence
@dataclass(frozen=True)
class ContentResolvedEvidence:
    """Evidence resolved to actual CONTENT through an immutable storage boundary. Binds the reference, the
    content digest, media/schema type, size, creation time, producer, provenance, the immutable storage id +
    storage version, and a resolution classification. `content_digest` is authoritative; the reference is only
    a pointer. The self digest covers all fields except itself."""

    contract_version: str
    reference: str
    content_digest: str
    media_type: str
    schema_type: str
    size_bytes: int
    creation_utc: str
    producer_id: str
    provenance: str
    immutable_storage_id: str
    storage_version: str
    resolution_classification: str
    evidence_digest: str

    def identity_fields(self) -> Dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "reference": self.reference,
            "content_digest": self.content_digest,
            "media_type": self.media_type,
            "schema_type": self.schema_type,
            "size_bytes": self.size_bytes,
            "creation_utc": self.creation_utc,
            "producer_id": self.producer_id,
            "provenance": self.provenance,
            "immutable_storage_id": self.immutable_storage_id,
            "storage_version": self.storage_version,
            "resolution_classification": self.resolution_classification,
        }

    def to_dict(self) -> Dict[str, object]:
        d = self.identity_fields()
        d["evidence_digest"] = self.evidence_digest
        return d

    @staticmethod
    def compute_digest(identity_fields: Mapping[str, object]) -> str:
        return hashlib.sha256(_canonical(dict(identity_fields)).encode("utf-8")).hexdigest()

    def recompute_digest(self) -> str:
        return ContentResolvedEvidence.compute_digest(self.identity_fields())


def new_content_resolved(**kwargs: object) -> ContentResolvedEvidence:
    """Construct a ContentResolvedEvidence with its `evidence_digest` computed. The ONLY blessed constructor.
    Constructing one blesses INTEGRITY only; TRUST comes from resolving it through a module-owned resolver."""
    kwargs.setdefault("contract_version", CONTRACT_VERSION)
    kwargs.setdefault("resolution_classification", "IMMUTABLE_RESOLVED_CONTENT")
    kwargs.setdefault("size_bytes", 0)
    kwargs["evidence_digest"] = ""
    c0 = ContentResolvedEvidence(**kwargs)  # type: ignore[arg-type]
    return dataclasses.replace(c0, evidence_digest=c0.recompute_digest())


# ============================================================================ §6 resolvers
_MODULE_TOKEN = object()   # module-private sentinel: only this module holds a reference to it
_TRUSTED_MARKER = object()  # stamped into every module-built SyntheticContentResolver


class ContentResolver(abc.ABC):
    """Resolves a reference to ContentResolvedEvidence. `is_module_trusted()` is False by default; only a
    module-built synthetic resolver (or a future production resolver) is trusted."""

    @abc.abstractmethod
    def resolve(self, reference: str, *, now_utc: str) -> ContentResolvedEvidence:
        raise NotImplementedError

    def is_module_trusted(self) -> bool:
        return False

    def is_synthetic(self) -> bool:
        return False


class ProductionContentResolver(ContentResolver):
    """The PRODUCTION resolver — NO live immutable storage in this WO. `resolve` ALWAYS raises
    ContentResolverUnavailable, so real-mode resolution fails closed unconditionally."""

    def resolve(self, reference: str, *, now_utc: str) -> ContentResolvedEvidence:
        raise ContentResolverUnavailable(
            "F2R3-CONTENT-RESOLVER-UNAVAILABLE: no live immutable content storage in this WO")

    def is_module_trusted(self) -> bool:
        return True   # a REAL production resolver would be trusted; but it always raises here.


class SyntheticContentResolver(ContentResolver):
    """A deterministic TEST_ONLY resolver over an in-memory content map. It is TRUSTED only when built by the
    module helper (which supplies the private token); a caller-built one is UNTRUSTED (`is_module_trusted()`
    False), so `require_content_resolver` rejects it — a caller cannot mint a trusted resolver."""

    def __init__(self, content_map: Mapping[str, ContentResolvedEvidence], *, _token: object = None) -> None:
        self._content_map: Dict[str, ContentResolvedEvidence] = dict(content_map)
        self._marker = _TRUSTED_MARKER if _token is _MODULE_TOKEN else None

    def resolve(self, reference: str, *, now_utc: str) -> ContentResolvedEvidence:
        if reference not in self._content_map:
            raise ContentResolverUnavailable(f"unresolved reference: {reference}")
        return self._content_map[reference]

    def is_module_trusted(self) -> bool:
        return self._marker is _TRUSTED_MARKER

    def is_synthetic(self) -> bool:
        return True


def new_synthetic_content_resolver(
    content_map: Mapping[str, ContentResolvedEvidence],
) -> SyntheticContentResolver:
    """Module helper: build a TRUSTED synthetic resolver. Tests call this and get trust WITHOUT ever seeing the
    module token. A caller who instantiates SyntheticContentResolver directly gets an UNTRUSTED one."""
    return SyntheticContentResolver(content_map, _token=_MODULE_TOKEN)


def require_content_resolver(resolver: object, *, real_mode: bool) -> Tuple[str, ...]:
    """Return () iff `resolver` is an acceptable content resolver for the mode. Fail-closed:
      * resolver is None / not a ContentResolver         -> CR-RESOLVER-MISSING
      * not module-trusted                               -> CR-RESOLVER-UNTRUSTED
      * a synthetic resolver used in real_mode           -> CR-SYNTHETIC-IN-REAL-MODE
    (In real_mode a production resolver is required, but it always raises at resolve time — so real resolution
    fails closed regardless.)"""
    reasons: List[str] = []
    if resolver is None or not isinstance(resolver, ContentResolver):
        return ("CR-RESOLVER-MISSING",)
    if not resolver.is_module_trusted():
        reasons.append("CR-RESOLVER-UNTRUSTED")
    if real_mode and resolver.is_synthetic():
        reasons.append("CR-SYNTHETIC-IN-REAL-MODE")
    return tuple(sorted(set(reasons)))


def resolve_and_verify(
    reference: str,
    expected_content_digest: str,
    *,
    resolver: object,
    now_utc: str,
    expected_storage_version: Optional[str] = None,
) -> Tuple[Optional[ContentResolvedEvidence], Tuple[str, ...]]:
    """§6 resolve a reference to CONTENT and verify it. Returns (evidence|None, reasons). Fail-closed:
      * resolver raises / returns None / not ContentResolvedEvidence -> CR-UNRESOLVED-REFERENCE
      * resolved evidence lacks immutable_storage_id or storage_version -> CR-MUTABLE-REFERENCE
      * resolved content_digest != expected_content_digest -> CR-REFERENCE-CONTENT-MISMATCH
      * expected_storage_version given and differs -> CR-CHANGED-CONTENT (stale version behind the reference)
    """
    reasons: List[str] = []
    if resolver is None or not isinstance(resolver, ContentResolver):
        return (None, ("CR-UNRESOLVED-REFERENCE",))
    try:
        resolved = resolver.resolve(reference, now_utc=now_utc)
    except ContentResolverUnavailable:
        return (None, ("CR-UNRESOLVED-REFERENCE",))
    if not isinstance(resolved, ContentResolvedEvidence):
        return (None, ("CR-UNRESOLVED-REFERENCE",))

    if not str(resolved.immutable_storage_id).strip() or not str(resolved.storage_version).strip():
        reasons.append("CR-MUTABLE-REFERENCE")

    if resolved.content_digest != expected_content_digest:
        reasons.append("CR-REFERENCE-CONTENT-MISMATCH")

    if expected_storage_version is not None and resolved.storage_version != expected_storage_version:
        reasons.append("CR-CHANGED-CONTENT")

    if reasons:
        return (None, tuple(sorted(set(reasons))))
    return (resolved, tuple())


# ============================================================================ §7 near-copy / alias detection
def detect_alias_or_nearcopy(
    *,
    build_content: object,
    oci_content: object,
    build_result: Mapping[str, object],
    oci_inspection: Mapping[str, object],
) -> Tuple[str, ...]:
    """§7 detect a near-copy / alias BEYOND byte equality. Returns () iff the OCI evidence is genuinely
    independent of the build evidence, else a sorted tuple of CR-* codes.

    `build_content` / `oci_content` are the resolved ContentResolvedEvidence for the two records;
    `build_result` / `oci_inspection` are the raw evidence mappings.

    Rejects:
      * same content_digest under different references           -> CR-ALIAS-SAME-CONTENT
      * OCI restates build fields with no independent inspection -> CR-NEAR-COPY-FROM-BUILD
        provenance (ALL OCI-only fields absent OR build-derived)
      * OCI digests present but not backed by resolved content   -> CR-FABRICATED-OCI-DIGEST
      * OCI derived from active-import (markers)                 -> CR-OCI-FROM-ACTIVE-IMPORT
      * one evidence embeds the other (build nested in oci / vv) -> CR-EMBEDDED-EVIDENCE
    """
    reasons: List[str] = []

    b_res = build_content if isinstance(build_content, ContentResolvedEvidence) else None
    o_res = oci_content if isinstance(oci_content, ContentResolvedEvidence) else None

    # same content under different references = an alias (one artifact, two names).
    if b_res is not None and o_res is not None:
        if b_res.content_digest == o_res.content_digest and b_res.reference != o_res.reference:
            reasons.append("CR-ALIAS-SAME-CONTENT")

    # near-copy: none of the OCI-only fields carry INDEPENDENT inspection values — either absent, or merely
    # equal-to / derived-from build metadata. Fields in MAY_AGREE_FIELDS are allowed to match (same image).
    independent_present = False
    for f in OCI_INDEPENDENT_FIELDS:
        if not _has(oci_inspection, f):
            continue
        oci_val = _get(oci_inspection, f)
        build_val = _get(build_result, f)
        # a value that simply restates a build field (or the derived_from ref) is NOT independent.
        if oci_val and oci_val != build_val:
            independent_present = True
            break
    if not independent_present:
        reasons.append("CR-NEAR-COPY-FROM-BUILD")

    # fabricated OCI digests: manifest/config/filesystem digests declared, but the resolved OCI content does
    # not back them (no resolved content, or the resolved digest does not appear in the record).
    declared_oci_digests = any(_has(oci_inspection, f) for f in
                               ("manifest_digest", "config_digest", "filesystem_digest"))
    if declared_oci_digests:
        if o_res is None:
            reasons.append("CR-FABRICATED-OCI-DIGEST")
        else:
            oci_map = dict(oci_inspection) if isinstance(oci_inspection, Mapping) else {}
            if o_res.content_digest not in _canonical(oci_map):
                reasons.append("CR-FABRICATED-OCI-DIGEST")

    # oci derived from active-import.
    o_ref = _get(oci_inspection, "evidence_ref")
    o_derived = _get(oci_inspection, "derived_from")
    o_prov = o_res.provenance if o_res is not None else ""
    if any(m in o_ref for m in _ACTIVE_IMPORT_MARKERS) or any(m in o_derived for m in _ACTIVE_IMPORT_MARKERS) \
            or any(m in str(o_prov) for m in _ACTIVE_IMPORT_MARKERS):
        reasons.append("CR-OCI-FROM-ACTIVE-IMPORT")

    # embedded evidence: one record nested inside the other.
    if isinstance(oci_inspection, Mapping) and isinstance(build_result, Mapping):
        b_canon = _canonical(dict(build_result))
        o_canon = _canonical(dict(oci_inspection))
        for v in oci_inspection.values():
            if isinstance(v, Mapping) and _canonical(dict(v)) == b_canon:
                reasons.append("CR-EMBEDDED-EVIDENCE")
                break
        else:
            for v in build_result.values():
                if isinstance(v, Mapping) and _canonical(dict(v)) == o_canon:
                    reasons.append("CR-EMBEDDED-EVIDENCE")
                    break

    return tuple(sorted(set(reasons)))
