# HMT-2 (real-money checkpoint) — Corpus Selection Methodology v2 Addendum

**Status: this addendum documents the FIRST real, billable Databento acquisition in the HMT-2
programme.** Everything in `docs/research/hmt2-corpus-selection-methodology-v1.md` remains
true and unchanged for v1's own frozen manifest (`research/hmt2/corpus-selection-manifest-v1.json`,
hash `f6df90058caa5bf769448b1993371059a0ff074ee77b54c2c835e3d87ed4c44c` — reconfirmed unchanged
by this checkpoint, byte-for-byte). This document covers ONLY what is new: the two real
acquisitions, the real reference-series/definitions processing they made possible, and the
resulting v2 manifest.

---

## 1. The two real, authorised acquisitions

Central PO / Chief Architect explicitly authorised exactly two, and only two, real billable
Databento requests, both against the governed range `2017-05-21`..`2026-09-18` (inclusive
cutoff; passed to the vendor as `end=2026-09-19`, exclusive — the same empirically-confirmed
end-exclusive behaviour `research/hmt2/hmt2b1_reference_and_definition_quotes.py` already
documented).

| | Reference series | GC definitions |
|---|---|---|
| Dataset | `GLBX.MDP3` | `GLBX.MDP3` |
| Symbols | `GC.v.0` | `GC.FUT` |
| `stype_in` | `continuous` | `parent` |
| Schema | `ohlcv-1h` | `definition` |
| Quoted cost (reconfirmed immediately pre-acquisition) | $0.546575635672 | $1.692707203329 |
| Actual cost | identical (see §1.1) | identical (see §1.1) |
| Record count | 55,158 (exact quote match) | 2,056,030 (70,016 outright `"F"` + 1,986,014 spread/other `"S"`) |
| Native artefact | `research-source/hmt2-gc-mbp1-v1/sessions/HMT2-REAL-ACQ-REFERENCE-SERIES-GC-V0-OHLCV1H/source/gc_v0_ohlcv1h_2017-05-21_2026-09-19.dbn.zst` | `research-source/hmt2-gc-mbp1-v1/sessions/HMT2-REAL-ACQ-GC-FUT-DEFINITIONS/definitions/gc_fut_definitions_2017-05-21_2026-09-19.dbn.zst` |

Both artefacts are retained, immutable, real vendor bytes (never committed to git —
`research-source/` is local/disposable per `market_truth/acquisition/source_store.py`'s own
documented layout; see the WO final report for their SHA-256 hashes and exact byte sizes).
Evidence JSON for both (request identity, dataset/schema/range, byte size, SHA-256, record
count, quoted/actual cost, acquisition UTC, library version, quality conditions) is retained
alongside them via `market_truth.acquisition.source_store.NativeSourceStore`.

### 1.1 Cost note — no separate "actual" cost readout available

The installed Databento SDK (neither 0.42.0 nor the upgraded 0.86.0 — see §1.2) exposes a
post-transfer usage/billing-history endpoint in this environment. Databento's historical
pricing is a flat, deterministic function of dataset/schema/symbols/stype_in/date-range, so
`actual_cost_usd` is recorded as identical to the quote reconfirmed immediately before each
real request executed. This is a disclosed limitation, not a claim of independent
verification.

### 1.2 Mid-acquisition compatibility fix — `databento` 0.42.0 → 0.86.0

The reference-series acquisition, run first under the previously-pinned `databento==0.42.0`,
transferred successfully (the real, billable HTTP request completed and the raw bytes were
retained to disk) but the installed client's DBN decoder (version 2) could not decode the
response body: the live `GLBX.MDP3` `ohlcv-1h` stream for `GC.v.0` is now emitted in DBN
encoding version 3. This was discovered AFTER the real transfer had already completed — the
vendor bills on data transfer, not on local decode success — so the already-retained bytes
were kept (never re-requested; immutability discipline: a native artefact, once retained, is
never re-acquired to "fix" a client-side problem). `requirements.txt`'s `databento` pin was
bumped to `0.86.0` (empirically verified: correctly decodes the already-retained file;
identical `Historical`/`timeseries.get_range`/`metadata.get_cost` call surface; the free-quote
script and the full `tests/hmt2/` suite were both re-run clean under 0.86.0) BEFORE the second
(definitions) acquisition was attempted for real.

