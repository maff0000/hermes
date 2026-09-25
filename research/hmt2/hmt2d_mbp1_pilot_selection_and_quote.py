#!/usr/bin/env python3
"""HMT-2 (real-money checkpoint) — Part 1/Part 2 of the governed MBP-1 pilot: deterministic
pilot-pair selection (zero cost) followed by the real, free Databento metadata pre-quote for
exactly those two sessions (the pilot's SCHEDULED_EVENT session and its one-to-one
MATCHED_CONTROL session).

SELECTION (Part 1, zero cost, pure/no network):
    1. Read the committed, hash-verified `research/hmt2/corpus-selection-manifest-v2.json`
       (`manifest_sha256` verified via `market_truth.acquisition.corpus_manifest_v2.
       verify_manifest_integrity_v2` — the codebase's own governed hashing discipline, not a
       raw whole-file byte hash, which would trivially differ under re-serialisation).
    2. Extract the 79 `primary_stratum == "SCHEDULED_EVENT"` rows and their one-to-one
       `primary_stratum == "MATCHED_CONTROL"` rows, linked via `matched_parent_session`
       (verified 79-to-79, one-to-one, every parent referenced exactly once).
    3. Sort the 79 (event, control) pairs by the event's `session_id` ascending (equivalently,
       `gc_trade_date` ascending — session IDs are `GC-YYYY-MM-DD`, so the two orderings agree).
    4. `pilot_seed_digest = SHA256("HERMES|HMT-2|MBP1-PILOT-V1|<manifest_sha256>")`;
       `pilot_index = int(pilot_seed_digest, 16) % 79`. No reseeding, no outcome inspection.

QUOTE (Part 2, real, free, informational Databento metadata calls only — NEVER acquires data):
    Looks up each pilot session's already-resolved active outright contract set from
    `research/hmt2/gc-session-contract-activity-v1.json` (reusing the already-governed
    `gc_active_windows.py` output — never recomputed here), builds the exact same
    trading-calendar-contiguous `SessionRun` grouping `mbp1_quote_request` uses for the full
    448-session manifest (restricted here to just the 2 pilot sessions — since they are 7
    calendar days apart, i.e. many real GC trading sessions apart, they form two separate,
    singleton runs, never merged), and calls the three free `DatabentoHistoricalProvider`
    metadata-estimate methods once per run.

Run from the repository root:

    /tmp/hmt2-work-venv/bin/python3 research/hmt2/hmt2d_mbp1_pilot_selection_and_quote.py
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_THIS_DIR, "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from market_truth.acquisition import mbp1_quote_request as mqr  # noqa: E402
from market_truth.acquisition import session_calendar  # noqa: E402
from market_truth.acquisition.corpus_manifest_v2 import (  # noqa: E402
    ManifestIntegrityErrorV2,
    read_manifest_v2,
    verify_manifest_integrity_v2,
)
from market_truth.acquisition.providers.databento_historical import (  # noqa: E402
    DatabentoHistoricalProvider,
    load_databento_api_key,
)

DATASET = "GLBX.MDP3"
SCHEMA = "mbp-1"
SEED_LABEL = "HERMES|HMT-2|MBP1-PILOT-V1"

MANIFEST_PATH = os.path.join(_THIS_DIR, "corpus-selection-manifest-v2.json")
SESSION_CONTRACT_ACTIVITY_PATH = os.path.join(_THIS_DIR, "gc-session-contract-activity-v1.json")


class PilotSelectionError(ValueError):
    pass


def select_pilot_pair(manifest_doc: dict) -> dict:
    verify_manifest_integrity_v2(manifest_doc)  # fails closed (ManifestIntegrityErrorV2) if drifted
    manifest_sha256 = manifest_doc["manifest_sha256"]

    rows = manifest_doc["rows"]
    events = [r for r in rows if r["primary_stratum"] == "SCHEDULED_EVENT"]
    controls = [r for r in rows if r["primary_stratum"] == "MATCHED_CONTROL"]
    if len(events) != 79 or len(controls) != 79:
        raise PilotSelectionError(
            f"expected exactly 79 SCHEDULED_EVENT and 79 MATCHED_CONTROL rows, "
            f"found {len(events)} and {len(controls)}"
        )

    by_parent = {}
    for c in controls:
        parent = c.get("matched_parent_session")
        if not parent:
            raise PilotSelectionError(f"MATCHED_CONTROL session {c['session_id']!r} has no matched_parent_session")
        if parent in by_parent:
            raise PilotSelectionError(f"event session {parent!r} has more than one MATCHED_CONTROL row")
        by_parent[parent] = c

    event_ids = {e["session_id"] for e in events}
    missing_parents = event_ids - set(by_parent)
    if missing_parents:
        raise PilotSelectionError(f"SCHEDULED_EVENT session(s) with no MATCHED_CONTROL row: {sorted(missing_parents)}")
    dangling = set(by_parent) - event_ids
    if dangling:
        raise PilotSelectionError(f"MATCHED_CONTROL row(s) whose matched_parent_session is not a SCHEDULED_EVENT session_id: {sorted(dangling)}")

    pairs_sorted = sorted(
        ({"event": e, "control": by_parent[e["session_id"]]} for e in events),
        key=lambda p: p["event"]["session_id"],
    )
    candidate_pair_count = len(pairs_sorted)
    if candidate_pair_count != 79:
        raise PilotSelectionError(f"expected 79 sorted (event, control) pairs, got {candidate_pair_count}")

    seed_input = f"{SEED_LABEL}|{manifest_sha256}"
    pilot_seed_digest = hashlib.sha256(seed_input.encode("utf-8")).hexdigest()
    seed_int = int(pilot_seed_digest, 16)
    pilot_index = seed_int % candidate_pair_count

    selected = pairs_sorted[pilot_index]
    return {
        "manifest_sha256": manifest_sha256,
        "seed_input": seed_input,
        "pilot_seed_digest": pilot_seed_digest,
        "candidate_pair_count": candidate_pair_count,
        "pilot_index": pilot_index,
        "selected_event_session_id": selected["event"]["session_id"],
        "selected_event_gc_trade_date": selected["event"]["gc_trade_date"],
        "selected_event_event_class": selected["event"]["event_class"],
        "selected_control_session_id": selected["control"]["session_id"],
        "selected_control_gc_trade_date": selected["control"]["gc_trade_date"],
        "selected_control_inclusion_reason": selected["control"]["inclusion_reason"],
    }


def build_pilot_session_runs(pilot_session_ids: tuple[str, str], manifest_doc: dict) -> tuple[mqr.SessionRun, ...]:
    activity = json.load(open(SESSION_CONTRACT_ACTIVITY_PATH, encoding="utf-8"))
    by_id = {s["session_id"]: s for s in activity["sessions"]}
    missing = [sid for sid in pilot_session_ids if sid not in by_id]
    if missing:
        raise PilotSelectionError(f"pilot session_id(s) not found in gc-session-contract-activity-v1.json: {missing}")

    universe_start = _dt.date.fromisoformat(manifest_doc["metadata"]["session_universe_start"])
    universe_end = _dt.date.fromisoformat(manifest_doc["metadata"]["session_universe_end"])
    full_calendar_dates = [
        record.session_date.isoformat()
        for record in session_calendar.build_session_universe(universe_start, universe_end)
    ]

    sessions = tuple(
        mqr.SessionActivity(
            session_id=by_id[sid]["session_id"],
            trade_date=by_id[sid]["trade_date"],
            request_start_utc=by_id[sid]["request_start_utc"],
            request_end_utc=by_id[sid]["request_end_utc"],
            active_raw_symbols=tuple(by_id[sid]["active_raw_symbols"]),
        )
        for sid in pilot_session_ids
    )
    return mqr.group_sessions_into_contiguous_runs(sessions, full_calendar_dates)


def main() -> None:
    manifest_doc = read_manifest_v2(MANIFEST_PATH)
    try:
        selection = select_pilot_pair(manifest_doc)
    except (PilotSelectionError, ManifestIntegrityErrorV2) as exc:
        print(json.dumps({"stage": "PART1_SELECTION", "status": "FAILED", "error": str(exc)}, indent=2))
        sys.exit(1)

    pilot_session_ids = (selection["selected_event_session_id"], selection["selected_control_session_id"])
    runs = build_pilot_session_runs(pilot_session_ids, manifest_doc)

    api_key = load_databento_api_key()
    provider = DatabentoHistoricalProvider(api_key=api_key)

    per_run_results = []
    total_record_count = 0
    total_billable_size_bytes = 0
    total_quoted_cost_usd = 0.0
    for run in runs:
        symbols = list(run.raw_symbols)
        cost = provider.get_cost_estimate(
            dataset=DATASET, schema=SCHEMA, symbols=symbols,
            start=run.start_utc, end=run.end_utc_exclusive, stype_in="raw_symbol",
        )
        record_count = provider.get_record_count_estimate(
            dataset=DATASET, schema=SCHEMA, symbols=symbols,
            start=run.start_utc, end=run.end_utc_exclusive, stype_in="raw_symbol",
        )
        billable_size = provider.get_billable_size_estimate(
            dataset=DATASET, schema=SCHEMA, symbols=symbols,
            start=run.start_utc, end=run.end_utc_exclusive, stype_in="raw_symbol",
        )
        total_record_count += record_count.record_count
        total_billable_size_bytes += billable_size.billable_size_bytes
        total_quoted_cost_usd += cost.quoted_cost_usd
        per_run_results.append(
            {
                "session_ids": list(run.session_ids),
                "start": run.start_utc,
                "end_exclusive": run.end_utc_exclusive,
                "symbol_count": run.symbol_count,
                "raw_symbols": list(run.raw_symbols),
                "record_count": record_count.record_count,
                "billable_size_bytes": billable_size.billable_size_bytes,
                "quoted_cost_usd": cost.quoted_cost_usd,
            }
        )

    result = {
        "stage": "PART1_AND_PART2",
        "status": "OK",
        "selection": selection,
        "quote": {
            "dataset": DATASET,
            "schema": SCHEMA,
            "stype_in": "raw_symbol",
            "run_count": len(runs),
            "distinct_contract_count": len({sym for run in runs for sym in run.raw_symbols}),
            "total_record_count": total_record_count,
            "total_billable_size_bytes": total_billable_size_bytes,
            "total_quoted_cost_usd": total_quoted_cost_usd,
            "per_run_results": per_run_results,
        },
        "gate_5_dollars": {
            "proceed_to_part3": total_quoted_cost_usd <= 5.00,
            "quoted_cost_usd": total_quoted_cost_usd,
        },
    }
    print(json.dumps(result, indent=2))
    out_path = os.path.join(_THIS_DIR, "hmt2d-mbp1-pilot-selection-and-quote-evidence-v1.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, sort_keys=True)
        f.write("\n")


if __name__ == "__main__":
    main()
