# HMT-0 — GC data volume and cost study

**Initiative:** `HMT-0 — Market Truth v2 Architecture Closure`
**Status:** DOCUMENTATION / ARCHITECTURE ONLY. See [`hmt0-closure-report.md`](hmt0-closure-report.md).

## Purpose

This document is deliberately structured as a **template** so that real Databento measurement data — once
Helm (or whoever performs the empirical measurement) returns it — can be dropped directly into the tables
below without restructuring this document. Per this pack's binding evidence discipline: where real
measurement data does not exist yet, this document uses the literal marker `PENDING_EVIDENCE` rather than
an invented, plausible-sounding number. No number in this document should be read as a real estimate.

## §1 — What needs to be measured, and why

The single open decision this study exists to support is the TBBO-vs-MBP-1 native-corpus choice deferred
in `canonical-market-events.md` §3 (`PENDING_EMPIRICAL_TBBO_VS_MBP1_MEASUREMENT`). That decision, in turn,
is gated by the build-v-rent hybrid ruling (`licensing-and-security.md` §1): permanent retention of the
native GC P0 source corpus requires empirical size/cost to genuinely support it, in addition to written
licensing permission. This document is the size/cost half of that gate.

## §2 — Measurement matrix (template — every cell `PENDING_EVIDENCE` until real data lands)

### 2.1 Raw data volume, by feed level and horizon

| Feed level | 1 day (GC, all sessions) | 1 month | 1 year | Full available history (per era, §2.3) |
|---|---|---|---|---|
| Trades | `PENDING_EVIDENCE` | `PENDING_EVIDENCE` | `PENDING_EVIDENCE` | `PENDING_EVIDENCE` |
| TBBO | `PENDING_EVIDENCE` | `PENDING_EVIDENCE` | `PENDING_EVIDENCE` | `PENDING_EVIDENCE` |
| MBP-1 | `PENDING_EVIDENCE` | `PENDING_EVIDENCE` | `PENDING_EVIDENCE` | `PENDING_EVIDENCE` |

Units, compression assumptions, and measurement methodology are themselves `PENDING_EVIDENCE` — this
table's column/row shape is fixed so a later revision only needs to fill cells, not redesign the table.

### 2.2 Acquisition cost, by feed level and horizon

| Feed level | One-time historical backfill cost | Ongoing live-subscription cost (monthly) |
|---|---|---|
| Trades | `PENDING_EVIDENCE` | `PENDING_EVIDENCE` |
| TBBO | `PENDING_EVIDENCE` | `PENDING_EVIDENCE` |
| MBP-1 | `PENDING_EVIDENCE` | `PENDING_EVIDENCE` |

### 2.3 Volume/cost by historical provenance era

Cross-referenced from `source-taxonomy.md` §1.3 / `time-order-sequence-model.md` §3 — the three
provenance eras (`PRE_2015_11_20_LEGACY`, `2015_11_20_TO_2017_05_20_LEGACY`, `MDP3_FROM_2017_05_21`) may
carry materially different acquisition cost/availability characteristics from Databento, and this table
exists so that difference is measured explicitly rather than assumed uniform.

| Era | Available from Databento? | Volume | Cost |
|---|---|---|---|
| `PRE_2015_11_20_LEGACY` | `PENDING_EVIDENCE` | `PENDING_EVIDENCE` | `PENDING_EVIDENCE` |
| `2015_11_20_TO_2017_05_20_LEGACY` | `PENDING_EVIDENCE` | `PENDING_EVIDENCE` | `PENDING_EVIDENCE` |
| `MDP3_FROM_2017_05_21` | `PENDING_EVIDENCE` | `PENDING_EVIDENCE` | `PENDING_EVIDENCE` |

### 2.4 Storage-tier cost implications

For each of the three storage tiers in `data-lifecycle-and-storage.md` §1, the incremental
infrastructure cost of holding the volumes in §2.1:

| Tier | Storage medium (proposed) | Estimated cost | Status |
|---|---|---|---|
| OPERATIONAL TRUTH | existing MariaDB/Redis (bounded depth) | `PENDING_EVIDENCE` | depends on §2.1 |
| MARKET RESEARCH STORE | object/file storage, Parquet/Zstd-partitioned | `PENDING_EVIDENCE` | depends on §2.1 |
| EVIDENCE VAULT | immutable promoted subset only (small relative to §1.2) | `PENDING_EVIDENCE` | depends on volume of *promoted* facts, not raw corpus — likely far smaller than §2.1's totals, but this is itself `PENDING_EVIDENCE`, not assumed |

## §3 — Decision criteria (already ruled — restated here for context, not re-derived)

Per `licensing-and-security.md` §1 (Central Architecture's hybrid build-v-rent ruling, restated in full
there, not re-litigated here): permanent retention of the native GC P0 source corpus requires **both**
(1) empirical size/cost from this document actually supporting permanent retention, **and** (2) written
licensing explicitly permitting it. Neither condition is met by this document alone — this document can
only ever supply condition (1)'s evidence, and only once real measurement replaces the `PENDING_EVIDENCE`
markers above.

## §4 — What this document does not do

Does not estimate, guess, or interpolate a number for any `PENDING_EVIDENCE` cell above, under any
circumstance — including by analogy to other instruments' typical tick-data volumes, other vendors'
published pricing, or back-of-envelope reasoning from message-rate assumptions. That kind of
plausible-sounding invented number is explicitly named in this pack's binding brief as worse than an
honest gap, because it could be mistaken for real evidence by a future HMT-1 author. Does not choose a
feed level — that is `canonical-market-events.md` §3's decision, informed by, but not concluded within,
this document once real data lands here.
