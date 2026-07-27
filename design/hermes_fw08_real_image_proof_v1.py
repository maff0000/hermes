#!/usr/bin/env python3
"""HERMES FW-08 F2-R3 real-image proof model + replay resistance v1 (PURE, fixture-backed).

WO-HELM-HERMES-FW08-F2-R3-EXECUTION-CONTENT-INDEPENDENCE-AND-REAL-IMAGE-PROOF-IMPLEMENTATION-0001 (§10,§11,§12).
Owner: HERMES (Helm). Created (UTC): 2026-07-27. Contract version: 1.

THE INVARIANT.
    REAL_IMAGE_PROOF => DISTINCT_EXECUTIONS + CONTENT_RESOLVED_EVIDENCE + GOVERNED_PRODUCER_AUTHORITY
                        + EXTERNALLY_VERIFIED_ACTIVE_IMPORT

No real proof may be asserted from fixture data. REAL_IMAGE_PROOF can ONLY be minted by the MODULE-OWNED
`_ProofIssuer` (ephemeral HMAC seal, mirroring F2-R1's ActivationAuthority) and REQUIRES a REAL candidate image
+ REAL build/OCI execution evidence + externally-configured authority + real SBOM/vuln — ALL UNAVAILABLE in
this WO (`production_real_image_source_available()` is False). So real proof ALWAYS fails closed here. SYNTHETIC
proof validates ONLY in TEST_ONLY / INERT_SIMULATION, sealed by the module issuer, mintable ONLY via the module
helper that supplies the private `_PROOF_TOKEN`. A caller CANNOT flip synthetic->real: the seal binds the
classification, and real inputs do not exist. Classification is set by the ISSUER, never by the caller string.

§11/§12 REPLAY / SUBSTITUTION. `verify_proof` rejects a forged seal, a tampered digest, a relabelled synthetic
in real mode, a wrong source/image, substituted execution/producer/SBOM/vuln, a reused candidate tag, an
expired proof, a stale proof, and any bound entity revoked at use.

PURE except the ephemeral issuer seal key (`secrets.token_bytes(32)`), mirroring GovernedEvidenceSealer /
ActivationAuthority: no I/O, no subprocess, no network, stdlib only, deterministic given a fixed key. NOT
imported by runtime; delegates to the F2-R3 execution/content/containment modules (no cycle into active_import).
"""
from __future__ import annotations

import dataclasses
import datetime
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Tuple

import design.hermes_fw08_execution_identity_v1 as ei
import design.hermes_fw08_content_resolution_v1 as cr
import design.hermes_fw08_legacy_comparator_containment_v1 as lc

CONTRACT_VERSION = "1"

PROOF_CLASSIFICATIONS = frozenset({
    "SYNTHETIC_TEST_PROOF",
    "REAL_IMAGE_PROOF",
    "REVOKED_PROOF",
    "INVALID_PROOF",
})


