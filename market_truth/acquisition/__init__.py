"""HMT-2B — Provider Foundation + Corrected Manifest v2 (governed GC MBP-1 historical research corpus).

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
"""
