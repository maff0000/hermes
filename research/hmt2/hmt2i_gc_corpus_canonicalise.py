#!/usr/bin/env python3
"""HMT-2 — governed GC MBP-1 CORPUS canonicalisation driver (canonicalisation checkpoint).

Builds the durable canonical partitions the bulk acquisition path (`hmt2h_gc_corpus_acquire.py`,
102 of 448 sessions already durably acquired as raw native source) never actually produced: that
driver retains native bytes but never runs them through HMT-1's canonicaliser into durable
canonical partitions — the ONLY place that ever happened before this checkpoint was the two pilot
sessions, and only into a DISPOSABLE `/tmp` tree (`hmt2f_mbp1_pilot_replay.py`). This script is
the durable equivalent, session-scoped, crash-safe, and lineage/quality-recorded — see
`market_truth.acquisition.canonical_worker` for the actual pipeline and
`hmt2i_gc_corpus_canonical_ledger.py` for the dedicated canonical-processing ledger this script
drives.

SCOPE FOR THIS DISPATCH — BINDING
------------------------------------------------------------------------------------------------
This dispatch is gated on ONE thing: proving the durable pipeline reproduces the ALREADY-GOVERNED
pilot result EXACTLY (`run_pilot_reproduction_check()` below). There is NO default that processes
"everything pending" — `--session-ids` is a MANDATORY, explicit allowlist, precisely so this
script can never accidentally widen its own scope to the corpus's other 100 already-acquired
sessions before that gate has passed. `main()`'s own `--session-ids` argument has no default
value at all; a caller must always say exactly which sessions to canonicalise in a given run.

Explicit-invocation only (never auto-triggered by any other code path):

    /tmp/hmt2-work-venv/bin/python3 research/hmt2/hmt2i_gc_corpus_canonicalise.py \\
        --session-ids GC-2019-03-22 GC-2019-03-29

    /tmp/hmt2-work-venv/bin/python3 research/hmt2/hmt2i_gc_corpus_canonicalise.py \\
        --pilot-reproduction-check

FULL-CORPUS V2 STORAGE-LAYOUT REBUILD (`--force-rebuild-v2`, additive) — the pilot-reproduction
gate above has long since passed and all 122 currently-acquired sessions are `CANONICAL_COMPLETE`
under the pre-fix, unnamespaced `canonical/` storage layout. `canonical_worker.py`'s namespace-
collision fix (`CANONICAL_STORAGE_LAYOUT_VERSION = "hmt2-canonical-storage-layout-v2"`) means every
session's PHYSICAL canonical-partition path changed, even sessions that were never a collision
victim — the architect's own ruling is that ALL 122 must be uniformly rebuilt onto `canonical-v2/`
from their retained native bytes, one at a time, correctness over elapsed time
(`process_sessions(..., force_rebuild_v2=True)`).

`force_rebuild_v2=True` changes ONE thing about the existing loop: a session already
`CANONICAL_COMPLETE` is no longer short-circuited into `verify_existing_completion()`'s
verify-and-reuse path (that path only ever re-verifies the EXISTING physical artefact — it can
never rebuild one under a new storage layout). Instead:

  - if the ledger row already carries `canonical_storage_layout_version ==
    canonical_worker.CANONICAL_STORAGE_LAYOUT_VERSION` (this exact session was already migrated —
    e.g. the namespace-collision fix's own bounded proof already durably reprocessed 6 sessions,
    including updating THEIR ledger rows), it is skipped as already-done (`already_v2=True` in
    the per-session `processed` entry) — this is what makes a follow-up dispatch resumable without
    redoing verified work;
  - otherwise the session's PRE-rebuild `canonical_event_set_hash` (whatever the ledger currently
    records — this is the "old v1 value" the dispatch's own regression check compares against) is
    captured BEFORE reprocessing, `canonical_worker.canonicalise_mbp1_session()` is called fresh
    (real reprocessing from retained native bytes, into the NEW `canonical-v2/session_id=<...>/`
    layout — never a copy of old Parquet files), and the ledger row is updated with the new
    result PLUS `canonical_storage_layout_version` (now always set on every successful outcome,
    force-rebuild or not) and two new, additive-only diagnostic fields: `pre_rebuild_v1_event_set_
    hash` and `v2_rebuild_matches_v1_hash` (`True` only when a pre-rebuild hash existed and
    matched exactly — the critical regression check for the 103 sessions with no known storage
    defect; a session with no pre-rebuild hash at all, e.g. a first-ever run, records `None`/
    `False` rather than a false match).

This flag changes NOTHING about `--pilot-reproduction-check` (that mode's own two pilot sessions
are already v2, already skipped as already-done under this same logic if it were ever combined,
though the two modes are never invoked together in practice).
"""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime as _dt
import functools
import json
import os
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_THIS_DIR, "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

import hmt2h_gc_corpus_ledger as source_ledger_mod  # noqa: E402
import hmt2i_gc_corpus_canonical_ledger as canonical_ledger_mod  # noqa: E402
from market_truth.acquisition import canonical_worker  # noqa: E402

MAPPING_TABLE_PATH = os.path.join(_THIS_DIR, "gc-contract-mapping-table-v2.json")
CORPUS_MANIFEST_REF = "research/hmt2/corpus-selection-manifest-v2.json"
PROVIDER_DEFINITION_REF = "research/hmt2/gc-contract-mapping-table-v2.json"
RESEARCH_SOURCE_ROOT = os.path.abspath(os.path.join(_THIS_DIR, "..", "..", "research-source"))
SOURCE_LEDGER_STATE_PATH = os.path.join(RESEARCH_SOURCE_ROOT, source_ledger_mod.LEDGER_STATE_RELATIVE_PATH)

