#!/usr/bin/env python3
"""HERMES FW-08 F-2 independent anchor bundle v1 (PURE, fixture-backed).

WO-HELM-HERMES-FW08-F2-INDEPENDENT-ACTIVE-IMPORT-ANCHORS-IMPLEMENTATION-0001 (§10).
Owner: HERMES (Helm). Created (UTC): 2026-07-21. Contract version: 1.

WHY THIS EXISTS (F-2). The active-import verdict compares evidence against expected anchors. Those anchors
are assembled here into ONE `IndependentAnchorBundle` that carries the image-identity anchor + the
filesystem anchor + the governed producer references. The bundle is the ONLY object the F-2 validator accepts
as the source of expected anchors.

STRUCTURAL GUARANTEE (F-2). The parameters are TYPED ANCHOR OBJECTS + a governed registry + producer ids.
There is NO active-import-evidence parameter. The `isinstance` guards make it structurally impossible to
"assemble a bundle out of active-import fields": a Mapping (the evidence record) passed where an anchor is
expected is rejected as AB-ANCHOR-WRONG-TYPE. The provenance additionally proves the bundle was NOT derived
from active-import evidence.

PURE: no I/O, no subprocess, no network, no secrets, stdlib only, deterministic. NOT imported by runtime.
This module does NOT import the active-import evidence module (avoids an import cycle).
"""
from __future__ import annotations

import dataclasses
import datetime
import hashlib
import json
from dataclasses import dataclass
from typing import List, Optional, Tuple

import design.hermes_fw08_producer_registry_v1 as pr
import design.hermes_fw08_image_identity_anchor_v1 as ii
import design.hermes_fw08_image_filesystem_anchor_v1 as ifs

CONTRACT_VERSION = "1"
_UTC_ISO_RE = ii._UTC_ISO_RE

_BUILD_EVIDENCE_TYPE = "BUILD_RESULT"
_BUILD_GATE = "STAGE_B_BUILD_RESULT"
_OCI_EVIDENCE_TYPE = "OCI_INSPECTION"
_OCI_GATE = "STAGE_B_OCI_INSPECTION"
_AI_EVIDENCE_TYPE = "ACTIVE_IMPORT"
_AI_GATE = "STAGE_B_ACTIVE_IMPORT"

# Markers that reveal an anchor was (illegitimately) derived from active-import evidence.
_ACTIVE_IMPORT_MARKERS = ("active_import", "active-import", "ACTIVE_IMPORT")


@dataclass(frozen=True)
class AnchorProvenance:
    """Provenance of the bundle. `derived_from_active_import` MUST be False for a valid bundle — it is the
    structural proof the anchors did NOT come from the record being validated."""

    build_producer_id: str
    oci_producer_id: str
    active_import_producer_id: str
    derived_from_active_import: bool
    provenance_refs: Tuple[str, ...]

    def to_dict(self):
        return {
            "build_producer_id": self.build_producer_id,
            "oci_producer_id": self.oci_producer_id,
            "active_import_producer_id": self.active_import_producer_id,
            "derived_from_active_import": bool(self.derived_from_active_import),
            "provenance_refs": list(self.provenance_refs),
        }


@dataclass(frozen=True)
class IndependentAnchorBundle:
    contract_version: str
    application: str
    candidate_id: str
    source_sha: str
    image_identity_anchor: "ii.ImageIdentityAnchor"
    image_filesystem_anchor: "ifs.ImageFilesystemAnchor"
    producer_registry_version: str
    build_producer_reg_ref: str
    oci_inspection_producer_reg_ref: str
    active_import_producer_reg_ref: str
    bundle_creation_utc: str
    provenance: "AnchorProvenance"
    bundle_checksum: str

    def to_dict(self):
        return {
            "contract_version": self.contract_version,
            "application": self.application,
            "candidate_id": self.candidate_id,
            "source_sha": self.source_sha,
            "image_identity_anchor": self.image_identity_anchor.to_dict(),
            "image_filesystem_anchor": self.image_filesystem_anchor.to_dict(),
            "producer_registry_version": self.producer_registry_version,
            "build_producer_reg_ref": self.build_producer_reg_ref,
            "oci_inspection_producer_reg_ref": self.oci_inspection_producer_reg_ref,
            "active_import_producer_reg_ref": self.active_import_producer_reg_ref,
            "bundle_creation_utc": self.bundle_creation_utc,
            "provenance": self.provenance.to_dict(),
            "bundle_checksum": self.bundle_checksum,
        }


