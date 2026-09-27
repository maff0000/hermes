"""Canonical root orchestrator: root-only refusal, single-argv-array launch command
construction, ledger-path resolution via the already-in-Git modules' own resolution functions
(never a second hand-maintained path string), and the dispatch loop's guard-fire /
false-completion-prevention / resumability / disk-stop / pre-session-gate / oracle-drift
behaviour -- all driven through injected fakes, never a real systemd-run or real /proc read."""
import json
import os

import pytest

from market_truth.acquisition.orchestration import canonical_root_orchestrator as orch
from market_truth.acquisition.orchestration.config import Hmt2OpsConfig


def _cfg(tmp_path, **overrides):
    env = {
        "HMT2_OPS_REPO_DIR": str(tmp_path / "repo"),
        "HMT2_OPS_LAUNCHER_PATH": "/usr/local/sbin/hmt2-run.sh",
        "HMT2_OPS_ACQUIRE_LAUNCHER_PATH": "/usr/local/sbin/hmt2-acquire-run.sh",
        "HMT2_OPS_VENV_PYTHON": "/venv/bin/python3",
        "HMT2_OPS_DISK_GUARD_MOUNT": str(tmp_path),
        "HMT2_OPS_SCRATCH_DIR": str(tmp_path / "scratch"),
        "HMT2_OPS_EVIDENCE_ROOT": str(tmp_path / "evidence"),
    }
    env.update(overrides)
    return Hmt2OpsConfig.from_env(env)


def test_refuse_unless_root_raises_systemexit_for_non_root():
    with pytest.raises(SystemExit, match="REFUSING"):
        orch.refuse_unless_root(1000)


def test_refuse_unless_root_is_silent_for_root():
    orch.refuse_unless_root(0)  # must not raise


def test_build_session_command_is_an_argv_array_never_a_shell_string(tmp_path):
    cfg = _cfg(tmp_path)
    cmd = orch.build_session_command(cfg, "GC-2020-01-01")
    assert isinstance(cmd, list)
    assert cmd[0] == cfg.launcher_path
    assert cmd[1] == cfg.venv_python
    assert cmd[2] == os.path.join(cfg.repo_dir, "research", "hmt2", "hmt2i_gc_corpus_canonicalise.py")
    assert cmd[3:] == ["--session-ids", "GC-2020-01-01"]
    # Literal argv element -- never string-interpolated (no shell metacharacter could ever be
    # interpreted, since there is no shell in the picture at all).
    assert all(isinstance(part, str) for part in cmd)


class _FakeSourceLedgerMod:
    LEDGER_STATE_RELATIVE_PATH = "hmt2-gc-mbp1-v1/manifest/gc_corpus_acquisition_ledger.json"
    STATE_COMPLETE = "COMPLETE"


class _FakeCanonicalLedgerMod:
    CANONICAL_LEDGER_STATE_RELATIVE_PATH = "manifest/gc_corpus_canonical_ledger.json"
    STATE_CANONICAL_COMPLETE = "CANONICAL_COMPLETE"


class _FakeCanonicalWorkerMod:
    def __init__(self, root):
        self._root = root

    def corpus_canonical_store_root(self, explicit, *, repo_root=None):
        from pathlib import Path

        base = Path(explicit) if explicit else Path(self._root)
        return base / "hmt2-gc-mbp1-v1"


def test_resolve_ledger_paths_reuses_the_already_in_git_modules_own_resolution(tmp_path):
    cfg = _cfg(tmp_path, HMT2_CANONICAL_RESEARCH_ROOT=str(tmp_path / "canonical-root"))
    modules = orch.LedgerModules(
        source_ledger_mod=_FakeSourceLedgerMod(),
        canonical_ledger_mod=_FakeCanonicalLedgerMod(),
        canonical_worker_mod=_FakeCanonicalWorkerMod(str(tmp_path / "canonical-root")),
    )
    acquisition_path, canonical_path = orch.resolve_ledger_paths(cfg, modules)
    assert acquisition_path == os.path.join(
        cfg.repo_dir, "research-source", "hmt2-gc-mbp1-v1/manifest/gc_corpus_acquisition_ledger.json"
    )
    assert canonical_path == str(
        tmp_path / "canonical-root" / "hmt2-gc-mbp1-v1" / "manifest" / "gc_corpus_canonical_ledger.json"
    )


