#!/usr/bin/env python3
"""HERMES FW-08 F-2 independent image-filesystem anchor v1 (PURE, fixture-backed).

WO-HELM-HERMES-FW08-F2-INDEPENDENT-ACTIVE-IMPORT-ANCHORS-IMPLEMENTATION-0001 (§8).
Owner: HERMES (Helm). Created (UTC): 2026-07-21. Contract version: 1.

WHY THIS EXISTS (F-2). The expected FILESYSTEM digest an active-import verdict compares against MUST come from
an INDEPENDENT governed source, never from the active-import evidence itself. This module derives an
`ImageFilesystemAnchor` from a dedicated OCI/image-inspection producer's manifest / config / filesystem
digests, correlated together, and bound to a previously-established `ImageIdentityAnchor`.

STRUCTURAL GUARANTEE (F-2). The signature has NO active-import-evidence parameter and NO caller "expected
digest" parameter. The digests can ONLY be sourced from the OCI inspection produced by the dedicated
inspection producer — they cannot be sourced from an import graph, a module inventory, a caller-supplied
expected value, image labels, or a fixture that merely flags itself "real", because none of those are inputs
to this function.

PURE: no I/O, no subprocess, no network, no secrets, stdlib only, deterministic. NOT imported by runtime.
This module does NOT import the active-import evidence module (avoids an import cycle).
"""
from __future__ import annotations

import dataclasses
import datetime
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Tuple

import design.hermes_fw08_producer_registry_v1 as pr
import design.hermes_fw08_image_identity_anchor_v1 as ii

CONTRACT_VERSION = "1"
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_UTC_ISO_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?(\+00:00|Z)$")

_OCI_EVIDENCE_TYPE = "OCI_INSPECTION"
_OCI_GATE = "STAGE_B_OCI_INSPECTION"


@dataclass(frozen=True)
class ImageFilesystemAnchor:
    """A filesystem identity independently established from the OCI inspection. `filesystem_digest` is the
    value the active-import verdict compares against — sourced here, NEVER from the active-import record."""

    contract_version: str
    application: str
    candidate_id: str
    source_sha: str
    image_id: str
    manifest_digest: str
    config_digest: str
    filesystem_digest: str
    digest_algorithms: Tuple[str, ...]
    layer_digests: Tuple[str, ...]
    tool_identity: str
    tool_version: str
    extraction_method: str
    tool_digest: str
    generated_utc: str
    producer_registry_ref: str
    evidence_ref: str
    evidence_checksum: str

    def _identity_fields(self) -> Dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "application": self.application,
            "candidate_id": self.candidate_id,
            "source_sha": self.source_sha,
            "image_id": self.image_id,
            "manifest_digest": self.manifest_digest,
            "config_digest": self.config_digest,
            "filesystem_digest": self.filesystem_digest,
            "digest_algorithms": list(self.digest_algorithms),
            "layer_digests": list(self.layer_digests),
            "tool_identity": self.tool_identity,
            "tool_version": self.tool_version,
            "extraction_method": self.extraction_method,
            "tool_digest": self.tool_digest,
            "generated_utc": self.generated_utc,
            "producer_registry_ref": self.producer_registry_ref,
            "evidence_ref": self.evidence_ref,
        }

    def to_dict(self) -> Dict[str, object]:
        d = self._identity_fields()
        d["evidence_checksum"] = self.evidence_checksum
        return d