---

## 2. Reference-series processing — frozen methodology

Implemented in `market_truth/acquisition/reference_series.py` (pure, network-free, unit-tested
against synthetic fixtures — see `tests/hmt2/test_reference_series.py`) BEFORE this checkpoint
ever ran it against the real retained data. Run exactly once against the real data via
`research/hmt2/generate_reference_series_v2.py`; the numbers below are whatever that one run
produced — no tuning after seeing results.

- **Session mapping:** each real hourly bar (`ts_event`, `high`, `low`, `instrument_id`) is
  assigned to the (at most one) governed GC session — from
  `market_truth.acquisition.session_calendar.build_session_universe()`, reused unmodified —
  whose `[window_start_utc, window_end_utc)` UTC window contains it.
- **Metric:** `session_log_range = ln(max(hourly highs) / min(hourly lows))` over a session's
  assigned bars. NOT close-to-close return. NOT MBP-1/TBBO-derived (none acquired).
- **Reference-quality tag**, per session:
  - `REFERENCE_MISSING` — zero bars.
  - `REFERENCE_ROLL_AMBIGUOUS` — more than one distinct `instrument_id` among the session's
    bars (the continuous series rolled mid-session). Never spliced; excluded from candidacy.
  - `REFERENCE_COMPLETE` — single instrument, `coverage_ratio >= 0.90` (frozen constant
    `MIN_COMPLETE_COVERAGE_RATIO`).
  - `REFERENCE_PARTIAL` — single instrument, `coverage_ratio < 0.90`, at least one bar. Gaps
    are never interpolated.
- **Candidacy:** only `REFERENCE_COMPLETE` sessions, not already claimed by
  `PROTECTED_HOLDOUT`/`SCHEDULED_EVENT`/`MATCHED_CONTROL`, are eligible.
- **Selection rule (frozen constant `CANDIDATE_FRACTION = 0.20`):** within each calendar year
  separately, eligible sessions are sorted ascending by `(session_log_range, session_date)`;
  `k = floor(eligible_count_in_year * 0.20)`; the bottom `k` (by this sort) are
  `COMPRESSION` candidates, the top `k` are `HIGH_VOL_NON_EVENT` candidates. A year too small
  to produce even one candidate (`k == 0`) contributes zero, disclosed, not forced. Tie-break
  at a percentile boundary is the ascending-date order already baked into the sort key — the
  earlier-dated session of an exact-value tie sorts toward the bottom (compression) side.

### 2.1 Real results

Real per-tag counts over the 2,346-session governed universe (2017-05-21..2026-09-18):
`REFERENCE_COMPLETE` = 2,308; `REFERENCE_PARTIAL` = 4; `REFERENCE_MISSING` = 0;
`REFERENCE_ROLL_AMBIGUOUS` = 34. Orphan bar count: 1,313 (real bars whose `ts_event` fell
outside every session window — recorded, never fabricated into a session).

**CORRECTION (superseding an earlier, incomplete same-checkpoint dispatch of this
methodology):** an earlier draft of this document (and the uncommitted code it described)
treated the raw per-year top/bottom-20%-within-year `HIGH_VOL_NON_EVENT`/`COMPRESSION`
candidate POOLS — several hundred sessions each, since `floor(N*0.20)` over a ~200+-session
eligible population per year, across ten years, mechanically produces that many — as the FINAL
paid selection. This was wrong: §5a below describes the corrected design, in which those pools
are candidate pools ONLY, reduced to a final ~70-each sample via year-stratified Hamilton
allocation plus a deterministic seeded draw. The real candidate-pool size feeding that
allocation (after excluding everything already claimed by `PROTECTED_HOLDOUT`/
`SCHEDULED_EVENT`/`RANDOM_DEVELOPMENT`/`MATCHED_CONTROL`) was 397 for each of the two strata
in the real run this checkpoint executed.

---

## 3. GC definitions processing

Implemented in `market_truth/acquisition/gc_definitions.py` (pure, network-free, unit-tested —
`tests/hmt2/test_gc_definitions.py`), run once via `research/hmt2/generate_gc_definitions_v2.py`.

