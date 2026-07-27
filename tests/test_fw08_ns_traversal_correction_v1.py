"""C-PR121-NS-TRAVERSAL correction tests — canonical namespace/tag/repository normalisation (§9,§10).

WO-HELM-HERMES-PR121-C-NS-TRAVERSAL-CANONICAL-NAMESPACE-NORMALISATION-CORRECTION-0001.
Authority: HELM (HERMES market-data lane). Created (UTC): 2026-07-27. Contract version: 1.

The prior head accepted candidate namespaces via a weak string prefix, so path-traversal / encoded / sibling
lookalike forms ESCAPED the isolated `hermes-fw08-candidate` root. These tests prove the corrected validator
CANONICALISES (never repairs) the namespace, proposed immutable tag and expected image repository and rejects
every escape BEFORE the candidate identity is accepted. No real freeze/allocation/tag/image — inert.
"""
from __future__ import annotations

import pytest

import design.hermes_fw08_candidate_identity_v1 as ci
import tests.test_fw08_source_freeze_candidate_identity_v1 as base

NOW = base.NOW


def _ns_reasons(**over):
    return set(ci.validate_candidate_identity(base._candidate(**over), now_utc=NOW))


# ============================================================================ §6 required traversal rejection
_NS_ESCAPES = [
    "hermes-fw08-candidate/../argus",
    "hermes-fw08-candidate/../../etc",
    "hermes-fw08-candidate/x/../../argus",
    "hermes-fw08-candidate/..%2fargus",
    "hermes-fw08-candidate/%2e%2e/argus",
    "hermes-fw08-candidate/%252e%252e%252fargus",
    "hermes-fw08-candidate-../argus",
    "hermes-fw08-candidate/./argus",
    "hermes-fw08-candidate//argus",
    "hermes-fw08-candidate\\..\\argus",
    "hermes-fw08-candidate%2f..%2fargus",
    "hermes-fw08-candidate%252f..%252fargus",
    # §5 sibling / prefix / absolute / foreign-app-first
    "hermes-fw08-candidate-evil/x",
    "hermes-fw08-candidate../x",
    "/hermes-fw08-candidate/x",
    "argus/hermes-fw08-candidate/x",
    # case / unicode-confusable / whitespace / control
    "HERMES-FW08-CANDIDATE/x",
    "hermes-fw08-candidate/x ",
    " hermes-fw08-candidate/x",
    "hermes-fw08-candidate/arguѕ",          # cyrillic 's' confusable -> non-ASCII
    "hermes-fw08-candidate/x\x00",
    # scheme / host / query / fragment injection
    "https://evil/hermes-fw08-candidate/x",
    "hermes-fw08-candidate/x?to=argus",
    "hermes-fw08-candidate/x#argus",
    # exact root with no child where a child is required
    "hermes-fw08-candidate",
]


@pytest.mark.parametrize("ns", _NS_ESCAPES)
def test_namespace_escape_rejected(ns):
    r = _ns_reasons(candidate_namespace=ns)
    assert ("CI-NAMESPACE-NOT-ISOLATED" in r) or ("CI-NAMESPACE-TRAVERSAL" in r), (ns, r)
    # the candidate identity as a whole is NOT accepted.
    assert r != set()


def test_traversal_never_silently_repaired():
    # a traversal that would "resolve" back inside the root must STILL reject (no silent normalisation).
    r = _ns_reasons(candidate_namespace="hermes-fw08-candidate/x/../y")
    assert "CI-NAMESPACE-TRAVERSAL" in r


# ============================================================================ §7 tag traversal
@pytest.mark.parametrize("tag", [
    "cand-../argus", "cand/../x", "cand%2f..%2fx", "cand\\x", "cand x", "CAND-X",
    "cand-ѕ", "cand-x\x00", "cand#x", "cand?x", "../latest",
])
def test_tag_traversal_rejected(tag):
    assert "CI-TAG-TRAVERSAL" in _ns_reasons(proposed_immutable_tag=tag)


# ============================================================================ §8 repository traversal
@pytest.mark.parametrize("repo", [
    "hermes-fw08-candidate/../argus", "hermes-fw08-candidate/%2e%2e/x", "hermes-fw08-candidate//x",
    "argus/hermes-fw08-candidate/img", "hermes-fw08-candidate-evil/img", "/hermes-fw08-candidate/img",
    "https://evil/hermes-fw08-candidate/img", "hermes-fw08-candidate", "HERMES-FW08-CANDIDATE/img",
])
def test_repository_escape_rejected(repo):
    r = _ns_reasons(expected_image_repository=repo)
    assert ("CI-REPO-NOT-ISOLATED" in r) or ("CI-REPO-TRAVERSAL" in r), (repo, r)


# ============================================================================ cross-application escape
def test_cross_application_namespace_still_flagged():
    r = _ns_reasons(candidate_namespace="argus-fw08-candidate/xyz")
    assert "CI-CROSS-APPLICATION-NAMESPACE" in r and "CI-NAMESPACE-NOT-ISOLATED" in r


def test_nested_root_after_foreign_head_rejected():
    r = _ns_reasons(candidate_namespace="argus/hermes-fw08-candidate/x")
    assert "CI-NAMESPACE-NOT-ISOLATED" in r


# ============================================================================ §9 positive canonical cases
def test_positive_canonical_namespace():
    r = _ns_reasons(candidate_namespace="hermes-fw08-candidate/lifecycle-001/candidate-001")
    assert not any(x.startswith("CI-NAMESPACE") for x in r), r


def test_positive_canonical_tag_and_repository():
    r = ci.validate_candidate_identity(base._candidate(), now_utc=NOW)
    assert r == (), r                       # the default fixture is fully canonical
    # an explicit canonical repository child.
    r2 = _ns_reasons(expected_image_repository="hermes-fw08-candidate/img/v1")
    assert not any(x.startswith("CI-REPO") for x in r2), r2


def test_positive_values_remain_byte_identical_after_validation():
    # the validator does NOT mutate the candidate — canonical inputs pass unchanged.
    c = base._candidate()
    ns0, tag0, repo0 = c.candidate_namespace, c.proposed_immutable_tag, c.expected_image_repository
    ci.validate_candidate_identity(c, now_utc=NOW)
    assert (c.candidate_namespace, c.proposed_immutable_tag, c.expected_image_repository) == (ns0, tag0, repo0)


# ============================================================================ inert boundary preserved
def test_inert_boundary_unchanged():
    import design.hermes_fw08_source_freeze_v1 as sf
    import design.hermes_fw08_lifecycle_state_v1 as ls
    assert sf.production_real_freeze_available() is False
    assert ls.real_state_transition_possible() is False
    c, r = ci.allocate_candidate_identity(request={}, mode="REAL_CANDIDATE", now_utc=NOW)
    assert c is None and "CI-REAL-ALLOCATION-UNAVAILABLE" in r
