#!/usr/bin/env python3
"""HERMES FW-08 lifecycle stage-2 candidate identity allocation contract v1 (PURE, INERT).

WO-HELM-HERMES-FW08-REAL-PROOF-LIFECYCLE-CANONICAL-SOURCE-FREEZE-AND-CANDIDATE-IDENTITY-CONTRACT-
IMPLEMENTATION-0001 (§6,§7,§8,§10). Owner: HERMES (Helm). Created (UTC): 2026-07-27. Contract version: 1.

WHAT THIS IS. Lifecycle stage 2 of the merged F2-R3 real-proof execution lifecycle — the CONTRACT for
allocating an ISOLATED candidate identity (a candidate image name/tag reservation) bound to the frozen
canonical source. This WO implements the CONTRACT only: NO real candidate is allocated, NO tag is created,
NO image is built. Real allocation is NOT AUTHORISED — `allocate_candidate_identity` in REAL mode ALWAYS
fails closed with (CI-REAL-ALLOCATION-UNAVAILABLE, ...). A synthetic candidate identity (status
SYNTHETIC_UNALLOCATED, execution_authorised=False) may be minted ONLY in test mode via the module-token gate.

§7 ISOLATION. A candidate MUST live under the isolated NAMESPACE_ROOT, MUST NOT reuse the deployed image
(DEPLOYED_IMAGE), a runtime service name, or the "latest" tag, MUST bind the source SHA in its tag/candidate,
MUST carry an expiry, and MUST NOT name an active deployment target. The current protected runtime (source
71ea3bd4598d8af5628f78de10f8022088ad3f05 / image c5fc2a62f424 / container d80018037b7f, consumer_live=false)
is UNTOUCHED.

D-PR120-STAGE-FIELDS. The model carries explicit allocation_request_utc + expiry_utc and an explicit
prerequisite_freeze_evidence_digest (the EV-01 source-freeze reference); validation rejects a record missing
any of them (CI-UTC-MISSING / CI-FREEZE-EV-REF-MISSING).

PURE: no I/O, no subprocess, no network, no secrets, stdlib only, deterministic. NOT imported by runtime.
No cycle: imports nothing from any FW-08 module.
"""
from __future__ import annotations

import dataclasses
import datetime
import hashlib
import json
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Tuple

CONTRACT_VERSION = "1"

# §6 candidate states. Only SYNTHETIC_UNALLOCATED is permitted in this WO — a real allocated status is
# UNREACHABLE here (real allocation is unauthorised).
CANDIDATE_STATES = frozenset({
    "SYNTHETIC_UNALLOCATED",
    "REAL_ALLOCATION_UNAVAILABLE",
    "REVOKED_CANDIDATE",
    "INVALID_CANDIDATE",
})

# Protected reference values — NEVER reused by a candidate. Deployed image + deployed runtime source.
DEPLOYED_IMAGE = "c5fc2a62f424"
DEPLOYED_SOURCE = "71ea3bd4598d8af5628f78de10f8022088ad3f05"
DEPLOYED_CONTAINER = "d80018037b7f"

# §7 isolated namespace root for candidates. A candidate namespace MUST be under this root.
NAMESPACE_ROOT = "hermes-fw08-candidate"
APPLICATION = "hermes"

# Tags/repositories a candidate may NEVER use (mutable/operational).
FORBIDDEN_TAGS = frozenset({"latest", "stable", "prod", "production", "current", "main", "release"})

# Runtime service names a candidate may NEVER reference (active deployment targets).
RUNTIME_SERVICE_NAMES = frozenset({
    "hermes", "hermes-consumer", "hermes-runtime", "hermes-live", "hermes-prod",
})