- **Outright/spread split:** Databento's real `definition`-schema `instrument_class` field —
  `"F"` denotes a genuine outright future (`OUTRIGHT_INSTRUMENT_CLASS`); every other value
  (calendar spreads, etc.) is not-outright. Confirmed against the real installed
  `databento_dbn` package's `InstrumentClass` enum (`FUTURE.value == "F"`) before this
  checkpoint ever ran against real data.
- **Contract-mapping table — CORRECTED after a real-data discovery.** Running
  `generate_gc_definitions_v2.py` against the real, retained artefact for the first time
  revealed that the real Databento outright `raw_symbol` wire form is
  `GC<month-code><ONE-digit-year-code>` (e.g. `GCG8`), NOT the two-digit-year shape (`GCZ26`)
  originally assumed — and that the one-digit form is genuinely, structurally ambiguous across
  decades: 27 of the 120 distinct real outright `raw_symbol` strings in this corpus's own
  ~9.3-year acquisition window denote TWO different real contracts roughly a decade apart
  (e.g. `GCF8` = delivery 2018 in one listing epoch, delivery 2028 in a later one), because a
  contract is listed in the definitions feed years before its own expiration.
  `DefinitionRecord` now also carries the real, structured, always-populated (0 nulls across
  all 70,016 real outright records) `maturity_year`/`maturity_month` fields, and
  `build_contract_mapping_entries()` uses THOSE as the authoritative delivery-year/month
  source — never the ambiguous wire-symbol year digit(s). The original two-digit regex parser
  (`parse_outright_symbol`) is kept, unchanged, purely as a fallback for a record with no
  maturity fields at all (e.g. a synthetic/legacy caller). A defensive month-code-letter
  cross-check (`parse_month_code_letter`) still catches a genuine symbol/maturity_month
  mismatch. `ContractMappingTable.entries` remains keyed by raw `raw_symbol` (an existing,
  unmodified `market_truth.futures` design choice, whose own module docstring already
  anticipated exactly this ambiguity) — so the 27 real, genuinely-conflicting symbols are
  caught by the existing `CONFLICTING_DUPLICATE_MAPPING` anomaly path and PERMANENTLY excluded
  from the table (a related latent bug — a later record for an already-poisoned symbol could
  previously slip back into the table — was found and fixed in the same pass; see
  `gc_definitions.py`'s own docstring and `tests/hmt2/test_gc_definitions.py`'s regression
  test). Cross-validated against the record's own `expiration` field exactly as before (a
  genuine outright expiring more than one year from its delivery year is flagged as an anomaly
  and excluded, never silently trusted).
- **Real results:** 2,056,030 total definition records (70,016 outright / 1,986,014
  spread/other). 120 distinct outright `raw_symbol` strings; 93 clean, unambiguous
  contract-mapping entries; 27 excluded as `CONFLICTING_DUPLICATE_MAPPING` (the real one-digit-
  year wire-form ambiguity described above) — zero other anomaly categories triggered in the
  real run.

---

## 4. `SCHEDULED_EVENT` — deterministic reselection (not a carry-forward of all 124 v1 sessions)

Per the architect's brief, v2 does NOT simply carry forward v1's 124 event sessions. Instead,
`market_truth/acquisition/corpus_selection_v2.py::stratified_scheduled_event_reselection()`
deterministically reselects ~20 sessions per event class (`FOMC_DECISION`, `CPI_RELEASE`,
`EMPLOYMENT_SITUATION_NFP`, `PCE_RELEASE`; target 80 total) from the now-complete
`scheduled-macro-event-snapshot-v2.json` (403 raw records, 403 unique dates, full coverage
2017-2026 for every class):

1. For each class, group its eligible (within the governed session universe) dates by
   calendar year, EXCLUDING any date already claimed by `PROTECTED_HOLDOUT` (see §6 — holdout
   is decided FIRST in v2 and is never displaced or duplicated; `exclude_dates=` is an
   additive, backward-compatible parameter on this function for exactly this purpose).
