"""Build provenance / source-identity contract. WO-...-PROVENANCE-SCOPE-READINESS-...-0001. Pure, no I/O."""
import os
import pathlib

import pytest

import utils.hermes_build_identity_v1 as bi

_ROOT = pathlib.Path(__file__).resolve().parents[1]
GOOD = "cdbaabd0cc3a069da8357bd8a2183a5261e3b99c"


@pytest.mark.parametrize("sha,valid", [
    (GOOD, True),
    ("", False),
    (None, False),
    ("UNKNOWN_SOURCE_SHA", False),
    ("abc123", False),                                   # too short
    (GOOD.upper(), False),                               # uppercase not 40-lowercase-hex
    (GOOD[:-1] + "z", False),                            # non-hex
])
def test_validate_build_identity_source_sha_forms(sha, valid):
    d = {"source_sha": sha, "build_utc": "2026-08-06T09:11:15Z"}   # valid build_utc so only the SHA varies
    reasons = bi.validate_build_identity(d)
    assert (len(reasons) == 0) is valid, (sha, reasons)


def test_build_identity_candidate_mode_flags_invalid_sha(monkeypatch):
    monkeypatch.setenv("HERMES_BUILD_CLASSIFICATION", "NON_PROMOTED_ENGINEERING_CANDIDATE")
    monkeypatch.delenv("SOURCE_SHA", raising=False)      # -> sentinel UNKNOWN_SOURCE_SHA
    d = bi.build_identity()
    assert d["source_sha"] == "UNKNOWN_SOURCE_SHA" and d["build_identity_valid"] is False


def test_build_identity_valid_with_governed_sha(monkeypatch):
    monkeypatch.setenv("HERMES_BUILD_CLASSIFICATION", "NON_PROMOTED_ENGINEERING_CANDIDATE")
    monkeypatch.setenv("SOURCE_SHA", GOOD)
    monkeypatch.setenv("BUILD_UTC", "2026-08-06T09:11:15Z")        # governed build stamps both
    d = bi.build_identity()
    assert d["source_sha"] == GOOD and d["build_identity_valid"] is True


def test_build_wrapper_is_fail_closed():
    """The governed build wrapper must enforce clean tree, 40-hex SHA, and OCI-revision==SOURCE_SHA before promotion."""
    src = (_ROOT / "ops/build/build_production_candidate.sh").read_text()
    assert "git status --porcelain" in src and "refuse to build" in src        # dirty-tree fail-closed
    assert "'^[0-9a-f]{40}$'" in src                                            # 40-hex enforcement
    assert 'org.opencontainers.image.revision' in src and '"$REV" != "$SOURCE_SHA"' in src   # label==sha verify
    assert "SOURCE_SHA=$(git rev-parse HEAD)" in src                           # SHA from git, not mutable workdir


def test_compose_passes_source_sha_build_arg():
    compose = (_ROOT / "docker-compose.yml").read_text()
    assert "SOURCE_SHA: ${SOURCE_SHA" in compose and "BUILD_UTC: ${BUILD_UTC" in compose
