#!/usr/bin/env python3
"""HMT-2 (real-money checkpoint) — reproduces the real reference-series session classification
from the retained, real `GC.v.0` continuous `ohlcv-1h` native artefact.

This script legitimately imports `databento` (read-only, local file decode — `DBNStore.from_file`,
never a network call, never re-requests the real data) because it lives in `research/hmt2/`, NOT
in `market_truth/acquisition/` — it is not subject to, and does not need an exception from,
`tests/hmt2/test_no_network_guard.py`'s package-scoped guard (mirrors
`research/hmt2/hmt2b1_reference_and_definition_quotes.py`'s own placement rationale exactly).

All session-mapping / log-range / quality-tagging logic itself lives in the pure, tested,
network-free `market_truth.acquisition.reference_series` module — this script only does the
one-time DBN-to-plain-Python translation and writes the resulting evidence files.

Run from the repository root:

    python3 research/hmt2/generate_reference_series_v2.py

Requires the real retained native artefact at the path this script hardcodes below (written by
the HMT-2 real-money checkpoint's throwaway acquisition script — see the WO final report). Zero
network access. Zero spend (already spent, once, for the real acquisition itself).
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

import databento as db  # noqa: E402  # read-only local decode of the already-retained file only

from market_truth.acquisition import (  # noqa: E402
    HMT2B1_ELIGIBLE_RANGE_START,
    HMT2B1_RESOLVED_FINAL_CUTOFF_DATE,
)
from market_truth.acquisition import reference_series as rs  # noqa: E402
from market_truth.acquisition.session_calendar import CALENDAR_VERSION, build_session_universe  # noqa: E402

NATIVE_ARTEFACT_PATH = os.path.join(
    _REPO_ROOT,
    "research-source", "hmt2-gc-mbp1-v1", "sessions",
    "HMT2-REAL-ACQ-REFERENCE-SERIES-GC-V0-OHLCV1H", "source",
    "gc_v0_ohlcv1h_2017-05-21_2026-09-19.dbn.zst",
)

OUT_SUMMARY_PATH = os.path.join(_THIS_DIR, "volatility-compression-reference-series-v2.json")
OUT_PER_SESSION_PATH = os.path.join(_THIS_DIR, "reference-series-per-session-v2.json")


def _sha256_of_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def load_hourly_bars(path: str) -> list[rs.HourlyBar]:
    store = db.DBNStore.from_file(path)
    df = store.to_df()
    bars = []
    for ts_event, row in zip(df.index, df.itertuples(index=False)):
        ts = ts_event.to_pydatetime()
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=_dt.timezone.utc)
        bars.append(
            rs.HourlyBar(
                ts_event_utc=ts,
                high=float(row.high),
                low=float(row.low),
                instrument_id=int(row.instrument_id),
            )
        )
    return bars


def main() -> None:
    if not os.path.exists(NATIVE_ARTEFACT_PATH):
        raise SystemExit(f"native artefact not found at {NATIVE_ARTEFACT_PATH}")

    artefact_sha256 = _sha256_of_file(NATIVE_ARTEFACT_PATH)
    bars = load_hourly_bars(NATIVE_ARTEFACT_PATH)

    start_date = _dt.date.fromisoformat(HMT2B1_ELIGIBLE_RANGE_START)
    end_date = _dt.date.fromisoformat(HMT2B1_RESOLVED_FINAL_CUTOFF_DATE)
    sessions = build_session_universe(start_date, end_date)

    results, orphans = rs.classify_all_sessions(sessions, bars)

    counts = {tag.value: 0 for tag in rs.ReferenceQualityTag}
    for r in results.values():
        counts[r.quality_tag.value] += 1

    per_session = [
        {
            "session_id": r.session_id,
            "session_date": r.session_date.isoformat(),
            "quality_tag": r.quality_tag.value,
            "session_log_range": r.session_log_range,
            "bar_count": r.bar_count,
            "expected_hour_count": r.expected_hour_count,
            "coverage_ratio": r.coverage_ratio,
            "distinct_instrument_ids": list(r.distinct_instrument_ids),
        }
        for r in sorted(results.values(), key=lambda r: r.session_id)
    ]

    with open(OUT_PER_SESSION_PATH, "w", encoding="utf-8") as f:
        json.dump(
            {
                "reference_series_version": rs.REFERENCE_SERIES_METHODOLOGY_VERSION,
                "per_session": per_session,
            },
            f, indent=2, sort_keys=True,
        )
        f.write("\n")

    summary = {
        "reference_series_version": rs.REFERENCE_SERIES_METHODOLOGY_VERSION,
        "status": "COMPUTED_FROM_REAL_DATA",
        "frozen_source": {
            "provider": "databento",
            "dataset": "GLBX.MDP3",
            "instrument": "GC.v.0",
            "stype_in": "continuous",
            "schema": "ohlcv-1h",
            "resolution": "1 hour",
            "price_basis": "hourly OHLC (high/low used; open/close not used by this methodology)",
            "date_range": f"{HMT2B1_ELIGIBLE_RANGE_START}..{HMT2B1_RESOLVED_FINAL_CUTOFF_DATE} (inclusive cutoff)",
            "native_artefact_relative_path": os.path.relpath(NATIVE_ARTEFACT_PATH, _REPO_ROOT),
            "native_artefact_sha256": artefact_sha256,
            "session_calendar_version": CALENDAR_VERSION,
        },
        "methodology": {
            "purpose": (
                "A coarse, session-level range measure used ONLY to classify each already-"
                "calendar-eligible GC session into HIGH_VOL_NON_EVENT or COMPRESSION candidacy "
                "for corpus stratification (manifest v2). NOT a P0 microstructure fact, NOT "
                "used for any strategy/threshold logic — selection-only."
            ),
            "transformation_formula": "session_log_range = ln(max(hourly highs) / min(hourly lows))",
            "quality_tagging": {
                "REFERENCE_MISSING": "zero hourly bars assigned to the session",
                "REFERENCE_ROLL_AMBIGUOUS": "more than one distinct instrument_id among the session's bars",
                "REFERENCE_COMPLETE": f"single instrument_id AND coverage_ratio >= {rs.MIN_COMPLETE_COVERAGE_RATIO}",
                "REFERENCE_PARTIAL": f"single instrument_id AND coverage_ratio < {rs.MIN_COMPLETE_COVERAGE_RATIO}, at least one bar",
            },
            "candidacy_rule": (
                "Only REFERENCE_COMPLETE sessions may enter HIGH_VOL_NON_EVENT/COMPRESSION "
                "candidacy. Within each calendar year separately, eligible (REFERENCE_COMPLETE, "
                "not already claimed by PROTECTED_HOLDOUT/SCHEDULED_EVENT/MATCHED_CONTROL) "
                f"sessions are ranked by session_log_range; top {rs.CANDIDATE_FRACTION*100:.0f}% "
                f"within year = HIGH_VOL_NON_EVENT candidates, bottom "
                f"{rs.CANDIDATE_FRACTION*100:.0f}% within year = COMPRESSION candidates. "
                "Sizing: floor(eligible_count_in_year * fraction); a year too small to produce "
                "even one candidate contributes zero, disclosed, not forced."
            ),
            "tie_break_rule": (
                "Sessions sorted ascending by (session_log_range, session_date) before slicing; "
                "at a percentile-boundary tie in session_log_range, the earlier session_date "
                "sorts first."
            ),
            "frozen_before_computation": (
                "This formula/method was implemented and its tests written BEFORE this script "
                "was ever run against the real retained data; it was run exactly once and this "
                "file's counts/candidates are whatever that one run produced — no tuning after "
                "seeing results."
            ),
            "no_mbp1_used": (
                "Zero MBP-1/TBBO/trades/MBO data was used, examined, or referenced anywhere in "
                "this computation — none has been acquired anywhere in this repository."
            ),
        },
        "orphan_bar_count": len(orphans),
        "session_universe_size": len(sessions),
        "quality_tag_counts": counts,
        "per_session_file": os.path.relpath(OUT_PER_SESSION_PATH, _REPO_ROOT),
    }

    with open(OUT_SUMMARY_PATH, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, sort_keys=True)
        f.write("\n")

    print(json.dumps({"quality_tag_counts": counts, "orphan_bar_count": len(orphans), "session_universe_size": len(sessions)}, indent=2))
    print(f"wrote {OUT_SUMMARY_PATH}")
    print(f"wrote {OUT_PER_SESSION_PATH}")


if __name__ == "__main__":
    main()