def _canonical(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _parse_utc(value: str) -> Optional[datetime.datetime]:
    try:
        return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


# ============================================================================ §10 real-image proof
@dataclass(frozen=True)
class RealImageProof:
    """A minted proof binding the full §10 field set. `proof_classification` is set by the ISSUER (never the
    caller). `proof_digest` binds all bound fields; `seal` is the module issuer's HMAC over the digest — a
    caller cannot forge it. A REAL_IMAGE_PROOF is UNMINTABLE in this WO (real inputs unavailable)."""

    contract_version: str
    canonical_source_sha: str
    candidate_image_id: str
    immutable_image_digest: str
    build_execution_identity_ref: str
    oci_execution_identity_ref: str
    build_producer_id: str
    oci_producer_id: str
    resolved_build_evidence_digest: str
    resolved_oci_evidence_digest: str
    manifest_digest: str
    config_digest: str
    layer_digests: Tuple[str, ...]
    filesystem_digest: str
    sbom_reference: str
    vulnerability_result_reference: str
    trust_anchor_lineage_ref: str
    resolver_lineage_ref: str
    activated_registry_handle_ref: str
    active_import_result_ref: str
    proof_creation_utc: str
    proof_expiry_utc: str
    proof_classification: str
    proof_digest: str
    seal: str

    def bound_fields(self) -> Dict[str, object]:
        """All fields the proof_digest covers (EXCLUDES proof_digest AND seal)."""
        return {
            "contract_version": self.contract_version,
            "canonical_source_sha": self.canonical_source_sha,
            "candidate_image_id": self.candidate_image_id,
            "immutable_image_digest": self.immutable_image_digest,
            "build_execution_identity_ref": self.build_execution_identity_ref,
            "oci_execution_identity_ref": self.oci_execution_identity_ref,
            "build_producer_id": self.build_producer_id,
            "oci_producer_id": self.oci_producer_id,
            "resolved_build_evidence_digest": self.resolved_build_evidence_digest,
            "resolved_oci_evidence_digest": self.resolved_oci_evidence_digest,
            "manifest_digest": self.manifest_digest,
            "config_digest": self.config_digest,
            "layer_digests": list(self.layer_digests),
            "filesystem_digest": self.filesystem_digest,
            "sbom_reference": self.sbom_reference,
            "vulnerability_result_reference": self.vulnerability_result_reference,
            "trust_anchor_lineage_ref": self.trust_anchor_lineage_ref,
            "resolver_lineage_ref": self.resolver_lineage_ref,
            "activated_registry_handle_ref": self.activated_registry_handle_ref,
            "active_import_result_ref": self.active_import_result_ref,
            "proof_creation_utc": self.proof_creation_utc,
            "proof_expiry_utc": self.proof_expiry_utc,
            "proof_classification": self.proof_classification,
        }

    def to_dict(self) -> Dict[str, object]:
        d = self.bound_fields()
        d["proof_digest"] = self.proof_digest
        d["seal"] = self.seal
        return d

    @staticmethod
    def compute_digest(bound_fields: Mapping[str, object]) -> str:
        return hashlib.sha256(_canonical(dict(bound_fields)).encode("utf-8")).hexdigest()

    def recompute_digest(self) -> str:
        return RealImageProof.compute_digest(self.bound_fields())


def _seal_message(proof: RealImageProof) -> bytes:
    """Bytes the issuer HMAC covers: the proof digest + the classification (so a relabelled classification
    breaks the seal)."""
    return (proof.proof_digest + "\x00" + proof.proof_classification).encode("utf-8")


# ============================================================================ module-owned issuer
class _ProofIssuer:
    """MODULE-OWNED in-process seal capability, mirroring F2-R1's ActivationAuthority / _ModuleVerifier. Holds
    an EPHEMERAL, name-mangled `self.__key` (`secrets.token_bytes(32)`) — NO getter, NEVER serialised, NEVER in
    any to_dict/log. Only the module builds/holds this; a caller cannot supply/forge a valid seal."""

    def __init__(self, *, _key: Optional[bytes] = None) -> None:
        self.__key = _key if _key is not None else secrets.token_bytes(32)

    def _seal(self, payload_bytes: bytes) -> str:
        return hmac.new(self.__key, payload_bytes, hashlib.sha256).hexdigest()

    def seal_proof(self, proof: RealImageProof) -> str:
        return self._seal(_seal_message(proof))

    def verify(self, proof: RealImageProof) -> bool:
        if not isinstance(proof.seal, str):
            return False
        expected = self._seal(_seal_message(proof))
        return hmac.compare_digest(expected, proof.seal)


_MODULE_ISSUER = _ProofIssuer()
_PROOF_TOKEN = object()   # module-private: only the module helper supplies it


def production_real_image_source_available() -> bool:
    """No real candidate image / real build+OCI execution evidence / external authority / real SBOM / real vuln
    result exists in this WO. Real-image proof therefore has NO source — REAL_CANDIDATE always fails closed.
    Performs NO filesystem / env / registry lookup: unconditionally False."""
    return False


def _mint(
    *,
    classification: str,
    source_sha: str,
    candidate_image_id: str,
    build_execution: ei.ExecutionIdentity,
    oci_execution: ei.ExecutionIdentity,
    build_content: cr.ContentResolvedEvidence,
    oci_content: cr.ContentResolvedEvidence,
    image_identity_anchor: object,
    filesystem_anchor: object,
    activated_handle: object,
    active_import_verdict: object,
    sbom_reference: str,
    vulnerability_result_reference: str,
    trust_context: object,
    now_utc: str,
    expiry_utc: str,
) -> RealImageProof:
    ii = image_identity_anchor
    fa = filesystem_anchor
    p0 = RealImageProof(
        contract_version=CONTRACT_VERSION,
        canonical_source_sha=str(source_sha),
        candidate_image_id=str(candidate_image_id),
        immutable_image_digest=str(getattr(ii, "image_id", "")),
        build_execution_identity_ref=build_execution.execution_attestation_digest,
        oci_execution_identity_ref=oci_execution.execution_attestation_digest,
        build_producer_id=build_execution.producer_id,
        oci_producer_id=oci_execution.producer_id,
        resolved_build_evidence_digest=build_content.content_digest,
        resolved_oci_evidence_digest=oci_content.content_digest,
        manifest_digest=str(getattr(fa, "manifest_digest", "")),
        config_digest=str(getattr(fa, "config_digest", "")) or oci_content.content_digest,
        layer_digests=tuple(getattr(fa, "layer_digests", ()) or ()),
        filesystem_digest=str(getattr(fa, "filesystem_digest", "")),
        sbom_reference=str(sbom_reference),
        vulnerability_result_reference=str(vulnerability_result_reference),
        trust_anchor_lineage_ref=str(getattr(trust_context, "candidate_mode", "SYNTHETIC")),
        resolver_lineage_ref=build_content.immutable_storage_id,
        activated_registry_handle_ref=str(getattr(activated_handle, "handle_checksum", "SYNTHETIC-HANDLE")),
        active_import_result_ref=str(getattr(active_import_verdict, "reason_codes", "")) or "ACCEPTED-INERT",
        proof_creation_utc=str(now_utc),
        proof_expiry_utc=str(expiry_utc),
        proof_classification=classification,
        proof_digest="",
        seal="",
    )
    p1 = dataclasses.replace(p0, proof_digest=p0.recompute_digest())
    return dataclasses.replace(p1, seal=_MODULE_ISSUER.seal_proof(p1))


def assemble_proof(
    *,
    candidate_mode: str,
    source_sha: str,
    candidate_image_id: str,
    build_execution: object,
    oci_execution: object,
    build_content: object,
    oci_content: object,
    image_identity_anchor: object,
    filesystem_anchor: object,
    activated_handle: object,
    active_import_verdict: object,
    sbom_reference: str,
    vulnerability_result_reference: str,
    trust_context: object,
    now_utc: str,
    expiry_utc: str,
    build_result: Optional[Mapping[str, object]] = None,
    oci_inspection: Optional[Mapping[str, object]] = None,
    test_token: object = None,
) -> Tuple[Optional[RealImageProof], Tuple[str, ...]]:
    """§10 assemble a proof, or (None, reasons). Fail-closed. The classification is set by THIS issuer, never
    by the caller.

    REAL_CANDIDATE: if not production_real_image_source_available() -> ALWAYS
        (None, ('RIP-REAL-SOURCE-UNAVAILABLE', 'F2R3-REAL-PROOF-REQUIRES-REAL-IMAGE-AND-AUTHORITY')). No
        fallback, no downgrade, no synthetic proof.

    Otherwise (TEST_ONLY / INERT_SIMULATION):
      * execution independence must pass (delegated)  -> RIP-EXECUTIONS-NOT-INDEPENDENT
      * content resolution + near-copy must pass      -> RIP-CONTENT-NOT-RESOLVED / RIP-NEAR-COPY
      * a valid module `test_token is _PROOF_TOKEN` (only the module helper supplies it) -> mint a
        SYNTHETIC_TEST_PROOF sealed by _MODULE_ISSUER; a caller without the token ->
        RIP-SYNTHETIC-REQUIRES-MODULE-TOKEN.
    """
    # REAL mode ALWAYS fails closed — real inputs do not exist in this WO.
    if candidate_mode == "REAL_CANDIDATE":
        if not production_real_image_source_available():
            return (None, ("F2R3-REAL-PROOF-REQUIRES-REAL-IMAGE-AND-AUTHORITY", "RIP-REAL-SOURCE-UNAVAILABLE"))
        # unreachable in this WO; defence in depth.
        return (None, ("RIP-REAL-SOURCE-UNAVAILABLE",))

    if candidate_mode not in ("TEST_ONLY", "INERT_SIMULATION"):
        return (None, ("RIP-INVALID-CANDIDATE-MODE",))

    if not isinstance(build_execution, ei.ExecutionIdentity) \
            or not isinstance(oci_execution, ei.ExecutionIdentity):
        return (None, ("RIP-EXECUTIONS-NOT-INDEPENDENT",))
    if not isinstance(build_content, cr.ContentResolvedEvidence) \
            or not isinstance(oci_content, cr.ContentResolvedEvidence):
        return (None, ("RIP-CONTENT-NOT-RESOLVED",))

    # execution independence.
    ei_reasons = ei.evaluate_execution_independence(
        build_execution=build_execution, oci_execution=oci_execution, candidate_mode=candidate_mode)
    if ei_reasons:
        return (None, ("RIP-EXECUTIONS-NOT-INDEPENDENT",) + tuple(ei_reasons))

    # near-copy analysis (content resolution proper is done by the caller via resolve_and_verify; here we run
    # the alias/near-copy check over the two resolved contents + raw records).
    if build_result is not None and oci_inspection is not None:
        nc = cr.detect_alias_or_nearcopy(
            build_content=build_content, oci_content=oci_content,
            build_result=build_result, oci_inspection=oci_inspection)
        if nc:
            return (None, ("RIP-NEAR-COPY",) + tuple(nc))

    # a verdict whose provenance is the raw legacy comparator is NOT proof authority.
    verdict_src = str(getattr(active_import_verdict, "verdict_source", ""))
    if verdict_src:
        g = lc.guard_not_proof(verdict_src)
        if g:
            return (None, ("RIP-COMPARATOR-NOT-PROOF",) + g)

    # synthetic proof is module-token gated.
    if test_token is not _PROOF_TOKEN:
        return (None, ("RIP-SYNTHETIC-REQUIRES-MODULE-TOKEN",))

    proof = _mint(
        classification="SYNTHETIC_TEST_PROOF",
        source_sha=source_sha, candidate_image_id=candidate_image_id,
        build_execution=build_execution, oci_execution=oci_execution,
        build_content=build_content, oci_content=oci_content,
        image_identity_anchor=image_identity_anchor, filesystem_anchor=filesystem_anchor,
        activated_handle=activated_handle, active_import_verdict=active_import_verdict,
        sbom_reference=sbom_reference, vulnerability_result_reference=vulnerability_result_reference,
        trust_context=trust_context, now_utc=now_utc, expiry_utc=expiry_utc)
    return (proof, tuple())


def new_synthetic_test_proof(**kwargs: object) -> Tuple[Optional[RealImageProof], Tuple[str, ...]]:
    """Module helper: mint a SYNTHETIC_TEST_PROOF by internally supplying `_PROOF_TOKEN`. Tests call this and
    get a sealed synthetic proof WITHOUT ever seeing the token. A caller who calls `assemble_proof` with no
    token gets RIP-SYNTHETIC-REQUIRES-MODULE-TOKEN (proved by a companion test)."""
    kwargs.setdefault("candidate_mode", "TEST_ONLY")
    kwargs["test_token"] = _PROOF_TOKEN
    return assemble_proof(**kwargs)  # type: ignore[arg-type]


# ============================================================================ §11/§12 verify (replay/subst.)
def verify_proof(
    proof: object,
    *,
    now_utc: str,
    real_mode: bool,
    current_revocation: object = None,
    expected_source_sha: Optional[str] = None,
    expected_image_id: Optional[str] = None,
    expected_build_execution_ref: Optional[str] = None,
    expected_oci_execution_ref: Optional[str] = None,
    expected_build_producer_id: Optional[str] = None,
    expected_oci_producer_id: Optional[str] = None,
    expected_sbom_reference: Optional[str] = None,
    expected_vulnerability_result_reference: Optional[str] = None,
    expected_candidate_image_id: Optional[str] = None,
) -> Tuple[bool, Tuple[str, ...]]:
    """§11/§12 verify a proof for USE. Returns (True, ()) iff every check passes, else (False, reasons).
    Fail-closed:
      * not a RealImageProof                                 -> RIP-WRONG-TYPE
      * seal invalid under _MODULE_ISSUER (caller-forged)    -> RIP-SEAL-INVALID
      * classification REVOKED_PROOF / INVALID_PROOF          -> RIP-REVOKED-PROOF / RIP-INVALID-PROOF
      * proof_digest does not recompute (tamper)             -> RIP-DIGEST-TAMPER
      * expired                                              -> RIP-EXPIRED
      * real_mode and classification != REAL_IMAGE_PROOF      -> RIP-SYNTHETIC-IN-REAL-MODE
      * replay/substitution vs an expected context           -> RIP-SOURCE-MISMATCH / RIP-IMAGE-MISMATCH /
        RIP-CANDIDATE-TAG-REUSE / RIP-SUBSTITUTED-EXECUTION / RIP-SUBSTITUTED-PRODUCER /
        RIP-SUBSTITUTED-SBOM / RIP-SUBSTITUTED-VULN
      * any bound entity revoked at use                      -> RIP-REVOKED-AT-USE
    """
    if not isinstance(proof, RealImageProof):
        return (False, ("RIP-WRONG-TYPE",))
    reasons: List[str] = []

    # seal FIRST — a caller-forged / copied proof-shaped object fails here (the ephemeral key is module-owned).
    if not _MODULE_ISSUER.verify(proof):
        return (False, ("RIP-SEAL-INVALID",))

    # digest tamper (the seal covers digest+classification; a body-field tamper breaks the digest recompute).
    if proof.proof_digest != proof.recompute_digest():
        reasons.append("RIP-DIGEST-TAMPER")

    if proof.proof_classification == "REVOKED_PROOF":
        reasons.append("RIP-REVOKED-PROOF")
    if proof.proof_classification == "INVALID_PROOF":
        reasons.append("RIP-INVALID-PROOF")

    # expiry.
    exp = _parse_utc(proof.proof_expiry_utc)
    now = _parse_utc(now_utc)
    if exp is None or now is None or now >= exp:
        reasons.append("RIP-EXPIRED")

    # real mode: a synthetic (or any non-real) classification never passes — and a real proof cannot exist here.
    if real_mode and proof.proof_classification != "REAL_IMAGE_PROOF":
        reasons.append("RIP-SYNTHETIC-IN-REAL-MODE")

    # §12 replay / substitution vs the expected context.
    if expected_source_sha is not None and proof.canonical_source_sha != expected_source_sha:
        reasons.append("RIP-SOURCE-MISMATCH")
    if expected_image_id is not None and proof.immutable_image_digest != expected_image_id:
        reasons.append("RIP-IMAGE-MISMATCH")
    if expected_candidate_image_id is not None and proof.candidate_image_id != expected_candidate_image_id:
        reasons.append("RIP-CANDIDATE-TAG-REUSE")
    if expected_build_execution_ref is not None and \
            proof.build_execution_identity_ref != expected_build_execution_ref:
        reasons.append("RIP-SUBSTITUTED-EXECUTION")
    if expected_oci_execution_ref is not None and \
            proof.oci_execution_identity_ref != expected_oci_execution_ref:
        reasons.append("RIP-SUBSTITUTED-EXECUTION")
    if expected_build_producer_id is not None and proof.build_producer_id != expected_build_producer_id:
        reasons.append("RIP-SUBSTITUTED-PRODUCER")
    if expected_oci_producer_id is not None and proof.oci_producer_id != expected_oci_producer_id:
        reasons.append("RIP-SUBSTITUTED-PRODUCER")
    if expected_sbom_reference is not None and proof.sbom_reference != expected_sbom_reference:
        reasons.append("RIP-SUBSTITUTED-SBOM")
    if expected_vulnerability_result_reference is not None and \
            proof.vulnerability_result_reference != expected_vulnerability_result_reference:
        reasons.append("RIP-SUBSTITUTED-VULN")

    # revocation at use: any bound entity revoked now.
    if current_revocation is not None and hasattr(current_revocation, "is_revoked"):
        bound = [
            ("SOURCE", proof.canonical_source_sha),
            ("IMAGE", proof.immutable_image_digest),
            ("CANDIDATE", proof.candidate_image_id),
            ("EXECUTION", proof.build_execution_identity_ref),
            ("EXECUTION", proof.oci_execution_identity_ref),
            ("PRODUCER_REGISTRATION", proof.build_producer_id),
            ("PRODUCER_REGISTRATION", proof.oci_producer_id),
            ("SBOM", proof.sbom_reference),
            ("VULN", proof.vulnerability_result_reference),
        ]
        for rtype, tid in bound:
            if tid and current_revocation.is_revoked(
                    revocation_type=rtype, target_id=str(tid), at_utc=now_utc):
                reasons.append("RIP-REVOKED-AT-USE")
                break

    if reasons:
        return (False, tuple(sorted(set(reasons))))
    return (True, tuple())