def _make_run_session_fn(*, canonical_ledger_path, outcomes):
    """`outcomes` maps session_id -> a dict describing what should happen: the ledger state to
    write for that session (simulating the real worker's own ledger update) plus the
    SessionRunResult fields to return."""
    calls = []

    def run_session_fn(cmd, *, cwd, cfg):
        sid = cmd[-1]
        calls.append(sid)
        outcome = outcomes[sid]
        if os.path.exists(canonical_ledger_path):
            with open(canonical_ledger_path) as fh:
                ledger = json.load(fh)
        else:
            ledger = {}
        if outcome.get("ledger_state") is not None:
            ledger[sid] = {"state": outcome["ledger_state"], "canonical_event_set_hash": outcome.get("event_hash")}
            with open(canonical_ledger_path, "w") as fh:
                json.dump(ledger, fh)
        return orch.SessionRunResult(
            exit_code=outcome.get("exit_code", 0),
            timed_out=outcome.get("timed_out", False),
            guard_fired=outcome.get("guard_fired", False),
            guard_detail=outcome.get("guard_detail"),
            min_mem_available_kb=outcome.get("min_mem_available_kb", 50 * 1024 * 1024),
            wall_seconds=outcome.get("wall_seconds", 1.0),
            stdout_tail="", stderr_tail="",
            pre_kill_sample=outcome.get("pre_kill_sample"),
        )

    return run_session_fn, calls


def _progress_events(progress_log_path):
    with open(progress_log_path) as fh:
        return [json.loads(line) for line in fh if line.strip()]


