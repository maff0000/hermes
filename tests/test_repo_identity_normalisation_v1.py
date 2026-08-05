"""Focused tests for canonical repository-identity normalisation.

WO-HELM-HERMES-CI-RUNNER-ENVIRONMENT-CONTRACT-REPAIR-0001.
The provenance/governed-build identity check must compare repository IDENTITY, not transport syntax, so a
GitHub Actions HTTPS checkout of the SAME repo is accepted while genuinely different repos are rejected.
"""
import pytest

from tools.hermes_clean_build_context_v1 import (
    canonical_repo_identity,
    CleanBuildContextError,
)

CANON = "github.com/maff0000/hermes"


@pytest.mark.parametrize("url", [
    "https://github.com/maff0000/hermes",
    "https://github.com/maff0000/hermes.git",
    "http://github.com/maff0000/hermes.git",
    "git@github.com:maff0000/hermes",
    "git@github.com:maff0000/hermes.git",
    "ssh://git@github.com/maff0000/hermes",
    "ssh://git@github.com/maff0000/hermes.git",
    "ssh://git@github.com:22/maff0000/hermes.git",
    "https://GITHUB.com/maff0000/hermes.git",          # host case-insensitive
    "https://x-access-token:TOKEN@github.com/maff0000/hermes.git",  # userinfo stripped (GH Actions token form)
])
def test_same_repo_all_transports_are_equal(url):
    assert canonical_repo_identity(url) == CANON


def test_https_and_ssh_forms_compare_equal():
    assert canonical_repo_identity("https://github.com/maff0000/hermes") \
        == canonical_repo_identity("git@github.com:maff0000/hermes.git")


@pytest.mark.parametrize("url", [
    "https://github.com/someoneelse/hermes.git",   # wrong owner
    "git@github.com:maff0000/other-repo.git",      # wrong repo
    "https://gitlab.com/maff0000/hermes.git",      # wrong host
    "git@evil.example:maff0000/hermes.git",        # wrong host
])
def test_different_repository_is_not_equal(url):
    assert canonical_repo_identity(url) != CANON


@pytest.mark.parametrize("bad", ["", "   ", "not-a-url", "https://github.com/onlyowner", "github.com"])
def test_unparseable_or_incomplete_fails_closed(bad):
    with pytest.raises(CleanBuildContextError):
        canonical_repo_identity(bad)
