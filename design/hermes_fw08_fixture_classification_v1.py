#!/usr/bin/env python3
"""HERMES FW-08 F-2 fixture classification (real-vs-synthetic authority) v1 (PURE).

WO-HELM-HERMES-FW08-F2-INDEPENDENT-ACTIVE-IMPORT-ANCHORS-IMPLEMENTATION-0001 (§16).
Owner: HERMES (Helm). Created (UTC): 2026-07-21. Contract version: 1.

WHY THIS EXISTS (F-2). "Realness" of candidate evidence is a GOVERNED property of the PRODUCER REGISTRATION,
NOT a self-declared field on the evidence. The whole F-2 correction ships FIXTURE producers whose
`real_evidence_authority` is False — so no fixture flow can ever be mistaken for a real, authorised image.

STRUCTURAL RULE. The evidence's OWN declared "classification" / "fixture_class" string is IGNORED for
realness. Flipping `evidence["classification"] = "REAL_CANDIDATE_EVIDENCE"` is structurally INSUFFICIENT,
because the authority is the resolved producer registration (`real_evidence_authority`), which a fixture
producer cannot set to True without a governed, externally-approved registration.

PURE: no I/O, no subprocess, no network, no secrets, stdlib only, deterministic. NOT imported by runtime.
"""
from __future__ import annotations

from typing import List, Mapping, Tuple

CONTRACT_VERSION = "1"

# Declarative classes a fixture may DECLARE about itself (advisory only — never confers realness).
FIXTURE_CLASSES = frozenset({
    "SYNTHETIC_BUILD_RESULT",
    "SYNTHETIC_OCI_INSPECTION",
    "SYNTHETIC_FILESYSTEM_EXPORT",
    "SYNTHETIC_ACTIVE_IMPORT_ANALYSIS",
    "TEST_ONLY",
})
# The ONLY class that would name real candidate evidence — but the NAME confers nothing; the registry does.
REAL_CLASS = "REAL_CANDIDATE_EVIDENCE"


def is_real_candidate(resolved_producer_registration: object) -> bool:
    """Realness is governed SOLELY by the resolved producer registration's `real_evidence_authority` flag."""
    return bool(getattr(resolved_producer_registration, "real_evidence_authority", False))


def require_real_candidate_evidence(
    evidence: Mapping[str, object],
    *,
    resolved_producer_registration: object,
) -> Tuple[bool, Tuple[str, ...]]:
    """(ok, reasons). ok is True ONLY if the RESOLVED producer registration carries
    `real_evidence_authority is True`. The evidence's own declared classification/fixture_class is
    deliberately IGNORED: changing it to REAL_CANDIDATE_EVIDENCE does NOT make this pass."""
    reasons: List[str] = []
    if resolved_producer_registration is None:
        return (False, ("FC-NOT-REAL-CANDIDATE",))
    if getattr(resolved_producer_registration, "real_evidence_authority", False) is not True:
        reasons.append("FC-NOT-REAL-CANDIDATE")
        reasons.append("FC-SYNTHETIC-PRODUCER")
    if reasons:
        return (False, tuple(sorted(set(reasons))))
    return (True, tuple())