PILOT_SESSION_IDS = ["GC-2019-03-22", "GC-2019-03-29"]
EXPECTED_PILOT_CANONICAL_EVENT_SET_HASH = "ffe0119a18c2b368fb6820a2c101fa889644cc796337d0edd64029d666acc60a"

SNAPSHOT_OUTPUT_PATH = os.path.join(_THIS_DIR, "hmt2i-gc-corpus-canonical-ledger-snapshot-v1.json")
PILOT_REPRODUCTION_OUTPUT_PATH = os.path.join(_THIS_DIR, "hmt2i-pilot-canonicalisation-reproduction-v1.json")


def _utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def resolve_snapshot_output_path(canonical_research_root_override: str | None = None) -> str:
    """Where THIS invocation's corpus-progress snapshot must be written.

    Bug fixed here: `write_snapshot()` used to write to the hardcoded, git-tracked
    `SNAPSHOT_OUTPUT_PATH` UNCONDITIONALLY, no matter whether the caller pointed the actual
    canonical output somewhere else entirely via `--canonical-research-root` /
    `HMT2_CANONICAL_RESEARCH_ROOT` (e.g. a disposable scratch directory used for testing) — so a
    scratch invocation of this driver could silently clobber the authoritative corpus-progress
    record checked into the real, shared worktree. This is a real, already-observed incident
    (caught and manually reverted by another engineer earlier).

    Invariant enforced here: the tracked, authoritative `SNAPSHOT_OUTPUT_PATH` is used ONLY when
    this invocation resolves to the real, default canonical research root — i.e. no explicit
    `--canonical-research-root` override AND no `HMT2_CANONICAL_RESEARCH_ROOT` environment
    override diverting the run elsewhere. Any invocation whose canonical research root resolves
    somewhere else — by either mechanism, there is no hidden code-level toggle here, just "was an
    alternate root ever selected for this run" — writes its snapshot alongside that resolved
    run's own canonical corpus store instead (`canonical_worker.corpus_canonical_store_root()`,
    the exact same store-root value every other code path in that run already uses), so it can
    never touch the tracked file at all.
    """
    override_selected = canonical_research_root_override is not None or bool(
        os.environ.get(canonical_worker.CANONICAL_RESEARCH_ROOT_ENV_VAR)
    )
    if not override_selected:
        return SNAPSHOT_OUTPUT_PATH
    canonical_store_root = canonical_worker.corpus_canonical_store_root(canonical_research_root_override)
    return str(canonical_store_root / os.path.basename(SNAPSHOT_OUTPUT_PATH))


def canonical_ledger_state_path(canonical_store_root) -> str:
    return os.path.join(str(canonical_store_root), canonical_ledger_mod.CANONICAL_LEDGER_STATE_RELATIVE_PATH)


def build_or_load_canonical_ledger(*, canonical_store_root, acquisition_ledger: dict) -> dict:
    state_path = canonical_ledger_state_path(canonical_store_root)
    existing = canonical_ledger_mod.load_canonical_ledger(state_path)
    if existing:
        added = canonical_ledger_mod.merge_new_source_complete_sessions(
            canonical_ledger=existing, acquisition_ledger=acquisition_ledger,
        )
        if added:
            canonical_ledger_mod.save_canonical_ledger_atomic(state_path, existing)
        return existing

    new_ledger = canonical_ledger_mod.build_initial_canonical_ledger(acquisition_ledger=acquisition_ledger)
    canonical_ledger_mod.save_canonical_ledger_atomic(state_path, new_ledger)
    return new_ledger