def _canonical(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _parse_utc(value: str) -> Optional[datetime.datetime]:
    if not isinstance(value, str) or not _UTC_ISO_RE.match(value):
        return None
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _get(m: object, key: str) -> str:
    return str(m.get(key, "")) if isinstance(m, Mapping) else ""


def _seq(v: object) -> Tuple[object, ...]:
    return tuple(v) if isinstance(v, (list, tuple)) else ()


def build_image_filesystem_anchor(
    *,
    oci_inspection: Mapping[str, object],
    image_identity_anchor: "ii.ImageIdentityAnchor",
    producer_registry: "pr.ProducerRegistry",
    oci_producer_id: str,
    now_utc: str,
    application: str = "hermes",
    max_age_hours: int = 24,
) -> Tuple[Optional[ImageFilesystemAnchor], Tuple[str, ...]]:
    """Derive an ImageFilesystemAnchor from the OCI inspection, bound to an ImageIdentityAnchor. Returns
    (anchor, ()) iff every check passes, else (None, sorted reasons). There is deliberately NO active-import
    parameter and NO caller `expected_fs_digest` parameter (F-2 structural rule)."""
    reasons: List[str] = []

    if not isinstance(image_identity_anchor, ii.ImageIdentityAnchor):
        return (None, ("IF-IDENTITY-ANCHOR-WRONG-TYPE",))

    oci_reg, _or = producer_registry.resolve(
        producer_id=oci_producer_id, evidence_type=_OCI_EVIDENCE_TYPE, gate_id=_OCI_GATE,
        application=application, now_utc=now_utc,
    )
    if oci_reg is None:
        reasons.append("IF-OCI-PRODUCER-UNAUTHORISED")

    manifest_digest = _get(oci_inspection, "manifest_digest")
    config_digest = _get(oci_inspection, "config_digest")
    filesystem_digest = _get(oci_inspection, "filesystem_digest")
    layer_digests = tuple(str(x) for x in _seq(oci_inspection.get("layer_digests") if isinstance(oci_inspection, Mapping) else None))

    for value, missing in ((manifest_digest, "IF-MANIFEST-DIGEST-MISSING"),
                           (config_digest, "IF-CONFIG-DIGEST-MISSING"),
                           (filesystem_digest, "IF-FILESYSTEM-DIGEST-MISSING")):
        if not value:
            reasons.append(missing)
        elif not _DIGEST_RE.match(value):
            reasons.append("IF-DIGEST-FORMAT")
    for ld in layer_digests:
        if not _DIGEST_RE.match(ld):
            reasons.append("IF-DIGEST-FORMAT")

    # bound to the identity anchor.
    if _get(oci_inspection, "image_id") != image_identity_anchor.image_id:
        reasons.append("IF-IMAGE-MISMATCH")
    if _get(oci_inspection, "source_sha") != image_identity_anchor.source_sha:
        reasons.append("IF-SOURCE-MISMATCH")
    if _get(oci_inspection, "candidate_id") != image_identity_anchor.candidate_id:
        reasons.append("IF-CANDIDATE-MISMATCH")

    # manifest / config / filesystem correlation: the OCI producer must attest the fs digest was derived from
    # the manifest + config set (represented as an explicit `filesystem_correlation` == True).
    if (isinstance(oci_inspection, Mapping) and oci_inspection.get("filesystem_correlation") is True) is not True:
        reasons.append("IF-CORRELATION-FAILED")

    tool_identity = _get(oci_inspection, "tool_identity")
    tool_version = _get(oci_inspection, "tool_version")
    if not tool_identity or not tool_version:
        reasons.append("IF-TOOL-IDENTITY-MISSING")
    extraction_method = _get(oci_inspection, "extraction_method")
    tool_digest = _get(oci_inspection, "tool_digest")

    gen = _get(oci_inspection, "generated_utc")
    dt = _parse_utc(gen)
    now = _parse_utc(now_utc)
    if dt is None or now is None:
        reasons.append("IF-UTC-INVALID")
    else:
        if dt > now:
            reasons.append("IF-FUTURE-DATED")
        if (now - dt) > datetime.timedelta(hours=max_age_hours):
            reasons.append("IF-STALE")

    if reasons:
        return (None, tuple(sorted(set(reasons))))

    anchor = ImageFilesystemAnchor(
        contract_version=CONTRACT_VERSION,
        application=application,
        candidate_id=image_identity_anchor.candidate_id,
        source_sha=image_identity_anchor.source_sha,
        image_id=image_identity_anchor.image_id,
        manifest_digest=manifest_digest,
        config_digest=config_digest,
        filesystem_digest=filesystem_digest,
        digest_algorithms=("sha256",),
        layer_digests=layer_digests,
        tool_identity=tool_identity,
        tool_version=tool_version,
        extraction_method=extraction_method,
        tool_digest=tool_digest,
        generated_utc=gen,
        producer_registry_ref=getattr(producer_registry, "registry_reference", ""),
        evidence_ref=_get(oci_inspection, "evidence_ref"),
        evidence_checksum="",
    )
    checksum = hashlib.sha256(_canonical(anchor._identity_fields()).encode("utf-8")).hexdigest()
    return (dataclasses.replace(anchor, evidence_checksum=checksum), tuple())
