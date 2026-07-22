#!/usr/bin/env python3
"""HERMES FW-08 F-2 independent image-identity anchor v1 (PURE, fixture-backed).

WO-HELM-HERMES-FW08-F2-INDEPENDENT-ACTIVE-IMPORT-ANCHORS-IMPLEMENTATION-0001 (§7).
Owner: HERMES (Helm). Created (UTC): 2026-07-21. Contract version: 1.

WHY THIS EXISTS (F-2). The expected image id an active-import verdict compares against MUST come from an
INDEPENDENT governed source, never from the active-import evidence itself. This module derives an
`ImageIdentityAnchor` from TWO independent producer outputs: the GOVERNED BUILD RESULT (the authoritative
build-time image-id boundary) and a dedicated OCI IMAGE INSPECTION (an independent post-build confirmation).
The two MUST agree on the image id, and the image id MUST be a real content digest (sha256:...), not a tag,
candidate name, or label.

STRUCTURAL GUARANTEE (F-2). There is NO parameter through which active-import evidence — or a caller-supplied
"expected_image_id" — can flow into this function. The image id can ONLY come from build_result +
oci_inspection, both resolved through a governed producer registry. It is therefore structurally impossible
to make the anchor repeat a value taken from the record being validated.

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
import design.hermes_fw08_producer_independence_v1 as pi  # F2-R2 §6 build/OCI producer independence

CONTRACT_VERSION = "1"

IMAGE_ID_ALGORITHMS = frozenset({"sha256"})
IMAGE_ID_FORMATS = frozenset({"OCI_IMAGE_ID_SHA256"})
_IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_UTC_ISO_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?(\+00:00|Z)$")

# Producer class + gate the build-result and oci-inspection producers MUST hold.
_BUILD_EVIDENCE_TYPE = "BUILD_RESULT"
_BUILD_GATE = "STAGE_B_BUILD_RESULT"
_OCI_EVIDENCE_TYPE = "OCI_INSPECTION"
_OCI_GATE = "STAGE_B_OCI_INSPECTION"


@dataclass(frozen=True)
class ImageIdentityAnchor:
    """An image identity independently established from the governed build result and an OCI inspection.
    `evidence_checksum` binds the anchor's identity fields; `lifecycle_state` is ANCHORED once built."""

    contract_version: str
    application: str
    candidate_id: str
    source_sha: str
    image_id: str
    image_id_algorithm: str
    image_id_format: str
    build_result_evidence_ref: str
    oci_inspection_evidence_ref: str
    build_utc: str
    inspection_utc: str
    producer_identity: str
    producer_registry_ref: str
    evidence_checksum: str
    lifecycle_state: str

    def _identity_fields(self) -> Dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "application": self.application,
            "candidate_id": self.candidate_id,
            "source_sha": self.source_sha,
            "image_id": self.image_id,
            "image_id_algorithm": self.image_id_algorithm,
            "image_id_format": self.image_id_format,
            "build_result_evidence_ref": self.build_result_evidence_ref,
            "oci_inspection_evidence_ref": self.oci_inspection_evidence_ref,
            "build_utc": self.build_utc,
            "inspection_utc": self.inspection_utc,
            "producer_identity": self.producer_identity,
            "producer_registry_ref": self.producer_registry_ref,
            "lifecycle_state": self.lifecycle_state,
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