def process_sessions(
    *,
    canonical_ledger: dict,
    acquisition_ledger: dict,
    session_ids,
    canonical_store_root,
    mapping_table_path: str,
    research_source_root: str,
    ledger_state_path: str,
    force_rebuild_v2: bool = False,
) -> dict:
    """The pure(ish) sequential orchestration loop, with every dependency injected — mirrors
    `hmt2h_gc_corpus_acquire.process_planned_sessions()`'s own testing discipline exactly. Only
    ever touches the sessions named in `session_ids` (the mandatory, explicit allowlist) —
    NEVER "every CANONICAL_PENDING row".

    `force_rebuild_v2=False` (default): completely unchanged original behaviour — a
    `CANONICAL_COMPLETE` session is verified-and-reused via `verify_existing_completion()`, never
    blindly redone.

    `force_rebuild_v2=True` (governed full-corpus v2 storage-layout rebuild — see module
    docstring): a `CANONICAL_COMPLETE` session already migrated to the current
    `canonical_worker.CANONICAL_STORAGE_LAYOUT_VERSION` (checked via the ledger row's own
    `canonical_storage_layout_version` field — never re-derived, so a follow-up dispatch can
    resume without re-touching already-verified work) is skipped as already-done; every other
    `CANONICAL_COMPLETE` session is genuinely reprocessed from its retained native bytes, and its
    ledger row's PRE-rebuild `canonical_event_set_hash` is captured before being overwritten, so
    the regression check (`v2_rebuild_matches_v1_hash`) can be recorded on the row."""
    processed = []
    stopped_reason = None

    for session_id in session_ids:
        entry = canonical_ledger.get(session_id)
        if entry is None:
            raise canonical_ledger_mod.CanonicalLedgerError(
                f"session {session_id!r} has no canonical-ledger row — it is not acquisition-"
                f"SOURCE-COMPLETE yet (or the ledger has not been built/refreshed)"
            )
        acquisition_entry = acquisition_ledger.get(session_id)
        if acquisition_entry is None or acquisition_entry.get("state") != source_ledger_mod.STATE_COMPLETE:
            raise canonical_ledger_mod.CanonicalLedgerError(
                f"session {session_id!r}: acquisition ledger no longer reports SOURCE COMPLETE "
                f"— refusing to canonicalise a session whose native-source completeness cannot "
                f"be reconfirmed"
            )
        artefact = acquisition_entry["artefact"]
        native_relative = artefact["object_relative_path"]
        native_full_path = os.path.join(research_source_root, native_relative)
        expected_native_sha256 = artefact["sha256"]

        already_complete = entry["state"] == canonical_ledger_mod.STATE_CANONICAL_COMPLETE
        pre_rebuild_event_set_hash = None

        if already_complete and not force_rebuild_v2:
            try:
                canonical_worker.verify_existing_completion(
                    canonical_store_root=canonical_store_root, session_id=session_id,
                    native_artefact_path=native_full_path, expected_native_sha256=expected_native_sha256,
                )
            except canonical_worker.VerificationFailedError as exc:
                entry["state"] = canonical_ledger_mod.STATE_CANONICAL_FAILED
                entry["failure_reason"] = f"re-verification of claimed-complete session failed: {exc}"
                entry["canonical_updated_utc"] = _utc_now_iso()
                canonical_ledger_mod.save_canonical_ledger_atomic(ledger_state_path, canonical_ledger)
                stopped_reason = (
                    f"AMBIGUOUS: claimed-complete session {session_id!r} failed re-verification "
                    f"— marked CANONICAL_FAILED, batch stopped (no auto-retry): {exc}"
                )
                break
            processed.append({"session_id": session_id, "reused_existing_canonical_result": True})
            continue

        if already_complete and force_rebuild_v2:
            if entry.get("canonical_storage_layout_version") == canonical_worker.CANONICAL_STORAGE_LAYOUT_VERSION:
                # Already migrated (this exact session) — resumability: never re-touch verified
                # work a prior dispatch already completed.
                processed.append(
                    {"session_id": session_id, "reused_existing_canonical_result": True, "already_v2": True}
                )
                continue
            # Not yet migrated — capture the PRE-rebuild value this row currently records (the
            # "old v1 value" the regression check below compares the fresh rebuild against)
            # before it is overwritten by the real reprocessing result.
            pre_rebuild_event_set_hash = entry.get("canonical_event_set_hash")

        try:
            result = canonical_worker.canonicalise_mbp1_session(
                session_id=session_id,
                native_artefact_path=native_full_path,
                native_artefact_relative_path=native_relative,
                expected_native_sha256=expected_native_sha256,
                mapping_table_path=mapping_table_path,
                canonical_store_root=canonical_store_root,
                provider_request_identity=acquisition_entry["request_identity"],
                provider_definition_ref=PROVIDER_DEFINITION_REF,
                corpus_manifest_ref=CORPUS_MANIFEST_REF,
                acquisition_epoch=acquisition_entry["request_identity"],
            )
        except Exception as exc:  # noqa: BLE001 - deliberate: any ambiguous failure stops the batch
            entry["state"] = canonical_ledger_mod.STATE_CANONICAL_FAILED
            entry["failure_reason"] = f"{type(exc).__name__}: {exc}"
            entry["canonical_updated_utc"] = _utc_now_iso()
            canonical_ledger_mod.save_canonical_ledger_atomic(ledger_state_path, canonical_ledger)
            stopped_reason = (
                f"AMBIGUOUS FAILURE canonicalising {session_id!r} — marked CANONICAL_FAILED, "
                f"batch stopped (no auto-retry): {type(exc).__name__}: {exc}"
            )
            break

        entry["state"] = canonical_ledger_mod.STATE_CANONICAL_COMPLETE
        entry["lineage_record_relative_path"] = result.lineage_record_relative_path
        entry["canonical_event_set_hash"] = result.canonical_event_set_hash
        entry["canonical_partition_relative_paths"] = list(result.partition_relative_paths)
        entry["evidence_manifest_relative_path"] = result.evidence_manifest_relative_path
        entry["quality_record_relative_path"] = result.quality_record_relative_path
        entry["canonical_updated_utc"] = _utc_now_iso()
        # Valid-empty architecture ruling — result-kind metadata, NOT a new ledger state (both
        # kinds are, and remain, CANONICAL_COMPLETE above).
        entry["canonical_result_kind"] = result.canonical_result_kind
        entry["empty_reason"] = result.empty_reason
        # Storage-layout defect remediation — every session `canonicalise_mbp1_session()`
        # successfully (re)processes from this checkpoint onward is, by construction, v2
        # (`canonical_worker.py` always promotes into the session-scoped `canonical-v2/` tree
        # now); recorded unconditionally, not just under `force_rebuild_v2`, so a session
        # processed for the first time going forward is never ambiguously "v1 or v2".
        entry["canonical_storage_layout_version"] = canonical_worker.CANONICAL_STORAGE_LAYOUT_VERSION
        processed_entry = {
            "session_id": session_id,
            "reused_existing_canonical_result": False,
            "canonical_event_set_hash": result.canonical_event_set_hash,
            "source_record_count": result.source_record_count,
            "canonical_event_counts_by_family": dict(result.canonical_event_counts_by_family),
            "quality_summary": dict(result.quality_summary),
            "canonical_result_kind": result.canonical_result_kind,
            "empty_reason": result.empty_reason,
        }
        if force_rebuild_v2 and already_complete:
            # The critical regression check (WO-binding for the full-corpus v2 rebuild): a
            # session with a genuine pre-rebuild value MUST reproduce it exactly, since
            # `canonical_event_set_hash` is a hash over LOGICAL canonical events, never over
            # physical storage bytes — the namespace-collision defect only ever touched the
            # latter. `None` (no pre-rebuild value at all, e.g. a row that somehow lacked one)
            # is recorded honestly as a non-match, never silently treated as a pass.
            matches = pre_rebuild_event_set_hash is not None and pre_rebuild_event_set_hash == result.canonical_event_set_hash
            entry["pre_rebuild_v1_event_set_hash"] = pre_rebuild_event_set_hash
            entry["v2_rebuild_matches_v1_hash"] = matches
            processed_entry["pre_rebuild_v1_event_set_hash"] = pre_rebuild_event_set_hash
            processed_entry["v2_rebuild_matches_v1_hash"] = matches
        canonical_ledger_mod.save_canonical_ledger_atomic(ledger_state_path, canonical_ledger)
        processed.append(processed_entry)

    return {
        "processed": processed,
        "stopped_reason": stopped_reason,
        "canonical_ledger_summary": canonical_ledger_mod.summarize_canonical(canonical_ledger),
    }