def _canonical(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _parse_utc(value: object) -> Optional[datetime.datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


# ============================================================================ §6 candidate identity
@dataclass(frozen=True)
class CandidateIdentity:
    """Frozen binding of the full §6 candidate-identity field set. `execution_authorised` MUST be False and
    `candidate_status` MUST be SYNTHETIC_UNALLOCATED in this WO. `candidate_digest` binds every field except
    itself; a tamper breaks the recompute."""

    contract_version: str
    lifecycle_id: str
    candidate_id: str
    application: str
    canonical_source_sha: str
    source_tree_digest: str
    candidate_namespace: str
    proposed_immutable_tag: str
    expected_image_repository: str
    purpose: str
    allocation_request_utc: str
    expiry_utc: str
    requesting_authority: str
    prerequisite_freeze_evidence_digest: str
    candidate_status: str
    execution_authorised: bool
    candidate_digest: str

    def identity_fields(self) -> Dict[str, object]:
        """Canonical fields the candidate_digest covers (EXCLUDES candidate_digest itself)."""
        return {
            "contract_version": self.contract_version,
            "lifecycle_id": self.lifecycle_id,
            "candidate_id": self.candidate_id,
            "application": self.application,
            "canonical_source_sha": self.canonical_source_sha,
            "source_tree_digest": self.source_tree_digest,
            "candidate_namespace": self.candidate_namespace,
            "proposed_immutable_tag": self.proposed_immutable_tag,
            "expected_image_repository": self.expected_image_repository,
            "purpose": self.purpose,
            "allocation_request_utc": self.allocation_request_utc,
            "expiry_utc": self.expiry_utc,
            "requesting_authority": self.requesting_authority,
            "prerequisite_freeze_evidence_digest": self.prerequisite_freeze_evidence_digest,
            "candidate_status": self.candidate_status,
            "execution_authorised": self.execution_authorised,
        }

    def to_dict(self) -> Dict[str, object]:
        d = self.identity_fields()
        d["candidate_digest"] = self.candidate_digest
        return d

    @staticmethod
    def compute_digest(identity_fields: Mapping[str, object]) -> str:
        return hashlib.sha256(_canonical(dict(identity_fields)).encode("utf-8")).hexdigest()

    def recompute_digest(self) -> str:
        return CandidateIdentity.compute_digest(self.identity_fields())


def new_candidate_identity(**kwargs: object) -> CandidateIdentity:
    """The ONLY blessed constructor: builds a CandidateIdentity with its candidate_digest computed over the
    identity fields. candidate_status defaults to SYNTHETIC_UNALLOCATED and execution_authorised to False
    (INERT). Blesses INTEGRITY only — validity is decided by `validate_candidate_identity`."""
    kwargs.setdefault("contract_version", CONTRACT_VERSION)
    kwargs.setdefault("application", APPLICATION)
    kwargs.setdefault("candidate_status", "SYNTHETIC_UNALLOCATED")
    kwargs.setdefault("execution_authorised", False)
    kwargs["candidate_digest"] = ""
    c0 = CandidateIdentity(**kwargs)  # type: ignore[arg-type]
    return dataclasses.replace(c0, candidate_digest=c0.recompute_digest())


def allocate_candidate_identity(
    *,
    request: Mapping[str, object],
    mode: str,
    now_utc: str,
    real_allocation: bool = False,
    test_token: object = None,
) -> Tuple[Optional[CandidateIdentity], Tuple[str, ...]]:
    """§6 allocate a candidate identity, or (None, reasons). Fail-closed. The status is set by THIS module,
    never the caller.

    REAL mode (mode == 'REAL_CANDIDATE' or real_allocation True): ALWAYS
        (None, ('CI-REAL-ALLOCATION-UNAVAILABLE', 'REAL_PROOF_LIFECYCLE_REAL_ALLOCATION_NOT_AUTHORISED')).
        No real candidate is EVER allocated in this WO.

    Otherwise (test mode): module-token gated. `test_token is _MODULE_TOKEN` (only the module helper supplies
    it) mints a synthetic CandidateIdentity with status SYNTHETIC_UNALLOCATED and execution_authorised=False;
    a caller without the token gets (None, ('CI-SYNTHETIC-REQUIRES-MODULE-TOKEN',))."""
    if real_allocation or mode == "REAL_CANDIDATE":
        return (None, (
            "CI-REAL-ALLOCATION-UNAVAILABLE",
            "REAL_PROOF_LIFECYCLE_REAL_ALLOCATION_NOT_AUTHORISED",
        ))

    if test_token is not _MODULE_TOKEN:
        return (None, ("CI-SYNTHETIC-REQUIRES-MODULE-TOKEN",))

    kw = dict(request)
    kw["candidate_status"] = "SYNTHETIC_UNALLOCATED"   # module sets it, never the caller.
    kw["execution_authorised"] = False                 # never allocates a real candidate.
    kw.setdefault("allocation_request_utc", now_utc)
    return (new_candidate_identity(**kw), tuple())


_MODULE_TOKEN = object()   # module-private: only the module helper supplies it


def new_synthetic_candidate_identity(
    **kwargs: object,
) -> Tuple[Optional[CandidateIdentity], Tuple[str, ...]]:
    """Module helper: mint a synthetic candidate identity by internally supplying `_MODULE_TOKEN`. Tests call
    this WITHOUT ever seeing the token; a caller calling `allocate_candidate_identity` with no token gets
    CI-SYNTHETIC-REQUIRES-MODULE-TOKEN."""
    kwargs.setdefault("mode", "TEST_ONLY")
    kwargs["test_token"] = _MODULE_TOKEN
    return allocate_candidate_identity(**kwargs)  # type: ignore[arg-type]


def _tag_or_candidate_binds_sha(ci: CandidateIdentity) -> bool:
    """The source SHA is bound if the full SHA (or a >=12-char prefix) appears in the tag OR the candidate id
    OR the namespace. A mutable/unbound tag that names no source is rejected."""
    sha = str(ci.canonical_source_sha or "").strip().lower()
    if not sha:
        return False
    haystacks = " ".join([
        str(ci.proposed_immutable_tag or ""),
        str(ci.candidate_id or ""),
        str(ci.candidate_namespace or ""),
    ]).lower()
    if sha in haystacks:
        return True
    # accept an unambiguous prefix (>=12 hex) bound in the tag/candidate.
    return len(sha) >= 12 and sha[:12] in haystacks


def validate_candidate_identity(
    ci: object,
    *,
    now_utc: str,
    application: str = "hermes",
) -> Tuple[str, ...]:
    """§7 return () iff the candidate identity is valid (naming + isolation), else a sorted tuple of CI-*
    reason codes. Fail-closed.

    Rejects:
      * not a CandidateIdentity                               -> CI-WRONG-TYPE
      * candidate_digest does not recompute (tamper)         -> CI-DIGEST-TAMPER
      * execution_authorised is not exactly False (INVARIANT)-> CI-EXECUTION-AUTHORISED-FORBIDDEN
      * namespace not under NAMESPACE_ROOT                    -> CI-NAMESPACE-NOT-ISOLATED
      * namespace carries a cross-application prefix          -> CI-CROSS-APPLICATION-NAMESPACE
      * proposed_immutable_tag == 'latest'                    -> CI-LATEST-FORBIDDEN
      * mutable/operational tag (stable/prod/...)             -> CI-MUTABLE-TAG
      * tag/repo references the deployed image                -> CI-DEPLOYED-TAG-REUSE
      * tag/repo references a runtime service name            -> CI-RUNTIME-SERVICE-TAG
      * source SHA not bound in the tag or candidate          -> CI-UNBOUND-SOURCE-SHA
      * lifecycle_id missing                                  -> CI-LIFECYCLE-ID-MISSING
      * no expiry (indefinite candidate)                     -> CI-NO-EXPIRY
      * active deployment target / runtime service present    -> CI-ACTIVE-DEPLOYMENT-TARGET
      * candidate_status != SYNTHETIC_UNALLOCATED             -> CI-REAL-STATUS-FORBIDDEN
      * allocation_request_utc missing (D-PR120)             -> CI-UTC-MISSING
      * prerequisite_freeze_evidence_digest missing (D-PR120)-> CI-FREEZE-EV-REF-MISSING
      * wrong application                                    -> CI-APPLICATION-MISMATCH
      * expiry at/earlier than now                           -> CI-EXPIRED
    """
    if not isinstance(ci, CandidateIdentity):
        return ("CI-WRONG-TYPE",)

    reasons: List[str] = []

    if ci.candidate_digest != ci.recompute_digest():
        reasons.append("CI-DIGEST-TAMPER")

    # INVARIANT: execution unauthorised.
    if ci.execution_authorised is not False:
        reasons.append("CI-EXECUTION-AUTHORISED-FORBIDDEN")

    ns = str(ci.candidate_namespace or "").strip()
    tag = str(ci.proposed_immutable_tag or "").strip()
    repo = str(ci.expected_image_repository or "").strip()
    tag_lc = tag.lower()
    repo_lc = repo.lower()

    # §7 namespace isolation: MUST be exactly the root or a child of the root.
    if not (ns == NAMESPACE_ROOT or ns.startswith(NAMESPACE_ROOT + "/") or ns.startswith(NAMESPACE_ROOT + "-")):
        reasons.append("CI-NAMESPACE-NOT-ISOLATED")

    # cross-application prefix: a namespace naming another application before the candidate root.
    ns_head = ns.split("/", 1)[0]
    if ns_head and ns_head != NAMESPACE_ROOT and not ns.startswith(NAMESPACE_ROOT):
        # e.g. "argus-fw08-candidate/..." or "otherapp/hermes-fw08-candidate"
        if application not in ns_head or ns_head.startswith("argus") or "/" + NAMESPACE_ROOT in ns:
            reasons.append("CI-CROSS-APPLICATION-NAMESPACE")

    # "latest" is a hard-forbidden mutable tag.
    if tag_lc == "latest":
        reasons.append("CI-LATEST-FORBIDDEN")
    elif tag_lc in FORBIDDEN_TAGS:
        reasons.append("CI-MUTABLE-TAG")

    # deployed-image reuse (tag or repository references the deployed image).
    if DEPLOYED_IMAGE.lower() in tag_lc or DEPLOYED_IMAGE.lower() in repo_lc \
            or DEPLOYED_SOURCE.lower() in tag_lc or DEPLOYED_CONTAINER.lower() in tag_lc:
        reasons.append("CI-DEPLOYED-TAG-REUSE")

    # runtime-service-name reuse in tag or repository.
    if any(svc == tag_lc or svc == repo_lc.rsplit("/", 1)[-1] for svc in RUNTIME_SERVICE_NAMES):
        reasons.append("CI-RUNTIME-SERVICE-TAG")

    # source SHA must be bound (no floating/mutable candidate identity).
    if not _tag_or_candidate_binds_sha(ci):
        reasons.append("CI-UNBOUND-SOURCE-SHA")

    # lifecycle id present.
    if not str(ci.lifecycle_id or "").strip():
        reasons.append("CI-LIFECYCLE-ID-MISSING")

    # D-PR120-STAGE-FIELDS: explicit UTC + freeze-evidence reference required.
    alloc = _parse_utc(ci.allocation_request_utc)
    exp = _parse_utc(ci.expiry_utc)
    now = _parse_utc(now_utc)
    if alloc is None:
        reasons.append("CI-UTC-MISSING")
    if not str(ci.prerequisite_freeze_evidence_digest or "").strip():
        reasons.append("CI-FREEZE-EV-REF-MISSING")

    # indefinite candidate without expiry.
    if exp is None:
        reasons.append("CI-NO-EXPIRY")
    elif now is not None and now >= exp:
        reasons.append("CI-EXPIRED")

    # active deployment target / runtime service name present anywhere identifying.
    if str(ci.expected_image_repository or "").strip().lower() in RUNTIME_SERVICE_NAMES \
            or str(ci.candidate_namespace or "").strip().lower() in RUNTIME_SERVICE_NAMES \
            or DEPLOYED_CONTAINER.lower() in repo_lc:
        reasons.append("CI-ACTIVE-DEPLOYMENT-TARGET")

    # §6 candidate status must be the inert synthetic status in this WO.
    if ci.candidate_status != "SYNTHETIC_UNALLOCATED":
        reasons.append("CI-REAL-STATUS-FORBIDDEN")

    # application binding.
    if str(ci.application or "").strip() != application:
        reasons.append("CI-APPLICATION-MISMATCH")

    return tuple(sorted(set(reasons)))


def validate_candidate_uniqueness(
    ci: object,
    *,
    existing_candidate_ids: Tuple[str, ...],
) -> Tuple[str, ...]:
    """Return () iff `ci`'s candidate_id is not already present in `existing_candidate_ids`, else
    ('CI-CANDIDATE-ID-REUSE',). A reused candidate_id collides an isolated allocation with a prior one."""
    if not isinstance(ci, CandidateIdentity):
        return ("CI-WRONG-TYPE",)
    if str(ci.candidate_id) in {str(x) for x in (existing_candidate_ids or ())}:
        return ("CI-CANDIDATE-ID-REUSE",)
    return tuple()
