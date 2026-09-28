#!/usr/bin/env python3
"""market_truth.acquisition.orchestration.canonical_root_orchestrator -- HMT-2 canonical dispatch.

Ports Trinity's `hmt2_canonical_root_orchestrator.py` (sealed HELM handoff bundle, 2026-09-27)
into portable, tested, externally-configured HERMES repository code.

TOPOLOGY -- corrects the anti-pattern identified 2026-09-26 and preserved (never revived) here:

    WRONG (retired `hmt2_routine_recovery_driver_v2.py`, see the obsolete-notes doc):
        systemd-run --uid=hmt-compute -> driver (as hmt-compute) -> systemd-run   (NESTED --
        requires the unprivileged hmt-compute identity to itself hold systemd-unit creation
        authority, which it correctly does not have -> polkit "Interactive authentication
        required")

    RIGHT (this module):
        root orchestrator (run directly as root, NO launcher wrapper of any kind)
            -> ONE systemd-run boundary per session, via the existing, unmodified
               `<launcher_path>` (root-owned, --uid=hmt-compute --gid=hmt, --slice=<slice_name>)
               -> canonical worker, as the canonical compute identity

This module MUST itself run directly as root (uid 0) -- enforced by an explicit
`os.geteuid() != 0` refusal at the top of `main()` -- and must NEVER be wrapped in anything that
would place IT inside `hmt2.slice` or drop its privilege; doing so would recreate the nested-
authority anti-pattern above.

PRIVILEGE MODEL -- this module contains NO reference, anywhere, to the Databento acquisition
credential's env-var name or file path (see `market_truth.acquisition.providers.databento_historical`
for that name -- deliberately not spelled out again here). The canonical code path launched from
here runs as the canonical compute identity via `<launcher_path>`, which is a completely separate
script from the acquisition launcher and never sets that variable. See
`tests/hmt2/orchestration/test_privilege_boundary.py` for the automated proof.

Session ids are taken ONLY from ledger truth (`chunk_allowlist.py`), intersected with the
operator-supplied allowlist file. Every candidate id is validated against the fixed grammar
GC-YYYY-MM-DD before use, and is passed to the launcher as a literal argv element -- never
interpolated into a shell string (`subprocess.Popen` is always called with an argv list and
`shell=False`, the default).
"""
from __future__ import annotations

import argparse
import datetime
import importlib
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from market_truth.acquisition.orchestration import chunk_allowlist
from market_truth.acquisition.orchestration.config import Hmt2OpsConfig
from market_truth.acquisition.orchestration.host_guards import (
    CausalMemorySampler,
    SustainedLowMemoryGuard,
    disk_pct,
    psi_guard_fires,
    read_cgroup_int,
    slice_is_idle,
)
from market_truth.acquisition.orchestration import exceptional_envelope as envelope_mod

_SCRIPT_NAME = os.path.basename(__file__)
CANONICALISE_SCRIPT_RELATIVE_PATH = os.path.join("research", "hmt2", "hmt2i_gc_corpus_canonicalise.py")


def utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def append_progress(path: str, rec: dict) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


def refuse_unless_root(euid: int) -> None:
    """The one, structural enforcement of "this orchestrator must run directly as root". Kept
    as a tiny, separately-testable function (injectable `euid`) rather than inline in `main()`
    so a test can prove the refusal fires for euid != 0 without needing to actually be non-root
    or actually be root."""
    if euid != 0:
        raise SystemExit(
            "REFUSING: this orchestrator must be run directly as root (uid 0), not wrapped in "
            "any launcher (that would recreate the nested-systemd-run anti-pattern)."
        )


@dataclass(frozen=True)
class LedgerModules:
    source_ledger_mod: Any
    canonical_ledger_mod: Any
    canonical_worker_mod: Any


def import_ledger_modules(cfg: Hmt2OpsConfig) -> LedgerModules:
    """Imports the already-in-Git, unmodified ledger/canonical-worker modules, using the exact
    same `sys.path` convention `research/hmt2/hmt2i_gc_corpus_canonicalise.py` itself uses
    (repo root + `research/hmt2/` inserted, then imported as top-level modules -- they are
    scripts, not a package). Never reimplements anything from these modules."""
    repo_dir = cfg.repo_dir
    research_hmt2_dir = cfg.research_hmt2_dir()
    for path in (repo_dir, research_hmt2_dir):
        if path not in sys.path:
            sys.path.insert(0, path)
    source_ledger_mod = importlib.import_module("hmt2h_gc_corpus_ledger")
    canonical_ledger_mod = importlib.import_module("hmt2i_gc_corpus_canonical_ledger")
    canonical_worker_mod = importlib.import_module("market_truth.acquisition.canonical_worker")
    return LedgerModules(
        source_ledger_mod=source_ledger_mod,
        canonical_ledger_mod=canonical_ledger_mod,
        canonical_worker_mod=canonical_worker_mod,
    )


