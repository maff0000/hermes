#!/usr/bin/env python3
"""HERMES FW-08 F2-R3 execution/process independence contract v1 (PURE, fixture-backed).

WO-HELM-HERMES-FW08-F2-R3-EXECUTION-CONTENT-INDEPENDENCE-AND-REAL-IMAGE-PROOF-IMPLEMENTATION-0001 (§4,§5).
Owner: HERMES (Helm). Created (UTC): 2026-07-27. Contract version: 1.

WHY THIS EXISTS (F2-R3 §4/§5). F2-R2 proved the build-result PRODUCER and the OCI-inspection PRODUCER were
independent at the REGISTRATION + EVIDENCE level. But a single PROCESS / runner / host could still have
emitted BOTH sets of evidence under two producer LABELS — registration independence does not imply EXECUTION
independence. F2-R3 adds an EXECUTION-IDENTITY binding: the build evidence and the OCI evidence must come from
DISTINCT EXECUTIONS (distinct execution id, distinct process, distinct runner/host container), neither a copy
nor a relabelling of the other, and neither emitting BOTH roles. §5 additionally forbids one EXECUTABLE run
under one AUTHORITY from confirming its own output — independence is keyed on the executable_digest + authority
+ runner, NOT on tool/producer NAMES (a renamed tool or a relabelled producer id must NOT bypass).

Independence is DERIVED from the two bound ExecutionIdentity records. There is deliberately NO `independent`
boolean parameter on `evaluate_execution_independence` — a caller cannot assert independence.

DUAL-ROLE. A governed dual-role exception may be SUPPLIED, but it is validated and STILL NOT ACTIVATED in this
WO — even a structurally-valid exception fails closed with EI-DUAL-ROLE-EXCEPTION-NOT-ACTIVATED.

PURE: no I/O, no subprocess, no network, no secrets, stdlib only, deterministic. NOT imported by runtime. This
module imports NOTHING from the active-import evidence module (avoids any import cycle).
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Tuple

CONTRACT_VERSION = "1"

EXECUTION_CLASSIFICATIONS = frozenset({
    "SYNTHETIC_TEST_EXECUTION",
    "REAL_EXECUTION",
    "REVOKED_EXECUTION",
    "INVALID_EXECUTION",
})


def _canonical(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


# ============================================================================ §4 execution identity
@dataclass(frozen=True)
class ExecutionIdentity:
    """A frozen binding of ONE execution's full identity: contract version, application, a unique execution id,
    the producer + its registration ref, the process / runner / host-container / tool identities, the
    executable + invocation digests, the start/completion timestamps, the authority reference, the evidence
    output reference, the execution attestation digest, and a self digest. `classification` is one of
    EXECUTION_CLASSIFICATIONS. The digest covers all identity fields EXCEPT itself."""

    contract_version: str
    application: str
    execution_id: str
    producer_id: str
    producer_registration_ref: str
    process_identity: str
    runner_identity: str
    host_container_identity: str
    tool_identity: str
    executable_digest: str
    invocation_digest: str
    start_utc: str
    completion_utc: str
    authority_reference: str
    evidence_output_reference: str
    execution_attestation_digest: str
    classification: str

    def identity_fields(self) -> Dict[str, object]:
        """Canonical fields the attestation digest covers (EXCLUDES execution_attestation_digest itself)."""
        return {
            "contract_version": self.contract_version,
            "application": self.application,
            "execution_id": self.execution_id,
            "producer_id": self.producer_id,
            "producer_registration_ref": self.producer_registration_ref,
            "process_identity": self.process_identity,
            "runner_identity": self.runner_identity,
            "host_container_identity": self.host_container_identity,
            "tool_identity": self.tool_identity,
            "executable_digest": self.executable_digest,
            "invocation_digest": self.invocation_digest,
            "start_utc": self.start_utc,
            "completion_utc": self.completion_utc,
            "authority_reference": self.authority_reference,
            "evidence_output_reference": self.evidence_output_reference,
            "classification": self.classification,
        }

    def to_dict(self) -> Dict[str, object]:
        d = self.identity_fields()
        d["execution_attestation_digest"] = self.execution_attestation_digest
        return d

    @staticmethod
    def compute_digest(identity_fields: Mapping[str, object]) -> str:
        return hashlib.sha256(_canonical(dict(identity_fields)).encode("utf-8")).hexdigest()

    def recompute_digest(self) -> str:
        return ExecutionIdentity.compute_digest(self.identity_fields())


def new_execution_identity(**kwargs: object) -> ExecutionIdentity:
    """Construct an ExecutionIdentity with its `execution_attestation_digest` computed over its identity
    fields. The ONLY blessed constructor. Constructing one blesses INTEGRITY only; it does not confer
    independence — that is derived by `evaluate_execution_independence`."""
    kwargs.setdefault("contract_version", CONTRACT_VERSION)
    kwargs.setdefault("classification", "SYNTHETIC_TEST_EXECUTION")
    kwargs["execution_attestation_digest"] = ""
    e0 = ExecutionIdentity(**kwargs)  # type: ignore[arg-type]
    return dataclasses.replace(e0, execution_attestation_digest=e0.recompute_digest())


# --- fields that do NOT identify a distinct execution (a copy/relabel changes ONLY these). Canonicalising an
#     execution MINUS these must DIFFER between two genuinely-distinct executions.
_RELABEL_FIELDS = ("execution_id", "producer_id")


def _canonical_minus(exec_identity: ExecutionIdentity, drop: Tuple[str, ...]) -> str:
    fields = exec_identity.identity_fields()
    for k in drop:
        fields.pop(k, None)
    return _canonical(fields)


def evaluate_execution_independence(
    *,
    build_execution: object,
    oci_execution: object,
    candidate_mode: str,
    dual_role_exception: object = None,
) -> Tuple[str, ...]:
    """Return () iff the build execution and the OCI execution are GENUINELY INDEPENDENT executions, else a
    sorted tuple of EI-* reason codes. Fail-closed. Independence is DERIVED from the two ExecutionIdentity
    records — there is NO `independent` parameter. `candidate_mode` scopes the synthetic-in-real check.

    Rejects:
      * either not an ExecutionIdentity                              -> EI-WRONG-TYPE
      * build_execution is oci_execution (object identity)          -> EI-SAME-OBJECT
      * equal execution_id                                          -> EI-SAME-EXECUTION-ID
      * equal process_identity, OR runner+host all equal           -> EI-SAME-PROCESS
      * canonical(build - relabel) == canonical(oci - relabel)      -> EI-COPIED-EXECUTION-EVIDENCE
      * one execution emits both roles (id in other's provenance /  -> EI-ONE-EXECUTION-BOTH-ROLES
        equal evidence_output_reference)
      * §5 same executable_digest AND authority AND runner          -> EI-COMMON-EXECUTABLE-AND-AUTHORITY
      * missing execution_id/process/executable_digest/attestation  -> EI-MISSING-EXECUTION-IDENTITY
      * attestation digest doesn't recompute                        -> EI-UNVERIFIABLE-EXECUTION
      * SYNTHETIC_TEST_EXECUTION under REAL_CANDIDATE               -> EI-SYNTHETIC-EXECUTION-IN-REAL-MODE
      * a supplied dual_role_exception (validated but not activated) -> EI-DUAL-ROLE-EXCEPTION-NOT-ACTIVATED
    """
    reasons: List[str] = []

    if not isinstance(build_execution, ExecutionIdentity) or not isinstance(oci_execution, ExecutionIdentity):
        return ("EI-WRONG-TYPE",)

    # object identity.
    if build_execution is oci_execution:
        reasons.append("EI-SAME-OBJECT")

    # required identity fields present on BOTH.
    for e in (build_execution, oci_execution):
        if not str(e.execution_id).strip() or not str(e.process_identity).strip() \
                or not str(e.executable_digest).strip() or not str(e.execution_attestation_digest).strip():
            reasons.append("EI-MISSING-EXECUTION-IDENTITY")
            break

    # attestation must recompute for BOTH (self-verifying integrity).
    for e in (build_execution, oci_execution):
        if e.execution_attestation_digest != e.recompute_digest():
            reasons.append("EI-UNVERIFIABLE-EXECUTION")
            break

    # distinct execution id.
    if str(build_execution.execution_id).strip() and \
            build_execution.execution_id == oci_execution.execution_id:
        reasons.append("EI-SAME-EXECUTION-ID")

    # distinct process, OR distinct runner+host.
    same_process = bool(str(build_execution.process_identity).strip()) and \
        build_execution.process_identity == oci_execution.process_identity
    same_runner_host = (
        build_execution.runner_identity == oci_execution.runner_identity
        and build_execution.host_container_identity == oci_execution.host_container_identity
        and bool(str(build_execution.runner_identity).strip())
    )
    if same_process or same_runner_host:
        reasons.append("EI-SAME-PROCESS")

    # copied / relabelled: identical once execution_id + producer_id are stripped.
    if _canonical_minus(build_execution, _RELABEL_FIELDS) == _canonical_minus(oci_execution, _RELABEL_FIELDS):
        reasons.append("EI-COPIED-EXECUTION-EVIDENCE")

    # one execution emits BOTH roles: the build execution id appears in the oci provenance, or the two share
    # an evidence output reference (one write served both roles).
    b_id = str(build_execution.execution_id)
    if b_id and (b_id in str(oci_execution.producer_registration_ref)
                 or b_id in str(oci_execution.evidence_output_reference)):
        reasons.append("EI-ONE-EXECUTION-BOTH-ROLES")
    if str(build_execution.evidence_output_reference).strip() and \
            build_execution.evidence_output_reference == oci_execution.evidence_output_reference:
        reasons.append("EI-ONE-EXECUTION-BOTH-ROLES")

    # §5 executable + authority: the SAME executable run under the SAME authority on the SAME runner is one
    # execution confirming its own output — keyed on executable_digest + authority_reference + runner_identity,
    # NOT on tool/producer NAMES. A changed tool NAME alone or changed producer ID alone does NOT bypass.
    if build_execution.executable_digest == oci_execution.executable_digest \
            and build_execution.authority_reference == oci_execution.authority_reference \
            and build_execution.runner_identity == oci_execution.runner_identity \
            and str(build_execution.executable_digest).strip():
        reasons.append("EI-COMMON-EXECUTABLE-AND-AUTHORITY")

    # §5 synthetic execution may not stand in for a real one.
    for e in (build_execution, oci_execution):
        if e.classification == "SYNTHETIC_TEST_EXECUTION" and candidate_mode == "REAL_CANDIDATE":
            reasons.append("EI-SYNTHETIC-EXECUTION-IN-REAL-MODE")
            break

    # dual-role exception: validated but NEVER activated in this WO — fail closed.
    if dual_role_exception is not None:
        reasons.append("EI-DUAL-ROLE-EXCEPTION-NOT-ACTIVATED")

    return tuple(sorted(set(reasons)))
