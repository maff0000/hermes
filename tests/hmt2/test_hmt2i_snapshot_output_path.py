"""HMT-2 canonicalisation checkpoint — regression tests for the corpus-progress SNAPSHOT output
path fix in `research/hmt2/hmt2i_gc_corpus_canonicalise.py`.

Real, already-observed incident this guards against: the driver used to write its
corpus-progress snapshot to the hardcoded, git-tracked `SNAPSHOT_OUTPUT_PATH` UNCONDITIONALLY —
even when a caller pointed the actual canonical output somewhere else entirely via
`--canonical-research-root` / `HMT2_CANONICAL_RESEARCH_ROOT` (e.g. a disposable scratch
directory used for testing). A scratch/test invocation could therefore silently clobber the
authoritative corpus-progress record checked into the real, shared worktree. It was caught and
manually reverted once already by another engineer before this fix.

The fix: `resolve_snapshot_output_path()` decides, purely from whether an alternate canonical
research root was ever selected for this invocation (CLI override OR env var — no hidden
code-level toggle), whether to use the tracked authoritative path or a path derived from that
invocation's own resolved canonical corpus store. `write_snapshot()` now REQUIRES an explicit
`output_path` argument, so it can never silently fall back to the tracked path on its own.

Every test here injects a FAKE `canonical_worker.canonicalise_mbp1_session()` — never a real
vendor decode — mirroring `tests/hmt2/test_hmt2i_gc_corpus_canonicalise.py`'s own established
fake-dependency discipline exactly.

------------------------------------------------------------------------------------------------
EMERGENCY INCIDENT NOTE (hmt-2/emergency-destructive-test-fix, 2026-09-25) — TEST-ISOLATION
DOCTRINE, read before touching this file again
------------------------------------------------------------------------------------------------
`test_default_root_cli_invocation_still_writes_the_tracked_snapshot_as_before` used to call
`main()` with NO `--canonical-research-root` override and NO env var override — i.e. it let
`canonical_worker.corpus_canonical_store_root(None)` resolve to the REAL, repo-relative default
canonical-store root (`<repo_root>/research-canonical-store/hmt2-gc-mbp1-v1`), then
unconditionally `shutil.rmtree()`'d that real path in its `finally` block. Run in a disposable
checkout that is merely wasteful; run against the persistent authoritative worktree (which had
150 real sessions' canonical data, none of it under any `tmp_path`) it destroyed all of it.
Native source data was not affected (separate, untouched directory).

The fix below keeps testing the EXACT same semantic property — "a CLI invocation with no
explicit `--canonical-research-root` (and no env var) still resolves to, and writes, the
'default root' behaviour" — but the test now monkeypatches
`canonical_worker.resolve_canonical_research_root` (the single function every "default root"
resolution in this driver ultimately goes through) so that its OWN "nothing selected" branch
resolves to a fake default root safely inside `tmp_path`, never the real
`research-canonical-store/`. `hmt2i_canonicalise.SNAPSHOT_OUTPUT_PATH` (the tracked-path
constant) is likewise monkeypatched to a `tmp_path`-scoped fake tracked file. The test still
omits `--canonical-research-root` from `sys.argv` and never sets the env var — that omission is
exactly the condition under test, "no override selected" — only the underlying resolution
FUNCTION is redirected, so it can never resolve to the real path in the first place. Any cleanup
this file still performs goes through `tests.support.destructive_cleanup_guard.safe_rmtree()`,
which fails closed (raises, never silently no-ops) unless the target is provably `tmp_path`-
scoped or explicitly sentinel-marked, and unconditionally refuses to touch the real HMT-2
default roots regardless of any other proof offered — see that module for the full doctrine and
`tests/support/test_destructive_cleanup_guard.py` for the proof it actually refuses.

STANDING RULE for any future test in this file or elsewhere in `tests/hmt2/` that needs a
recursive directory delete: never call `shutil.rmtree()` directly on anything derived from a
"no override" / default-root resolution. Route it through
`tests.support.destructive_cleanup_guard.safe_rmtree()` instead. See also the session-start
guard added to `tests/conftest.py` (`pytest_sessionstart`), which refuses to start the WHOLE
suite if the real default HMT-2 canonical-store or research-source root is ever found non-empty
— the doctrine is: this suite is only ever run from a disposable/fresh checkout or an empty
worktree, never from a worktree already holding real retained/canonicalised HMT-2 data.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from market_truth.acquisition import canonical_worker  # noqa: E402
from market_truth.acquisition import hmt2_slice_guard  # noqa: E402
from tests.support.destructive_cleanup_guard import safe_rmtree  # noqa: E402


def _load_module(name: str, relative_path: str):
    """Dynamically load a `research/hmt2/*.py` script by file path, registering it into
    `sys.modules` first — see the identical, more fully-commented helper in
    `tests/hmt2/test_hmt2i_gc_corpus_canonicalise.py` for why this registration matters (it
    keeps a plain `import <name>` inside the dynamically-loaded driver script resolving to the
    SAME module object this test file holds, rather than silently re-executing a second,
    distinct copy)."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