2. Compute a per-year quota: `base = 20 // N` for `N` years with eligible coverage;
   the remainder (`20 % N`) goes one-each to the **earliest** `remainder` years (a fixed,
   documented tie-break — this checkpoint's general convention of resolving ties toward the
   earlier date/year, applied here to year selection rather than session selection).
3. Within a (class, year) bucket, if fewer dates are available than the quota, take all of
   them (quota capped at availability, disclosed). Otherwise pick `quota` dates via
   deterministic even spacing: index `i` (0-indexed) picks the date at
   `(i * available_count) // quota` from the ascending-sorted candidate list — no randomness,
   no date-value-based cherry-picking.
4. Same-day multi-class collisions are merged into one row exactly as v1 already does
   (`select_scheduled_events()`, reused unmodified).

Real result: 79 `SCHEDULED_EVENT` rows (target ~80, indicative; one (class, year) bucket's
quota was not fully satisfiable at availability after routing around holdout — capped,
disclosed, not forced).

A holdout date that happens to coincide with an event-snapshot date is NOT double-counted as a
separate `SCHEDULED_EVENT` row — see §6's `secondary_context` mechanism.

---

## 5. Corrected precedence order (v2) — differs from v1

**CORRECTION (superseding the earlier, incomplete same-checkpoint dispatch):** an earlier
draft implemented `PROTECTED_HOLDOUT`/`RANDOM_DEVELOPMENT` invalidation using sets that
included mere classification-collision reasons (a holdout session invalidated for colliding
with the event snapshot; a random-development session invalidated for colliding with the
`HIGH_VOL_NON_EVENT`/`COMPRESSION` classification outcome) — this is exactly the "replace on
mere classification collision" behaviour the architect's brief prohibits, and is corrected
here. The corrected, final v2 precedence order (highest first) is:

1. **`PROTECTED_HOLDOUT`** — never displaced by anything (§6). Decided FIRST.
2. **`SCHEDULED_EVENT`** (§4) — reselected, routed around `PROTECTED_HOLDOUT`.
3. **`RANDOM_DEVELOPMENT`** (§6) — preserved from v1 with near-zero, reason-restricted churn.
   **NOTE: this is HIGHER precedence than `MATCHED_CONTROL`/`HIGH_VOL_NON_EVENT`/
   `COMPRESSION` in v2 — a deliberate change from v1's own order**, per the architect's brief.
4. **`MATCHED_CONTROL`** — one per final selected `SCHEDULED_EVENT` session,
   `market_truth.acquisition.corpus_selection.match_controls()` (reused unmodified) with its
   exclusion set = the FULL v2 event snapshot (403 dates — "no selected catalyst present" is
   read, as a disclosed judgment call, as "absent from the full snapshot", not merely "absent
   from the ~80 chosen event sessions": a control is meant to be a genuinely quiet day, and a
   real CPI/NFP/PCE/FOMC day that merely wasn't drawn into the corpus's own sample is still not
   quiet in reality) UNION `PROTECTED_HOLDOUT` UNION `RANDOM_DEVELOPMENT` (routes around both,
   never displaces either).
5. **`HIGH_VOL_NON_EVENT`** — excludes exactly
   `{PROTECTED_HOLDOUT, SCHEDULED_EVENT, RANDOM_DEVELOPMENT, MATCHED_CONTROL}`; final sample
   via Hamilton allocation + seeded draw (§5a).
6. **`COMPRESSION`** — same mechanic as #5, against the bottom-20%-within-year pool.

Real result: `MATCHED_CONTROL` = 79 (one per `SCHEDULED_EVENT` row, 0 unmatched).

---

## 5a. `HIGH_VOL_NON_EVENT`/`COMPRESSION` — candidate pool vs. final paid selection

The raw per-year top/bottom-20%-within-year pools (§2.1; 397 real candidates each, after
excluding everything claimed by strata 1-4) are candidate pools ONLY, never the final paid
strata. From each pool, a final target of **70** sessions is selected via:

