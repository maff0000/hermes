"""Fail-closed cleanup-ownership invariant for tests that need a recursive directory delete.

Born directly from the hmt-2/emergency-destructive-test-fix incident: a merged test
(`tests/hmt2/test_hmt2i_snapshot_output_path.py::
test_default_root_cli_invocation_still_writes_the_tracked_snapshot_as_before`) resolved the REAL
default HMT-2 canonical-store root (`canonical_worker.corpus_canonical_store_root(None)`, no
override selected) and unconditionally `shutil.rmtree()`'d it in a bare `finally` block. Run
against the persistent authoritative worktree, that destroyed 150 real sessions' canonical data.
Native source data was not affected (separate directory).

`assert_safe_to_delete()` (and the `safe_rmtree()` convenience wrapper built on it) is the single
choke point every test in this suite performing a recursive delete must now pass through. It
REFUSES -- raises `UnsafeDeleteError`, never a silent no-op and never a silent proceed -- unless
the caller can prove the target is test-owned, via at least one of:

  1. The target `Path.is_relative_to()` the test's own `tmp_path` (pytest's per-test scratch
     directory, created fresh and torn down by pytest itself -- the strongest available proof of
     test-ownership).
  2. The target directory contains an explicit, test-created sentinel file (default name
     `TEST_OWNED_SENTINEL_FILENAME` below), for the rarer case a test legitimately needs to clean
     up something NOT under `tmp_path`. The sentinel must actually exist on disk because a test's
     own setup code wrote it -- never inferred from the directory's name or path shape alone.

Independently of, and BEFORE, both proofs above, this function unconditionally refuses to delete
a path that resolves -- by real, resolved-path comparison, never by name/prefix guesswork -- to,
inside, or as an ancestor of either real HMT-2 default: `canonical_worker.
corpus_canonical_store_root(None)` and the driver's own default `research-source/` root. This
check cannot be defeated by either proof above: even a path that is (incorrectly) inside the
tmp_path passed in, or that carries the sentinel file, is still refused if it also overlaps one of
these two real roots. This is the exact defence the incident needed and did not have.
"""
from __future__ import annotations

from pathlib import Path

TEST_OWNED_SENTINEL_FILENAME = ".pytest-owned-scratch-directory"


class UnsafeDeleteError(RuntimeError):
    """Raised instead of silently no-op'ing or proceeding, whenever a caller cannot prove a
    recursive-delete target is test-owned, or the target overlaps a real HMT-2 default root."""


def _resolved(path) -> Path:
    return Path(path).resolve()


def _overlaps(a: Path, b: Path) -> bool:
    """True if `a` and `b` are the same path, or either is an ancestor of the other -- i.e.
    deleting one would necessarily affect the other."""
    return a == b or a in b.parents or b in a.parents


def _real_forbidden_roots() -> list[tuple[str, Path]]:
    """The real, resolved HMT-2 default roots that may never be deleted through this helper,
    regardless of any other proof of ownership. Resolved fresh on every call (never cached), so
    a test's own env-var monkeypatching of HMT2_CANONICAL_RESEARCH_ROOT during the same process
    is still correctly honoured. Import failures degrade to "no forbidden roots known" rather
    than raising, so this module stays usable from test contexts unrelated to HMT-2 -- the
    tmp_path / sentinel proofs below still apply in full."""
    forbidden: list[tuple[str, Path]] = []
    try:
        from market_truth.acquisition import canonical_worker as _canonical_worker
    except ImportError:
        return forbidden

    try:
        forbidden.append((
            "HMT-2 real default canonical-store root",
            _resolved(_canonical_worker.corpus_canonical_store_root(None)),
        ))
    except Exception:
        pass

    try:
        repo_root = Path(_canonical_worker.__file__).resolve().parents[2]
        forbidden.append((
            "HMT-2 real default research-source root",
            _resolved(repo_root / "research-source"),
        ))
    except Exception:
        pass

    return forbidden


def assert_safe_to_delete(path, *, tmp_path=None) -> None:
    """Raise `UnsafeDeleteError` unless `path` is demonstrably test-owned and does not overlap a
    real HMT-2 default root. Returns normally (does nothing else) when the target passes --
    callers still perform the actual delete themselves; this function's only job is the
    fail-closed permission check. See module docstring for the exact proof rules."""
    target = _resolved(path)

    for label, forbidden_path in _real_forbidden_roots():
        if _overlaps(target, forbidden_path):
            raise UnsafeDeleteError(
                f"REFUSING to delete {target} -- it IS (or overlaps) the {label} "
                f"({forbidden_path}). This is exactly the class of accident that destroyed 150 "
                f"real HMT-2 sessions' canonical data (hmt-2/emergency-destructive-test-fix "
                f"incident). This real path may never be deleted through assert_safe_to_delete() "
                f"/ safe_rmtree(), regardless of any other ownership proof offered."
            )

    if tmp_path is not None:
        tmp_path_resolved = _resolved(tmp_path)
        if target == tmp_path_resolved or tmp_path_resolved in target.parents:
            return  # proof 1: genuinely inside pytest's own per-test scratch directory

    if target.is_dir() and (target / TEST_OWNED_SENTINEL_FILENAME).is_file():
        return  # proof 2: explicit, test-created sentinel found on disk

    raise UnsafeDeleteError(
        f"REFUSING to delete {target} -- not demonstrably test-owned: it is not inside the "
        f"tmp_path passed to assert_safe_to_delete()/safe_rmtree(), and it contains no "
        f"{TEST_OWNED_SENTINEL_FILENAME!r} sentinel file. Pass the test's own tmp_path, or "
        f"create that sentinel file yourself in test setup, before attempting a recursive "
        f"delete."
    )


def safe_rmtree(path, *, tmp_path=None) -> None:
    """`assert_safe_to_delete(path, tmp_path=tmp_path)` followed by an actual `shutil.rmtree()`
    -- the convenience, single-call form most cleanup code should prefer. Does nothing if the
    (already-proven-safe) path does not exist."""
    import shutil

    assert_safe_to_delete(path, tmp_path=tmp_path)
    target = Path(path)
    if target.exists():
        shutil.rmtree(target)