source_ledger_mod = _load_module("hmt2h_gc_corpus_ledger", "research/hmt2/hmt2h_gc_corpus_ledger.py")
canonical_ledger_mod = _load_module("hmt2i_gc_corpus_canonical_ledger", "research/hmt2/hmt2i_gc_corpus_canonical_ledger.py")
hmt2i_canonicalise = _load_module("hmt2i_gc_corpus_canonicalise", "research/hmt2/hmt2i_gc_corpus_canonicalise.py")


def _fake_result(session_id, *, event_set_hash="deadbeef"):
    return canonical_worker.SessionCanonicalisationResult(
        session_id=session_id, native_artefact_sha256="a" * 64, source_record_count=10,
        canonical_event_counts_by_family={"market_trade": 5}, canonical_event_set_hash=event_set_hash,
        partition_relative_paths=("schema=v1/x/part-00000.parquet",),
        partition_semantic_hashes={"schema=v1/x/part-00000.parquet": "s" * 64},
        partition_artifact_hashes={"schema=v1/x/part-00000.parquet": "p" * 64},
        evidence_manifest_relative_path=f"evidence/{session_id}.json",
        evidence_manifest_deterministic_hash="d" * 64,
        lineage_record_relative_path=f"lineage/{session_id}.json",
        quality_record_relative_path=f"quality/{session_id}.json",
        quality_summary={"native_record_count": 10},
    )


def _write_fake_acquisition_ledger(research_source_root: Path, session_ids):
    """Builds a real, on-disk fake acquisition ledger + native artefact bytes under
    `research_source_root`, mirroring `_setup()` in `tests/hmt2/test_hmt2i_gc_corpus_canonicalise.py`."""
    acquisition_ledger = {}
    for i, sid in enumerate(session_ids):
        rel = f"hmt2-gc-mbp1-v1/sessions/{sid}/source/x.dbn.zst"
        native_path = research_source_root / rel
        native_path.parent.mkdir(parents=True, exist_ok=True)
        native_path.write_bytes(b"native bytes")
        acquisition_ledger[sid] = {
            "session_id": sid, "trade_date": f"2020-01-{i + 1:02d}", "request_identity": "req-0001",
            "state": source_ledger_mod.STATE_COMPLETE,
            "artefact": {"sha256": "a" * 64, "object_relative_path": rel},
        }
    ledger_state_path = research_source_root / source_ledger_mod.LEDGER_STATE_RELATIVE_PATH
    source_ledger_mod.save_ledger_atomic(str(ledger_state_path), acquisition_ledger)
    return ledger_state_path


