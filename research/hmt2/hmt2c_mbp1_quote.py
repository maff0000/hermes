#!/usr/bin/env python3
"""HMT-2 (real-money checkpoint) — reproduces the real, free Databento MBP-1 metadata quote for
the final governed GC MBP-1 corpus-selection manifest v2 (448 sessions). QUOTE-ONLY: this script
calls exactly three free, informational, metadata-only methods on `DatabentoHistoricalProvider`
(`get_cost_estimate`, `get_record_count_estimate`, `get_billable_size_estimate`), once per
trading-calendar-contiguous SESSION RUN (see `market_truth.acquisition.mbp1_quote_request`
module docstring for exactly why grouping by session-date run with a multi-symbol `symbols`
list, rather than one call per contract, is both correct AND the only practical reading of "use
whichever is correct and efficient"). It NEVER calls `download_historical_range()`, never
references `.get_range`/`.Live` anywhere, and never acquires any actual MBP-1 (or any other
schema's) historical data — see `tests/hmt2/test_hmt2c_mbp1_quote_script_guard.py` for the
static AST guard proving this file makes no such call anywhere (extends the same pattern as
`tests/hmt2/test_hmt2b1_quote_reproduction_script_guard.py`).

Requires a real, governed Databento historical API key at the default path
(`/srv-dev/secrets/databento_historical_api_key`, or `DATABENTO_HISTORICAL_API_KEY_PATH`) and
real network access to Databento's historical metadata API. Running this script again
reproduces the same request SHAPE (exact session-run/symbol groups) deterministically — the
real quoted USD figures could in principle differ slightly run-to-run per Databento's own
disclaimer that pricing/record counts are informational estimates, not a frozen guarantee; see
`research/hmt2/hmt2b1_reference_and_definition_quotes.py`'s own docstring for the identical
caveat on its two quotes.

Purpose boundary (repeated, because it matters): this is the pre-download quotation gate for a
POSSIBLE future MBP-1 pilot acquisition. It is NOT an acquisition. No MBP-1 data of any kind is
requested, transferred, or stored by this script.

Run from the repository root:

    python3 research/hmt2/hmt2c_mbp1_quote.py
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_THIS_DIR, "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from market_truth.acquisition import mbp1_quote_request as mqr  # noqa: E402
from market_truth.acquisition import session_calendar  # noqa: E402
from market_truth.acquisition.providers.databento_historical import (  # noqa: E402
    DatabentoHistoricalProvider,
    load_databento_api_key,
)

DATASET = "GLBX.MDP3"
SCHEMA = "mbp-1"

SESSION_CONTRACT_ACTIVITY_PATH = os.path.join(_THIS_DIR, "gc-session-contract-activity-v1.json")
MANIFEST_PATH = os.path.join(_THIS_DIR, "corpus-selection-manifest-v2.json")


def build_session_runs() -> tuple[mqr.SessionRun, ...]:
    """Pure (no network) request-construction step, split out from `main()` so it can be
    exercised deterministically by tests without touching the network."""
    activity = json.load(open(SESSION_CONTRACT_ACTIVITY_PATH, encoding="utf-8"))
    manifest = json.load(open(MANIFEST_PATH, encoding="utf-8"))

    universe_start = _dt.date.fromisoformat(manifest["metadata"]["session_universe_start"])
    universe_end = _dt.date.fromisoformat(manifest["metadata"]["session_universe_end"])
    full_calendar_dates = [
        record.session_date.isoformat()
        for record in session_calendar.build_session_universe(universe_start, universe_end)
    ]

    sessions = tuple(
        mqr.SessionActivity(
            session_id=s["session_id"],
            trade_date=s["trade_date"],
            request_start_utc=s["request_start_utc"],
            request_end_utc=s["request_end_utc"],
            active_raw_symbols=tuple(s["active_raw_symbols"]),
        )
        for s in activity["sessions"]
    )
    return mqr.group_sessions_into_contiguous_runs(sessions, full_calendar_dates)


def main() -> None:
    runs = build_session_runs()

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
                "start": run.start_utc,
                "end_exclusive": run.end_utc_exclusive,
                "session_count": run.session_count,
                "session_ids": list(run.session_ids),
                "symbol_count": run.symbol_count,
                "raw_symbols": list(run.raw_symbols),
                "record_count": record_count.record_count,
                "billable_size_bytes": billable_size.billable_size_bytes,
                "quoted_cost_usd": cost.quoted_cost_usd,
            }
        )

    print(
        json.dumps(
            {
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
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