def test_process_batch_happy_path_two_sessions(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    canonical_ledger_path = str(tmp_path / "canonical_ledger.json")
    progress_log_path = str(tmp_path / "progress.jsonl")

    monkeypatch.setattr(
        "market_truth.acquisition.orchestration.host_guards.mem_available_kb",
        lambda: 60 * 1024 * 1024,
    )

    run_session_fn, calls = _make_run_session_fn(
        canonical_ledger_path=canonical_ledger_path,
        outcomes={
            "GC-2020-01-01": {"ledger_state": "CANONICAL_COMPLETE"},
            "GC-2020-01-02": {"ledger_state": "CANONICAL_COMPLETE"},
        },
    )

    modules = orch.LedgerModules(source_ledger_mod=_FakeSourceLedgerMod(), canonical_ledger_mod=_FakeCanonicalLedgerMod(), canonical_worker_mod=None)
    summary = orch.process_batch(
        cfg=cfg, validated_session_ids=["GC-2020-01-01", "GC-2020-01-02"], already_complete_session_ids=[],
        progress_log_path=progress_log_path, modules=modules, canonical_ledger_path=canonical_ledger_path,
        oracle=None, run_session_fn=run_session_fn, disk_pct_fn=lambda mount: 10.0,
    )
    assert calls == ["GC-2020-01-01", "GC-2020-01-02"]
    assert summary["recovered"] == 2
    assert summary["failed"] == 0
    assert summary["recovered_sessions"] == ["GC-2020-01-01", "GC-2020-01-02"]


def test_process_batch_stops_on_first_failure_never_proceeds_to_next_session(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    canonical_ledger_path = str(tmp_path / "canonical_ledger.json")
    progress_log_path = str(tmp_path / "progress.jsonl")
    monkeypatch.setattr(
        "market_truth.acquisition.orchestration.host_guards.mem_available_kb", lambda: 60 * 1024 * 1024
    )
    run_session_fn, calls = _make_run_session_fn(
        canonical_ledger_path=canonical_ledger_path,
        outcomes={
            "GC-2020-01-01": {"exit_code": 1, "ledger_state": "CANONICAL_FAILED"},
            "GC-2020-01-02": {"ledger_state": "CANONICAL_COMPLETE"},
        },
    )
    modules = orch.LedgerModules(source_ledger_mod=_FakeSourceLedgerMod(), canonical_ledger_mod=_FakeCanonicalLedgerMod(), canonical_worker_mod=None)
    summary = orch.process_batch(
        cfg=cfg, validated_session_ids=["GC-2020-01-01", "GC-2020-01-02"], already_complete_session_ids=[],
        progress_log_path=progress_log_path, modules=modules, canonical_ledger_path=canonical_ledger_path,
        oracle=None, run_session_fn=run_session_fn, disk_pct_fn=lambda mount: 10.0,
    )
    assert calls == ["GC-2020-01-01"]  # second session never attempted
    assert summary["failed"] == 1
    assert summary["recovered"] == 0
    events = _progress_events(progress_log_path)
    assert any(e.get("event") == "STOP_SESSION_FAILURE" for e in events)


def test_process_batch_guard_fire_pauses_batch_and_detects_false_completion(tmp_path, monkeypatch):
    """Guard-fire path must verify the canonical ledger did NOT record a false CANONICAL_COMPLETE
    for the terminated session, and must pause (never auto-resume) -- proven here by the second
    session never being attempted."""
    cfg = _cfg(tmp_path)
    canonical_ledger_path = str(tmp_path / "canonical_ledger.json")
    progress_log_path = str(tmp_path / "progress.jsonl")
    monkeypatch.setattr(
        "market_truth.acquisition.orchestration.host_guards.mem_available_kb", lambda: 60 * 1024 * 1024
    )
    # Simulate the worst case: the guard fired AND the ledger somehow still shows COMPLETE (a
    # false completion) -- the orchestrator must surface false_complete_detected=True, not hide it.
    run_session_fn, calls = _make_run_session_fn(
        canonical_ledger_path=canonical_ledger_path,
        outcomes={
            "GC-2020-01-01": {"guard_fired": True, "guard_detail": "sustained low memory", "ledger_state": "CANONICAL_COMPLETE"},
            "GC-2020-01-02": {"ledger_state": "CANONICAL_COMPLETE"},
        },
    )
    modules = orch.LedgerModules(source_ledger_mod=_FakeSourceLedgerMod(), canonical_ledger_mod=_FakeCanonicalLedgerMod(), canonical_worker_mod=None)
    summary = orch.process_batch(
        cfg=cfg, validated_session_ids=["GC-2020-01-01", "GC-2020-01-02"], already_complete_session_ids=[],
        progress_log_path=progress_log_path, modules=modules, canonical_ledger_path=canonical_ledger_path,
        oracle=None, run_session_fn=run_session_fn, disk_pct_fn=lambda mount: 10.0,
    )
    assert calls == ["GC-2020-01-01"]  # batch paused, never auto-resumed onto session 2
    assert summary["recovered"] == 0
    assert summary["failed"] == 0  # guard-fire is neither a recorded success nor a failure entry
    events = _progress_events(progress_log_path)
    fired = [e for e in events if e.get("event") == "GUARD_FIRED_HOST_MEMORY"]
    assert len(fired) == 1
    assert fired[0]["false_complete_detected"] is True


def test_process_batch_stops_on_disk_threshold_before_any_session(tmp_path):
    cfg = _cfg(tmp_path)
    canonical_ledger_path = str(tmp_path / "canonical_ledger.json")
    progress_log_path = str(tmp_path / "progress.jsonl")
    run_session_fn, calls = _make_run_session_fn(canonical_ledger_path=canonical_ledger_path, outcomes={})
    modules = orch.LedgerModules(source_ledger_mod=_FakeSourceLedgerMod(), canonical_ledger_mod=_FakeCanonicalLedgerMod(), canonical_worker_mod=None)
    summary = orch.process_batch(
        cfg=cfg, validated_session_ids=["GC-2020-01-01"], already_complete_session_ids=[],
        progress_log_path=progress_log_path, modules=modules, canonical_ledger_path=canonical_ledger_path,
        oracle=None, run_session_fn=run_session_fn, disk_pct_fn=lambda mount: 95.0,
    )
    assert calls == []
    assert summary["attempted"] == 0
    events = _progress_events(progress_log_path)
    assert any(e.get("event") == "STOP_DISK_THRESHOLD" for e in events)


def test_process_batch_pauses_on_pre_session_memory_gate(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    canonical_ledger_path = str(tmp_path / "canonical_ledger.json")
    progress_log_path = str(tmp_path / "progress.jsonl")
    monkeypatch.setattr(
        "market_truth.acquisition.orchestration.host_guards.mem_available_kb", lambda: 1024  # far below floor
    )
    run_session_fn, calls = _make_run_session_fn(canonical_ledger_path=canonical_ledger_path, outcomes={})
    modules = orch.LedgerModules(source_ledger_mod=_FakeSourceLedgerMod(), canonical_ledger_mod=_FakeCanonicalLedgerMod(), canonical_worker_mod=None)
    summary = orch.process_batch(
        cfg=cfg, validated_session_ids=["GC-2020-01-01"], already_complete_session_ids=[],
        progress_log_path=progress_log_path, modules=modules, canonical_ledger_path=canonical_ledger_path,
        oracle=None, run_session_fn=run_session_fn, disk_pct_fn=lambda mount: 10.0,
    )
    assert calls == []
    events = _progress_events(progress_log_path)
    assert any(e.get("event") == "PAUSE_PRE_SESSION_MEMORY_GATE" for e in events)


def test_process_batch_oracle_drift_stops_batch(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    canonical_ledger_path = str(tmp_path / "canonical_ledger.json")
    progress_log_path = str(tmp_path / "progress.jsonl")
    monkeypatch.setattr(
        "market_truth.acquisition.orchestration.host_guards.mem_available_kb", lambda: 60 * 1024 * 1024
    )
    run_session_fn, calls = _make_run_session_fn(
        canonical_ledger_path=canonical_ledger_path,
        outcomes={"GC-2020-01-01": {"ledger_state": "CANONICAL_COMPLETE", "event_hash": "actual-hash"}},
    )
    oracle = {"GC-2020-01-01": {"compare_report": {"authoritative_canonical_event_set_hash": "expected-hash"}}}
    modules = orch.LedgerModules(source_ledger_mod=_FakeSourceLedgerMod(), canonical_ledger_mod=_FakeCanonicalLedgerMod(), canonical_worker_mod=None)
    summary = orch.process_batch(
        cfg=cfg, validated_session_ids=["GC-2020-01-01"], already_complete_session_ids=[],
        progress_log_path=progress_log_path, modules=modules, canonical_ledger_path=canonical_ledger_path,
        oracle=oracle, run_session_fn=run_session_fn, disk_pct_fn=lambda mount: 10.0,
    )
    assert calls == ["GC-2020-01-01"]
    assert summary["failed"] == 1
    events = _progress_events(progress_log_path)
    assert any(e.get("event") == "STOP_ORACLE_DRIFT" for e in events)


def test_process_batch_records_skip_already_complete(tmp_path):
    cfg = _cfg(tmp_path)
    canonical_ledger_path = str(tmp_path / "canonical_ledger.json")
    progress_log_path = str(tmp_path / "progress.jsonl")
    run_session_fn, calls = _make_run_session_fn(canonical_ledger_path=canonical_ledger_path, outcomes={})
    modules = orch.LedgerModules(source_ledger_mod=_FakeSourceLedgerMod(), canonical_ledger_mod=_FakeCanonicalLedgerMod(), canonical_worker_mod=None)
    summary = orch.process_batch(
        cfg=cfg, validated_session_ids=[], already_complete_session_ids=["GC-2019-03-22"],
        progress_log_path=progress_log_path, modules=modules, canonical_ledger_path=canonical_ledger_path,
        oracle=None, run_session_fn=run_session_fn, disk_pct_fn=lambda mount: 10.0,
    )
    assert calls == []
    assert summary["skipped_already_complete"] == 1
    events = _progress_events(progress_log_path)
    assert any(e.get("event") == "SKIP_ALREADY_COMPLETE" and e.get("session_id") == "GC-2019-03-22" for e in events)