def _monkeypatch_default_root_into_tmp_path(monkeypatch, tmp_path: Path) -> Path:
    """Redirects the underlying "no override selected" default-root RESOLUTION FUNCTION itself
    (`canonical_worker.resolve_canonical_research_root`) into a fake default root safely inside
    `tmp_path` — required approach per the emergency incident fix (see module docstring): the
    caller must still be free to omit `--canonical-research-root` / the env var (that omission is
    the condition under test), but the function that decides where "no override" resolves TO
    must never be able to reach the real, repo-relative `research-canonical-store/` root.

    An explicit override (CLI arg or env var) is deliberately still passed through to the REAL
    resolver unchanged — this fake is a faithful drop-in for the "nothing selected" branch only,
    never a blanket stub, so a test that mixes both scenarios still gets correct behaviour for
    the override case.
    """
    fake_default_research_root = tmp_path / "fake-default-hmt2-canonical-research-root"
    real_resolve = canonical_worker.resolve_canonical_research_root

    def _fake_resolve_canonical_research_root(explicit=None, *, repo_root=None):
        if explicit or os.environ.get(canonical_worker.CANONICAL_RESEARCH_ROOT_ENV_VAR):
            return real_resolve(explicit, repo_root=repo_root)
        return fake_default_research_root

    monkeypatch.setattr(canonical_worker, "resolve_canonical_research_root", _fake_resolve_canonical_research_root)
    return fake_default_research_root


# ----------------------------------------------------------------------------------------------
# Unit-level: resolve_snapshot_output_path() branching logic.
# ----------------------------------------------------------------------------------------------

def test_no_override_resolves_to_the_tracked_authoritative_path(monkeypatch):
    monkeypatch.delenv(canonical_worker.CANONICAL_RESEARCH_ROOT_ENV_VAR, raising=False)
    assert hmt2i_canonicalise.resolve_snapshot_output_path(None) == hmt2i_canonicalise.SNAPSHOT_OUTPUT_PATH


def test_explicit_cli_override_diverts_the_snapshot_under_the_alternate_root(tmp_path, monkeypatch):
    monkeypatch.delenv(canonical_worker.CANONICAL_RESEARCH_ROOT_ENV_VAR, raising=False)
    scratch_root = tmp_path / "scratch"

    result_path = hmt2i_canonicalise.resolve_snapshot_output_path(str(scratch_root))

    assert result_path != hmt2i_canonicalise.SNAPSHOT_OUTPUT_PATH
    expected_store_root = canonical_worker.corpus_canonical_store_root(str(scratch_root))
    assert Path(result_path) == expected_store_root / "hmt2i-gc-corpus-canonical-ledger-snapshot-v1.json"
    assert str(scratch_root) in result_path


def test_env_var_override_alone_also_diverts_the_snapshot(tmp_path, monkeypatch):
    scratch_root = tmp_path / "scratch-env"
    monkeypatch.setenv(canonical_worker.CANONICAL_RESEARCH_ROOT_ENV_VAR, str(scratch_root))

    result_path = hmt2i_canonicalise.resolve_snapshot_output_path(None)

    assert result_path != hmt2i_canonicalise.SNAPSHOT_OUTPUT_PATH
    assert str(scratch_root) in result_path


def test_write_snapshot_requires_an_explicit_output_path(tmp_path):
    """Guards against the bug ever being reintroduced by a future edit that gives
    `write_snapshot()` a convenient default of `SNAPSHOT_OUTPUT_PATH` again."""
    with pytest.raises(TypeError):
        hmt2i_canonicalise.write_snapshot({"ok": True})  # missing required output_path kwarg


# ----------------------------------------------------------------------------------------------
# End-to-end via main(): (a) a scratch invocation must never dirty the tracked snapshot file.
# ----------------------------------------------------------------------------------------------