def _canonical(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _is_ai_ref(ref: str) -> bool:
    return any(m in ref for m in _ACTIVE_IMPORT_MARKERS)


def assemble_anchor_bundle(
    *,
    image_identity_anchor: "ii.ImageIdentityAnchor",
    image_filesystem_anchor: "ifs.ImageFilesystemAnchor",
    producer_registry: "pr.ProducerRegistry",
    build_producer_id: str,
    oci_producer_id: str,
    active_import_producer_id: str,
    now_utc: str,
    application: str = "hermes",
    max_age_hours: int = 24,
) -> Tuple[Optional[IndependentAnchorBundle], Tuple[str, ...]]:
    """Assemble an IndependentAnchorBundle from two TYPED anchors + a governed registry. Returns (bundle, ())
    iff every check passes, else (None, sorted reasons). Passing the evidence Mapping (or any non-anchor) as
    an anchor is rejected as AB-ANCHOR-WRONG-TYPE — this is the structural block on F-2."""
    # Structural type gate FIRST: a Mapping-as-anchor cannot proceed.
    if not isinstance(image_identity_anchor, ii.ImageIdentityAnchor) or \
            not isinstance(image_filesystem_anchor, ifs.ImageFilesystemAnchor):
        return (None, ("AB-ANCHOR-WRONG-TYPE",))

    a_ii = image_identity_anchor
    a_fs = image_filesystem_anchor
    reasons: List[str] = []

    if a_ii.candidate_id != a_fs.candidate_id:
        reasons.append("AB-CANDIDATE-MISMATCH")
    if a_ii.source_sha != a_fs.source_sha:
        reasons.append("AB-SOURCE-MISMATCH")
    if a_ii.image_id != a_fs.image_id:
        reasons.append("AB-IMAGE-MISMATCH")
    if not a_fs.filesystem_digest:
        reasons.append("AB-FS-DIGEST-MISSING")

    # Governed producers for each class + gate (registry-only).
    build_reg, _b = producer_registry.resolve(
        producer_id=build_producer_id, evidence_type=_BUILD_EVIDENCE_TYPE, gate_id=_BUILD_GATE,
        application=application, now_utc=now_utc,
    )
    oci_reg, _o = producer_registry.resolve(
        producer_id=oci_producer_id, evidence_type=_OCI_EVIDENCE_TYPE, gate_id=_OCI_GATE,
        application=application, now_utc=now_utc,
    )
    ai_reg, _a = producer_registry.resolve(
        producer_id=active_import_producer_id, evidence_type=_AI_EVIDENCE_TYPE, gate_id=_AI_GATE,
        application=application, now_utc=now_utc,
    )
    if build_reg is None or oci_reg is None or ai_reg is None:
        reasons.append("AB-PRODUCER-UNREGISTERED")

    reg_ver = getattr(producer_registry, "registry_reference", "")
    if a_ii.producer_registry_ref != reg_ver or a_fs.producer_registry_ref != reg_ver:
        reasons.append("AB-REGISTRY-VERSION-MISMATCH")

    # Cycle / self-reference: an anchor must be built from raw producer evidence, NOT from the other anchor.
    if a_ii.evidence_checksum == a_fs.evidence_ref or \
            a_fs.evidence_checksum in (a_ii.build_result_evidence_ref, a_ii.oci_inspection_evidence_ref):
        reasons.append("AB-ANCHOR-CYCLE")

    # Duplicate / conflicting: the identity anchor and the filesystem anchor must share the SAME OCI
    # inspection evidence (they describe one image). A divergent OCI source is a conflicting duplicate.
    if a_ii.oci_inspection_evidence_ref != a_fs.evidence_ref:
        reasons.append("AB-DUPLICATE-CONFLICTING-ANCHOR")

    # No anchor may derive from active-import evidence.
    ii_ai = [r for r in (a_ii.build_result_evidence_ref, a_ii.oci_inspection_evidence_ref) if _is_ai_ref(r)]
    fs_ai = [r for r in (a_fs.evidence_ref,) if _is_ai_ref(r)]
    derived_from_active_import = bool(ii_ai or fs_ai)
    if set(ii_ai) & set(fs_ai):
        reasons.append("AB-ANCHORS-SHARE-ACTIVE-IMPORT-SOURCE")
    if derived_from_active_import:
        reasons.append("AB-BUNDLE-SELF-DERIVED")

    # Stale anchors.
    now = ii._parse_utc(now_utc)
    for ts in (a_ii.build_utc, a_ii.inspection_utc, a_fs.generated_utc):
        dt = ii._parse_utc(ts)
        if dt is not None and now is not None and (now - dt) > datetime.timedelta(hours=max_age_hours):
            reasons.append("AB-STALE-ANCHOR")

    if reasons:
        return (None, tuple(sorted(set(reasons))))

    provenance = AnchorProvenance(
        build_producer_id=build_producer_id,
        oci_producer_id=oci_producer_id,
        active_import_producer_id=active_import_producer_id,
        derived_from_active_import=False,
        provenance_refs=(
            a_ii.build_result_evidence_ref, a_ii.oci_inspection_evidence_ref, a_fs.evidence_ref,
        ),
    )
    bundle = IndependentAnchorBundle(
        contract_version=CONTRACT_VERSION,
        application=application,
        candidate_id=a_ii.candidate_id,
        source_sha=a_ii.source_sha,
        image_identity_anchor=a_ii,
        image_filesystem_anchor=a_fs,
        producer_registry_version=reg_ver,
        build_producer_reg_ref=build_reg.registry_record_checksum,
        oci_inspection_producer_reg_ref=oci_reg.registry_record_checksum,
        active_import_producer_reg_ref=ai_reg.registry_record_checksum,
        bundle_creation_utc=now_utc,
        provenance=provenance,
        bundle_checksum="",
    )
    payload = {
        "contract_version": bundle.contract_version,
        "application": bundle.application,
        "candidate_id": bundle.candidate_id,
        "source_sha": bundle.source_sha,
        "image_identity_anchor": a_ii.evidence_checksum,
        "image_filesystem_anchor": a_fs.evidence_checksum,
        "producer_registry_version": bundle.producer_registry_version,
        "build_producer_reg_ref": bundle.build_producer_reg_ref,
        "oci_inspection_producer_reg_ref": bundle.oci_inspection_producer_reg_ref,
        "active_import_producer_reg_ref": bundle.active_import_producer_reg_ref,
        "bundle_creation_utc": bundle.bundle_creation_utc,
        "provenance": provenance.to_dict(),
    }
    checksum = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
    return (dataclasses.replace(bundle, bundle_checksum=checksum), tuple())
