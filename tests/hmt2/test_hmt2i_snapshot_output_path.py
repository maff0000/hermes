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
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from market_truth.acquisition import canonical_worker  # noqa: E402


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
# ----------------------------------------------------------------------------------------------

def test_default_root_cli_invocation_still_writes_the_tracked_snapshot_as_before(tmp_path, monkeypatch):
    tracked_path = Path(hmt2i_canonicalise.SNAPSHOT_OUTPUT_PATH)
    assert tracked_path.exists()
    before_bytes = tracked_path.read_bytes()

    research_source_root = tmp_path / "research-source"
    ledger_state_path = _write_fake_acquisition_ledger(research_source_root, ["GC-2020-02-02"])

    monkeypatch.delenv(canonical_worker.CANONICAL_RESEARCH_ROOT_ENV_VAR, raising=False)
    monkeypatch.setattr(hmt2i_canonicalise, "RESEARCH_SOURCE_ROOT", str(research_source_root))
    monkeypatch.setattr(hmt2i_canonicalise, "SOURCE_LEDGER_STATE_PATH", str(ledger_state_path))
    monkeypatch.setattr(canonical_worker, "canonicalise_mbp1_session", lambda **kw: _fake_result(kw["session_id"]))
    monkeypatch.setattr(sys, "argv", [
        "hmt2i_gc_corpus_canonicalise.py",
        "--session-ids", "GC-2020-02-02",
    ])  # no --canonical-research-root override -- exercises the real, default root

    real_default_store_root = canonical_worker.corpus_canonical_store_root(None)
    try:
        hmt2i_canonicalise.main()

        assert tracked_path.exists()
        snapshot = json.loads(tracked_path.read_text(encoding="utf-8"))
        assert snapshot["result"]["processed"][0]["session_id"] == "GC-2020-02-02"
        assert snapshot["result"]["processed"][0]["canonical_event_set_hash"] == "deadbeef"
    finally:
        # Restore the tracked file to its pre-test content (this test's whole point is that the
        # DEFAULT invocation legitimately writes here, so we must clean up after ourselves) and
        # remove the real, gitignored default canonical-store artefacts this run created.
        tracked_path.write_bytes(before_bytes)
        if real_default_store_root.exists():
            shutil.rmtree(real_default_store_root)
