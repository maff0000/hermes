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
) -> dict:
    """The pure(ish) sequential orchestration loop, with every dependency injected — mirrors
    `hmt2h_gc_corpus_acquire.process_planned_sessions()`'s own testing discipline exactly. Only
    ever touches the sessions named in `session_ids` (the mandatory, explicit allowlist) —
    NEVER "every CANONICAL_PENDING row"."""
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

        if entry["state"] == canonical_ledger_mod.STATE_CANONICAL_COMPLETE:
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
        canonical_ledger_mod.save_canonical_ledger_atomic(ledger_state_path, canonical_ledger)
        processed.append(
            {
                "session_id": session_id,
                "reused_existing_canonical_result": False,
                "canonical_event_set_hash": result.canonical_event_set_hash,
                "source_record_count": result.source_record_count,
                "canonical_event_counts_by_family": dict(result.canonical_event_counts_by_family),
                "quality_summary": dict(result.quality_summary),
                "canonical_result_kind": result.canonical_result_kind,
                "empty_reason": result.empty_reason,
            }
        )

    return {
        "processed": processed,
        "stopped_reason": stopped_reason,
        "canonical_ledger_summary": canonical_ledger_mod.summarize_canonical(canonical_ledger),
    }


def run_batch(*, session_ids, canonical_research_root_override=None) -> dict:
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


def write_snapshot(result: dict) -> None:
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
    with open(SNAPSHOT_OUTPUT_PATH, "w", encoding="utf-8") as f:
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
    args = parser.parse_args()

    if args.pilot_reproduction_check:
        result = run_pilot_reproduction_check(canonical_research_root_override=args.canonical_research_root)
        write_pilot_reproduction_evidence(result)
        write_snapshot(result["batch_result"])
        print(json.dumps(result, indent=2, sort_keys=True))
        if not result["reproduces_exactly"]:
            sys.exit(1)
        return

    result = run_batch(session_ids=args.session_ids, canonical_research_root_override=args.canonical_research_root)
    write_snapshot(result)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
