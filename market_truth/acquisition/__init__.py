"""HMT-2B — Provider Foundation (governed GC MBP-1 historical research corpus).

Builds on HMT-2A (session/calendar semantics, frozen scheduled-macro-event snapshot, frozen
selection algorithm, frozen manifest v1 — all UNCHANGED here). This checkpoint adds:

  - `providers/databento_historical.py` — a zero-spend Databento historical metadata adapter
    (symbology/definitions resolution + free cost/record-count/billable-size estimates only; no
    bulk data download, no live streaming client of any kind).
  - `source_store.py` — native source-store artefact model (structure/interface only — no real
    acquired data exists yet; every test exercises it against clearly-labelled synthetic
    placeholder records).
  - `lineage.py` — source→canonical lineage catalogue (same structure/interface-only status).

Zero real market-data acquisition. Zero spend. Zero live provider API calls that transfer any
billable historical data. See the HMT-2B WO final report for the Part 3 (volatility/compression
reference-series) investigation outcome and its consequences for manifest v2.

See docs/research/hmt2-corpus-selection-methodology-v1.md for the HMT-2A methodology and honest
evidence-tier disclosures (unchanged by this checkpoint).

HMT-2B.1 (first half) — resolved eligible-range extension
-------------------------------------------------------------
Adds, in this checkpoint:
  - `research/hmt2/scheduled-macro-event-snapshot-v2.json` — extended scheduled-macro-event
    reference snapshot (FOMC/CPI/NFP/PCE), honestly-disclosed coverage per class. Does NOT
    modify or replace v1 (v1 remains frozen evidence at its own 2025-12-31 cutoff).
  - The `HMT2B1_*` constants below — the single source of truth for the resolved final
    eligible-universe cutoff (WO Part 2) governing this checkpoint's Part 1/3/4 work ONLY.
    They do NOT alter HMT-2A's frozen `CORPUS_START`/`CORPUS_CUTOFF` constants in
    `research/hmt2/generate_selection_manifest.py` (still `2017-05-21`/`2025-12-31`, still
    governing the frozen manifest v1) — manifest v2's own eligible range is a follow-up-
    dispatch decision that will consume these constants, not redefine them independently.
  - A narrow, backward-compatible `stype_in` passthrough extension to
    `providers/databento_historical.py`'s three free metadata-estimate methods (documented in
    that module's own docstring) — required for a correct continuous-contract (Part 3) and
    parent-symbology (Part 4) quote. No new capability, credential path, or scope boundary.

Part 3 (`GC.v.0` continuous reference series) and Part 4 (`GC.FUT` parent definition schema)
quotes obtained under this cutoff are QUOTE-ONLY — see the HMT-2B.1 WO final report for the
exact real cost/record-count/billable-size figures. Zero acquisition, zero spend, in this
checkpoint.
"""
import datetime as _dt

# HMT-2B.1 Part 2 — resolved final eligible-universe cutoff (binding for Part 1/3/4 of this
# checkpoint only; see module docstring above). Computed via
# `market_truth.acquisition.session_calendar.build_session_universe()` — the last governed GC
# session whose UTC window had already fully closed at freeze time. session_calendar.py itself
# needed NO modification to compute this: every holiday/early-close rule in that module is
# parametrized by year (nth-weekday-of-month, Easter/Good-Friday, observed-fixed-holiday), not a
# bounded lookup table, so it already computes correctly for 2026 (and beyond) unmodified.
HMT2B1_ELIGIBLE_RANGE_START = "2017-05-21"
HMT2B1_RESOLVED_FINAL_CUTOFF_DATE = "2026-09-18"
HMT2B1_RESOLVED_FINAL_CUTOFF_SESSION_ID = "GC-2026-09-18"
HMT2B1_CUTOFF_FREEZE_UTC = "2026-09-21T15:11:09Z"
HMT2B1_CUTOFF_RESOLUTION_METHOD = (
    "market_truth.acquisition.session_calendar.build_session_universe(2017-05-21, ...) — last "
    "SessionRecord whose window_end_utc < freeze_utc, at freeze_utc = "
    "2026-09-21T15:11:09Z (a `date -u` / datetime.now(timezone.utc) capture on dell-debian "
    "during this checkpoint's live resolution run)."
)


def hmt2b1_resolved_cutoff_as_date() -> _dt.date:
    """Convenience accessor for `HMT2B1_RESOLVED_FINAL_CUTOFF_DATE` as a `datetime.date`."""
    return _dt.date.fromisoformat(HMT2B1_RESOLVED_FINAL_CUTOFF_DATE)
