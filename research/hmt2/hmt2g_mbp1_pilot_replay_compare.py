#!/usr/bin/env python3
"""HMT-2 (real-money checkpoint) — Part 5: cross-process replay-determinism comparison.

Loads N (>=3) run-summary JSON files, each produced by an INDEPENDENT `hmt2f_mbp1_pilot_replay.py`
subprocess invocation (a genuinely separate OS process, ideally with a different `PYTHONHASHSEED`
each), and asserts pairwise equality across every WO Part 5 dimension: source hashes, source
record counts, canonical event counts, event identity-hash lists, full event-row content, the
event-set hash, semantic partition-content hashes, physical Parquet artifact hashes, and the
evidence manifest's deterministic-fields hash. No tolerance — any mismatch is reported and this
script exits non-zero.

Run:
    /tmp/hmt2-work-venv/bin/python3 research/hmt2/hmt2g_mbp1_pilot_replay_compare.py \\
        /tmp/hmt2-mbp1-replay/run1.json /tmp/hmt2-mbp1-replay/run2.json /tmp/hmt2-mbp1-replay/run3.json
"""
from __future__ import annotations

import json
import sys

_DIMENSIONS = (
    "source_hashes",
    "record_counts_by_session",
    "source_record_count",
    "canonical_event_counts_by_family",
    "canonical_event_set_hash",
    "identity_hashes_sorted",
    "event_rows_sorted_hex",
    "reload_reconstructed_rows_sorted_hex",
    "partition_content_hashes",
    "artifact_hashes",
    "manifest_deterministic_fields_sha256",
)


def compare_summaries(summaries: list[dict]) -> dict:
    """Pure comparison logic (no file I/O, no argv, no exit) — exercised directly by
    `tests/hmt2/test_mbp1_pilot_replay_compare.py` against small synthetic summaries, and by
    `main()` below against the real 3 (or more) independent subprocess run summaries. Returns
    `{"all_pass": bool, "results": {dimension: "PASS"|"FAIL (...)", ...}}`. Requires at least 2
    summaries (an N-way comparison against summaries[0] as baseline); the real WO usage always
    passes >= 3."""
    if len(summaries) < 2:
        raise ValueError("compare_summaries requires at least 2 run summaries")

    all_pass = True
    results = {}
    baseline = summaries[0]
    for dim in _DIMENSIONS:
        baseline_value = baseline[dim]
        mismatches = [i for i, s in enumerate(summaries[1:], start=1) if s[dim] != baseline_value]
        results[dim] = "PASS" if not mismatches else f"FAIL (mismatch at run index {mismatches})"
        if mismatches:
            all_pass = False

    reload_match_ok = all(s["reload_reconstruction_matches_live"] for s in summaries)
    results["reload_reconstruction_matches_live (each run, self-consistent)"] = "PASS" if reload_match_ok else "FAIL"
    if not reload_match_ok:
        all_pass = False

    return {"all_pass": all_pass, "results": results}


def main() -> None:
    paths = sys.argv[1:]
    if len(paths) < 3:
        print("usage: hmt2g_mbp1_pilot_replay_compare.py <run1.json> <run2.json> <run3.json> [...]")
        sys.exit(2)

    runs = []
    for p in paths:
        with open(p, "r", encoding="utf-8") as f:
            runs.append({"path": p, "summary": json.load(f)})

    print(f"Comparing {len(runs)} independent replay runs:")
    for r in runs:
        print(f"  {r['path']}  pid={r['summary'].get('pid')}  PYTHONHASHSEED={r['summary'].get('pythonhashseed')}")

    comparison = compare_summaries([r["summary"] for r in runs])

    print()
    print("=== Replay determinism comparison — every dimension, exact equality required ===")
    for dim, status in comparison["results"].items():
        print(f"  {dim}: {status}")

    baseline = runs[0]["summary"]
    print()
    print("Baseline (run 1) real values:")
    print(f"  source_hashes: {baseline['source_hashes']}")
    print(f"  record_counts_by_session: {baseline['record_counts_by_session']}")
    print(f"  source_record_count: {baseline['source_record_count']}")
    print(f"  canonical_event_counts_by_family: {baseline['canonical_event_counts_by_family']}")
    print(f"  canonical_event_set_hash: {baseline['canonical_event_set_hash']}")
    print(f"  partition_content_hashes: {baseline['partition_content_hashes']}")
    print(f"  artifact_hashes: {baseline['artifact_hashes']}")
    print(f"  manifest_deterministic_fields_sha256: {baseline['manifest_deterministic_fields_sha256']}")
    print(f"  event_count_total: {len(baseline['identity_hashes_sorted'])}")
    print()
    print("OVERALL:", "PASS — all dimensions match exactly across all runs" if comparison["all_pass"] else "FAIL")
    sys.exit(0 if comparison["all_pass"] else 1)


if __name__ == "__main__":
    main()
