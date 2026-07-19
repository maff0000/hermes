#!/usr/bin/env python3
"""HERMES FW-08 candidate-readiness evaluator v1 (PURE, immutable inputs, NO I/O).

WO-HELM-HERMES-FW08-GOVERNED-STAGE-B-IMAGE-BUILD-ENFORCEMENT-IMPLEMENTATION-0001.
Owner: HERMES (Helm). Created (UTC): 2026-07-19. Contract version: 1.

The evaluator is a pure function over an immutable inputs record. It returns `ready=True` ONLY when
EVERY gate is satisfied (§19). Any single failing gate yields `ready=False` with the exact reason codes.
Candidate readiness is NOT publication, NOT deployment, NOT activation — a `consumer_live` or
`shadow_enabled` or `publish_attempted` or `deploy_attempted` flag being set FORCES rejection.

PURE: no I/O, no subprocess, no network, no secrets, stdlib only, deterministic. NOT imported by runtime.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

CONTRACT_VERSION = "1"


@dataclass(frozen=True)
class ReadinessInputs:
    """Immutable set of gate results. All booleans are POSITIVE assertions (True == gate satisfied)
    except the four SAFETY flags, which must be False for a candidate to be ready."""

    trusted_source_verified: bool
    clean_context_manifest_valid: bool
    effective_docker_context_valid: bool
    prohibited_findings_count: int
    secret_findings_count: int
    secret_findings_all_governed_nonsecret: bool  # only True if policy explicitly classified matches
    build_succeeded: bool
    image_id_captured: bool
    oci_labels_exact_match: bool
    image_content_passed: bool
    sbom_passed: bool
    vuln_scan_passed: bool
    # SAFETY flags — MUST be False (mechanical no-publish / no-deploy / no-activation guard).
    publish_attempted: bool
    deploy_attempted: bool
    consumer_live: bool
    shadow_enabled: bool
    phase2_activated: bool


@dataclass(frozen=True)
class ReadinessVerdict:
    ready: bool
    reason_codes: Tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "contract_version": CONTRACT_VERSION,
            "ready": self.ready,
            "reason_codes": list(self.reason_codes),
        }


def evaluate(inputs: ReadinessInputs) -> ReadinessVerdict:
    """Return a candidate-readiness verdict. `ready` is True ONLY if every gate passes and every safety
    flag is clear. Deterministic and side-effect free."""
    fail: List[str] = []

    if not inputs.trusted_source_verified:
        fail.append("R-TRUSTED-SOURCE-NOT-VERIFIED")
    if not inputs.clean_context_manifest_valid:
        fail.append("R-CLEAN-CONTEXT-MANIFEST-INVALID")
    if not inputs.effective_docker_context_valid:
        fail.append("R-EFFECTIVE-CONTEXT-INVALID")
    if inputs.prohibited_findings_count != 0:
        fail.append("R-PROHIBITED-PATHS-PRESENT")
    if inputs.secret_findings_count != 0 and not inputs.secret_findings_all_governed_nonsecret:
        fail.append("R-SECRET-FINDINGS-PRESENT")
    if not inputs.build_succeeded:
        fail.append("R-BUILD-NOT-SUCCEEDED")
    if not inputs.image_id_captured:
        fail.append("R-IMAGE-ID-NOT-CAPTURED")
    if not inputs.oci_labels_exact_match:
        fail.append("R-OCI-LABELS-NOT-EXACT")
    if not inputs.image_content_passed:
        fail.append("R-IMAGE-CONTENT-FAILED")
    if not inputs.sbom_passed:
        fail.append("R-SBOM-FAILED")
    if not inputs.vuln_scan_passed:
        fail.append("R-VULN-SCAN-FAILED")

    # SAFETY flags — any set flag forces rejection (readiness != publication/deployment/activation).
    if inputs.publish_attempted:
        fail.append("R-PUBLISH-ATTEMPTED")
    if inputs.deploy_attempted:
        fail.append("R-DEPLOY-ATTEMPTED")
    if inputs.consumer_live:
        fail.append("R-CONSUMER-LIVE-TRUE")
    if inputs.shadow_enabled:
        fail.append("R-SHADOW-ENABLED-TRUE")
    if inputs.phase2_activated:
        fail.append("R-PHASE2-ACTIVATED")

    return ReadinessVerdict(ready=(len(fail) == 0), reason_codes=tuple(fail))
