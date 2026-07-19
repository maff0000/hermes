#!/usr/bin/env python3
"""HERMES FW-08 evidence-bound candidate-readiness evaluator v1 (PURE, I/O-free).

WO-HELM-HERMES-PR114-FW08-EXACT-AUDIT-CORRECTIONS-AND-CANONICAL-PREFLIGHT-CLOSURE-0001 (§12).
Owner: HERMES (Helm). Created (UTC): 2026-07-19. Contract version: 1.

WHY THIS EXISTS. The prior readiness evaluator (design/hermes_fw08_readiness_evaluator_v1.py) accepted a
record of BARE success booleans. R2D2 §12: a caller could fabricate readiness by handing it `True`s. This
module keeps the evaluation PURE and I/O-free, but its inputs are IMMUTABLE, TYPED EVIDENCE RECORDS — each
gate result is bound to gate_id + result + reason_code + source_sha + image_id (where applicable) +
evidence_checksum + producer/tool identity + UTC + contract_version. The evaluator recomputes each record's
binding checksum (so a bare `result=True` with no consistent structure is rejected) and validates cross-gate
consistency: same source_sha; same image_id for every post-build gate; correct lifecycle ordering; no stale
evidence; no duplicate-conflicting evidence; no missing evidence.

PURE: no I/O, no subprocess, no network, no secrets, stdlib only, deterministic. NOT imported by runtime.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

CONTRACT_VERSION = "1"
_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_UTC_ISO_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?(\+00:00|Z)$")

# Lifecycle-ordered gate ids. Pre-build gates carry NO image id; post-build (image-bearing) gates MUST all
# carry the same image id produced by the build gate.
GATE_ORDER: Tuple[str, ...] = (
    "G-TRUSTED-SOURCE",
    "G-CLEAN-CONTEXT",
    "G-EFFECTIVE-CONTEXT",
    "G-BUILD",
    "G-OCI-LABELS",
    "G-IMAGE-CONTENT",
    "G-SBOM",
    "G-VULN-SCAN",
)
_GATE_INDEX: Dict[str, int] = {g: i for i, g in enumerate(GATE_ORDER)}
_IMAGE_BEARING = frozenset({"G-BUILD", "G-OCI-LABELS", "G-IMAGE-CONTENT", "G-SBOM", "G-VULN-SCAN"})


@dataclass(frozen=True)
class GateEvidence:
    """Immutable typed evidence for one gate. `evidence_checksum` binds the safe fields (no secrets)."""

    gate_id: str
    result: bool
    reason_code: str
    source_sha: str
    producer: str
    tool_version: str
    at_utc: str
    evidence_checksum: str
    image_id: Optional[str] = None
    contract_version: str = CONTRACT_VERSION

    def bound_fields(self) -> Dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "gate_id": self.gate_id,
            "result": bool(self.result),
            "reason_code": self.reason_code,
            "source_sha": self.source_sha,
            "image_id": self.image_id,
            "producer": self.producer,
            "tool_version": self.tool_version,
            "at_utc": self.at_utc,
        }

    def recompute_checksum(self) -> str:
        blob = json.dumps(self.bound_fields(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def to_dict(self) -> Dict[str, object]:
        d = self.bound_fields()
        d["evidence_checksum"] = self.evidence_checksum
        return d


def make_gate_evidence(
    *, gate_id: str, result: bool, reason_code: str, source_sha: str, producer: str,
    tool_version: str, at_utc: str, image_id: Optional[str] = None,
) -> GateEvidence:
    """Build a GateEvidence with a correctly-bound checksum (used by the real producer path)."""
    partial = GateEvidence(
        gate_id=gate_id, result=result, reason_code=reason_code, source_sha=source_sha,
        producer=producer, tool_version=tool_version, at_utc=at_utc, image_id=image_id,
        evidence_checksum="",
    )
    return GateEvidence(
        gate_id=gate_id, result=result, reason_code=reason_code, source_sha=source_sha,
        producer=producer, tool_version=tool_version, at_utc=at_utc, image_id=image_id,
        evidence_checksum=partial.recompute_checksum(),
    )


@dataclass(frozen=True)
class EvidenceVerdict:
    ready: bool
    reason_codes: Tuple[str, ...]

    def to_dict(self) -> dict:
        return {"contract_version": CONTRACT_VERSION, "ready": self.ready,
                "reason_codes": list(self.reason_codes)}


def _parse_utc(value: str) -> Optional[datetime.datetime]:
    if not isinstance(value, str) or not _UTC_ISO_RE.match(value):
        return None
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _wellformed(rec: GateEvidence) -> Optional[str]:
    """Return None if structurally valid, else a reason code."""
    if rec.contract_version != CONTRACT_VERSION:
        return "EV-CONTRACT-VERSION"
    if rec.gate_id not in _GATE_INDEX:
        return "EV-UNKNOWN-GATE"
    if not isinstance(rec.result, bool):
        return "EV-RESULT-NOT-BOOL"
    if not isinstance(rec.reason_code, str) or not rec.reason_code:
        return "EV-REASON-MISSING"
    if not _FULL_SHA_RE.match(str(rec.source_sha)):
        return "EV-SOURCE-SHA-INVALID"
    if not isinstance(rec.producer, str) or not rec.producer.strip():
        return "EV-PRODUCER-MISSING"
    if not isinstance(rec.tool_version, str) or not rec.tool_version.strip():
        return "EV-TOOL-VERSION-MISSING"
    if _parse_utc(rec.at_utc) is None:
        return "EV-UTC-INVALID"
    if not isinstance(rec.evidence_checksum, str) or not _HEX64_RE.match(rec.evidence_checksum):
        return "EV-CHECKSUM-INVALID"
    if rec.evidence_checksum != rec.recompute_checksum():
        return "EV-CHECKSUM-MISMATCH"          # fabricated / tampered bare 'pass' -> rejected
    return None


def evaluate_evidence(
    records: Sequence[GateEvidence],
    *,
    expected_source_sha: str,
    expected_image_id: str,
    now_utc: str,
    max_evidence_age_hours: int = 24,
) -> EvidenceVerdict:
    """Evaluate evidence-bound readiness. `ready` is True ONLY if every required gate is present exactly
    once, structurally valid, checksum-consistent, source-/image-bound, correctly ordered, unexpired, and
    result=True. Deterministic, I/O-free."""
    fail: List[str] = []
    now = _parse_utc(now_utc)
    if now is None:
        return EvidenceVerdict(False, ("EV-NOW-INVALID",))

    by_gate: Dict[str, List[GateEvidence]] = {}
    for rec in records:
        code = _wellformed(rec)
        if code:
            fail.append(code)
            continue
        by_gate.setdefault(rec.gate_id, []).append(rec)

    # No missing evidence.
    for g in GATE_ORDER:
        if g not in by_gate:
            fail.append(f"EV-MISSING:{g}")

    # No duplicate-conflicting evidence (a gate must appear exactly once; a second record — identical or
    # conflicting — is rejected as a duplicate).
    for g, recs in by_gate.items():
        if len(recs) > 1:
            fail.append(f"EV-DUPLICATE:{g}")

    # If any structural / presence / duplicate problem, stop here (cannot trust cross-gate view).
    if fail:
        return EvidenceVerdict(False, tuple(sorted(set(fail))))

    ordered = [by_gate[g][0] for g in GATE_ORDER]

    # Cross-gate: single source SHA, correct image binding, result True, lifecycle ordering, freshness.
    prev_utc: Optional[datetime.datetime] = None
    for rec in ordered:
        if rec.source_sha != expected_source_sha:
            fail.append(f"EV-SOURCE-SHA-CONFLICT:{rec.gate_id}")
        if rec.gate_id in _IMAGE_BEARING:
            if not rec.image_id or rec.image_id != expected_image_id:
                fail.append(f"EV-IMAGE-ID-MISMATCH:{rec.gate_id}")
        else:
            if rec.image_id:
                fail.append(f"EV-PREBUILD-IMAGE-ID-SET:{rec.gate_id}")
        if rec.result is not True:
            fail.append(f"EV-GATE-NOT-PASSED:{rec.gate_id}")
        ts = _parse_utc(rec.at_utc)
        if ts is None:
            fail.append(f"EV-UTC-INVALID:{rec.gate_id}")
            continue
        if (now - ts) > datetime.timedelta(hours=max_evidence_age_hours):
            fail.append(f"EV-STALE:{rec.gate_id}")
        if ts > now:
            fail.append(f"EV-FUTURE-DATED:{rec.gate_id}")
        if prev_utc is not None and ts < prev_utc:
            fail.append(f"EV-OUT-OF-ORDER:{rec.gate_id}")
        prev_utc = ts

    return EvidenceVerdict(ready=(len(fail) == 0), reason_codes=tuple(sorted(set(fail))))
