#!/usr/bin/env python3
"""HERMES FW-08 F2-R2 build/OCI producer-independence enforcement v1 (PURE, fixture-backed).

WO-HELM-HERMES-FW08-F2-R2-BUILD-AND-OCI-PRODUCER-INDEPENDENCE-IMPLEMENTATION-0001 (§3,§4,§5).
Owner: HERMES (Helm). Created (UTC): 2026-07-22. Contract version: 1.

WHY THIS EXISTS (F2-R2). The F-2 anchor chain resolves a GOVERNED BUILD RESULT producer and a dedicated
OCI/IMAGE-INSPECTION producer separately, but NOTHING enforced that the two are GENUINELY INDEPENDENT.
`build_image_identity_anchor` composed `producer_identity = f"{build_producer_id}+{oci_producer_id}"` — which
happily accepts "X+X" — and `assemble_anchor_bundle` resolved all three producers without an independence
check. The F2-R2 invariant is:

    BUILD_RESULT_PRODUCER != OCI_INSPECTION_PRODUCER  (plus independent evidence provenance)

so that the SAME producer / process / evidence object / evidence reference / provenance / registration cannot
both CREATE and CONFIRM the same image. Independence is DERIVED from the bound evidence + registrations — it
is NEVER a caller boolean (`independent=true`). There is deliberately NO `independent` parameter on
`evaluate_producer_independence`.

DUAL-ROLE POLICY. The default policy is DUAL_ROLE_FORBIDDEN. A `DualRoleException` type is MODELLED here so a
future governed WO can activate a narrowly-scoped, independently-controlled dual-role exception — but this WO
does NOT activate it. Even a fully-valid exception fails closed with
F2R2-DUAL-ROLE-EXCEPTION-NOT-ACTIVATED.

PURE: no I/O, no subprocess, no network, no secrets, stdlib only, deterministic. NOT imported by runtime.
This module imports NOTHING from the active-import evidence module (avoids an import cycle) and does NOT
import the F-2 anchor modules.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import dataclass
from typing import List, Mapping, Optional, Tuple

import design.hermes_fw08_producer_registry_v1 as pr

CONTRACT_VERSION = "1"

DUAL_ROLE_POLICY_DEFAULT = "DUAL_ROLE_FORBIDDEN"

# Evidence types / gates that identify each ROLE. A build producer authorised for any OCI type/gate (or an OCI
# producer authorised for any build type/gate) is claiming BOTH roles — dual-role.
_BUILD_EVIDENCE_TYPE = "BUILD_RESULT"
_BUILD_GATE = "STAGE_B_BUILD_RESULT"
_OCI_EVIDENCE_TYPE = "OCI_INSPECTION"
_OCI_GATE = "STAGE_B_OCI_INSPECTION"

# Markers that reveal OCI evidence was (illegitimately) derived from the active-import path.
_ACTIVE_IMPORT_MARKERS = ("active_import", "ACTIVE_IMPORT")


def _canonical(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _get(m: object, key: str) -> str:
    return str(m.get(key, "")) if isinstance(m, Mapping) else ""


def _has(m: object, key: str) -> bool:
    return isinstance(m, Mapping) and bool(str(m.get(key, "")).strip())


# ============================================================================ §4 dual-role exception (INERT)
@dataclass(frozen=True)
class DualRoleException:
    """A MODELLED, INERT governed exception permitting a narrowly-scoped dual-role. NEVER ACTIVATED in this
    WO: `evaluate_producer_independence` still rejects with F2R2-DUAL-ROLE-EXCEPTION-NOT-ACTIVATED even for a
    structurally-valid exception. The type + validator exist so a future governed WO can wire activation with
    an already-proven shape. `exception_digest` binds the exception fields (excludes itself)."""

    policy_record_id: str
    build_execution_identity: str
    oci_execution_identity: str
    build_evidence_ref: str
    oci_evidence_ref: str
    build_authority_approval: str
    oci_authority_approval: str
    build_tool_digest: str
    oci_tool_digest: str
    exception_reason: str
    approval_authority: str
    created_by: str
    # A governance marker asserting the two executions are independently controlled DESPITE a shared tool
    # digest (e.g. two separately-approved deployments of the same signed tool). Empty == not marked.
    independently_controlled_execution: str
    exception_digest: str

    def _identity_fields(self) -> dict:
        return {
            "policy_record_id": self.policy_record_id,
            "build_execution_identity": self.build_execution_identity,
            "oci_execution_identity": self.oci_execution_identity,
            "build_evidence_ref": self.build_evidence_ref,
            "oci_evidence_ref": self.oci_evidence_ref,
            "build_authority_approval": self.build_authority_approval,
            "oci_authority_approval": self.oci_authority_approval,
            "build_tool_digest": self.build_tool_digest,
            "oci_tool_digest": self.oci_tool_digest,
            "exception_reason": self.exception_reason,
            "approval_authority": self.approval_authority,
            "created_by": self.created_by,
            "independently_controlled_execution": self.independently_controlled_execution,
        }

    def to_dict(self) -> dict:
        d = self._identity_fields()
        d["exception_digest"] = self.exception_digest
        return d

    @staticmethod
    def compute_digest(identity_fields: Mapping[str, object]) -> str:
        return hashlib.sha256(_canonical(dict(identity_fields)).encode("utf-8")).hexdigest()

    def recompute_digest(self) -> str:
        return DualRoleException.compute_digest(self._identity_fields())


def new_dual_role_exception(**kwargs: object) -> DualRoleException:
    """Construct a DualRoleException with its `exception_digest` computed over its own identity fields. The
    ONLY blessed constructor. `independently_controlled_execution` defaults to "" (not marked)."""
    kwargs.setdefault("independently_controlled_execution", "")
    kwargs["exception_digest"] = ""
    exc0 = DualRoleException(**kwargs)  # type: ignore[arg-type]
    return dataclasses.replace(exc0, exception_digest=exc0.recompute_digest())


def validate_dual_role_exception(exc: object) -> Tuple[str, ...]:
    """Validate a DualRoleException's SHAPE. Returns () iff structurally valid (fail-closed), else a sorted
    tuple of DRE-* reason codes. NB: a valid exception is still NOT activated by
    `evaluate_producer_independence` in this WO."""
    reasons: List[str] = []
    if not isinstance(exc, DualRoleException):
        return ("DRE-NOT-AN-EXCEPTION",)
    b_exec = str(exc.build_execution_identity).strip()
    o_exec = str(exc.oci_execution_identity).strip()
    if not b_exec or not o_exec or b_exec == o_exec:
        reasons.append("DRE-SAME-EXECUTION")
    b_ref = str(exc.build_evidence_ref).strip()
    o_ref = str(exc.oci_evidence_ref).strip()
    if not b_ref or not o_ref or b_ref == o_ref:
        reasons.append("DRE-SAME-EVIDENCE-REF")
    b_appr = str(exc.build_authority_approval).strip()
    o_appr = str(exc.oci_authority_approval).strip()
    if not b_appr or not o_appr or b_appr == o_appr:
        reasons.append("DRE-SAME-APPROVAL")
    # distinct tool digests OR an explicit independently-controlled-execution marker.
    if str(exc.build_tool_digest) == str(exc.oci_tool_digest) and \
            not str(exc.independently_controlled_execution).strip():
        reasons.append("DRE-SAME-TOOL")
    if not str(exc.exception_reason).strip():
        reasons.append("DRE-REASON-MISSING")
    appr = str(exc.approval_authority).strip()
    if not appr or appr == str(exc.created_by).strip() or appr in (b_exec, o_exec):
        reasons.append("DRE-SELF-APPROVED")
    if exc.exception_digest != exc.recompute_digest():
        reasons.append("DRE-DIGEST-TAMPER")
    return tuple(sorted(set(reasons)))


# =========================================================================== §3/§5 producer independence
def _authorised_for(reg: "pr.ProducerRegistration", *, evidence_type: str, gate: str) -> bool:
    """True iff `reg` is scoped for BOTH the given evidence type AND gate (i.e. it can act in that role)."""
    return evidence_type in reg.allowed_evidence_types and gate in reg.allowed_gate_ids


def evaluate_producer_independence(
    *,
    build_result: Mapping[str, object],
    oci_inspection: Mapping[str, object],
    build_registration: "pr.ProducerRegistration",
    oci_registration: "pr.ProducerRegistration",
    dual_role_exception: Optional["DualRoleException"] = None,
) -> Tuple[str, ...]:
    """Return () iff the build-result producer and the OCI-inspection producer are GENUINELY INDEPENDENT,
    else a sorted tuple of F2R2-* (and, when a dual-role exception is supplied and malformed, DRE-*) reason
    codes. Fail-closed. Independence is DERIVED from the bound registrations + evidence — there is NO
    `independent` parameter."""
    reasons: List[str] = []

    # ---- registration typing / identity / registration-record independence.
    if not isinstance(build_registration, pr.ProducerRegistration) or \
            not isinstance(oci_registration, pr.ProducerRegistration):
        return ("F2R2-REGISTRATION-WRONG-TYPE",)

    if build_registration.producer_id == oci_registration.producer_id:
        reasons.append("F2R2-SAME-PRODUCER-ID")
    if build_registration.registry_record_checksum == oci_registration.registry_record_checksum:
        reasons.append("F2R2-SAME-REGISTRATION")

    # ---- evidence-object / reference / payload independence.
    if build_result is oci_inspection:
        reasons.append("F2R2-SAME-EVIDENCE-OBJECT")

    b_ref = _get(build_result, "evidence_ref")
    o_ref = _get(oci_inspection, "evidence_ref")
    if not b_ref or not o_ref:
        reasons.append("F2R2-MISSING-EVIDENCE-REF")
    elif b_ref == o_ref:
        reasons.append("F2R2-SAME-EVIDENCE-REF")

    # a copied payload (byte-equal under canonical json) means one record was cloned from the other.
    if isinstance(build_result, Mapping) and isinstance(oci_inspection, Mapping) and \
            _canonical(dict(build_result)) == _canonical(dict(oci_inspection)):
        reasons.append("F2R2-COPIED-PAYLOAD")

    # ---- same tool AND authority: the SAME signed tool run under the SAME approver is not two independent
    #      confirmations. ALL THREE (tool_identity, tool_digest, approval_authority) must coincide — the
    #      genuinely-independent F-2 fixtures share tool_digest + authority but DIFFER in tool_identity, so
    #      they are NOT hit.
    if build_registration.tool_identity == oci_registration.tool_identity and \
            build_registration.tool_digest == oci_registration.tool_digest and \
            build_registration.approval_authority == oci_registration.approval_authority:
        reasons.append("F2R2-SAME-TOOL-AND-AUTHORITY")

    # ---- dual-role: one registration is authorised for BOTH roles.
    build_claims_oci = _authorised_for(build_registration, evidence_type=_OCI_EVIDENCE_TYPE, gate=_OCI_GATE)
    oci_claims_build = _authorised_for(oci_registration, evidence_type=_BUILD_EVIDENCE_TYPE, gate=_BUILD_GATE)
    if build_claims_oci or oci_claims_build:
        if dual_role_exception is None:
            reasons.append("F2R2-DUAL-ROLE-WITHOUT-EXCEPTION")
        else:
            dre = validate_dual_role_exception(dual_role_exception)
            if dre:
                reasons.extend(dre)
            else:
                # a valid exception is STILL not activated in this WO — fail closed.
                reasons.append("F2R2-DUAL-ROLE-EXCEPTION-NOT-ACTIVATED")

    # ---- OCI derived from build alone: the OCI record must carry its OWN image digests, and must not declare
    #      it was derived from the build evidence.
    oci_has_own = _has(oci_inspection, "manifest_digest") and _has(oci_inspection, "config_digest") and \
        _has(oci_inspection, "filesystem_digest")
    if not oci_has_own or _get(oci_inspection, "derived_from") == b_ref:
        reasons.append("F2R2-OCI-DERIVED-FROM-BUILD")

    # ---- OCI derived from active-import: never let the import path masquerade as an independent OCI source.
    o_derived = _get(oci_inspection, "derived_from")
    if any(m in o_ref for m in _ACTIVE_IMPORT_MARKERS) or any(m in o_derived for m in _ACTIVE_IMPORT_MARKERS):
        reasons.append("F2R2-OCI-DERIVED-FROM-ACTIVE-IMPORT")

    # ---- shared provenance: neither record may point its provenance at the other's evidence ref.
    if b_ref and _get(oci_inspection, "provenance_ref") == b_ref:
        reasons.append("F2R2-SHARED-PROVENANCE-REF")
    if o_ref and _get(build_result, "provenance_ref") == o_ref:
        reasons.append("F2R2-SHARED-PROVENANCE-REF")

    return tuple(sorted(set(reasons)))


def build_oci_producers_independent(
    build_registration: "pr.ProducerRegistration",
    oci_registration: "pr.ProducerRegistration",
) -> bool:
    """Convenience for callers that hold ONLY the two registrations (e.g. the bundle): True iff the build and
    OCI producers are independent by producer id + registration record + dual-role scope. Derives from the
    registrations alone (no evidence payloads); returns False fail-closed on any independence reason."""
    if not isinstance(build_registration, pr.ProducerRegistration) or \
            not isinstance(oci_registration, pr.ProducerRegistration):
        return False
    if build_registration.producer_id == oci_registration.producer_id:
        return False
    if build_registration.registry_record_checksum == oci_registration.registry_record_checksum:
        return False
    if _authorised_for(build_registration, evidence_type=_OCI_EVIDENCE_TYPE, gate=_OCI_GATE):
        return False
    if _authorised_for(oci_registration, evidence_type=_BUILD_EVIDENCE_TYPE, gate=_BUILD_GATE):
        return False
    if build_registration.tool_identity == oci_registration.tool_identity and \
            build_registration.tool_digest == oci_registration.tool_digest and \
            build_registration.approval_authority == oci_registration.approval_authority:
        return False
    return True
