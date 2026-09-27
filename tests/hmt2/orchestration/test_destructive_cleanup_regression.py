"""Adversarial destructive-cleanup protection for this dispatch's new fixtures/tests.

Regression-tests the exact prior real incident (hmt-2/emergency-destructive-test-fix: a merged
test unconditionally `shutil.rmtree()`'d the REAL default HMT-2 canonical-store root in a bare
`finally` block and destroyed 150 real sessions' canonical data) against the specific new surface
this dispatch adds (quarantine roots, allowlist/evidence fixtures) -- reusing the existing,
already-in-Git `tests/support/destructive_cleanup_guard.py` choke point rather than inventing a
second, divergent cleanup-safety mechanism.

No test in this new suite ever calls `shutil.rmtree()` directly on anything -- every fixture
directory this dispatch's tests create lives under pytest's own `tmp_path` and is torn down by
pytest itself. This file exists to PROVE, adversarially, that if a future test in this area ever
did add an explicit cleanup step, `assert_safe_to_delete()`/`safe_rmtree()` would still refuse
every category of unsafe target the governing WO calls out by name."""
import os

import pytest

from tests.support.destructive_cleanup_guard import UnsafeDeleteError, assert_safe_to_delete, safe_rmtree


def test_refuses_the_real_research_source_and_canonical_store_roots(tmp_path):
    """The exact incident class: a path that IS (or overlaps) one of the two real HMT-2 default
    roots must be refused, regardless of any other proof of ownership offered."""
    try:
        from market_truth.acquisition import canonical_worker
    except ImportError:
        pytest.skip("market_truth.acquisition.canonical_worker not importable in this environment")

    real_canonical_root = canonical_worker.corpus_canonical_store_root(None)
    with pytest.raises(UnsafeDeleteError):
        assert_safe_to_delete(real_canonical_root, tmp_path=tmp_path)

    import market_truth.acquisition.canonical_worker as cw_mod
    repo_root = __import__("pathlib").Path(cw_mod.__file__).resolve().parents[2]
    real_source_root = repo_root / "research-source"
    with pytest.raises(UnsafeDeleteError):
        assert_safe_to_delete(real_source_root, tmp_path=tmp_path)


def test_refuses_ancestor_path_deletion(tmp_path):
    """An ancestor of a forbidden root (e.g. the repo root itself, or `/`) must also be refused
    -- deleting an ancestor would necessarily delete the forbidden root too."""
    try:
        from market_truth.acquisition import canonical_worker
    except ImportError:
        pytest.skip("market_truth.acquisition.canonical_worker not importable in this environment")

    real_canonical_root = canonical_worker.corpus_canonical_store_root(None)
    ancestor = real_canonical_root.parent.parent  # well above the actual store root
    with pytest.raises(UnsafeDeleteError):
        assert_safe_to_delete(ancestor, tmp_path=tmp_path)


def test_refuses_symlink_dereferencing_into_a_forbidden_root(tmp_path):
    """A symlink INSIDE tmp_path that points AT a forbidden root must still be refused --
    `assert_safe_to_delete` resolves the path (follows symlinks) before comparing, so the
    tmp_path-containment proof cannot be defeated by indirection."""
    try:
        from market_truth.acquisition import canonical_worker
    except ImportError:
        pytest.skip("market_truth.acquisition.canonical_worker not importable in this environment")

    real_canonical_root = canonical_worker.corpus_canonical_store_root(None)
    decoy = tmp_path / "looks-like-a-scratch-dir"
    try:
        decoy.symlink_to(real_canonical_root, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation not permitted in this test environment")
    with pytest.raises(UnsafeDeleteError):
        assert_safe_to_delete(decoy, tmp_path=tmp_path)


def test_refuses_path_traversal_out_of_tmp_path(tmp_path):
    """A path that LOOKS like it is under tmp_path but `..`s its way back out to a forbidden
    root must still be refused -- proving the check compares fully-resolved paths, never raw
    (unresolved) prefixes."""
    try:
        from market_truth.acquisition import canonical_worker
    except ImportError:
        pytest.skip("market_truth.acquisition.canonical_worker not importable in this environment")

    real_canonical_root = canonical_worker.corpus_canonical_store_root(None)
    # Construct "<tmp_path>/../../../.../<real root>" via relative traversal from inside tmp_path.
    rel = os.path.relpath(str(real_canonical_root), start=str(tmp_path))
    traversal_path = tmp_path / rel
    with pytest.raises(UnsafeDeleteError):
        assert_safe_to_delete(traversal_path, tmp_path=tmp_path)


def test_refuses_a_sentinel_planted_inside_a_forbidden_root(tmp_path, monkeypatch):
    """Even the strongest non-tmp_path proof (a `.pytest-owned-scratch-directory` sentinel file
    genuinely present on disk) must NOT override the forbidden-root check -- proving the
    forbidden-root refusal is checked first and unconditionally, exactly as documented. Here we
    monkeypatch the resolved forbidden root itself to a disposable fixture directory so the
    sentinel-planting is genuinely adversarial (a real forbidden root cannot safely be written
    into by a test)."""
    try:
        from market_truth.acquisition import canonical_worker
    except ImportError:
        pytest.skip("market_truth.acquisition.canonical_worker not importable in this environment")

    pretend_forbidden_root = tmp_path / "pretend-forbidden-root"
    pretend_forbidden_root.mkdir()
    (pretend_forbidden_root / ".pytest-owned-scratch-directory").write_text("planted")

    monkeypatch.setattr(
        canonical_worker, "corpus_canonical_store_root", lambda *a, **k: pretend_forbidden_root
    )

    # Even though the sentinel is genuinely present on disk, AND tmp_path genuinely contains
    # this target, the forbidden-root check (checked first, unconditionally) must still refuse.
    with pytest.raises(UnsafeDeleteError):
        assert_safe_to_delete(pretend_forbidden_root, tmp_path=tmp_path)


def test_refuses_a_spoofed_tmp_path_claim(tmp_path):
    """Passing a `tmp_path=` that is NOT actually the pytest fixture for this test (e.g. a
    caller claiming some unrelated directory is 'the' tmp_path) must not grant ownership of a
    target that is not really inside it."""
    unrelated_dir = tmp_path / "unrelated"
    unrelated_dir.mkdir()
    spoofed_tmp_path_claim = tmp_path / "not-actually-an-ancestor" / "of-target"
    target = tmp_path / "real-target-elsewhere"
    target.mkdir()
    with pytest.raises(UnsafeDeleteError):
        assert_safe_to_delete(target, tmp_path=spoofed_tmp_path_claim)


def test_safe_rmtree_succeeds_for_a_genuine_tmp_path_owned_directory(tmp_path):
    """Positive control: a genuinely tmp_path-owned directory (this dispatch's own quarantine
    fixtures, for example) is correctly ALLOWED -- proving this suite is not merely
    fail-closed-to-everything, but discriminates correctly."""
    owned = tmp_path / "quarantine-fixture"
    owned.mkdir()
    (owned / "file.txt").write_text("disposable")
    safe_rmtree(owned, tmp_path=tmp_path)
    assert not owned.exists()