# ==================================================================================================
# BOUNDED PROCESS-LEVEL CONCURRENCY AT THE SESSION BOUNDARY (additive, orchestration-only; see the
# dispatch's final report for the full design rationale/proof). Nothing below this point ever
# modifies `process_sessions()` above -- it remains the exact, byte-for-byte unmodified SERIAL
# baseline every determinism proof compares against -- or any HMT-1 semantic module (contracts /
# identity / canonicaliser / partition / evidence / replay -- all untouched).
#
# Design, one paragraph: a single coordinator (this process, never a worker) owns every ledger
# read and write and all session assignment. Each WORKER process runs exactly one HMT-2 session at
# a time, end to end, through the SAME unmodified `canonical_worker.canonicalise_mbp1_session()` /
# `verify_existing_completion()` the serial path above already calls -- no HMT-1 semantics are ever
# touched inside a worker, and no two workers ever address the same physical output path (every
# destination -- canonical partitions, evidence, lineage, quality, staging -- is already namespaced
# by `session_id` at the `canonical_worker.py` level; running two DIFFERENT sessions in two OS
# processes only adds a second, complete layer of isolation -- separate address spaces -- on top of
# that). Sessions are grouped into WAVES of at most `max_workers` sessions; a wave is dispatched,
# the coordinator waits for EVERY task in that wave to reach its own natural terminal point
# (success or exception -- a worker is never killed or cancelled mid-write), applies each session's
# ledger mutation itself, one at a time, in a FIXED order (the wave's own scheduling order, never
# physical completion order -- this is what makes the final ledger state provably independent of
# which worker happens to finish first), and only THEN considers dispatching the next wave. If any
# task in a wave produced an ambiguous failure, no further wave is ever dispatched (fail-closed, no
# auto-retry) -- exactly `process_sessions()`'s own stop-the-batch discipline, generalised from one
# session to a whole wave of already-in-flight sessions.
# ==================================================================================================

DEFAULT_CONCURRENT_MAX_WORKERS = 2
MAX_SUPPORTED_CONCURRENT_WORKERS = 4

# Memory-aware scheduling (WO Part 4, "keep this simple") -- a session whose RETAINED NATIVE byte
# size (`artefact["byte_size"]`, the SAME existing, deterministic workload metadata the acquisition
# ledger already durably records for every COMPLETE session -- nothing new estimated/derived) is
# strictly greater than this threshold is treated as "large" for SCHEDULING purposes only, never
# for anything about the canonicalisation result itself. 10 MiB is deliberately conservative on
# this host (24 logical CPUs / 12 physical cores / 62 GiB RAM, per the dispatch's own host
# reference) -- see the dispatch's final report for the real observed byte-size distribution this
# was picked against (the single largest of the 142 currently-acquired sessions is ~49 MB).
LARGE_SESSION_NATIVE_BYTE_SIZE_THRESHOLD = 10 * 1024 * 1024