def test_scratch_cli_invocation_never_mutates_the_tracked_snapshot_file(tmp_path, monkeypatch):
    tracked_path = Path(hmt2i_canonicalise.SNAPSHOT_OUTPUT_PATH)
    assert tracked_path.exists(), "tracked snapshot file must already exist in this worktree"
    before_bytes = tracked_path.read_bytes()
    before_mtime_ns = tracked_path.stat().st_mtime_ns

    research_source_root = tmp_path / "research-source"
    ledger_state_path = _write_fake_acquisition_ledger(research_source_root, ["GC-2020-01-01"])
    scratch_canonical_root = tmp_path / "scratch-canonical-root"

    monkeypatch.delenv(canonical_worker.CANONICAL_RESEARCH_ROOT_ENV_VAR, raising=False)
    # This test calls main() in-process, outside the hmt2.slice cgroup, against a tiny synthetic
    # fixture (fake canonicalise_mbp1_session(), no real vendor decode) -- exactly the case the
    # containment guard's own error message names as the sanctioned bypass (see
    # tests/hmt2/test_hmt2_slice_guard_entrypoints.py for the identical established pattern).
    monkeypatch.setenv(hmt2_slice_guard.ALLOW_OUTSIDE_SLICE_ENV_VAR, "1")
    monkeypatch.setattr(hmt2i_canonicalise, "RESEARCH_SOURCE_ROOT", str(research_source_root))
    monkeypatch.setattr(hmt2i_canonicalise, "SOURCE_LEDGER_STATE_PATH", str(ledger_state_path))
    monkeypatch.setattr(canonical_worker, "canonicalise_mbp1_session", lambda **kw: _fake_result(kw["session_id"]))
    monkeypatch.setattr(sys, "argv", [
        "hmt2i_gc_corpus_canonicalise.py",
        "--session-ids", "GC-2020-01-01",
        "--canonical-research-root", str(scratch_canonical_root),
    ])

    try:
        hmt2i_canonicalise.main()
    finally:
        # Safety net regardless of outcome -- never leave the tracked file mutated by this test.
        after_bytes = tracked_path.read_bytes()
        if after_bytes != before_bytes:
            tracked_path.write_bytes(before_bytes)

    assert after_bytes == before_bytes, (
        "REGRESSION: a scratch invocation (--canonical-research-root pointing at a disposable "
        "directory) mutated the tracked authoritative corpus-progress snapshot file"
    )
    assert tracked_path.stat().st_mtime_ns == before_mtime_ns

    # Positive control: the scratch run really did execute and really did write ITS OWN
    # snapshot, just under the scratch root instead -- proving this isn't a false pass from the
    # run failing outright before ever reaching the snapshot-write step.
    scratch_snapshot_path = canonical_worker.corpus_canonical_store_root(str(scratch_canonical_root)) / (
        "hmt2i-gc-corpus-canonical-ledger-snapshot-v1.json"
    )
    assert scratch_snapshot_path.exists()
    scratch_snapshot = json.loads(scratch_snapshot_path.read_text(encoding="utf-8"))
    assert scratch_snapshot["result"]["processed"][0]["session_id"] == "GC-2020-01-01"


# ----------------------------------------------------------------------------------------------
# End-to-end via main(): (b) a default/real-root invocation is unaffected -- no regression.
#
# See the EMERGENCY INCIDENT NOTE at the top of this file: this test used to exercise the REAL
# default canonical-store root and unconditionally shutil.rmtree() it afterwards. It now
# monkeypatches the underlying default-root resolution function into tmp_path via
# `_monkeypatch_default_root_into_tmp_path()` — the CLI/env override is still genuinely absent
# (that is the condition under test), but "absent" can no longer resolve anywhere real.
# ----------------------------------------------------------------------------------------------

