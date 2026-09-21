# HMT-0 — GC data volume and cost study

**Initiative:** `HMT-0 — Market Truth v2 Architecture Closure`
**Status:** DOCUMENTATION / ARCHITECTURE ONLY. See [`hmt0-closure-report.md`](hmt0-closure-report.md).

## Purpose

This document was originally structured as a **template**, every cell `PENDING_EVIDENCE`, so that real
Databento measurement data could be dropped directly into the tables below without restructuring the
document. That measurement has now landed (produced by Helm, delivered via Fabric, independently
cross-validated by Rogue against the directive) and is populated below. Per this pack's binding evidence
discipline, the original template rule still governs anything genuinely unmeasured: where real
measurement data does not exist, this document uses a marker rather than an invented, plausible-sounding
number — either `PENDING_EVIDENCE` (a genuinely open question) or `NOT_MEASURED / NOT_REQUIRED_FOR_HMT0`
(a cell that was never required for HMT-0's own ruling, see §2.1/§2.2/§2.4 below). No number in this
document beyond what is explicitly reported below should be read as a real estimate.

## §1 — What needed to be measured, and why (now measured)

The single open decision this study existed to support was the TBBO-vs-MBP-1 native-corpus choice
previously deferred in `canonical-market-events.md` §3 as `PENDING_EMPIRICAL_TBBO_VS_MBP1_MEASUREMENT`.
That measurement now exists (§2 below) and Central Architecture has ruled on it: **the P0 native corpus
is MBP-1** (see `canonical-market-events.md` §3 for the ruling and its rationale). That decision was
informed by the build-v-rent hybrid ruling (`licensing-and-security.md` §1): empirical size/cost
genuinely supporting permanent retention was the engineering-relevant condition this document exists to
supply evidence for, and it is now satisfied (§2 below). Per Central Architecture's separate ruling,
licensing/commercial correspondence is external programme administration and does not gate HERMES's
engineering capability — see `licensing-and-security.md` for the full restatement of that boundary. This
document supplies the empirical size/cost evidence only; it does not, and need not, address licensing.

## §2 — Measurement matrix (real data below; cells still genuinely unmeasured remain `PENDING_EVIDENCE`
or, where non-gating, `NOT_MEASURED / NOT_REQUIRED_FOR_HMT0`)

**Measurement scope (applies to every populated cell in this section):** 120 verified GC outright futures
contracts only (spreads excluded; methodology: `symbology.resolve` per-bucket outright/spread split,
cross-validated against Databento's own `instrument_class=F` definition-schema field across 4
representative windows spanning all eras — zero mismatches). Historical interval measured:
`2010-06-06T00:00:00Z` → `2026-09-19T00:00:00Z`. These are the **authoritative, outright-only** figures.
The prior spread-inclusive full-history figures are retained, clearly labelled as superseded, in §2.5.

### 2.1 Raw data volume, by feed level and horizon

| Feed level | 1 day (GC, all sessions) | 1 month | 1 year | Full available history (per era, §2.3) |
|---|---|---|---|---|
| Trades | `NOT_MEASURED / NOT_REQUIRED_FOR_HMT0` | `NOT_MEASURED / NOT_REQUIRED_FOR_HMT0` | `NOT_MEASURED / NOT_REQUIRED_FOR_HMT0` | `NOT_MEASURED / NOT_REQUIRED_FOR_HMT0` |
| TBBO | `NOT_MEASURED / NOT_REQUIRED_FOR_HMT0` | `NOT_MEASURED / NOT_REQUIRED_FOR_HMT0` | `NOT_MEASURED / NOT_REQUIRED_FOR_HMT0` | **422,689,297 records / 33,815,143,760 bytes (~33.815 GB)** — outright-only, measured, full interval above |
| MBP-1 | `NOT_MEASURED / NOT_REQUIRED_FOR_HMT0` | `NOT_MEASURED / NOT_REQUIRED_FOR_HMT0` | `NOT_MEASURED / NOT_REQUIRED_FOR_HMT0` | **4,324,008,111 records / 345,920,648,880 bytes (~345.921 GB)** — outright-only, measured, full interval above |

Trades-level volume, and the 1-day/1-month/1-year horizons for every feed level, were not part of this
measurement exercise. They are marked `NOT_MEASURED / NOT_REQUIRED_FOR_HMT0` rather than `PENDING_EVIDENCE`
because they are planning-granularity data superseded by the full-history MBP-1 measurement that actually
grounds the P0 corpus ruling — none of these cells were ever required for that ruling, only nice-to-have
granularity. Do not infer them from the full-history figures above by division or any other estimation.