def _validate_and_snapshot_task(
    *, session_id, canonical_ledger, acquisition_ledger, research_source_root, force_rebuild_v2,
):
    """Exactly the SAME per-session existence/state checks `process_sessions()` performs inline --
    factored out so the concurrent coordinator can run them, in the caller's own `session_ids`
    order, BEFORE dispatching a single worker. Raises the identical `CanonicalLedgerError` for an
    unknown/not-source-complete session. Returns a plain, picklable task-description dict; never
    mutates `canonical_ledger`/`acquisition_ledger` (read-only snapshot -- the coordinator is the
    only thing that ever mutates the ledger, and only once a result comes back).

    Disclosed, deliberate strengthening (never a weakening): validating every session_id UP FRONT,
    before any dispatch, means an invalid session_ids list fails closed with ZERO side effects,
    rather than (as the serial path does) after however many earlier sessions in the list already
    ran. This can only ever make an invalid batch fail EARLIER and with LESS side effect, never
    later or with more."""
    entry = canonical_ledger.get(session_id)
    if entry is None:
        raise canonical_ledger_mod.CanonicalLedgerError(
            f"session {session_id!r} has no canonical-ledger row — it is not acquisition-"
            f"SOURCE-COMPLETE yet (or the ledger has not been built/refreshed)"
        )
    acquisition_entry = acquisition_ledger.get(session_id)
    if acquisition_entry is None or acquisition_entry.get("state") != source_ledger_mod.STATE_COMPLETE:
        raise canonical_ledger_mod.CanonicalLedgerError(
            f"session {session_id!r}: acquisition ledger no longer reports SOURCE COMPLETE "
            f"— refusing to canonicalise a session whose native-source completeness cannot "
            f"be reconfirmed"
        )
    artefact = acquisition_entry["artefact"]
    native_relative = artefact["object_relative_path"]
    native_full_path = os.path.join(research_source_root, native_relative)
    expected_native_sha256 = artefact["sha256"]
    native_byte_size = artefact.get("byte_size") or 0

    already_complete = entry["state"] == canonical_ledger_mod.STATE_CANONICAL_COMPLETE

    if (
        already_complete and force_rebuild_v2
        and entry.get("canonical_storage_layout_version") == canonical_worker.CANONICAL_STORAGE_LAYOUT_VERSION
    ):
        return {"session_id": session_id, "kind": "already_v2_skip", "native_byte_size": native_byte_size}

    if already_complete and not force_rebuild_v2:
        kind = "verify"
        pre_rebuild_event_set_hash = None
    else:
        kind = "process"
        pre_rebuild_event_set_hash = (
            entry.get("canonical_event_set_hash") if (already_complete and force_rebuild_v2) else None
        )

    return {
        "session_id": session_id,
        "kind": kind,
        "already_complete": already_complete,
        "force_rebuild_v2": force_rebuild_v2,
        "pre_rebuild_event_set_hash": pre_rebuild_event_set_hash,
        "native_relative": native_relative,
        "native_full_path": native_full_path,
        "expected_native_sha256": expected_native_sha256,
        "native_byte_size": native_byte_size,
        "provider_request_identity": acquisition_entry["request_identity"],
    }


def _run_one_session_worker(task: dict):
    """Runs in a WORKER PROCESS (never the coordinator) -- exactly one session, end to end,
    through the SAME unmodified `canonical_worker` entry points the serial path calls.
    `canonicalise_mbp1_session()`/`verify_existing_completion()` already construct their own fresh
    `Canonicaliser`, their own provider, and their own pid+uuid4-scoped staging directory per call
    (see `market_truth.acquisition.canonical_worker` module docstring); running two of these in two
    separate OS processes gives each an entirely separate address space on top of that, so there is
    no shared mutable state between workers to reason about at all. NEVER touches any ledger file
    -- only the coordinator (the caller, in the main process) ever writes a ledger. Returns the
    real result, or raises the real exception, unmodified -- the coordinator applies the exact same
    exception-type-specific handling `process_sessions()` uses inline, so a worker never has to
    reimplement or approximate that logic."""
    if task["kind"] == "verify":
        canonical_worker.verify_existing_completion(
            canonical_store_root=task["canonical_store_root"], session_id=task["session_id"],
            native_artefact_path=task["native_full_path"], expected_native_sha256=task["expected_native_sha256"],
        )
        return {"outcome": "reused"}

    result = canonical_worker.canonicalise_mbp1_session(
        session_id=task["session_id"],
        native_artefact_path=task["native_full_path"],
        native_artefact_relative_path=task["native_relative"],
        expected_native_sha256=task["expected_native_sha256"],
        mapping_table_path=task["mapping_table_path"],
        canonical_store_root=task["canonical_store_root"],
        provider_request_identity=task["provider_request_identity"],
        provider_definition_ref=PROVIDER_DEFINITION_REF,
        corpus_manifest_ref=CORPUS_MANIFEST_REF,
        acquisition_epoch=task["provider_request_identity"],
    )
    return {"outcome": "processed", "result": result}


def _build_memory_aware_waves(tasks, *, max_workers, large_byte_threshold):
    """Simple, deliberately non-clever scheduler (WO Part 4: "keep this simple"). Splits `tasks`
    (already in the caller's own, stable order) into "large" and "the rest" by native byte size,
    then builds waves of at most `max_workers` tasks each, never placing a second "large" task into
    a wave while ANY non-large task is still waiting to be scheduled -- the one, narrow case this
    is meant to avoid is two very-large sessions each holding their own full in-memory canonical
    event list on separate workers AT THE SAME TIME. Once non-large tasks run out, remaining large
    tasks are still scheduled (capacity is never left idle purely to keep the rule)."""
    large = [t for t in tasks if t["native_byte_size"] > large_byte_threshold]
    rest = [t for t in tasks if t["native_byte_size"] <= large_byte_threshold]
    waves = []
    while large or rest:
        wave = []
        if large:
            wave.append(large.pop(0))
        while len(wave) < max_workers and rest:
            wave.append(rest.pop(0))
        while len(wave) < max_workers and large:
            wave.append(large.pop(0))
        waves.append(wave)
    return waves


