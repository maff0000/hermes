#!/usr/bin/env python3
"""HMT-2B.1 Part 3/4 — reproduces the real, free Databento metadata quotes this checkpoint
obtained. QUOTE-ONLY: this script calls exactly three free, informational, metadata-only
methods on DatabentoHistoricalProvider (`get_cost_estimate`, `get_record_count_estimate`,
`get_billable_size_estimate`) for each of the two specified requests below. It NEVER calls
`download_historical_range()`, never references `.get_range`/`.Live`, and never acquires any
actual historical data — see tests/hmt2/test_hmt2b1_quote_reproduction_script_guard.py for the
static AST guard proving this file makes no such call anywhere.

Requires a real, governed Databento historical API key at the default path
(`/srv-dev/secrets/databento_historical_api_key`, or `DATABENTO_HISTORICAL_API_KEY_PATH`) and
real network access to Databento's historical metadata API. Running this script again
reproduces the same exact quote figures reported in the HMT-2B.1 WO final report and recorded
in docs/research/hmt2-corpus-selection-methodology-v1.md, AS OF THE TIME IT IS RUN — Databento's
own free/informational pricing schedule and this dataset's exact record/byte counts could in
principle change between runs; this script does not cache or freeze a result, it always asks
the real provider.

Run from the repository root:

    python3 research/hmt2/hmt2b1_reference_and_definition_quotes.py

Purpose boundary (repeated, because it matters): the Part 3 request (GC.v.0 continuous,
ohlcv-1h) is corpus-selection metadata ONLY — permanently NOT canonical GC Market Truth v2,
NOT the P0 corpus, NOT a derived HERMES microstructure fact, NOT a DARWIN input, NOT a trading
feature/signal. The Part 4 request (GC.FUT parent, definition schema) is preparatory scoping
for a later outright-vs-spread contract-identity verification capability; the actual
definitions-fetching capability is explicitly out of scope for this checkpoint (WO Part 4).
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

from market_truth.acquisition import HMT2B1_ELIGIBLE_RANGE_START, HMT2B1_RESOLVED_FINAL_CUTOFF_DATE  # noqa: E402
from market_truth.acquisition.providers.databento_historical import (  # noqa: E402
    DatabentoHistoricalProvider,
    load_databento_api_key,
)

DATASET = "GLBX.MDP3"
START = HMT2B1_ELIGIBLE_RANGE_START
# Databento's `metadata.get_cost`/`get_record_count`/`get_billable_size` treat `end` as
# EXCLUSIVE (empirically confirmed live: end=2026-09-18 -> 55137 GC.v.0 ohlcv-1h records,
# end=2026-09-19 -> 55158 — the 2026-09-18 session itself was missing at the naive value). The
# WO's cutoff is INCLUSIVE of the resolved cutoff date, so this script passes cutoff + 1 day as
# `end` to genuinely include the cutoff session.
_CUTOFF_DATE = _dt.date.fromisoformat(HMT2B1_RESOLVED_FINAL_CUTOFF_DATE)
END = (_CUTOFF_DATE + _dt.timedelta(days=1)).isoformat()


def _estimate_to_dict(cost, record_count, billable_size) -> dict:
    return {
        "dataset": cost.dataset,
        "schema": cost.schema,
        "symbols": list(cost.symbols),
        "stype_in": cost.stype_in,
        "start": cost.start,
        "end_exclusive": cost.end,
        "eligible_cutoff_date_inclusive": HMT2B1_RESOLVED_FINAL_CUTOFF_DATE,
        "quoted_cost_usd": cost.quoted_cost_usd,
        "record_count": record_count.record_count,
        "billable_size_bytes": billable_size.billable_size_bytes,
    }


def main() -> None:
    api_key = load_databento_api_key()
    provider = DatabentoHistoricalProvider(api_key=api_key)

    # Part 3 — reference-series (corpus-selection metadata ONLY, see module docstring).
    part3 = _estimate_to_dict(
        provider.get_cost_estimate(
            dataset=DATASET, schema="ohlcv-1h", symbols=["GC.v.0"], start=START, end=END,
            stype_in="continuous",
        ),
        provider.get_record_count_estimate(
            dataset=DATASET, schema="ohlcv-1h", symbols=["GC.v.0"], start=START, end=END,
            stype_in="continuous",
        ),
        provider.get_billable_size_estimate(
            dataset=DATASET, schema="ohlcv-1h", symbols=["GC.v.0"], start=START, end=END,
            stype_in="continuous",
        ),
    )

    # Part 4 — definition-data quote (preparatory scoping ONLY, see module docstring).
    part4 = _estimate_to_dict(
        provider.get_cost_estimate(
            dataset=DATASET, schema="definition", symbols=["GC.FUT"], start=START, end=END,
            stype_in="parent",
        ),
        provider.get_record_count_estimate(
            dataset=DATASET, schema="definition", symbols=["GC.FUT"], start=START, end=END,
            stype_in="parent",
        ),
        provider.get_billable_size_estimate(
            dataset=DATASET, schema="definition", symbols=["GC.FUT"], start=START, end=END,
            stype_in="parent",
        ),
    )

    print(json.dumps({"part3_reference_series_quote": part3, "part4_definition_data_quote": part4}, indent=2))


if __name__ == "__main__":
    main()