### 2.2 Acquisition cost, by feed level and horizon

| Feed level | One-time historical backfill cost | Ongoing live-subscription cost (monthly) |
|---|---|---|
| Trades | `NOT_MEASURED / NOT_REQUIRED_FOR_HMT0` | `NOT_MEASURED / NOT_REQUIRED_FOR_HMT0` |
| TBBO | **$881.798589** — outright-only, full interval above, quoted cost | `NOT_MEASURED / NOT_REQUIRED_FOR_HMT0` |
| MBP-1 | **$579.894677** — outright-only, full interval above, quoted cost | `NOT_MEASURED / NOT_REQUIRED_FOR_HMT0` |

Reconfirmed at outright-only precision: MBP-1 remains **cheaper in dollars** than TBBO ($579.89 vs
$881.80) despite ~10.2x more records (4,324,008,111 vs 422,689,297 records). This cost-model quirk was
also observed in the earlier, spread-inclusive study (§2.5) and is robust to the outright/spread
distinction — it is not a spread-inclusion artifact. This document states the measured fact only and does
not speculate about Databento's pricing mechanics beyond it. Ongoing live-subscription monthly cost was
not part of this measurement; it is marked `NOT_MEASURED / NOT_REQUIRED_FOR_HMT0` because it is
planning-granularity data not required for the P0 corpus ruling, which rests on the one-time historical
acquisition cost already measured above.

### 2.3 Volume/cost by historical provenance era

Cross-referenced from `source-taxonomy.md` §1.3 / `time-order-sequence-model.md` §3 — the three
provenance eras (`PRE_2015_11_20_LEGACY`, `2015_11_20_TO_2017_05_20_LEGACY`, `MDP3_FROM_2017_05_21`) carry
materially different volume/cost characteristics from Databento, measured explicitly below rather than
assumed uniform. Figures are outright-only; era sums equal the §2.1/§2.2 totals exactly for both schemas
(cross-validated).

| Era | Available from Databento? | Volume | Cost |
|---|---|---|---|
| `PRE_2015_11_20_LEGACY` | Yes (measured) | TBBO: 137,317,880 records / 10,985,430,400 bytes; MBP-1: 565,080,777 records / 45,206,462,160 bytes | TBBO: $286.467421; MBP-1: $75.783238 |
| `2015_11_20_TO_2017_05_20_LEGACY` | Yes (measured) | TBBO: 29,808,714 records / 2,384,697,120 bytes; MBP-1: 314,293,111 records / 25,143,448,880 bytes | TBBO: $62.185823; MBP-1: $42.149991 |
| `MDP3_FROM_2017_05_21` | Yes (measured) | TBBO: 255,562,703 records / 20,445,016,240 bytes; MBP-1: 3,444,634,223 records / 275,570,737,840 bytes | TBBO: $533.145345; MBP-1: $461.961448 |