def process_sessions_concurrent(
    *,
    canonical_ledger: dict,
    acquisition_ledger: dict,
    session_ids,
    canonical_store_root,
    mapping_table_path: str,
    research_source_root: str,
    ledger_state_path: str,
    force_rebuild_v2: bool = False,
    max_workers: int = DEFAULT_CONCURRENT_MAX_WORKERS,
    large_byte_threshold: int = LARGE_SESSION_NATIVE_BYTE_SIZE_THRESHOLD,
    executor_factory=None,
) -> dict:
    """Bounded, session-boundary process-level concurrency, additive alongside the unmodified
    `process_sessions()` above. Same external contract (same return shape, same ledger mutations,
    same fail-closed/no-auto-retry discipline, same explicit-allowlist-only scope) -- the ONLY
    thing that differs from the serial path is HOW MANY sessions are in flight at once and WHICH
    process runs each one; every ledger mutation is still applied by this coordinator alone, one
    session at a time, never concurrently.

    `executor_factory`, if given, is called with `max_workers=` and must return a context-manager
    executor exposing `.submit()` (mirrors `concurrent.futures.Executor`) -- injected purely so
    tests can exercise the real scheduling/ledger/stop-on-failure logic against a fast, in-process
    fake executor without paying for real OS process start-up on every test. The real caller
    (`run_batch_concurrent()` below) always uses a real `concurrent.futures.ProcessPoolExecutor`
    (real, separate OS processes -- never threads)."""
    if max_workers < 1:
        raise ValueError(f"max_workers must be >= 1, got {max_workers!r}")
    if max_workers > MAX_SUPPORTED_CONCURRENT_WORKERS:
        raise ValueError(
            f"max_workers={max_workers!r} exceeds this dispatch's own governed ceiling of "
            f"{MAX_SUPPORTED_CONCURRENT_WORKERS} -- see the dispatch's final report for the "
            f"determinism/performance proof this ceiling is based on"
        )
    if executor_factory is None:
        executor_factory = functools.partial(concurrent.futures.ProcessPoolExecutor, max_workers=max_workers)

    processed = []
    stopped_reason = None

    tasks = [
        _validate_and_snapshot_task(
            session_id=session_id, canonical_ledger=canonical_ledger, acquisition_ledger=acquisition_ledger,
            research_source_root=research_source_root, force_rebuild_v2=force_rebuild_v2,
        )
        for session_id in session_ids
    ]
    for task in tasks:
        task["canonical_store_root"] = str(canonical_store_root)
        task["mapping_table_path"] = mapping_table_path

    dispatchable = [t for t in tasks if t["kind"] != "already_v2_skip"]
    for t in tasks:
        if t["kind"] == "already_v2_skip":
            processed.append(
                {"session_id": t["session_id"], "reused_existing_canonical_result": True, "already_v2": True}
            )

    waves = _build_memory_aware_waves(dispatchable, max_workers=max_workers, large_byte_threshold=large_byte_threshold)

    with executor_factory() as executor:
        for wave in waves:
            if stopped_reason is not None:
                break
            # Submit the WHOLE wave before collecting any result, so all of it runs concurrently;
            # collect in FIXED (wave) order below -- never physical-completion order -- so the
            # ledger's own final state (and this function's `processed` list) can never depend on
            # which worker happened to finish first.
            futures_in_order = [(t, executor.submit(_run_one_session_worker, t)) for t in wave]
            for task, future in futures_in_order:
                session_id = task["session_id"]
                entry = canonical_ledger[session_id]
                try:
                    outcome = future.result()
                except canonical_worker.VerificationFailedError as exc:
                    entry["state"] = canonical_ledger_mod.STATE_CANONICAL_FAILED
                    entry["failure_reason"] = f"re-verification of claimed-complete session failed: {exc}"
                    entry["canonical_updated_utc"] = _utc_now_iso()
                    canonical_ledger_mod.save_canonical_ledger_atomic(ledger_state_path, canonical_ledger)
                    if stopped_reason is None:
                        stopped_reason = (
                            f"AMBIGUOUS: claimed-complete session {session_id!r} failed re-verification "
                            f"— marked CANONICAL_FAILED, batch stopped (no auto-retry): {exc}"
                        )
                    continue
                except Exception as exc:  # noqa: BLE001 - deliberate, mirrors process_sessions() exactly
                    entry["state"] = canonical_ledger_mod.STATE_CANONICAL_FAILED
                    entry["failure_reason"] = f"{type(exc).__name__}: {exc}"
                    entry["canonical_updated_utc"] = _utc_now_iso()
                    canonical_ledger_mod.save_canonical_ledger_atomic(ledger_state_path, canonical_ledger)
                    if stopped_reason is None:
                        stopped_reason = (
                            f"AMBIGUOUS FAILURE canonicalising {session_id!r} — marked CANONICAL_FAILED, "
                            f"batch stopped (no auto-retry): {type(exc).__name__}: {exc}"
                        )
                    continue

                if outcome["outcome"] == "reused":
                    processed.append({"session_id": session_id, "reused_existing_canonical_result": True})
                    continue

                result = outcome["result"]
                entry["state"] = canonical_ledger_mod.STATE_CANONICAL_COMPLETE
                entry["lineage_record_relative_path"] = result.lineage_record_relative_path
                entry["canonical_event_set_hash"] = result.canonical_event_set_hash
                entry["canonical_partition_relative_paths"] = list(result.partition_relative_paths)
                entry["evidence_manifest_relative_path"] = result.evidence_manifest_relative_path
                entry["quality_record_relative_path"] = result.quality_record_relative_path
                entry["canonical_updated_utc"] = _utc_now_iso()
                entry["canonical_result_kind"] = result.canonical_result_kind
                entry["empty_reason"] = result.empty_reason
                entry["canonical_storage_layout_version"] = canonical_worker.CANONICAL_STORAGE_LAYOUT_VERSION
                processed_entry = {
                    "session_id": session_id,
                    "reused_existing_canonical_result": False,
                    "canonical_event_set_hash": result.canonical_event_set_hash,
                    "source_record_count": result.source_record_count,
                    "canonical_event_counts_by_family": dict(result.canonical_event_counts_by_family),
                    "quality_summary": dict(result.quality_summary),
                    "canonical_result_kind": result.canonical_result_kind,
                    "empty_reason": result.empty_reason,
                }
                if task["force_rebuild_v2"] and task["already_complete"]:
                    pre_rebuild_event_set_hash = task["pre_rebuild_event_set_hash"]
                    matches = (
                        pre_rebuild_event_set_hash is not None
                        and pre_rebuild_event_set_hash == result.canonical_event_set_hash
                    )
                    entry["pre_rebuild_v1_event_set_hash"] = pre_rebuild_event_set_hash
                    entry["v2_rebuild_matches_v1_hash"] = matches
                    processed_entry["pre_rebuild_v1_event_set_hash"] = pre_rebuild_event_set_hash
                    processed_entry["v2_rebuild_matches_v1_hash"] = matches
                canonical_ledger_mod.save_canonical_ledger_atomic(ledger_state_path, canonical_ledger)
                processed.append(processed_entry)

    return {
        "processed": processed,
        "stopped_reason": stopped_reason,
        "canonical_ledger_summary": canonical_ledger_mod.summarize_canonical(canonical_ledger),
    }