def resolve_ledger_paths(cfg: Hmt2OpsConfig, modules: LedgerModules) -> Tuple[str, str]:
    """Resolves the acquisition and canonical ledger STATE file paths, reusing the already-in-Git
    modules' own constants/resolution functions -- never a second, hand-maintained hardcoded
    path string.

    Acquisition ledger: always `<repo_dir>/research-source/<LEDGER_STATE_RELATIVE_PATH>` --
    `research-source/` is a fixed, repo-relative convention in the already-in-Git acquisition
    script (never itself made env-configurable by this dispatch; excluded from changes). Host-
    specific native storage location is realised OUTSIDE this code, e.g. by mounting/symlinking
    `<repo_dir>/research-source` to wherever native artefacts actually live on that host.

    Canonical ledger: `canonical_worker.corpus_canonical_store_root()` resolves
    `HMT2_CANONICAL_RESEARCH_ROOT` / an explicit override / the default -- exactly the same
    resolution every other canonical code path already uses.
    """
    from pathlib import Path

    acquisition_ledger_path = os.path.join(
        cfg.repo_dir, "research-source", modules.source_ledger_mod.LEDGER_STATE_RELATIVE_PATH
    )
    canonical_store_root = modules.canonical_worker_mod.corpus_canonical_store_root(
        cfg.canonical_research_root, repo_root=Path(cfg.repo_dir)
    )
    canonical_ledger_path = os.path.join(
        str(canonical_store_root), modules.canonical_ledger_mod.CANONICAL_LEDGER_STATE_RELATIVE_PATH
    )
    return acquisition_ledger_path, canonical_ledger_path


@dataclass
class SessionRunResult:
    exit_code: Optional[int]
    timed_out: bool
    guard_fired: bool
    guard_detail: Optional[str]
    min_mem_available_kb: Optional[int]
    wall_seconds: float
    stdout_tail: str
    stderr_tail: str
    pre_kill_sample: Optional[dict]


