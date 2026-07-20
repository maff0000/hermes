#!/usr/bin/env python3
"""HERMES FW-08 vulnerability-disposition governance v1 (PURE).

WO-HELM-HERMES-PR114-FW08-EXACT-AUDIT-CORRECTIONS-AND-CANONICAL-PREFLIGHT-CLOSURE-0001 (§11).
Owner: HERMES (Helm). Created (UTC): 2026-07-19. Contract version: 1.

WHY THIS EXISTS. The prior vuln gate governed a CRITICAL/HIGH finding on the mere presence of a truthy
`governance_id` in the allow-list — R2D2 §11: a truthy id alone is not governance. A vulnerability may be
governed ONLY by an EXACT, TYPED, UNEXPIRED disposition binding EVERY required field: contract_version,
vuln_id, package, installed_version, image_id, source_sha, severity, reason, risk_owner, approval_authority,
creation UTC, expiry UTC, scanner id + version, vuln-DB id + timestamp, evidence_checksum, disposition_id.

Rejected: truthy-id-alone; wildcard vuln/package/version/image; missing reason/owner/approval/expiry;
expired; scanner-mismatch; image-mismatch; source-mismatch; checksum-mismatch; unknown-severity. Critical/
high remain BLOCKING unless an exact unexpired governed disposition matches EVERY required field. No blanket
exception.

PURE: no I/O, no subprocess, no network, no secrets, stdlib only, deterministic. NOT imported by runtime.
"""
from __future__ import annotations

import datetime
import re
from dataclasses import dataclass
from typing import Mapping, Optional, Tuple

CONTRACT_VERSION = "1"
SEVERITY_TAXONOMY = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "NEGLIGIBLE", "UNKNOWN")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_UTC_ISO_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?(\+00:00|Z)$")
_WILDCARDS = ("*", "?", "")


class VulnDispositionError(Exception):
    """Fail-closed error for a malformed / under-specified vuln disposition."""


_REQUIRED_STR_FIELDS = (
    "contract_version", "disposition_id", "vuln_id", "package", "installed_version", "image_id",
    "source_sha", "severity", "reason", "risk_owner", "approval_authority", "created_utc", "expiry_utc",
    "scanner_id", "scanner_version", "vuln_db_id", "vuln_db_timestamp_utc", "evidence_checksum",
)


@dataclass(frozen=True)
class VulnDisposition:
    contract_version: str
    disposition_id: str
    vuln_id: str
    package: str
    installed_version: str
    image_id: str
    source_sha: str
    severity: str
    reason: str
    risk_owner: str
    approval_authority: str
    created_utc: str
    expiry_utc: str
    scanner_id: str
    scanner_version: str
    vuln_db_id: str
    vuln_db_timestamp_utc: str
    evidence_checksum: str


def from_dict(raw: Mapping[str, object]) -> "VulnDisposition":
    missing = [k for k in _REQUIRED_STR_FIELDS if k not in raw]
    if missing:
        raise VulnDispositionError(f"vuln disposition missing fields: {missing}")
    return VulnDisposition(**{k: str(raw[k]) for k in _REQUIRED_STR_FIELDS})


def _no_wildcard(value: str, field: str) -> None:
    if not isinstance(value, str) or value.strip() in _WILDCARDS or "*" in value or "?" in value:
        raise VulnDispositionError(f"vuln disposition {field} must be an exact value (no wildcard/blank)")


def validate_vuln_disposition(d: "VulnDisposition") -> None:
    """Fail closed on any malformed / wildcarded / under-specified disposition."""
    if not isinstance(d, VulnDisposition):
        raise VulnDispositionError("not a VulnDisposition")
    if d.contract_version != CONTRACT_VERSION:
        raise VulnDispositionError("contract_version mismatch")
    for f in ("disposition_id", "vuln_id", "package", "installed_version", "image_id", "source_sha",
              "reason", "risk_owner", "approval_authority", "scanner_id", "scanner_version", "vuln_db_id"):
        _no_wildcard(getattr(d, f), f)
    if d.severity not in SEVERITY_TAXONOMY:
        raise VulnDispositionError(f"unknown severity {d.severity!r}")
    if not _HEX64_RE.match(d.evidence_checksum):
        raise VulnDispositionError("evidence_checksum must be 64-hex")
    for f in ("created_utc", "expiry_utc", "vuln_db_timestamp_utc"):
        if not _UTC_ISO_RE.match(getattr(d, f)):
            raise VulnDispositionError(f"{f} must be tz-aware UTC ISO-8601")


def _parse(value: str) -> Optional[datetime.datetime]:
    if not _UTC_ISO_RE.match(value):
        return None
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def governs(
    d: "VulnDisposition",
    finding: Mapping[str, object],
    *,
    image_id: str,
    source_sha: str,
    scanner_id: str,
    scanner_version: str,
    now_utc: str,
) -> Tuple[bool, str]:
    """Return (True, "GOVERNED") iff `d` governs `finding` under the current scan context. Every required
    field must match EXACTLY and the disposition must be well-formed and unexpired. Otherwise (False, reason)."""
    try:
        validate_vuln_disposition(d)
    except VulnDispositionError as exc:
        return (False, f"MALFORMED:{exc}")
    fid = str(finding.get("id", ""))
    fpkg = str(finding.get("package", ""))
    fver = str(finding.get("installed_version", ""))
    fsev = str(finding.get("severity", "")).upper()
    if d.vuln_id != fid:
        return (False, "VULN-ID-MISMATCH")
    if d.package != fpkg:
        return (False, "PACKAGE-MISMATCH")
    if d.installed_version != fver:
        return (False, "VERSION-MISMATCH")
    if d.severity != fsev:
        return (False, "SEVERITY-MISMATCH")
    if d.image_id != image_id:
        return (False, "IMAGE-MISMATCH")
    if d.source_sha != source_sha:
        return (False, "SOURCE-MISMATCH")
    if d.scanner_id != scanner_id:
        return (False, "SCANNER-MISMATCH")
    if d.scanner_version != scanner_version:
        return (False, "SCANNER-VERSION-MISMATCH")
    exp = _parse(d.expiry_utc)
    now = _parse(now_utc)
    if exp is None or now is None:
        return (False, "TIMESTAMP-UNPARSEABLE")
    if now >= exp:
        return (False, "EXPIRED")
    return (True, "GOVERNED")