def run_batch_concurrent(
    *, session_ids, canonical_research_root_override=None, force_rebuild_v2: bool = False,
    max_workers: int = DEFAULT_CONCURRENT_MAX_WORKERS,
) -> dict:
    """Real-file-wiring wrapper for `process_sessions_concurrent()` -- mirrors `run_batch()`
    exactly, additive alongside it (never replaces it)."""
    acquisition_ledger = source_ledger_mod.load_ledger(SOURCE_LEDGER_STATE_PATH)
    canonical_store_root = canonical_worker.corpus_canonical_store_root(canonical_research_root_override)
    canonical_ledger = build_or_load_canonical_ledger(
        canonical_store_root=canonical_store_root, acquisition_ledger=acquisition_ledger,
    )
    ledger_state_path = canonical_ledger_state_path(canonical_store_root)

    result = process_sessions_concurrent(
        canonical_ledger=canonical_ledger,
        acquisition_ledger=acquisition_ledger,
        session_ids=session_ids,
        canonical_store_root=canonical_store_root,
        mapping_table_path=MAPPING_TABLE_PATH,
        research_source_root=RESEARCH_SOURCE_ROOT,
        ledger_state_path=ledger_state_path,
        force_rebuild_v2=force_rebuild_v2,
        max_workers=max_workers,
    )
    result["canonical_store_root"] = str(canonical_store_root)
    return result


def run_batch(*, session_ids, canonical_research_root_override=None, force_rebuild_v2: bool = False) -> dict:
    """Real-file-wiring wrapper: loads the acquisition ledger + real dependencies against the
    actual repo paths, then delegates to `process_sessions()`."""
    acquisition_ledger = source_ledger_mod.load_ledger(SOURCE_LEDGER_STATE_PATH)
    canonical_store_root = canonical_worker.corpus_canonical_store_root(canonical_research_root_override)
    canonical_ledger = build_or_load_canonical_ledger(
        canonical_store_root=canonical_store_root, acquisition_ledger=acquisition_ledger,
    )
    ledger_state_path = canonical_ledger_state_path(canonical_store_root)

    result = process_sessions(
        canonical_ledger=canonical_ledger,
        acquisition_ledger=acquisition_ledger,
        session_ids=session_ids,
        canonical_store_root=canonical_store_root,
        mapping_table_path=MAPPING_TABLE_PATH,
        research_source_root=RESEARCH_SOURCE_ROOT,
        ledger_state_path=ledger_state_path,
        force_rebuild_v2=force_rebuild_v2,
    )
    result["canonical_store_root"] = str(canonical_store_root)
    return result


def run_pilot_reproduction_check(*, canonical_research_root_override=None) -> dict:
    """WO Part 6 — THE gate. Runs (or reuses, if already durably complete+verified) the two real
    pilot sessions through the NEW durable pipeline, then combines their own durably-recorded
    canonical events (read back from disk, never from memory) into ONE corpus-level event-set
    hash, exactly mirroring how the pilot's own original disposable script combined both sessions
    into one shared-canonicaliser run. Compares that combined hash against
    `EXPECTED_PILOT_CANONICAL_EVENT_SET_HASH` — the value independently reproduced fresh, this
    same dispatch, by re-running `hmt2f_mbp1_pilot_replay.py` (the OLD disposable script) end to
    end (see final report for that run's own real output). Never rationalises a mismatch: if the
    hashes differ, this function reports the mismatch in full and does NOT try to "fix" the
    comparison.
    """
    batch_result = run_batch(session_ids=PILOT_SESSION_IDS, canonical_research_root_override=canonical_research_root_override)
    canonical_store_root = canonical_worker.corpus_canonical_store_root(canonical_research_root_override)

    # Load each session's own durably-recorded canonical events EXACTLY ONCE (never twice) —
    # reused both for the per-session count AND the combined corpus-level hash below. For a
    # session the size of the pilot's own busiest session (1,266,770 native records), a Parquet
    # reload reconstructs a comparable number of full canonical-event objects; loading it a
    # second time purely to recompute a count that was already available from the first load
    # would be a real, avoidable cost, not just a style nit.
    events_by_session = {
        session_id: canonical_worker.load_session_canonical_events(canonical_store_root, session_id)
        for session_id in PILOT_SESSION_IDS
    }
    per_session_event_counts = {sid: len(events) for sid, events in events_by_session.items()}

    all_events = [event for session_id in PILOT_SESSION_IDS for event in events_by_session[session_id]]
    combined_hash = canonical_worker.compute_event_set_hash(all_events)
    matches = combined_hash == EXPECTED_PILOT_CANONICAL_EVENT_SET_HASH

    return {
        "generated_utc": _utc_now_iso(),
        "pilot_session_ids": PILOT_SESSION_IDS,
        "durable_combined_canonical_event_set_hash": combined_hash,
        "expected_pilot_canonical_event_set_hash": EXPECTED_PILOT_CANONICAL_EVENT_SET_HASH,
        "reproduces_exactly": matches,
        "per_session_event_counts": per_session_event_counts,
        "batch_result": batch_result,
    }


