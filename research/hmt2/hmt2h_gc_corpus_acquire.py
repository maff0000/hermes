#!/usr/bin/env python3
"""HMT-2 (real-money checkpoint) — governed GC MBP-1 CORPUS acquisition driver (HMT-2 Part 5+).

Extends the already-CLOSED, governed MBP-1 pilot (`hmt2e_mbp1_pilot_acquire.py`, 2 sessions,
$0.175759524107 real spend) to the REMAINING 446 sessions of the frozen 448-session
corpus-selection manifest v2, using the ledger defined in `hmt2h_gc_corpus_ledger.py`.

Explicit-invocation only (never auto-triggered by any other code path):

    /tmp/hmt2-work-venv/bin/python3 research/hmt2/hmt2h_gc_corpus_acquire.py [--max-sessions N]

REUSE, NOT REIMPLEMENTATION (see `hmt2h_gc_corpus_ledger.py` module docstring for the full,
disclosed rationale):
  - `DatabentoHistoricalProvider.acquire_mbp1_pilot_session_data()` — imported and called
    UNCHANGED, via the SAME `acquire_one_session()` orchestration function `hmt2e_mbp1_pilot_
    acquire.py` already defines (imported here, never copied/duplicated).
  - `market_truth.acquisition.source_store.compute_request_identity()` — the SAME
    duplicate-request-reuse identity function, applied to every governed session, pilot
    sessions included (so the pilot's own already-catalogued artefacts are found and reused
    with no special-casing).
  - The SAME on-disk artefact catalogue (`mbp1_pilot_acquisition_catalogue.json`) the pilot
    already reads/writes — this driver adds MORE entries to it, never a separate/parallel one.

WHAT THIS SCRIPT ADDS: the ledger-driven, per-session ORCHESTRATION LOOP over the remaining 446
sessions — for each `PLANNED` ledger row, in deterministic `trade_date` order:

  1. Obtain a FRESH, per-session free quote (`get_cost_estimate` / `get_record_count_estimate` /
     `get_billable_size_estimate`) — even though an aggregate 448-session quote already exists
     (`hmt2c-mbp1-quote-evidence-v1.json`), this checkpoint's WO explicitly requires the
     per-group quoting step to never be skipped. Persisted onto the ledger row regardless of
     outcome (audit trail).
  2. Recompute `actual_spend_so_far + this_request's_quoted_cost` and confirm it stays
     <= $100 (`hmt2h_gc_corpus_ledger.check_cost_ceiling`) — BEFORE the billable request. If it
     would exceed $100, STOP before this specific request (loop `break`), leave its ledger row
     `PLANNED`, and report — never silently skip past it.
  3. Call `acquire_one_session()` exactly once for this session. On success: verify (already
     done inside `acquire_one_session` — file exists, non-empty, hashed) then mark the ledger
     row `COMPLETE` with the real artefact identity + actual cost (= quoted cost, the SAME
     "no post-transfer cost endpoint exists" convention every prior real acquisition in this
     checkpoint already documents and uses).
  4. On ANY ambiguous failure (network error, partial-write stray-file refusal, decode error):
     mark the ledger row `FAILED_AMBIGUOUS` with a clear reason, save the ledger, and STOP the
     WHOLE batch (never auto-retry, never silently continue past an ambiguous failure onto
     later sessions — preserves the no-duplicate-billing invariant for whoever investigates
     next; a human must inspect and decide, exactly as `hmt2e_mbp1_pilot_acquire.py`'s own
     docstring already establishes for the single-session case).

Every ledger write (`save_ledger_atomic`) happens synchronously, immediately after each row's
outcome is fully known — a row is NEVER persisted in `IN_PROGRESS`: the whole quote+acquire+
verify+hash+evidence-write sequence for one session completes (or raises) before the next atomic
ledger save, so an interrupted dispatch always leaves the ledger in a consistent, resumable
state (the interrupted session's row is simply still `PLANNED`, exactly as if it had not been
attempted yet — see `hmt2e_mbp1_pilot_acquire.acquire_one_session()`'s own "REFUSING TO PROCEED"
stray-file guard for what happens if a *partial artefact* was left on disk by a hard process
kill mid-transfer; that specific case surfaces as an ambiguous failure on the NEXT run of this
script, exactly as intended, never silently retried).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_THIS_DIR, "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

import hmt2h_gc_corpus_ledger as ledger_mod  # noqa: E402
from hmt2d_mbp1_pilot_selection_and_quote import select_pilot_pair  # noqa: E402
from hmt2e_mbp1_pilot_acquire import acquire_one_session  # noqa: E402  (REUSED, not copied)
from market_truth.acquisition.corpus_manifest_v2 import read_manifest_v2  # noqa: E402
from market_truth.acquisition.providers.databento_historical import (  # noqa: E402
    DatabentoHistoricalProvider,
    load_databento_api_key,
)
from market_truth.acquisition.source_store import (  # noqa: E402
    NativeSourceStore,
    compute_request_identity,
    manifest_relative_path,
)

MANIFEST_PATH = os.path.join(_THIS_DIR, "corpus-selection-manifest-v2.json")
SESSION_CONTRACT_ACTIVITY_PATH = os.path.join(_THIS_DIR, "gc-session-contract-activity-v1.json")
RESEARCH_SOURCE_ROOT = os.path.abspath(os.path.join(_THIS_DIR, "..", "..", "research-source"))
CATALOGUE_RELATIVE_PATH = manifest_relative_path("mbp1_pilot_acquisition_catalogue.json")
LEDGER_STATE_PATH = os.path.join(RESEARCH_SOURCE_ROOT, ledger_mod.LEDGER_STATE_RELATIVE_PATH)

# Real, already-spent, already-independently-evidenced baseline for this checkpoint — the 3
# real acquisitions that happened BEFORE this ledger existed. Read live from their own PRIMARY,
# per-session evidence files (never a secondary rollup) so this figure can never silently drift
# from -- or depend on the continued existence of -- anything but the artefacts that actually
# justify it. `real_acquisition_summary.json` (a convenience rollup of the same two figures)
# was found to be ABSENT from this local gitignored tree partway through this checkpoint's own
# dispatch (its two source evidence files below were independently confirmed still intact and
# byte-identical to what this rollup file had recorded) -- reading the two primary files
# directly, instead of that rollup, is both more robust and more consistent with this
# repository's own "evidence must be independent of what it certifies" / "verify contents, not
# names" discipline.
REFERENCE_SERIES_EVIDENCE_PATH = os.path.join(
    RESEARCH_SOURCE_ROOT, "hmt2-gc-mbp1-v1", "sessions", "HMT2-REAL-ACQ-REFERENCE-SERIES-GC-V0-OHLCV1H",
    "evidence", "reference_series_acquisition_evidence.json",
)
GC_DEFINITIONS_EVIDENCE_PATH = os.path.join(
    RESEARCH_SOURCE_ROOT, "hmt2-gc-mbp1-v1", "sessions", "HMT2-REAL-ACQ-GC-FUT-DEFINITIONS",
    "evidence", "gc_definitions_acquisition_evidence.json",
)
PILOT_QUOTE_EVIDENCE_PATH = os.path.join(_THIS_DIR, "hmt2d-mbp1-pilot-selection-and-quote-evidence-v1.json")

SNAPSHOT_OUTPUT_PATH = os.path.join(_THIS_DIR, "hmt2h-gc-corpus-acquisition-ledger-snapshot-v1.json")


def _utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def compute_prior_actual_spend_usd(
    *,
    reference_series_evidence_path: str = REFERENCE_SERIES_EVIDENCE_PATH,
    gc_definitions_evidence_path: str = GC_DEFINITIONS_EVIDENCE_PATH,
    pilot_quote_evidence_path: str = PILOT_QUOTE_EVIDENCE_PATH,
) -> dict:
    """The real spend already committed BEFORE this ledger's own tracked sessions: the
    reference-series + definitions real acquisitions (read from their own PRIMARY per-session
    evidence files, never a secondary rollup — see the module-level comment above these
    constants), plus the pilot's own 2 sessions (real spend recorded at the free-quote step per
    this checkpoint's established "actual == quoted, no post-transfer cost endpoint exists"
    convention — see `hmt2d-mbp1-pilot-selection-and-quote-evidence-v1.json`'s own
    `gate_5_dollars.quoted_cost_usd`).

    Paths are injectable (defaulting to the real repo paths) so this can be unit-tested against
    synthetic fixtures — the two `research-source/`-rooted paths are real, gitignored local
    acquisition state (absent on a fresh CI checkout), so a test must never depend on either
    existing."""
    with open(reference_series_evidence_path, "r", encoding="utf-8") as f:
        reference_series_evidence = json.load(f)
    with open(gc_definitions_evidence_path, "r", encoding="utf-8") as f:
        definitions_evidence = json.load(f)
    with open(pilot_quote_evidence_path, "r", encoding="utf-8") as f:
        pilot_quote = json.load(f)
    reference_series_usd = float(reference_series_evidence["actual_cost_usd"])
    definitions_usd = float(definitions_evidence["actual_cost_usd"])
    pilot_usd = float(pilot_quote["gate_5_dollars"]["quoted_cost_usd"])
    return {
        "reference_series_usd": reference_series_usd,
        "definitions_usd": definitions_usd,
        "pilot_usd": pilot_usd,
        "total_usd": reference_series_usd + definitions_usd + pilot_usd,
    }


def _load_catalogue(store_root: str) -> dict:
    path = os.path.join(store_root, CATALOGUE_RELATIVE_PATH)
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_or_load_ledger() -> dict:
    existing = ledger_mod.load_ledger(LEDGER_STATE_PATH)
    if existing:
        return existing

    manifest_doc = read_manifest_v2(MANIFEST_PATH)
    selection = select_pilot_pair(manifest_doc)
    pilot_session_ids = frozenset(
        {selection["selected_event_session_id"], selection["selected_control_session_id"]}
    )

    activity = json.load(open(SESSION_CONTRACT_ACTIVITY_PATH, encoding="utf-8"))
    pilot_catalogue = _load_catalogue(RESEARCH_SOURCE_ROOT)

    new_ledger = ledger_mod.build_initial_ledger(
        activity_sessions=activity["sessions"],
        pilot_session_ids=pilot_session_ids,
        pilot_catalogue=pilot_catalogue,
        compute_request_identity_fn=compute_request_identity,
    )
    ledger_mod.save_ledger_atomic(LEDGER_STATE_PATH, new_ledger)
    return new_ledger


def process_planned_sessions(
    *,
    ledger: dict,
    provider,
    store,
    catalogue: dict,
    prior_total_usd: float,
    ledger_state_path: str,
    max_sessions: int,
) -> dict:
    """The pure(ish) sequential orchestration loop, with every dependency injected — this is
    what `tests/hmt2/test_hmt2h_gc_corpus_acquire.py` exercises directly, with a fake provider/
    store/catalogue/tmp-path ledger file, exactly mirroring `test_mbp1_pilot_acquisition.py`'s
    own `acquire_one_session()` testing discipline. `run_batch()` below is the thin, real-file-
    wiring wrapper `main()` actually calls."""
    planned_session_ids = sorted(
        (sid for sid, entry in ledger.items() if entry["state"] == ledger_mod.STATE_PLANNED),
        key=lambda sid: ledger[sid]["trade_date"],
    )

    completed_this_run = []
    stopped_reason = None

    for session_id in planned_session_ids:
        if len(completed_this_run) >= max_sessions:
            stopped_reason = f"reached --max-sessions={max_sessions} for this dispatch"
            break

        entry = ledger[session_id]

        # Step 1 — fresh, per-session free quote (never skipped, even with an aggregate quote).
        try:
            cost_est = provider.get_cost_estimate(
                dataset=ledger_mod.DATASET, schema=ledger_mod.SCHEMA, symbols=entry["symbols"],
                start=entry["start_utc"], end=entry["end_utc"], stype_in=ledger_mod.STYPE_IN,
            )
            count_est = provider.get_record_count_estimate(
                dataset=ledger_mod.DATASET, schema=ledger_mod.SCHEMA, symbols=entry["symbols"],
                start=entry["start_utc"], end=entry["end_utc"], stype_in=ledger_mod.STYPE_IN,
            )
            size_est = provider.get_billable_size_estimate(
                dataset=ledger_mod.DATASET, schema=ledger_mod.SCHEMA, symbols=entry["symbols"],
                start=entry["start_utc"], end=entry["end_utc"], stype_in=ledger_mod.STYPE_IN,
            )
        except Exception as exc:
            # A free-quote failure never billed anything — stop cleanly, leave PLANNED, report.
            stopped_reason = f"quote step failed for {session_id!r} ({type(exc).__name__}: {exc}) — stopped before any billable request; row left PLANNED"
            break

        entry["quote"] = {
            "quoted_cost_usd": cost_est.quoted_cost_usd,
            "quoted_record_count": count_est.record_count,
            "quoted_billable_size_bytes": size_est.billable_size_bytes,
            "quoted_utc": _utc_now_iso(),
        }
        ledger_mod.save_ledger_atomic(ledger_state_path, ledger)

        # Step 2 — $100 ceiling check, BEFORE the billable request.
        spend_so_far = prior_total_usd + ledger_mod.sum_completed_actual_cost_usd(ledger)
        try:
            ledger_mod.check_cost_ceiling(spend_so_far=spend_so_far, quoted_cost=cost_est.quoted_cost_usd)
        except ledger_mod.CostCeilingExceededError as exc:
            stopped_reason = f"cost ceiling would be exceeded by {session_id!r}: {exc} — stopped before this specific request; row left PLANNED"
            break

        # Step 3/4 — the one real, billable, per-session acquisition call (reused, unchanged).
        try:
            outcome = acquire_one_session(
                session_id=session_id, trade_date=entry["trade_date"],
                active_raw_symbols=tuple(entry["symbols"]), start=entry["start_utc"], end=entry["end_utc"],
                provider=provider, store=store, catalogue=catalogue,
            )
        except Exception as exc:
            entry["state"] = ledger_mod.STATE_FAILED_AMBIGUOUS
            entry["failure_reason"] = f"{type(exc).__name__}: {exc}"
            entry["ledger_updated_utc"] = _utc_now_iso()
            ledger_mod.save_ledger_atomic(ledger_state_path, ledger)
            stopped_reason = f"AMBIGUOUS FAILURE acquiring {session_id!r} — marked FAILED_AMBIGUOUS, batch stopped (no auto-retry): {type(exc).__name__}: {exc}"
            break

        record = outcome["record"]
        entry["state"] = ledger_mod.STATE_COMPLETE
        entry["artefact"] = {
            "object_relative_path": record["object_relative_path"],
            "byte_size": record["byte_size"],
            "sha256": record["sha256"],
            "record_count": record["reported_record_count"],
            "acquisition_utc": record["acquisition_utc"],
        }
        entry["actual_cost_usd"] = cost_est.quoted_cost_usd
        entry["ledger_updated_utc"] = _utc_now_iso()
        ledger_mod.save_ledger_atomic(ledger_state_path, ledger)
        completed_this_run.append(session_id)

    summary = ledger_mod.summarize(ledger)
    running_spend = prior_total_usd + ledger_mod.sum_completed_actual_cost_usd(ledger)
    return {
        "completed_this_run": completed_this_run,
        "completed_this_run_count": len(completed_this_run),
        "stopped_reason": stopped_reason,
        "ledger_summary": summary,
        "prior_baseline_spend_usd": prior_total_usd,
        "running_actual_spend_usd": running_spend,
        "ceiling_usd": ledger_mod.GOVERNED_SPEND_CEILING_USD,
        "headroom_to_ceiling_usd": ledger_mod.GOVERNED_SPEND_CEILING_USD - running_spend,
    }


def run_batch(*, max_sessions: int) -> dict:
    """Real-file-wiring wrapper: loads/builds the ledger and real dependencies against the
    actual repo paths, then delegates to `process_planned_sessions()`."""
    ledger = build_or_load_ledger()
    prior = compute_prior_actual_spend_usd()

    provider = DatabentoHistoricalProvider(api_key=load_databento_api_key())
    store = NativeSourceStore(RESEARCH_SOURCE_ROOT)
    catalogue = _load_catalogue(RESEARCH_SOURCE_ROOT)

    result = process_planned_sessions(
        ledger=ledger, provider=provider, store=store, catalogue=catalogue,
        prior_total_usd=prior["total_usd"], ledger_state_path=LEDGER_STATE_PATH,
        max_sessions=max_sessions,
    )
    result["prior_baseline_spend_usd"] = prior
    return result


def write_snapshot(result: dict) -> None:
    """Committed (git-tracked, under research/hmt2/) evidence snapshot of the ledger's state
    immediately after this dispatch — a follow-up dispatch resumes from the LOCAL gitignored
    ledger state file (`LEDGER_STATE_PATH`), never from this snapshot (this snapshot is
    human/audit-facing evidence, not itself the resumption mechanism) — but this snapshot lets
    anyone reviewing the PR see real progress without needing filesystem access to
    `research-source/`."""
    snapshot = {
        "generated_by": "research/hmt2/hmt2h_gc_corpus_acquire.py",
        "generated_utc": _utc_now_iso(),
        "ledger_schema_version": ledger_mod.LEDGER_SCHEMA_VERSION,
        "ledger_state_path_note": "Actual per-session ledger state is LOCAL and gitignored under research-source/ (see .gitignore's blanket research-source/ rule) at hmt2-gc-mbp1-v1/manifest/gc_corpus_acquisition_ledger.json -- this file is a committed SNAPSHOT only.",
        "result": result,
    }
    with open(SNAPSHOT_OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2, sort_keys=True)
        f.write("\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--max-sessions", type=int, default=40,
        help="Maximum number of PLANNED sessions to acquire in this dispatch (default 40) -- "
             "deliberately bounded; a real production data-engineering job, not a one-shot batch "
             "over all 446 remaining sessions at once.",
    )
    args = parser.parse_args()

    result = run_batch(max_sessions=args.max_sessions)
    write_snapshot(result)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
