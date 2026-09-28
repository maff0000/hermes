"""Proof that `tests.support.destructive_cleanup_guard` genuinely fails closed.

Companion to the hmt-2/emergency-destructive-test-fix incident fix
(`tests/hmt2/test_hmt2i_snapshot_output_path.py`). These tests exist to independently prove the
new cleanup-ownership invariant actually refuses an out-of-bounds deletion attempt -- rather than
just trusting that it does because the code "looks right".
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from market_truth.acquisition import canonical_worker  # noqa: E402
from tests.support.destructive_cleanup_guard import (  # noqa: E402
    TEST_OWNED_SENTINEL_FILENAME,
    UnsafeDeleteError,
    assert_safe_to_delete,
    safe_rmtree,
)


def test_refuses_a_path_with_no_ownership_proof(tmp_path):
    """The core guarantee: a directory that is neither under the given tmp_path nor sentinel-
    marked must be refused, not silently skipped and not silently deleted."""
    outside = Path(tempfile.mkdtemp(prefix="cleanup-guard-out-of-bounds-"))
    (outside / "definitely-real-looking-data.txt").write_text("do not delete me\n", encoding="utf-8")
    try:
        with pytest.raises(UnsafeDeleteError):
            assert_safe_to_delete(outside, tmp_path=tmp_path)
        # Explicitly prove it is a REFUSAL, not a no-op that quietly "succeeded": the directory
        # and its content must still be exactly there afterwards.
        assert outside.exists()
        assert (outside / "definitely-real-looking-data.txt").read_text(encoding="utf-8") == "do not delete me\n"
    finally:
        import shutil
        shutil.rmtree(outside, ignore_errors=True)


def test_safe_rmtree_refuses_and_does_not_delete_an_out_of_bounds_path(tmp_path):
    """Same proof, but through the convenience `safe_rmtree()` wrapper most call sites will
    actually use -- confirms the guard is wired all the way through to the real delete, not just
    the standalone assertion."""
    outside = Path(tempfile.mkdtemp(prefix="cleanup-guard-safe-rmtree-out-of-bounds-"))
    marker = outside / "still-here.txt"
    marker.write_text("still here\n", encoding="utf-8")
    try:
        with pytest.raises(UnsafeDeleteError):
            safe_rmtree(outside, tmp_path=tmp_path)
        assert marker.exists(), "safe_rmtree must not delete anything when the guard refuses"
    finally:
        import shutil
        shutil.rmtree(outside, ignore_errors=True)


def test_allows_a_path_genuinely_inside_tmp_path(tmp_path):
    owned = tmp_path / "scratch-owned-by-this-test"
    owned.mkdir()
    (owned / "file.txt").write_text("x\n", encoding="utf-8")

    assert_safe_to_delete(owned, tmp_path=tmp_path)  # must not raise
    safe_rmtree(owned, tmp_path=tmp_path)
    assert not owned.exists()


def test_allows_a_path_outside_tmp_path_when_sentinel_marked(tmp_path):
    outside = Path(tempfile.mkdtemp(prefix="cleanup-guard-sentinel-owned-"))
    (outside / TEST_OWNED_SENTINEL_FILENAME).write_text("owned-by-test\n", encoding="utf-8")
    (outside / "data.txt").write_text("x\n", encoding="utf-8")
    try:
        assert_safe_to_delete(outside, tmp_path=None)  # must not raise: sentinel proof
        safe_rmtree(outside, tmp_path=None)
        assert not outside.exists()
    finally:
        import shutil
        shutil.rmtree(outside, ignore_errors=True)


def test_forbidden_root_check_cannot_be_defeated_by_tmp_path_membership(tmp_path, monkeypatch):
    """The critical, incident-specific proof: even if a path were (incorrectly) inside the given
    tmp_path, the guard must STILL refuse it if it resolves to the real HMT-2 default canonical-
    store root -- proving the forbidden-root check is a hard override, not just another vote
    alongside the tmp_path/sentinel proofs. We simulate this by monkeypatching
    `canonical_worker.corpus_canonical_store_root` to return a path that genuinely IS under
    tmp_path, and confirm the guard refuses it anyway."""
    fake_real_default = tmp_path / "looks-like-tmp-path-but-is-treated-as-the-real-default"
    fake_real_default.mkdir()

    monkeypatch.setattr(
        canonical_worker, "corpus_canonical_store_root", lambda explicit=None, **kw: fake_real_default
    )

    with pytest.raises(UnsafeDeleteError):
        assert_safe_to_delete(fake_real_default, tmp_path=tmp_path)
    assert fake_real_default.exists(), "must not have been deleted"


def test_forbidden_root_check_also_refuses_a_parent_of_the_real_default(tmp_path, monkeypatch):
    """Deleting an ANCESTOR of the real default root would take the real data down with it --
    this must be refused just as hard as deleting the root itself."""
    fake_real_default = tmp_path / "parent" / "hmt2-gc-mbp1-v1"
    fake_real_default.mkdir(parents=True)
    parent_dir = fake_real_default.parent

    monkeypatch.setattr(
        canonical_worker, "corpus_canonical_store_root", lambda explicit=None, **kw: fake_real_default
    )

    with pytest.raises(UnsafeDeleteError):
        assert_safe_to_delete(parent_dir, tmp_path=tmp_path)
    assert parent_dir.exists()


def test_refuses_the_real_default_research_source_root_by_path_not_name(tmp_path, monkeypatch):
    """The forbidden-root check must work by real path comparison, not by matching a filename
    convention like "research-source" -- prove a differently-NAMED directory that the resolver
    says IS the default research-source root is still refused."""
    oddly_named = tmp_path / "not-named-research-source-at-all"
    oddly_named.mkdir()

    # Make the module's own resolution machinery report this oddly-named directory as THE real
    # default research-source root, by patching what the guard actually consults --
    # canonical_worker.__file__-derived repo_root resolution is bypassed here in favour of
    # directly proving the by-real-path (not by-name) comparison the guard performs.
    from tests.support import destructive_cleanup_guard as guard_mod

    def _fake_forbidden_roots():
        return [("HMT-2 real default research-source root (fake, path-based)", oddly_named.resolve())]

    monkeypatch.setattr(guard_mod, "_real_forbidden_roots", _fake_forbidden_roots)

    with pytest.raises(UnsafeDeleteError):
        assert_safe_to_delete(oddly_named, tmp_path=tmp_path)
    assert oddly_named.exists()