def build_image_identity_anchor(
    *,
    build_result: Mapping[str, object],
    oci_inspection: Mapping[str, object],
    candidate_id: str,
    source_sha: str,
    producer_registry: "pr.ProducerRegistry",
    build_producer_id: str,
    oci_producer_id: str,
    now_utc: str,
    application: str = "hermes",
    max_age_hours: int = 24,
) -> Tuple[Optional[ImageIdentityAnchor], Tuple[str, ...]]:
    """Derive an ImageIdentityAnchor from the governed build result + the OCI inspection. Returns
    (anchor, ()) iff every check passes, else (None, sorted reasons). The image id is taken from
    build_result["image_id"] and INDEPENDENTLY confirmed by oci_inspection["image_id"]; the two MUST match.
    There is deliberately NO `expected_image_id` parameter (F-2 structural rule)."""
    reasons: List[str] = []

    # Governed producer authority (registry-only; never reads the evidence).
    build_reg, _br = producer_registry.resolve(
        producer_id=build_producer_id, evidence_type=_BUILD_EVIDENCE_TYPE, gate_id=_BUILD_GATE,
        application=application, now_utc=now_utc,
    )
    if build_reg is None:
        reasons.append("II-BUILD-PRODUCER-UNAUTHORISED")
    oci_reg, _or = producer_registry.resolve(
        producer_id=oci_producer_id, evidence_type=_OCI_EVIDENCE_TYPE, gate_id=_OCI_GATE,
        application=application, now_utc=now_utc,
    )
    if oci_reg is None:
        reasons.append("II-OCI-PRODUCER-UNAUTHORISED")

    # --- F2-R2 §6: the build-result producer and the OCI-inspection producer MUST be GENUINELY INDEPENDENT.
    #     The pre-F2-R2 `producer_identity = f"{build}+{oci}"` composition happily accepted "X+X"; here we
    #     first reject the same-id degenerate case explicitly, then run the full evidence-derived independence
    #     evaluation (same registration / evidence object / evidence ref / copied payload / same tool+authority
    #     / dual-role / provenance sharing). Independence is DERIVED — never a caller boolean. ---
    if build_producer_id == oci_producer_id:
        reasons.append("II-BUILD-OCI-SAME-PRODUCER")
    if build_reg is not None and oci_reg is not None:
        for _pi_reason in pi.evaluate_producer_independence(
            build_result=build_result, oci_inspection=oci_inspection,
            build_registration=build_reg, oci_registration=oci_reg,
        ):
            reasons.append(_pi_reason)

    build_img = _get(build_result, "image_id")
    insp_img = _get(oci_inspection, "image_id")

    if not build_img or not insp_img:
        reasons.append("II-IMAGE-ID-MISSING")
    # tag / candidate-name / label masquerading as an image id.
    if build_img and (build_img == candidate_id or "sha256:" not in build_img):
        reasons.append("II-IMAGE-ID-IS-TAG-OR-LABEL")
    # must be a real content digest.
    if build_img and not _IMAGE_ID_RE.match(build_img):
        reasons.append("II-IMAGE-ID-NOT-DIGEST")
    # the two INDEPENDENT sources must agree.
    if build_img and insp_img and build_img != insp_img:
        reasons.append("II-BUILD-INSPECT-IMAGE-ID-MISMATCH")

    # candidate / source cross-checked across build_result, oci_inspection AND the args.
    if _get(build_result, "candidate_id") != candidate_id or _get(oci_inspection, "candidate_id") != candidate_id:
        reasons.append("II-CANDIDATE-MISMATCH")
    if _get(build_result, "source_sha") != source_sha or _get(oci_inspection, "source_sha") != source_sha:
        reasons.append("II-SOURCE-MISMATCH")

    build_utc = _get(build_result, "build_utc")
    inspection_utc = _get(oci_inspection, "inspection_utc")
    now = _parse_utc(now_utc)
    for value in (build_utc, inspection_utc):
        dt = _parse_utc(value)
        if dt is None or now is None:
            reasons.append("II-UTC-INVALID")
        else:
            if dt > now:
                reasons.append("II-FUTURE-DATED")
            if (now - dt) > datetime.timedelta(hours=max_age_hours):
                reasons.append("II-STALE")

    if reasons:
        return (None, tuple(sorted(set(reasons))))

    producer_identity = f"{build_producer_id}+{oci_producer_id}"
    anchor = ImageIdentityAnchor(
        contract_version=CONTRACT_VERSION,
        application=application,
        candidate_id=candidate_id,
        source_sha=source_sha,
        image_id=build_img,
        image_id_algorithm="sha256",
        image_id_format="OCI_IMAGE_ID_SHA256",
        build_result_evidence_ref=_get(build_result, "evidence_ref"),
        oci_inspection_evidence_ref=_get(oci_inspection, "evidence_ref"),
        build_utc=build_utc,
        inspection_utc=inspection_utc,
        producer_identity=producer_identity,
        producer_registry_ref=getattr(producer_registry, "registry_reference", ""),
        evidence_checksum="",
        lifecycle_state="ANCHORED",
    )
    checksum = hashlib.sha256(_canonical(anchor._identity_fields()).encode("utf-8")).hexdigest()
    return (dataclasses.replace(anchor, evidence_checksum=checksum), tuple())