def run_one_session_with_monitor(
    cmd: List[str],
    *,
    cwd: str,
    cfg: Hmt2OpsConfig,
    popen_factory: Callable[..., "subprocess.Popen"] = subprocess.Popen,
    sampler: Optional[CausalMemorySampler] = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> SessionRunResult:
    """Single systemd-run boundary: the caller (root, via `cmd[0] == cfg.launcher_path`) invokes
    the existing, unmodified launcher directly. No nested systemd-run, no wrapper placing this
    orchestrator inside any slice.

    The causal-sampling contract (see `host_guards.CausalMemorySampler` docstring, and the real
    incident it exists to prevent): `sampler` is constructed (if not injected) and given its
    FIRST sample BEFORE `popen_factory` is even called, and every poll tick takes a fresh sample
    and makes its guard-fire decision from THAT SAME sample -- never from a snapshot taken only
    after `proc.terminate()`.
    """
    sampler = sampler or CausalMemorySampler(slice_name=cfg.slice_name)
    pre_launch_sample = sampler.sample()  # started BEFORE the worker launches -- see docstring.

    sustained_guard = SustainedLowMemoryGuard(
        threshold_kb=cfg.in_session_min_memavailable_kb,
        sustained_seconds=cfg.sustained_low_mem_seconds,
        clock=clock,
    )

    proc = popen_factory(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    t0 = clock()

    min_mem = pre_launch_sample.mem_available_kb
    guard_fired = False
    guard_detail: Optional[str] = None
    timed_out = False
    fire_sample: Optional[dict] = None

    while True:
        ret = proc.poll()
        if ret is not None:
            break

        elapsed = clock() - t0
        if elapsed > cfg.per_session_timeout_sec:
            timed_out = True
            _terminate_then_kill(proc)
            break

        sample = sampler.sample()  # LIVE, every tick -- this is the causal reading.
        if sample.mem_available_kb is not None and (min_mem is None or sample.mem_available_kb < min_mem):
            min_mem = sample.mem_available_kb

        mem_fired = sustained_guard.observe(sample.mem_available_kb)
        psi_fired = psi_guard_fires(sample.psi_memory_full_avg10, threshold=cfg.psi_full_avg10_stall_threshold)

        if mem_fired or psi_fired:
            guard_fired = True
            fire_sample = sample.as_dict()
            details = []
            if mem_fired:
                details.append(
                    f"sustained MemAvailable < {cfg.in_session_min_memavailable_kb}kB for >= "
                    f"{cfg.sustained_low_mem_seconds}s (last={sample.mem_available_kb}kB)"
                )
            if psi_fired:
                details.append(f"genuine host PSI reclaim: full avg10={sample.psi_memory_full_avg10}")
            guard_detail = " | ".join(details)
            _terminate_then_kill(proc)
            break

        sleep_fn(cfg.poll_interval_sec)

    wall = clock() - t0
    out, err = "", ""
    try:
        out = proc.stdout.read() or ""
        err = proc.stderr.read() or ""
    except Exception:  # pragma: no cover - defensive only; real Popen streams are always readable here
        pass

    return SessionRunResult(
        exit_code=proc.returncode,
        timed_out=timed_out,
        guard_fired=guard_fired,
        guard_detail=guard_detail,
        min_mem_available_kb=min_mem,
        wall_seconds=wall,
        stdout_tail=out[-2000:],
        stderr_tail=err[-2000:],
        pre_kill_sample=fire_sample,
    )


def _terminate_then_kill(proc, *, wait_seconds: float = 15.0) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=wait_seconds)
    except subprocess.TimeoutExpired:
        proc.kill()


def build_session_command(cfg: Hmt2OpsConfig, session_id: str) -> List[str]:
    """Argv-array (never a shell string) for the ONE systemd-run boundary per session."""
    script_path = os.path.join(cfg.repo_dir, CANONICALISE_SCRIPT_RELATIVE_PATH)
    return [cfg.launcher_path, cfg.venv_python, script_path, "--session-ids", session_id]


def process_batch(
    *,
    cfg: Hmt2OpsConfig,
    validated_session_ids: List[str],
    already_complete_session_ids: List[str],
    progress_log_path: str,
    modules: LedgerModules,
    canonical_ledger_path: str,
    oracle: Optional[Dict[str, dict]] = None,
    canonical_state_complete: str = "CANONICAL_COMPLETE",
    run_session_fn: Callable[..., SessionRunResult] = run_one_session_with_monitor,
    disk_pct_fn: Callable[[str], float] = disk_pct,
) -> dict:
    """The dispatch loop itself, factored out of `main()` so it is independently testable with
    injected ledger content, an injected `run_session_fn` (never a real `systemd-run`), and no
    real filesystem dependency beyond `progress_log_path` (point it at a disposable tmp file)."""
    for sid in already_complete_session_ids:
        append_progress(progress_log_path, {"utc": utcnow(), "event": "SKIP_ALREADY_COMPLETE", "session_id": sid})

    recovered: List[str] = []
    failed: List[str] = []
    oracle_matches = 0
    lowest_memavailable_seen: Optional[int] = None

    for i, sid in enumerate(validated_session_ids, 1):
        dpct = disk_pct_fn(cfg.disk_guard_mount)
        if dpct >= cfg.disk_guard_stop_pct:
            append_progress(progress_log_path, {"utc": utcnow(), "event": "STOP_DISK_THRESHOLD", "disk_pct": dpct})
            break

        from market_truth.acquisition.orchestration.host_guards import mem_available_kb

        pre_mem = mem_available_kb()
        if pre_mem is None or pre_mem < cfg.pre_session_min_memavailable_kb:
            append_progress(
                progress_log_path,
                {
                    "utc": utcnow(),
                    "event": "PAUSE_PRE_SESSION_MEMORY_GATE",
                    "session_id": sid,
                    "pre_session_memavailable_kb": pre_mem,
                    "required_kb": cfg.pre_session_min_memavailable_kb,
                },
            )
            break

        start = utcnow()
        cmd = build_session_command(cfg, sid)
        result = run_session_fn(cmd, cwd=cfg.repo_dir, cfg=cfg)
        end = utcnow()

        if result.min_mem_available_kb is not None:
            if lowest_memavailable_seen is None or result.min_mem_available_kb < lowest_memavailable_seen:
                lowest_memavailable_seen = result.min_mem_available_kb

        canonical_ledger = load_json(canonical_ledger_path) if os.path.exists(canonical_ledger_path) else {}
        rec = canonical_ledger.get(sid, {})
        state = rec.get("state")
        event_hash = rec.get("canonical_event_set_hash")
        result_kind = rec.get("canonical_result_kind")

        if result.guard_fired:
            false_complete = state == canonical_state_complete
            append_progress(
                progress_log_path,
                {
                    "utc": end,
                    "event": "GUARD_FIRED_HOST_MEMORY",
                    "session_id": sid,
                    "guard_detail": result.guard_detail,
                    "pre_session_memavailable_kb": pre_mem,
                    "min_memavailable_kb_during": result.min_mem_available_kb,
                    "wall_seconds": round(result.wall_seconds, 2),
                    "ledger_state_after_termination": state,
                    "false_complete_detected": false_complete,
                    "pre_kill_sample": result.pre_kill_sample,
                },
            )
            break

        oracle_status = "ORACLE_NOT_CONFIGURED"
        oracle_ok = True
        if oracle is not None:
            if sid in oracle:
                oracle_status = "CHECKED"
                expected = oracle[sid]["compare_report"]["authoritative_canonical_event_set_hash"]
                if event_hash != expected:
                    oracle_ok = False
                    oracle_status = f"DRIFT expected={expected} actual={event_hash}"
                else:
                    oracle_matches += 1
            else:
                oracle_status = "NOT_IN_ORACLE"

        progress_rec = {
            "utc": end,
            "session_id": sid,
            "index": i,
            "of": len(validated_session_ids),
            "start_utc": start,
            "end_utc": end,
            "wall_seconds": round(result.wall_seconds, 2),
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "ledger_state": state,
            "canonical_event_set_hash": event_hash,
            "canonical_result_kind": result_kind,
            "oracle_status": oracle_status,
            "disk_pct": dpct,
            "pre_session_memavailable_kb": pre_mem,
            "min_memavailable_kb_during": result.min_mem_available_kb,
            "launched_by": "hmt2_canonical_root_orchestrator_v2_ported",
        }
        append_progress(progress_log_path, progress_rec)

        if result.timed_out or result.exit_code != 0 or state != canonical_state_complete:
            failed.append(sid)
            append_progress(
                progress_log_path,
                {
                    "utc": utcnow(),
                    "event": "STOP_SESSION_FAILURE",
                    "session_id": sid,
                    "worker_stdout_tail": result.stdout_tail,
                    "worker_stderr_tail": result.stderr_tail,
                },
            )
            break

        if not oracle_ok:
            failed.append(sid)
            append_progress(
                progress_log_path, {"utc": utcnow(), "event": "STOP_ORACLE_DRIFT", "session_id": sid, "detail": oracle_status}
            )
            break

        recovered.append(sid)

    summary = {
        "utc": utcnow(),
        "event": "RECOVERY_LOOP_SUMMARY",
        "attempted": len(recovered) + len(failed),
        "recovered": len(recovered),
        "failed": len(failed),
        "skipped_already_complete": len(already_complete_session_ids),
        "oracle_matches": oracle_matches,
        "lowest_memavailable_kb_seen": lowest_memavailable_seen,
        "recovered_sessions": recovered,
        "failed_sessions": failed,
    }
    append_progress(progress_log_path, summary)
    return summary


def _load_oracle(cfg: Hmt2OpsConfig) -> Optional[Dict[str, dict]]:
    if not cfg.oracle_path:
        return None
    if not os.path.exists(cfg.oracle_path):
        return None
    doc = load_json(cfg.oracle_path)
    return doc.get("completed", doc)


def main(argv: Optional[List[str]] = None) -> int:
    refuse_unless_root(os.geteuid())

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--allowlist", required=True, help="Path to a JSON file: {\"session_ids\": [...]}")
    ap.add_argument("--progress-log", required=True)
    args = ap.parse_args(argv)

    cfg = Hmt2OpsConfig.from_env()
    modules = import_ledger_modules(cfg)
    acquisition_ledger_path, canonical_ledger_path = resolve_ledger_paths(cfg, modules)

    acquisition_ledger = load_json(acquisition_ledger_path)
    canonical_ledger = load_json(canonical_ledger_path) if os.path.exists(canonical_ledger_path) else {}

    requested = chunk_allowlist.load_requested_session_ids(args.allowlist)
    try:
        validated, already_complete = chunk_allowlist.validate_and_partition_allowlist(
            requested,
            canonical_ledger=canonical_ledger,
            acquisition_ledger=acquisition_ledger,
            source_state_complete=modules.source_ledger_mod.STATE_COMPLETE,
            canonical_state_complete=modules.canonical_ledger_mod.STATE_CANONICAL_COMPLETE,
        )
    except chunk_allowlist.AllowlistError as exc:
        print(f"REFUSING allowlist: {exc}", file=sys.stderr)
        return 2

    if len(validated) > cfg.max_canonical_chunk_sessions:
        print(
            f"REFUSING: allowlist requests {len(validated)} sessions, exceeding the bounded-chunk "
            f"ceiling of {cfg.max_canonical_chunk_sessions} (see config.ENV_MAX_CANONICAL_CHUNK_SESSIONS)",
            file=sys.stderr,
        )
        return 2

    oracle = _load_oracle(cfg)

    summary = process_batch(
        cfg=cfg,
        validated_session_ids=validated,
        already_complete_session_ids=already_complete,
        progress_log_path=args.progress_log,
        modules=modules,
        canonical_ledger_path=canonical_ledger_path,
        oracle=oracle,
        canonical_state_complete=modules.canonical_ledger_mod.STATE_CANONICAL_COMPLETE,
    )
    print(json.dumps(summary))
    return 0 if not summary["failed_sessions"] else 1


if __name__ == "__main__":
    sys.exit(main())
