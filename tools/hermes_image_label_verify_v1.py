#!/usr/bin/env python3
"""HERMES OCI provenance-label verification tool v1.

WO-HELM-HERMES-PRE-STAGE-B-BUILD-CONTEXT-OCI-PROVENANCE-AND-SAFETY-GATE-CORRECTIONS-IMPLEMENTATION-0001.
Owner: HERMES (Helm). Purpose: verify that a FUTURE Stage-B candidate image carries MANDATORY, correct
source-SHA + build-UTC OCI provenance labels. Created (UTC): 2026-07-18. Contract version: 1.

INERT / PRE-STAGE-B. This tool builds NO image and runs NO `docker build`. It validates a set of labels
(as would be produced by `docker inspect --format '{{json .Config.Labels}}' <image>`) against the
contract. It is designed to be pointed at real image metadata by a future operator/CI, but here it is
exercised against fixture label sets only.

CONTRACT (labels MANDATORY for every FUTURE candidate image):
  * org.opencontainers.image.revision  -> the SOURCE SHA. MUST be a full 40-hex commit id. Abbreviated
    SHAs, branch names, tags and "latest" are REJECTED. There is NO env fallback that could invent a
    source SHA — a missing/empty value fails closed.
  * org.opencontainers.image.created   -> the BUILD UTC. MUST be a tz-aware ISO-8601 timestamp at UTC
    (offset 00:00). Naive (no tz) or non-UTC-offset values are REJECTED.
  * Candidate readiness FAILS if EITHER label is absent, empty or malformed.
  * When an expected source SHA is supplied (from the clean build context), the revision label MUST
    equal it — the image's declared source must match the exact-SHA context it was built from.

LEGACY SCOPE. The already-running legacy image `c5fc2a62f424` carries NO provenance labels. It is NOT
retroactively invalid — this requirement is scoped to FUTURE candidate images only (`is_candidate=True`).
The legacy image's label absence is a diagnostic limitation, not a Stage-B contract breach.

Bounded, deterministic, stdlib only, UTC only, no network, no secrets.
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Sequence

TOOL_VERSION = "1"
CONTRACT_VERSION = "1"

LABEL_REVISION = "org.opencontainers.image.revision"
LABEL_CREATED = "org.opencontainers.image.created"
REQUIRED_LABELS = (LABEL_REVISION, LABEL_CREATED)

LEGACY_IMAGE_DIGEST = "c5fc2a62f424"
LEGACY_IMAGE_NOTE = (
    "legacy image c5fc2a62f424 predates the provenance-label contract; NOT retroactively invalid; "
    "requirement applies to FUTURE candidate images only"
)

_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_REJECT_REVISION_LITERALS = frozenset({"latest", "head", "HEAD", "main", "master", ""})


class LabelVerifyError(Exception):
    """Raised for malformed tool input (e.g. undecodable inspect JSON). Contract failures are returned
    as a LabelVerifyResult with ok=False, never raised."""


@dataclass
class LabelVerifyResult:
    ok: bool
    is_candidate: bool
    source_sha: Optional[str]
    build_utc: Optional[str]
    errors: List[str] = field(default_factory=list)
    checks: Dict[str, bool] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        return {
            "tool_version": TOOL_VERSION,
            "contract_version": CONTRACT_VERSION,
            "ok": self.ok,
            "is_candidate": self.is_candidate,
            "source_sha": self.source_sha,
            "build_utc": self.build_utc,
            "checks": dict(sorted(self.checks.items())),
            "errors": list(self.errors),
            "legacy_scope_note": LEGACY_IMAGE_NOTE,
        }


def validate_source_sha(value: object) -> Optional[str]:
    """Return an error string if the revision/source-SHA value is not a full 40-hex commit id."""
    if value is None:
        return "revision label absent (no env fallback invents a source SHA)"
    if not isinstance(value, str):
        return f"revision label wrong type: {type(value).__name__}"
    v = value.strip()
    if v == "" or v in _REJECT_REVISION_LITERALS:
        return "revision label empty or a non-commit literal ('latest'/branch) rejected"
    if not _FULL_SHA_RE.match(v):
        return "revision label must be a full 40-hex commit id (abbreviated/branch/tag rejected)"
    return None


def validate_build_utc(value: object) -> Optional[str]:
    """Return an error string if the created/build-UTC value is not a tz-aware UTC ISO-8601 timestamp."""
    if value is None:
        return "created label absent"
    if not isinstance(value, str):
        return f"created label wrong type: {type(value).__name__}"
    v = value.strip()
    if v == "":
        return "created label empty"
    try:
        parsed = datetime.datetime.fromisoformat(v.replace("Z", "+00:00") if v.endswith("Z") else v)
    except ValueError:
        return "created label is not a valid ISO-8601 timestamp"
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return "created label is naive (no timezone); a tz-aware UTC timestamp is required"
    if parsed.utcoffset() != datetime.timedelta(0):
        return "created label is not UTC (offset must be 00:00)"
    return None


def verify_labels(
    labels: Optional[Mapping[str, object]],
    *,
    expected_source_sha: Optional[str] = None,
    is_candidate: bool = True,
) -> LabelVerifyResult:
    """Verify the provenance-label contract. Candidate images (is_candidate=True) FAIL closed when a
    label is absent/empty/malformed. A legacy image (is_candidate=False) is reported as label-absent but
    is NOT a contract breach (see LEGACY_IMAGE_NOTE)."""
    errors: List[str] = []
    checks: Dict[str, bool] = {}
    labels = dict(labels) if labels else {}

    revision = labels.get(LABEL_REVISION)
    created = labels.get(LABEL_CREATED)

    rev_err = validate_source_sha(revision)
    created_err = validate_build_utc(created)
    checks["revision_present_and_valid"] = rev_err is None
    checks["created_present_and_valid"] = created_err is None

    if rev_err:
        errors.append(f"{LABEL_REVISION}: {rev_err}")
    if created_err:
        errors.append(f"{LABEL_CREATED}: {created_err}")

    source_sha = revision.strip() if isinstance(revision, str) and rev_err is None else None
    build_utc = created.strip() if isinstance(created, str) and created_err is None else None

    if expected_source_sha is not None:
        match = source_sha is not None and source_sha == expected_source_sha
        checks["revision_matches_clean_context"] = match
        if not match:
            errors.append(
                f"{LABEL_REVISION}: does not match clean build-context source SHA "
                f"(expected {expected_source_sha}, got {source_sha})"
            )

    if not is_candidate:
        # Legacy image: absence is a diagnostic limitation, not a contract breach.
        return LabelVerifyResult(
            ok=True, is_candidate=False, source_sha=source_sha, build_utc=build_utc,
            errors=[], checks={**checks, "legacy_scope_exempt": True},
        )

    ok = len(errors) == 0
    return LabelVerifyResult(
        ok=ok, is_candidate=True, source_sha=source_sha, build_utc=build_utc,
        errors=errors, checks=checks,
    )


def candidate_readiness(
    labels: Optional[Mapping[str, object]],
    *,
    expected_source_sha: Optional[str] = None,
) -> LabelVerifyResult:
    """Stage-B candidate readiness gate. FAILS closed if provenance labels are absent/malformed."""
    return verify_labels(labels, expected_source_sha=expected_source_sha, is_candidate=True)


def labels_from_docker_inspect(inspect_payload: object) -> Dict[str, object]:
    """Extract `.Config.Labels` from a `docker inspect <image>` payload (a JSON array of objects, or a
    single object, or the `.Config.Labels` mapping directly). Does NOT run docker."""
    if isinstance(inspect_payload, str):
        try:
            inspect_payload = json.loads(inspect_payload)
        except json.JSONDecodeError as exc:
            raise LabelVerifyError(f"inspect payload is not valid JSON: {exc}") from exc
    node = inspect_payload
    if isinstance(node, list):
        if not node:
            return {}
        node = node[0]
    if isinstance(node, Mapping):
        if "Config" in node and isinstance(node["Config"], Mapping):
            labels = node["Config"].get("Labels")
            return dict(labels) if isinstance(labels, Mapping) else {}
        if "Labels" in node and isinstance(node["Labels"], Mapping):
            return dict(node["Labels"])
        # Already a labels mapping.
        return dict(node)
    raise LabelVerifyError("unrecognised docker inspect payload shape")


def compare_expected_actual(
    expected_labels: Mapping[str, object],
    actual_labels: Mapping[str, object],
) -> List[str]:
    """Return a list of mismatch descriptions between expected and actual required labels. Empty == match.
    Designed for a `docker inspect`-based check WITHOUT running a real build (fixtures in tests)."""
    mismatches: List[str] = []
    for key in REQUIRED_LABELS:
        exp = expected_labels.get(key)
        act = actual_labels.get(key)
        if exp != act:
            mismatches.append(f"{key}: expected {exp!r} != actual {act!r}")
    return mismatches


# --------------------------------------------------------------------------- CLI
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="HERMES OCI provenance-label verify v1 (INERT, no docker build).")
    p.add_argument("--labels-file", required=True, help="JSON file: docker-inspect output or a labels map.")
    p.add_argument("--expected-source-sha", default=None, help="Full 40-hex SHA from the clean context.")
    p.add_argument("--inspect", action="store_true", help="Treat the file as `docker inspect` output.")
    p.add_argument("--legacy", action="store_true", help="Evaluate as the legacy (non-candidate) image.")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        raw = json.loads(open(args.labels_file, "r", encoding="utf-8").read())
    except (OSError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"FAIL: cannot read labels file: {exc}\n")
        return 2
    try:
        labels = labels_from_docker_inspect(raw) if args.inspect else (dict(raw) if isinstance(raw, Mapping) else {})
    except LabelVerifyError as exc:
        sys.stderr.write(f"FAIL: {exc}\n")
        return 2
    result = verify_labels(
        labels, expected_source_sha=args.expected_source_sha, is_candidate=not args.legacy
    )
    sys.stdout.write(json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n")
    return 0 if result.ok else 3


if __name__ == "__main__":
    raise SystemExit(main())