def test_default_root_cli_invocation_still_writes_the_tracked_snapshot_as_before(tmp_path, monkeypatch):
    fake_tracked_path = tmp_path / "fake-tracked-snapshot-dir" / "hmt2i-gc-corpus-canonical-ledger-snapshot-v1.json"
    fake_tracked_path.parent.mkdir(parents=True, exist_ok=True)
    fake_tracked_path.write_text(json.dumps({"placeholder": True}), encoding="utf-8")
    monkeypatch.setattr(hmt2i_canonicalise, "SNAPSHOT_OUTPUT_PATH", str(fake_tracked_path))

    research_source_root = tmp_path / "research-source"
    ledger_state_path = _write_fake_acquisition_ledger(research_source_root, ["GC-2020-02-02"])

    # The default-root redirection is scoped to its OWN monkeypatch.context() and explicitly
    # exited before cleanup below. Reason: while it is active,
    # canonical_worker.resolve_canonical_research_root() -- and therefore
    # tests.support.destructive_cleanup_guard's own forbidden-root check, which calls the SAME
    # live function -- cannot distinguish "this test's fake tmp_path default" from "the real
    # production default", because inside this process they are, deliberately, the same value.
    # Exiting the context first restores the REAL resolver, so the guard used for cleanup below
    # correctly computes the TRUE real default (never this test's fake) and can tell the two
    # apart again.
    with monkeypatch.context() as scoped:
        scoped.delenv(canonical_worker.CANONICAL_RESEARCH_ROOT_ENV_VAR, raising=False)
        fake_default_research_root = _monkeypatch_default_root_into_tmp_path(scoped, tmp_path)
        # See identical note above -- in-process main() call outside hmt2.slice, tiny synthetic
        # fixture only, sanctioned bypass per the guard's own documented escape hatch.
        scoped.setenv(hmt2_slice_guard.ALLOW_OUTSIDE_SLICE_ENV_VAR, "1")
        scoped.setattr(hmt2i_canonicalise, "RESEARCH_SOURCE_ROOT", str(research_source_root))
        scoped.setattr(hmt2i_canonicalise, "SOURCE_LEDGER_STATE_PATH", str(ledger_state_path))
        scoped.setattr(canonical_worker, "canonicalise_mbp1_session", lambda **kw: _fake_result(kw["session_id"]))
        scoped.setattr(sys, "argv", [
            "hmt2i_gc_corpus_canonicalise.py",
            "--session-ids", "GC-2020-02-02",
        ])  # no --canonical-research-root override -- exercises the "default root" branch, which
            # this test has redirected (above) into tmp_path rather than the real repo path.

        # Positive proof this test still exercises the REAL "no override" branch of
        # resolve_canonical_research_root(), not a bypass of it: the fake resolver must actually
        # be in effect for `explicit=None`.
        assert canonical_worker.resolve_canonical_research_root(None) == fake_default_research_root

        hmt2i_canonicalise.main()

        assert fake_tracked_path.exists()
        snapshot = json.loads(fake_tracked_path.read_text(encoding="utf-8"))
        assert snapshot["result"]["processed"][0]["session_id"] == "GC-2020-02-02"
        assert snapshot["result"]["processed"][0]["canonical_event_set_hash"] == "deadbeef"

        # Positive control, mirroring the original test's own: the run really did execute
        # against the (fake, tmp_path-scoped) default root and really did write real
        # ledger/store artefacts there -- proving this isn't a false pass from the run failing
        # before reaching the snapshot-write step.
        real_default_store_root = canonical_worker.corpus_canonical_store_root(None)
        assert real_default_store_root == fake_default_research_root / canonical_worker.CANONICAL_CORPUS_STORE_ROOT_NAME
        assert real_default_store_root.exists()

    # `scoped` has now been fully undone -- resolve_canonical_research_root() is back to the
    # genuine original. Cleanup target is provably tmp_path-scoped (it is literally
    # `fake_default_research_root`, created above under `tmp_path`); routed through the
    # fail-closed guard rather than a bare shutil.rmtree(), per this file's standing rule. Note
    # this is technically redundant with pytest's own tmp_path teardown -- kept anyway to
    # demonstrate (and exercise, on every run) the standing rule in practice.
    safe_rmtree(fake_default_research_root, tmp_path=tmp_path)