**Quality note (era-boundary corroboration — see `time-order-sequence-model.md` §3 for the full
statement):** the `MDP3_FROM_2017_05_21` era boundary is independently corroborated by Databento's own
`mbo`/`cmbp-1`/`cbbo-*` schema-availability boundary (those schemas only exist from 2017-05-21 onward).
The `PRE_2015_11_20_LEGACY` / `2015_11_20_TO_2017_05_20_LEGACY` boundary (2015-11-20) is **not**
independently corroborated by any Databento-native schema-availability flag observed in this study — it
corresponds instead to CME's own nanosecond-resolution timestamp introduction on that date (a
resolution change, not a schema-availability change). This volume/cost measurement does not itself
confirm or deny per-record timestamp/provenance quality for any era — that is a separate question, now
resolved in `time-order-sequence-model.md` §3 (per Databento's official CME GLBX.MDP3 documentation), not
re-derived by this document.

### 2.4 Storage-tier cost implications

For each of the three storage tiers in `data-lifecycle-and-storage.md` §1, the incremental
infrastructure cost of holding the volumes in §2.1:

| Tier | Storage medium (proposed) | Estimated cost | Status |
|---|---|---|---|
| OPERATIONAL TRUTH | existing MariaDB/Redis (bounded depth) | `NOT_MEASURED / NOT_REQUIRED_FOR_HMT0` | not required for HMT-0 closure — this tier holds bounded live/current depth, not the full raw corpus this study measured, so its cost depends on future operational sizing decisions, not this document's evidence |
| MARKET RESEARCH STORE | object/file storage, Parquet/Zstd-partitioned | `NOT_MEASURED / NOT_REQUIRED_FOR_HMT0` | raw corpus **volume** is now known (MBP-1 outright-only full history ≈ 345.9 GB, §2.1) but the storage-medium **$/GB infrastructure rate** was not part of this measurement and is not required for the P0 corpus ruling — this is distinct from, and must not be conflated with, the Databento acquisition cost in §2.2 |
| EVIDENCE VAULT | immutable promoted subset only (small relative to §1.2) | `NOT_MEASURED / NOT_REQUIRED_FOR_HMT0` | not required for HMT-0 closure — depends on the volume of *promoted* facts, a future quantity unrelated to the raw corpus this study measured |

### 2.5 Outright-only vs. spread-inclusive comparison (prior figures — superseded, retained as historical/conservative evidence)

An earlier, less-precise measurement covered the full history **inclusive of GC calendar-spread
symbols**. That measurement is **superseded by** the outright-only figures in §2.1/§2.2/§2.3 above as the
authoritative P0-corpus estimate, but is retained here, clearly labelled, as historical/conservative
evidence — not deleted:

| Schema | Prior (spread-inclusive, superseded) | Current (outright-only, authoritative) | Spread inflation |
|---|---|---|---|
| TBBO | 441,780,876 records / 35,342,470,080 bytes / $921.626726 | 422,689,297 records / 33,815,143,760 bytes / $881.798589 | +4.517% (records/bytes/cost — cost scales linearly with record count for TBBO, no nonlinearity) |
| MBP-1 | 4,554,289,985 records / 364,343,198,800 bytes / $610.777883 | 4,324,008,111 records / 345,920,648,880 bytes / $579.894677 | +5.325% (records/bytes/cost, also linear) |

**Interpretation:** despite spread symbols vastly outnumbering outright symbols per bucket, spread
trading *activity* contributes only ~4.5–5.3% of total volume — the prior spread-inclusive study was a
modest (not severe) overstatement of the true outright-only P0 corpus size/cost.

## §3 — Decision criteria (ruled — restated here for context, not re-derived)

Per `licensing-and-security.md` §1 (Central Architecture's hybrid build-v-rent ruling, restated in full
there, not re-litigated here): permanent retention of the native GC P0 source corpus requires empirical
size/cost to genuinely support it. **That condition is now satisfied** — the measurement in §2 above
supports permanent retention (manageable ~345.9 GB storage; acquisition cost for MBP-1 is actually below
TBBO's for the same complete history) — and Central Architecture's ruling in `canonical-market-events.md`
§3 reflects that. Per Central Architecture's separate ruling on licensing, licensing/commercial
correspondence is external programme administration — it does not gate HERMES's engineering capability
and is not a condition this document, or any engineering-closure document in this pack, needs to satisfy
(see `licensing-and-security.md` for the full restatement). This document supplies the empirical
size/cost evidence that grounds the engineering ruling; it says nothing about, and need not address,
separately-administered commercial/licensing arrangements.

## §4 — What this document does not do

Does not estimate, guess, or interpolate a number for any `PENDING_EVIDENCE` or
`NOT_MEASURED / NOT_REQUIRED_FOR_HMT0` cell above, under any circumstance — including by analogy to
other instruments' typical tick-data volumes, other vendors' published pricing, back-of-envelope
reasoning from message-rate assumptions, or extrapolation/division from the full-history figures now
populated in §2 (e.g. no invented "per day" or "per month" figure, no invented storage-tier $/GB rate, no
invented live-subscription monthly cost, no invented Trades-level volume/cost). That kind of
plausible-sounding invented number is explicitly named in this pack's binding brief as worse than an
honest gap, because it could be mistaken for real evidence by a future HMT-1 author. Does not itself
choose a feed level — the ruling was made in `canonical-market-events.md` §3, informed by, but stated in,
that document, not this one. Does not address separately-administered licensing/commercial
correspondence (`licensing-and-security.md`) — that is external programme administration, not an
engineering question this document, or this pack, gates on. The `PRE_2015_11_20_LEGACY` per-record
timestamp/provenance question is resolved in `time-order-sequence-model.md` §3 (per Databento's official
CME GLBX.MDP3 documentation) — a different question from, and not settled by, the volume/cost evidence
supplied here.
