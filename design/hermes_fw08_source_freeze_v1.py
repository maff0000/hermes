#!/usr/bin/env python3
"""HERMES FW-08 lifecycle stage-1 canonical-source freeze contract v1 (PURE, fixture-backed, INERT).

WO-HELM-HERMES-FW08-REAL-PROOF-LIFECYCLE-CANONICAL-SOURCE-FREEZE-AND-CANDIDATE-IDENTITY-CONTRACT-
IMPLEMENTATION-0001 (§4,§5,§8,§9,§10). Owner: HERMES (Helm). Created (UTC): 2026-07-27. Contract version: 1.

WHAT THIS IS. Lifecycle stage 1 of the merged F2-R3 real-proof execution lifecycle (22 stages) — the
CONTRACT for freezing the canonical source SHA before any candidate is built. This WO implements the
CONTRACT only. It performs NO real freeze: no git operation, no tag, no image, no runtime touch. Real
source-freeze is NOT AUTHORISED — `production_real_freeze_available()` is False, so `observe_source_freeze`
in REAL mode ALWAYS fails closed with (SF-REAL-FREEZE-UNAVAILABLE, ...). Only SYNTHETIC freeze evidence may
be minted, and ONLY in test/synthetic mode, sealed by the MODULE-OWNED issuer and mintable ONLY via the
module helper that supplies the private `_MODULE_TOKEN`. A caller CANNOT flip synthetic->real: the seal
binds the classification, and real inputs do not exist. The classification is set by the ISSUER, never by
the caller string — mirroring `design.hermes_fw08_real_image_proof_v1`.

D-PR120-EV-TAIL. `eventual_evidence_record_ids()` defines the downstream evidence record identifiers
(EV-01 source-freeze, EV-02 candidate-allocation) so that later EV-20/EV-21/EV-22 decision/closure evidence
can reference them unambiguously.

D-PR120-STAGE-FIELDS. Every model carries EXPLICIT UTC fields (requested_utc, expiry_utc, observation_utc,
freeze_expiry_utc) and an EXPLICIT evidence_reference; validators reject a record missing any of these
(SFR-UTC-MISSING / SF-UTC-MISSING / SF-EVIDENCE-REF-MISSING) so later stages can reference and time-bound it.

INVARIANT. execution_authorised MUST be False on a freeze request in this WO; a request asserting
execution_authorised=True is rejected (SFR-EXECUTION-AUTHORISED-FORBIDDEN).

PURE except the ephemeral issuer seal key (`secrets.token_bytes(32)`), mirroring the F2-R3 proof issuer:
no I/O, no subprocess, no network, stdlib only, deterministic given a fixed key. NOT imported by runtime.
No cycle: imports nothing from any FW-08 module.
"""
from __future__ import annotations

import dataclasses
import datetime
import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Tuple

CONTRACT_VERSION = "1"

# §8 lifecycle states this stage participates in. SOURCE_FROZEN is the FUTURE real state and is NEVER reached
# in this WO (real freeze is unauthorised).
SOURCE_FREEZE_STATES = frozenset({
    "LIFECYCLE_DESIGNED",
    "SOURCE_FREEZE_CONTRACT_READY",
    "SOURCE_FROZEN",
})

# §5 freeze-evidence classifications. Only SYNTHETIC_FREEZE_EVIDENCE is mintable in this WO, and only by the
# module issuer. A real classification is unavailable; INVALID/REVOKED are rejected on use.
FREEZE_EVIDENCE_CLASSIFICATIONS = frozenset({
    "SYNTHETIC_FREEZE_EVIDENCE",
    "REAL_FREEZE_EVIDENCE_UNAVAILABLE",
    "INVALID_FREEZE_EVIDENCE",
    "REVOKED_FREEZE_EVIDENCE",
})

_HEX40_RE = re.compile(r"^[0-9a-f]{40}$")