1. **Hamilton (largest-remainder) year-stratified apportionment**
   (`corpus_selection_v2.hamilton_apportionment()`): for each year `y` with eligible-candidate
   count `C_y`, `raw_allocation_y = 70 * C_y / total_candidates`; `floor_allocation_y =
   floor(raw_allocation_y)`; the leftover seats (`70 - sum(floor)`) go one-at-a-time to the
   years with the largest fractional remainder, largest first, ties broken by ascending year.
   If genuinely fewer than 70 eligible candidates exist across all years combined, every
   candidate is allocated a seat and the shortfall is disclosed (`shortfall=true`) rather than
   pretending 70 was reached — did not occur in the real run (397 ≥ 70 for both strata).
2. **Deterministic domain-separated seeded draw**
   (`corpus_selection_v2.select_final_sample_by_year()`): ONE seeded `random.Random` per
   stratum, using NEW seed domains `HIGH_VOL_NON_EVENT_V2`/`COMPRESSION_V2` (same
   `SHA256("HERMES|HMT-2|GC-MBP1|corpus-selection-v1|{BASE_SHA}|{domain}")` construction as
   every other seed domain, same `BASE_SHA`) — visits years in ascending order, shuffles that
   year's sorted candidate ids, and keeps the first `final_allocation` of them. Deliberately
   NOT a selection by log-range magnitude — that would just re-select the most extreme members
   and defeat the purpose of sampling the classified regime broadly.

Full allocation tables (year, `C_y`, raw, floor, remainder, final) for both strata, and the
seed digests used, are recorded in `research/hmt2/corpus-selection-manifest-v2-allocation-
evidence.json` (a companion evidence file the manifest schema itself has no field for) and in
the WO final report. Real result: `HIGH_VOL_NON_EVENT` = 70, `COMPRESSION` = 70 (target
reached exactly for both).

---

## 6. `RANDOM_DEVELOPMENT`/`PROTECTED_HOLDOUT` — preservation and backfill (corrected)

`market_truth/acquisition/corpus_selection_v2.py::preserve_or_backfill()` remains, and always
was, a correct, generic, reason-agnostic primitive — the bug in the earlier draft was entirely
in what its CALLER passed as `invalid_if_in` for each stratum. Built on the additive
`corpus_selection.deterministic_shuffled_pool()` helper (does NOT change
`draw_random_development()`/`draw_protected_holdout()` — v1's manifest remains byte-
reproducible; reconfirmed by this checkpoint).

- **Reconstruction:** v1's own selection is reproduced exactly from its frozen inputs
  (`corpus-selection-manifest-v1.json`'s own 100 holdout / 50 random-development session
  dates), together with the FULL deterministic shuffled pool for each seed domain (not just
  the drawn 100/50) — `deterministic_shuffled_pool(...)[:count]` is proven, by construction and
  by a dedicated test (`tests/hmt2/test_deterministic_shuffled_pool.py`), to reproduce exactly
  v1's own draw.
- **`PROTECTED_HOLDOUT` validity (CORRECTED):** `invalid_if_in` is passed as an EMPTY set — a
  holdout session is NEVER displaced by a collision with anything (event snapshot, other
  strata, nothing). The only invalidation category available (`NOT_A_VALID_GOVERNED_SESSION_
  DATE`) fires only if a v1 holdout date somehow fell outside the v2 governed session universe
  — it did not, for any of the real 100 dates. A holdout date that coincides with an event-
  snapshot date instead gains a purely descriptive `secondary_context` tag (e.g. `"FOMC_
  DECISION"`) — never a change to `primary_stratum`, never a duplicate session entry, never
  trading-signal meaning. Real result: 100 kept, 0 invalidated, 0 backfilled. 14 holdout rows
  carry a `secondary_context` tag.