def write_snapshot(result: dict, *, output_path: str) -> None:
    """Writes the corpus-progress snapshot to `output_path` — ALWAYS an explicit, caller-supplied
    argument (never a module-level default silently substituted here), precisely so this
    function itself can never reintroduce the fixed bug by falling back to the hardcoded
    `SNAPSHOT_OUTPUT_PATH` when a caller forgets to pass one. Every real call site
    (`main()`) computes `output_path` via `resolve_snapshot_output_path()`, which is the single
    place that decides tracked-authoritative-path vs. scratch-root-local-path."""
    snapshot = {
        "generated_by": "research/hmt2/hmt2i_gc_corpus_canonicalise.py",
        "generated_utc": _utc_now_iso(),
        "canonical_ledger_schema_version": canonical_ledger_mod.CANONICAL_LEDGER_SCHEMA_VERSION,
        "canonical_ledger_state_path_note": (
            "Actual per-session canonical-ledger state is LOCAL (gitignored, under the durable "
            "canonical research-store root -- see HMT2_CANONICAL_RESEARCH_ROOT / "
            "research-canonical-store/) -- this file is a committed SNAPSHOT only."
        ),
        "result": result,
    }
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2, sort_keys=True)
        f.write("\n")


def write_pilot_reproduction_evidence(result: dict) -> None:
    with open(PILOT_REPRODUCTION_OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, sort_keys=True)
        f.write("\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--session-ids", nargs="+", metavar="SESSION_ID",
        help="Explicit allowlist of session_ids to canonicalise in this dispatch -- mandatory, "
             "no 'process everything pending' default (canonicalisation-checkpoint scope "
             "discipline: this dispatch's own WO forbids canonicalising the corpus's other "
             "already-acquired sessions until the pilot-reproduction gate has passed).",
    )
    group.add_argument(
        "--pilot-reproduction-check", action="store_true",
        help="Run ONLY the two governed pilot sessions and report whether the durable pipeline "
             "reproduces the already-governed pilot canonical-event-set hash exactly.",
    )
    parser.add_argument("--canonical-research-root", default=None, help="Overrides HMT2_CANONICAL_RESEARCH_ROOT for this invocation only.")
    parser.add_argument(
        "--force-rebuild-v2", action="store_true",
        help="Governed full-corpus v2 storage-layout rebuild (module docstring) — only valid with "
             "--session-ids. Reprocesses every named session already CANONICAL_COMPLETE from its "
             "retained native bytes into the new canonical-v2/session_id=<...>/ layout, UNLESS the "
             "ledger row already records canonical_storage_layout_version == the current version "
             "(already migrated — skipped, resumable). Never used with --pilot-reproduction-check.",
    )
    parser.add_argument(
        "--max-workers", type=int, default=None, metavar="N",
        help="Bounded process-level concurrency at the session boundary (additive; only valid "
             "with --session-ids, never --pilot-reproduction-check). OMITTED (the default): "
             "byte-for-byte the same, unmodified SERIAL path (`run_batch()`/`process_sessions()`) "
             "this script has always used -- existing invocations are completely unaffected. "
             f"Given: uses `run_batch_concurrent()`/`process_sessions_concurrent()` instead, with "
             f"exactly N worker PROCESSES (1-{MAX_SUPPORTED_CONCURRENT_WORKERS}; see that "
             f"function's own governed ceiling).",
    )
    args = parser.parse_args()

    if args.pilot_reproduction_check:
        if args.force_rebuild_v2:
            parser.error("--force-rebuild-v2 may not be combined with --pilot-reproduction-check")
        if args.max_workers is not None:
            parser.error("--max-workers may not be combined with --pilot-reproduction-check")
        result = run_pilot_reproduction_check(canonical_research_root_override=args.canonical_research_root)
        write_pilot_reproduction_evidence(result)
        write_snapshot(
            result["batch_result"],
            output_path=resolve_snapshot_output_path(args.canonical_research_root),
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        if not result["reproduces_exactly"]:
            sys.exit(1)
        return

    if args.max_workers is None:
        result = run_batch(
            session_ids=args.session_ids, canonical_research_root_override=args.canonical_research_root,
            force_rebuild_v2=args.force_rebuild_v2,
        )
    else:
        result = run_batch_concurrent(
            session_ids=args.session_ids, canonical_research_root_override=args.canonical_research_root,
            force_rebuild_v2=args.force_rebuild_v2, max_workers=args.max_workers,
        )
    write_snapshot(result, output_path=resolve_snapshot_output_path(args.canonical_research_root))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