def _canonical(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _parse_utc(value: object) -> Optional[datetime.datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


# ============================================================================ §4 source-freeze request
@dataclass(frozen=True)
class SourceFreezeRequest:
    """Frozen binding of the full §4 source-freeze REQUEST field set. `execution_authorised` MUST be False in
    this WO. `request_digest` binds every identity field EXCEPT itself; a tamper breaks the recompute."""

    contract_version: str
    application: str
    repository: str
    canonical_branch: str
    canonical_source_sha: str
    source_tree_digest: str
    request_id: str
    requesting_authority: str
    requested_utc: str
    intended_lifecycle_id: str
    intended_candidate_purpose: str
    expiry_utc: str
    prerequisite_audit_reference: str
    prior_canonical_reference: str
    configuration_classification: str
    execution_authorised: bool
    request_digest: str

    def identity_fields(self) -> Dict[str, object]:
        """Canonical fields the request_digest covers (EXCLUDES request_digest itself)."""
        return {
            "contract_version": self.contract_version,
            "application": self.application,
            "repository": self.repository,
            "canonical_branch": self.canonical_branch,
            "canonical_source_sha": self.canonical_source_sha,
            "source_tree_digest": self.source_tree_digest,
            "request_id": self.request_id,
            "requesting_authority": self.requesting_authority,
            "requested_utc": self.requested_utc,
            "intended_lifecycle_id": self.intended_lifecycle_id,
            "intended_candidate_purpose": self.intended_candidate_purpose,
            "expiry_utc": self.expiry_utc,
            "prerequisite_audit_reference": self.prerequisite_audit_reference,
            "prior_canonical_reference": self.prior_canonical_reference,
            "configuration_classification": self.configuration_classification,
            "execution_authorised": self.execution_authorised,
        }

    def to_dict(self) -> Dict[str, object]:
        d = self.identity_fields()
        d["request_digest"] = self.request_digest
        return d

    @staticmethod
    def compute_digest(identity_fields: Mapping[str, object]) -> str:
        return hashlib.sha256(_canonical(dict(identity_fields)).encode("utf-8")).hexdigest()

    def recompute_digest(self) -> str:
        return SourceFreezeRequest.compute_digest(self.identity_fields())


def new_source_freeze_request(**kwargs: object) -> SourceFreezeRequest:
    """The ONLY blessed constructor: builds a SourceFreezeRequest with its request_digest computed over the
    identity fields. execution_authorised defaults to False (INERT). Blesses INTEGRITY only — validity is
    decided by `validate_source_freeze_request`."""
    kwargs.setdefault("contract_version", CONTRACT_VERSION)
    kwargs.setdefault("execution_authorised", False)
    kwargs["request_digest"] = ""
    r0 = SourceFreezeRequest(**kwargs)  # type: ignore[arg-type]
    return dataclasses.replace(r0, request_digest=r0.recompute_digest())


def validate_source_freeze_request(
    req: object,
    *,
    now_utc: str,
    application: str = "hermes",
    repository: str = "/srv/trading/hermes",
    canonical_branch: str = "main",
    max_age_hours: int = 24,
) -> Tuple[str, ...]:
    """Return () iff the freeze request is valid, else a sorted tuple of SFR-* reason codes. Fail-closed.

    Rejects:
      * not a SourceFreezeRequest                              -> SFR-WRONG-TYPE
      * request_digest does not recompute (tamper)            -> SFR-DIGEST-TAMPER
      * execution_authorised is not exactly False (INVARIANT) -> SFR-EXECUTION-AUTHORISED-FORBIDDEN
      * canonical_source_sha missing                          -> SFR-SHA-MISSING
      * SHA present but not a full 40-hex sha (abbreviated)   -> SFR-SHA-ABBREVIATED
      * canonical_branch != the canonical branch              -> SFR-NON-CANONICAL-BRANCH
      * source_tree_digest missing                            -> SFR-TREE-DIGEST-MISSING
      * a branch is named but NO exact SHA bound (mutable id) -> SFR-MUTABLE-BRANCH-IDENTITY
      * prerequisite_audit_reference missing                  -> SFR-AUDIT-REF-MISSING
      * wrong application                                     -> SFR-APPLICATION-MISMATCH
      * another repository                                    -> SFR-REPOSITORY-MISMATCH
      * requested_utc older than max_age_hours vs now         -> SFR-STALE
      * expiry_utc at/earlier than now                        -> SFR-EXPIRED
      * requested_utc or expiry_utc missing (D-PR120-STAGE)   -> SFR-UTC-MISSING
      * a caller-selected GREEN/frozen status field present   -> SFR-CALLER-STATUS-FORBIDDEN
    """
    if not isinstance(req, SourceFreezeRequest):
        return ("SFR-WRONG-TYPE",)

    reasons: List[str] = []

    # digest tamper.
    if req.request_digest != req.recompute_digest():
        reasons.append("SFR-DIGEST-TAMPER")

    # INVARIANT: execution must NOT be authorised in this WO. `is not False` rejects True AND truthy non-bool.
    if req.execution_authorised is not False:
        reasons.append("SFR-EXECUTION-AUTHORISED-FORBIDDEN")

    # SHA presence + full-length hex (an abbreviated sha is a mutable/ambiguous identity).
    sha = str(req.canonical_source_sha or "").strip()
    if not sha:
        reasons.append("SFR-SHA-MISSING")
    elif not _HEX40_RE.match(sha):
        reasons.append("SFR-SHA-ABBREVIATED")

    # canonical branch.
    if str(req.canonical_branch or "").strip() != canonical_branch:
        reasons.append("SFR-NON-CANONICAL-BRANCH")

    # source-tree digest present.
    if not str(req.source_tree_digest or "").strip():
        reasons.append("SFR-TREE-DIGEST-MISSING")

    # branch-only mutable identity: a branch is named but there is no exact full SHA bound.
    if str(req.canonical_branch or "").strip() and not _HEX40_RE.match(sha):
        reasons.append("SFR-MUTABLE-BRANCH-IDENTITY")

    # prerequisite audit reference present.
    if not str(req.prerequisite_audit_reference or "").strip():
        reasons.append("SFR-AUDIT-REF-MISSING")

    # application / repository binding.
    if str(req.application or "").strip() != application:
        reasons.append("SFR-APPLICATION-MISMATCH")
    if str(req.repository or "").strip() != repository:
        reasons.append("SFR-REPOSITORY-MISMATCH")

    # D-PR120-STAGE-FIELDS: explicit UTC fields required.
    requested = _parse_utc(req.requested_utc)
    expiry = _parse_utc(req.expiry_utc)
    now = _parse_utc(now_utc)
    if requested is None or expiry is None:
        reasons.append("SFR-UTC-MISSING")

    # staleness / expiry vs now (only meaningful once UTC fields parse).
    if now is not None and requested is not None:
        if now - requested > datetime.timedelta(hours=max_age_hours):
            reasons.append("SFR-STALE")
    if now is not None and expiry is not None:
        if now >= expiry:
            reasons.append("SFR-EXPIRED")

    # A caller-selected GREEN/frozen status field must NOT ride in the request — the module decides status.
    if str(req.configuration_classification or "").strip().upper() in {
        "SOURCE_FROZEN", "FROZEN", "GREEN", "SOURCE_FREEZE_CONTRACT_READY",
    }:
        reasons.append("SFR-CALLER-STATUS-FORBIDDEN")

    return tuple(sorted(set(reasons)))


# ============================================================================ §5 source-freeze evidence
@dataclass(frozen=True)
class SourceFreezeEvidence:
    """Frozen binding of the full §5 source-freeze EVIDENCE field set. `freeze_classification` is set by the
    ISSUER (never the caller). `evidence_content_digest` binds every field EXCEPT itself and the seal; `seal`
    is the module issuer's HMAC over the digest+classification — a caller cannot forge it. A real freeze
    classification is UNMINTABLE in this WO."""

    contract_version: str
    freeze_request_ref: str
    observed_canonical_sha: str
    observed_source_tree_digest: str
    local_main_origin_agreement: bool
    clean_tracked_tree: bool
    github_branch_identity: str
    observation_utc: str
    observer_identity: str
    evidence_reference: str
    evidence_content_digest: str
    freeze_classification: str
    freeze_expiry_utc: str
    revocation_status: str
    seal: str

    def bound_fields(self) -> Dict[str, object]:
        """All fields the content digest covers (EXCLUDES evidence_content_digest AND seal)."""
        return {
            "contract_version": self.contract_version,
            "freeze_request_ref": self.freeze_request_ref,
            "observed_canonical_sha": self.observed_canonical_sha,
            "observed_source_tree_digest": self.observed_source_tree_digest,
            "local_main_origin_agreement": self.local_main_origin_agreement,
            "clean_tracked_tree": self.clean_tracked_tree,
            "github_branch_identity": self.github_branch_identity,
            "observation_utc": self.observation_utc,
            "observer_identity": self.observer_identity,
            "evidence_reference": self.evidence_reference,
            "freeze_classification": self.freeze_classification,
            "freeze_expiry_utc": self.freeze_expiry_utc,
            "revocation_status": self.revocation_status,
        }

    def to_dict(self) -> Dict[str, object]:
        d = self.bound_fields()
        d["evidence_content_digest"] = self.evidence_content_digest
        d["seal"] = self.seal
        return d

    @staticmethod
    def compute_digest(bound_fields: Mapping[str, object]) -> str:
        return hashlib.sha256(_canonical(dict(bound_fields)).encode("utf-8")).hexdigest()

    def recompute_digest(self) -> str:
        return SourceFreezeEvidence.compute_digest(self.bound_fields())


def _seal_message(evidence: SourceFreezeEvidence) -> bytes:
    """Bytes the issuer HMAC covers: the content digest + the classification (so a relabelled classification
    breaks the seal — a synthetic record cannot be re-badged real and still verify)."""
    return (evidence.evidence_content_digest + "\x00" + evidence.freeze_classification).encode("utf-8")


# ============================================================================ module-owned issuer
class _FreezeIssuer:
    """MODULE-OWNED in-process seal capability, mirroring the F2-R3 _ProofIssuer. Holds an EPHEMERAL,
    name-mangled `self.__key` (`secrets.token_bytes(32)`) — NO getter, NEVER serialised, NEVER logged. Only
    the module builds/holds this; a caller cannot supply/forge a valid seal, so a caller-built (or relabelled)
    evidence object fails the module-provenance seal check."""

    def __init__(self, *, _key: Optional[bytes] = None) -> None:
        self.__key = _key if _key is not None else secrets.token_bytes(32)

    def _seal(self, payload_bytes: bytes) -> str:
        return hmac.new(self.__key, payload_bytes, hashlib.sha256).hexdigest()

    def seal_evidence(self, evidence: SourceFreezeEvidence) -> str:
        return self._seal(_seal_message(evidence))

    def verify(self, evidence: SourceFreezeEvidence) -> bool:
        if not isinstance(evidence.seal, str):
            return False
        expected = self._seal(_seal_message(evidence))
        return hmac.compare_digest(expected, evidence.seal)


_MODULE_ISSUER = _FreezeIssuer()
_MODULE_TOKEN = object()   # module-private: only the module helper supplies it


def production_real_freeze_available() -> bool:
    """No real canonical-source freeze is authorised in this WO: no git freeze, no external freeze operator,
    no real freeze evidence source. Real freeze therefore has NO source — REAL mode always fails closed.
    Performs NO filesystem / env / git lookup: unconditionally False."""
    return False


def observe_source_freeze(
    *,
    freeze_request: SourceFreezeRequest,
    mode: str,
    observed_sha: str,
    observed_tree_digest: str,
    agreement: bool,
    clean_tree: bool,
    github_identity: str,
    now_utc: str,
    expiry_utc: str,
    evidence_reference: str,
    observer_identity: str,
    real_freeze: bool = False,
    test_token: object = None,
) -> Tuple[Optional[SourceFreezeEvidence], Tuple[str, ...]]:
    """§5 mint a SourceFreezeEvidence, or (None, reasons). Fail-closed. The classification is set by THIS
    issuer, never the caller.

    REAL mode (mode == 'REAL_CANDIDATE' or real_freeze True): ALWAYS
        (None, ('REAL_PROOF_LIFECYCLE_REAL_FREEZE_NOT_AUTHORISED', 'SF-REAL-FREEZE-UNAVAILABLE')).
        `production_real_freeze_available()` is False — NO fallback, NO downgrade to synthetic.

    Otherwise (test/synthetic mode): a valid module `test_token is _MODULE_TOKEN` (only the module helper
    supplies it) mints a SYNTHETIC_FREEZE_EVIDENCE sealed by _MODULE_ISSUER; a caller without the token gets
    (None, ('SF-SYNTHETIC-REQUIRES-MODULE-TOKEN',))."""
    # REAL mode ALWAYS fails closed — real freeze inputs do not exist in this WO.
    if real_freeze or mode == "REAL_CANDIDATE":
        if not production_real_freeze_available():
            return (None, (
                "REAL_PROOF_LIFECYCLE_REAL_FREEZE_NOT_AUTHORISED",
                "SF-REAL-FREEZE-UNAVAILABLE",
            ))
        return (None, ("SF-REAL-FREEZE-UNAVAILABLE",))  # unreachable defence-in-depth.

    # synthetic freeze is module-token gated: a caller cannot mint it.
    if test_token is not _MODULE_TOKEN:
        return (None, ("SF-SYNTHETIC-REQUIRES-MODULE-TOKEN",))

    e0 = SourceFreezeEvidence(
        contract_version=CONTRACT_VERSION,
        freeze_request_ref=freeze_request.request_digest,
        observed_canonical_sha=str(observed_sha),
        observed_source_tree_digest=str(observed_tree_digest),
        local_main_origin_agreement=bool(agreement),
        clean_tracked_tree=bool(clean_tree),
        github_branch_identity=str(github_identity),
        observation_utc=str(now_utc),
        observer_identity=str(observer_identity),
        evidence_reference=str(evidence_reference),
        evidence_content_digest="",
        freeze_classification="SYNTHETIC_FREEZE_EVIDENCE",  # module sets it, never the caller.
        freeze_expiry_utc=str(expiry_utc),
        revocation_status="NONE",
        seal="",
    )
    e1 = dataclasses.replace(e0, evidence_content_digest=e0.recompute_digest())
    return (dataclasses.replace(e1, seal=_MODULE_ISSUER.seal_evidence(e1)), tuple())


def new_synthetic_freeze_evidence(
    **kwargs: object,
) -> Tuple[Optional[SourceFreezeEvidence], Tuple[str, ...]]:
    """Module helper: mint a SYNTHETIC_FREEZE_EVIDENCE by internally supplying `_MODULE_TOKEN`. Tests call
    this and get a sealed synthetic record WITHOUT ever seeing the token. A caller who calls
    `observe_source_freeze` with no token gets SF-SYNTHETIC-REQUIRES-MODULE-TOKEN."""
    kwargs.setdefault("mode", "TEST_ONLY")
    kwargs["test_token"] = _MODULE_TOKEN
    return observe_source_freeze(**kwargs)  # type: ignore[arg-type]


# Real freeze classifications (NONE mintable in this WO). A synthetic classification NEVER passes in real mode.
_REAL_FREEZE_CLASSIFICATIONS = frozenset({"REAL_FREEZE_EVIDENCE"})


def validate_freeze_evidence(
    evidence: object,
    *,
    freeze_request: SourceFreezeRequest,
    now_utc: str,
    real_mode: bool,
    other_evidence: Optional[Tuple[SourceFreezeEvidence, ...]] = None,
) -> Tuple[str, ...]:
    """Return () iff the freeze evidence is valid for USE against `freeze_request`, else a sorted tuple of
    SF-* reason codes. Fail-closed.

    Rejects:
      * not a SourceFreezeEvidence                             -> SF-WRONG-TYPE
      * seal invalid under _MODULE_ISSUER (caller-built)      -> SF-SYNTHETIC-NOT-MODULE-MINTED
      * evidence_content_digest does not recompute (tamper)   -> SF-CONTENT-DIGEST-TAMPER
      * classification not in FREEZE_EVIDENCE_CLASSIFICATIONS -> SF-INVALID-CLASSIFICATION
      * real_mode and classification is NOT a real class      -> SF-SYNTHETIC-IN-REAL-MODE
      * REVOKED / INVALID classification                      -> SF-REVOKED-EVIDENCE / SF-INVALID-EVIDENCE
      * observed_sha != request canonical_source_sha          -> SF-SHA-MISMATCH
      * observed_tree_digest != request source_tree_digest    -> SF-TREE-DIGEST-MISMATCH
      * local_main_origin_agreement False                     -> SF-LOCAL-MAIN-ORIGIN-MISMATCH
      * clean_tracked_tree False                              -> SF-DIRTY-TREE
      * observation_utc/freeze_expiry_utc missing (D-PR120)   -> SF-UTC-MISSING
      * evidence_reference missing (D-PR120)                  -> SF-EVIDENCE-REF-MISSING
      * stale/expired vs now                                  -> SF-STALE / SF-EXPIRED
      * copied evidence: same content under a different ref,  -> SF-COPIED-EVIDENCE
        OR the same evidence_reference reused across records
    """
    if not isinstance(evidence, SourceFreezeEvidence):
        return ("SF-WRONG-TYPE",)

    reasons: List[str] = []

    # Module-provenance seal FIRST: a caller-built or relabelled record (the only SYNTHETIC source is the
    # module mint) fails here — the ephemeral key is module-owned and the seal binds the classification.
    if not _MODULE_ISSUER.verify(evidence):
        reasons.append("SF-SYNTHETIC-NOT-MODULE-MINTED")

    # content-digest tamper.
    if evidence.evidence_content_digest != evidence.recompute_digest():
        reasons.append("SF-CONTENT-DIGEST-TAMPER")

    # classification membership.
    if evidence.freeze_classification not in FREEZE_EVIDENCE_CLASSIFICATIONS:
        reasons.append("SF-INVALID-CLASSIFICATION")

    # real mode: a synthetic (or any non-real) classification never passes — a real class cannot exist here.
    if real_mode and evidence.freeze_classification not in _REAL_FREEZE_CLASSIFICATIONS:
        reasons.append("SF-SYNTHETIC-IN-REAL-MODE")

    # revoked / invalid classification.
    if evidence.freeze_classification == "REVOKED_FREEZE_EVIDENCE" \
            or str(evidence.revocation_status or "").strip().upper() == "REVOKED":
        reasons.append("SF-REVOKED-EVIDENCE")
    if evidence.freeze_classification == "INVALID_FREEZE_EVIDENCE":
        reasons.append("SF-INVALID-EVIDENCE")

    # binding to the request.
    if str(evidence.observed_canonical_sha) != str(freeze_request.canonical_source_sha):
        reasons.append("SF-SHA-MISMATCH")
    if str(evidence.observed_source_tree_digest) != str(freeze_request.source_tree_digest):
        reasons.append("SF-TREE-DIGEST-MISMATCH")

    # observation invariants.
    if evidence.local_main_origin_agreement is not True:
        reasons.append("SF-LOCAL-MAIN-ORIGIN-MISMATCH")
    if evidence.clean_tracked_tree is not True:
        reasons.append("SF-DIRTY-TREE")

    # D-PR120-STAGE-FIELDS: explicit UTC + evidence-reference required.
    obs = _parse_utc(evidence.observation_utc)
    exp = _parse_utc(evidence.freeze_expiry_utc)
    now = _parse_utc(now_utc)
    if obs is None or exp is None:
        reasons.append("SF-UTC-MISSING")
    if not str(evidence.evidence_reference or "").strip():
        reasons.append("SF-EVIDENCE-REF-MISSING")

    # staleness / expiry vs the request's max age and the evidence's own expiry.
    if now is not None and obs is not None:
        if now - obs > datetime.timedelta(hours=24):
            reasons.append("SF-STALE")
    if now is not None and exp is not None:
        if now >= exp:
            reasons.append("SF-EXPIRED")

    # copied evidence: identical bound content under a DIFFERENT reference, OR a reference reused across a
    # supplied corpus. Either is a replay of one observation as if it were another.
    if other_evidence:
        this_body = _canonical(evidence.bound_fields())
        this_ref = str(evidence.evidence_reference)
        for o in other_evidence:
            if not isinstance(o, SourceFreezeEvidence) or o is evidence:
                continue
            o_ref = str(o.evidence_reference)
            o_body = _canonical(o.bound_fields())
            if this_ref and o_ref == this_ref:
                reasons.append("SF-COPIED-EVIDENCE")
                break
            # content-equal ignoring the reference field -> a copy under a different ref.
            this_no_ref = dict(evidence.bound_fields())
            this_no_ref.pop("evidence_reference", None)
            o_no_ref = dict(o.bound_fields())
            o_no_ref.pop("evidence_reference", None)
            if _canonical(this_no_ref) == _canonical(o_no_ref) and o_ref != this_ref:
                reasons.append("SF-COPIED-EVIDENCE")
                break

    return tuple(sorted(set(reasons)))


# ============================================================================ D-PR120-EV-TAIL
def eventual_evidence_record_ids() -> Dict[str, str]:
    """D-PR120-EV-TAIL. The downstream evidence record identifiers this stage-1/stage-2 contract DEFINES, so
    that later decision/closure evidence (EV-20 publication, EV-21 deployment, EV-22 closure) can reference
    the source-freeze and candidate-allocation records unambiguously. Stable, deterministic, no I/O."""
    return {
        "source_freeze": "EV-01-SOURCE-FREEZE",
        "candidate_allocation": "EV-02-CANDIDATE-ALLOCATION",
    }