- **`RANDOM_DEVELOPMENT` validity (CORRECTED, restricted to exactly two reasons):** a v1
  random-development date is invalidated ONLY if (a) it is no longer a valid governed session
  date, or (b) it is now one of the FINAL selected `SCHEDULED_EVENT` sessions (an "ordinary
  baseline" day secretly turned out to be a selected catalyst day) — `invalid_if_in` is passed
  as exactly the final ~80 `SCHEDULED_EVENT` dates, nothing else. It is deliberately NEVER
  invalidated for colliding with the `HIGH_VOL_NON_EVENT`/`COMPRESSION` classification outcome
  (that set is not even computed yet at this point in the corrected pipeline order, by
  construction — see §5) — conditioning the random baseline on observed market behaviour is
  exactly what this rule exists to prevent. Backfill additionally routes around
  `PROTECTED_HOLDOUT` (never displacing it) without inventing a third invalidation reason.
  Real result: 45 kept, 5 invalidated (all for reason `NOW_A_FINAL_SELECTED_SCHEDULED_EVENT_
  SESSION`), 5 backfilled from the continuation of the same original v1 shuffled sequence.
- **Backfill mechanic (unchanged):** for each invalidated original, the NEXT not-yet-used,
  not-invalid candidate is drawn from the CONTINUATION of that domain's SAME original v1
  shuffled sequence (never a fresh draw, never a different seed).

---

## 7. Manifest v2

`research/hmt2/corpus-selection-manifest-v2.json` — schema `market_truth.acquisition.
corpus_manifest_v2` (additive; does not touch v1's `corpus_manifest.py` schema/hashing; gained
one additive `secondary_context` field this checkpoint — see §6). Records
`superseded_manifest_relative_path`/`superseded_manifest_sha256` pointing back to v1. Every
row's `selection_algorithm_version` is `hmt2-corpus-selection-v2`
(`corpus_selection_v2.CORPUS_SELECTION_V2_VERSION`) — v1 rows, unaffected, keep their own
`hmt2-corpus-selection-v1` value in the untouched v1 file.

**Real final counts:** `PROTECTED_HOLDOUT`=100, `SCHEDULED_EVENT`=79, `RANDOM_DEVELOPMENT`=50,
`MATCHED_CONTROL`=79, `HIGH_VOL_NON_EVENT`=70, `COMPRESSION`=70 — **448 total unique sessions**,
zero duplicates across strata (enforced by an explicit assertion in the generation script), 14
`secondary_context` tags.

Reproducibility: `research/hmt2/generate_selection_manifest_v2.py`, run twice from the same
repository state, produces byte-identical output for both the manifest and its companion
allocation-evidence file (verified this checkpoint;
`tests/hmt2/test_manifest_v2_generation.py::test_generation_script_is_byte_for_byte_reproducible`).

See the WO final report for v2's own `manifest_sha256`, the full Hamilton allocation tables,
and confirmation that v1's manifest file/hash and v1's event snapshot are both untouched.

---

## 8. Final all-outright MBP-1 quote resolution (checkpoint: quote only, zero MBP-1 acquired)

This checkpoint resolves the final, exact MBP-1 metadata quote request for the 448-session
manifest v2 above — the pre-download quotation gate for a possible future MBP-1 pilot
acquisition. **No MBP-1 (or any other schema's) data was acquired, downloaded, or transferred
at any point** — see `tests/hmt2/test_hmt2c_mbp1_quote_script_guard.py` for the static AST
guard proving `research/hmt2/hmt2c_mbp1_quote.py` calls only the three free metadata-estimate
methods.

### 8.1 Real activation/expiration windows (93 clean outright contracts)

`research/hmt2/generate_gc_outright_active_windows_v1.py` re-decodes the SAME already-
acquired, already-audited `GC.FUT` parent `definition`-schema native artefact §3 processed
(re-hashed and compared against that checkpoint's own recorded SHA-256 before proceeding — no
new acquisition of any kind), and extracts the real `activation`/`expiration` definition-schema
fields (confirmed real field names against the actual decoded dataframe columns) for each of
the 93 clean outright `provider_symbol`s in `gc-contract-mapping-table-v2.json`. Output:
`research/hmt2/gc-outright-active-windows-v1.json`.

**Real-data disclosure:** 92 of the 93 clean symbols have exactly one distinct
(activation, expiration) pair across every one of their own republished definition-schema rows;
`GCX1` has two, from a genuine, disclosed real vendor correction
(`security_update_action="M"`) that moved its expiration one hour earlier mid-life
(`2021-11-26T18:30:00Z` → `17:30:00Z`, activation unchanged). Resolved by taking the row with
the latest `ts_event` per symbol (last-write-wins) — see
`market_truth.acquisition.gc_active_windows` module docstring. This one-hour shift never
changes which calendar date a session falls on, so it has zero effect on any active/inactive
determination below.

### 8.2 Session-to-contract activity (research/hmt2/generate_session_contract_activity_v1.py)

For each of the 448 manifest rows, `market_truth.acquisition.gc_active_windows.
determine_active_contracts_for_sessions()` applies a straightforward overlap test — a
session's `[request_start_utc, request_end_utc)` window overlaps a contract's real
`[activation_utc, expiration_utc]` listing window — against the 93-contract table. Output:
`research/hmt2/gc-session-contract-activity-v1.json`.

**Real, checkpoint-discovered finding (important — read before interpreting §8.3's contract
count):** COMEX lists GC outright contracts many months, and in several observed cases multiple
YEARS, before their own delivery month (e.g. `GCZ6`, Dec-2026 delivery, activated
2020-12-30 — six years out). Consequently a typical session in this corpus has **~15
simultaneously-listed outright contracts** overlapping it (real range across the 448 sessions:
**2..25**; every one of the 448 sessions has 2 or more). This is a straightforward, literal
application of the WO's own specified filter ("is this session's date within this contract's
active listing window") — it is **not** a liquidity/"front-month"-only interpretation (which
would need real trading-volume data this checkpoint neither has nor was asked to compute), and
it diverges materially from the WO's own illustrative "front month, plus one more near a roll
date" framing. This is disclosed here plainly as the single largest judgment call in this
checkpoint's MBP-1 quote resolution — see the WO final report for the full discussion.

**Real result:** relevant outright contract count for this manifest = **93 of 93** (all of
them) — every one of the 93 clean contracts overlaps at least one of the 448 sessions somewhere
across the full 2017–2026 corpus range, contrary to the WO's own speculation that this would
"likely be fewer than 93." The cost-reduction this checkpoint's quote achieves versus quoting
each contract's own full multi-year real listed lifetime comes entirely from narrowing the
requested DATE COVERAGE per contract down to the exact sampled session windows — never from a
reduction in contract count.

### 8.3 Quote-request construction and grouping (research/hmt2/mbp1_quote_request.py)

Databento's free `metadata.get_cost`/`get_record_count`/`get_billable_size` endpoints each take
exactly one contiguous `[start, end)` range per call (confirmed against the installed SDK's own
source, not assumed) — no discontiguous multi-range parameter exists. The chosen approach is
explicitly **(b)** per the WO's own stated architect intent: quote exactly what would be
purchased for the sampled 448-session corpus, never each contract's own full real tradable-life
range (interpretation (a), which would be far more expensive and is not wanted).

Because the real `symbols` parameter accepts up to 2,000 symbols in a single call, this
checkpoint groups by **contiguous session-date RUN first** (using the real GC trading-session
calendar, `session_calendar.build_session_universe()`, to guarantee a run never spans a real
trading day this manifest did not select), then issues ONE call per run carrying the sorted
UNION of every contract active in any session within that run as its `symbols` list. This is
provably exact (never bills for a real trading day the manifest did not select; a symbol with
no real listed activity on some day inside its own run's span simply has zero real records
there — nothing is ever fabricated or inflated) while being far more call-efficient than a naive
per-(contract, date) grouping (measured this checkpoint: 5,375 groups per-contract vs. **359**
session-runs with the union-of-symbols strategy — a ~15x reduction).

### 8.4 Real quote result

`research/hmt2/hmt2c_mbp1_quote.py` — quote-only (three free metadata-estimate calls per run,
359 runs, dataset `GLBX.MDP3`, schema `mbp-1`, `stype_in="raw_symbol"`). Full request/result
detail: `research/hmt2/hmt2c-mbp1-quote-evidence-v1.json`.

**Real result:** 359 metadata-estimate call groups, 93 distinct contracts, **449,637,036
records**, **35,970,962,880 billable bytes** (≈33.5 GiB), **$60.301025569441016 quoted cost**.

**Running total projected HMT-2 spend:** reference-series actual ($0.546575635672) +
GC.FUT definitions actual ($1.692707203329) + this MBP-1 quote, not yet spent
($60.301025569441016) = **$62.540308408442016**, against the **$100** ceiling (**$37.46**
headroom remaining).
