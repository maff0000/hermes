# HMT-2A — Governed GC MBP-1 Historical Research Corpus: Selection Methodology v1

**Status: this document is committed BEFORE any MBP-1 (or any other real market) data for
this corpus exists.** HMT-2A performs zero real market-data acquisition, zero spend, and
zero live provider API calls of any kind — there is no MBP-1 outcome anywhere on Earth yet
for this corpus to have been influenced by. That is trivially true today, and this document
records it as a completed governance fact rather than a promise: the selection algorithm
below, the frozen scheduled-macro-event snapshot, and the deterministic seeds were all fixed
and this document committed strictly prior to, and independently of, any acquisition
checkpoint (HMT-2B/2C) that might later request a cost quote for the sessions this checkpoint
selects.

Scope boundary (repeated from the WO brief, because it matters for how to read this
document): this checkpoint is corpus **selection** only. No provider adapter, no quote
tooling, no source store, no lineage tool, no quality tool, and no actual tick/quote/trade
data of any kind (real or fabricated) exists anywhere in this checkpoint's code or data.

---

## 1. Eligible universe

- **Instrument:** CME/COMEX GC (100 oz gold futures), continuous session concept — the
  selection is over trading *sessions* (dates), not specific contract months. Contract-month
  resolution is explicitly out of scope for HMT-2A and is deferred to whichever checkpoint
  actually acquires data.
- **Corpus start:** `2017-05-21`. Fixed by the WO brief.
- **Corpus cutoff (chosen and justified in this checkpoint):** `2025-12-31`.
  - *Why this date:* it is a clean calendar-year boundary, roughly nine months before this
    checkpoint's actual build date (2026-09-21), so every session in range is unambiguously
    historical — no partial year, no data still subject to near-term revision cycles, and no
    proximity to "live" market conditions. It also cleanly excludes the still-in-progress 2026
    calendar year, keeping the corpus's outer edge a full, complete year rather than an
    arbitrary mid-year cutoff.
- **Session definition:** a governed CME/COMEX GC exchange trading session, NOT an arbitrary
  UTC calendar day. See `market_truth/acquisition/session_calendar.py` for the full rule-set
  and its evidence-tier disclosures (summarised in §2 below). Every session carries an
  absolute UTC acquisition window (DST-aware, derived from America/Chicago trading hours) —
  provider requests are always absolute UTC intervals, never naive calendar days.
- **Session universe size in this corpus:** computed programmatically by
  `market_truth.acquisition.session_calendar.build_session_universe(2017-05-21, 2025-12-31)`;
  see the WO final report for the exact count from an actual run.

## 2. Session/calendar semantics — evidence tiers (full detail in the module docstring)

| Rule | Evidence tier | Summary |
|---|---|---|
| Weekend exclusion | Definitional | Saturdays/Sundays are never sessions. |
| Standard US federal holidays (New Year's, MLK, Presidents, Memorial, Juneteenth from 2021, Independence Day, Labor Day, Thanksgiving, Christmas) | Standard-rule reproduction (rule-based, computed, not hand-typed dates), cross-checked against multiple secondary sources 2026-09-21 | `_cme_full_closure_holidays()` |
| Good Friday for GC specifically | **Web-verified**: cmegroup.com's own 2026 Good Friday clearing advisory PDF and multiple independent sources confirm COMEX has **no trading at all** on Good Friday ("not a valid delivery and payment processing day for CME, CBT, NYMEX, COMEX and GME products with the exception of Treasury deliveries"), confirmed for both 2023 and 2026. | Modelled as a **full closure**, same as every other listed holiday — NOT a shortened session. This directly resolves the WO brief's "investigate and state clearly" instruction: the caution that COMEX metals might have historically shortened (not fully closed) Good Friday sessions was not borne out for GC by this research; the Good-Friday carve-out belongs to Treasury/interest-rate products, not COMEX metals. |
| Early-close days (day before Independence Day, day after Thanksgiving, Dec 24) | Standard-rule reproduction from secondary sources 2026-09-21 | Recorded as `EARLY_CLOSE`, structurally distinct from a full closure (`is_gc_full_closure` vs `gc_early_close_reason`) — early close never removes a session from the universe, it only shortens its window. |
| Early-close clock times (1:30 PM CT pre-holiday/pre-July-4th; 12:00 PM CT post-Thanksgiving/Christmas-Eve) | Standard-rule reproduction from secondary sources 2026-09-21, NOT independently re-verified against a CME per-year advisory for every date 2017-2025 | `_EARLY_CLOSE_PRE_HOLIDAY_CT`, `_EARLY_CLOSE_POST_THANKSGIVING_CT`, `_EARLY_CLOSE_CHRISTMAS_EVE_CT` |
| Regular GC Globex hours (Sun-Fri 17:00-16:00 CT, one-hour daily maintenance halt) | Standard-rule reproduction, consistent across every secondary source consulted (CME's own Gold Futures and Options fact card plus several independent trading-education sites) | `_REGULAR_OPEN_CT`, `_REGULAR_CLOSE_CT` |
| UTC window derivation | Computed, DST-aware, via `zoneinfo("America/Chicago")` — not a hardcoded UTC offset | `_ct_datetime_to_utc()` |

**Disclosed open questions (not silently resolved — see `session_calendar.OPEN_QUESTIONS`):**
1. The exact CME Globex reopen behaviour on the evening of a full-day holiday closure that
   falls on a weekday other than Monday (e.g. a midweek Independence Day) was not
   independently verified for every year 2017-2025. This module assumes the standard
   "previous calendar day 17:00 CT" rule uniformly, and flags the following session
   `HOLIDAY_ADJACENT` as an analyst caution — not a verified claim of a different window.
2. The exact CT early-close clock times are a secondary-source reproduction, not re-verified
   against a CME per-year advisory for every specific date in range.

Both of the primary sources cited above were retrieved during this checkpoint's live web
research session on **2026-09-21**. Full citation list is in the `session_calendar.py`
module docstring.

`CALENDAR_VERSION = "gc-session-calendar-v1"`.

## 3. Scheduled-macro-event reference snapshot

File: `research/hmt2/scheduled-macro-event-snapshot-v1.json`
Version: `hmt2-scheduled-macro-event-snapshot-v1`
Event taxonomy version: `hmt2-event-taxonomy-v1`

This is a **one-time, frozen, versioned reference snapshot for corpus-selection purposes
only.** It is explicitly NOT a HERMES runtime economic calendar and NOT a generalised
calendar service, and is explicitly out of ARES's architectural authority for anything
beyond this one-time selection use.

### Coverage, honestly disclosed per class

| Event class | Authority | Coverage in this snapshot | Total dates | Primary source |
|---|---|---|---|---|
| `FOMC_DECISION` | Federal Reserve | **FULL**: 2017-05-21 through 2025-12-31 | 69 | federalreserve.gov FOMC calendar + historical archive pages (2017, 2018 fetched directly; 2019-2026 from the current consolidated calendar page) |
| `CPI_RELEASE` | BLS | **PARTIAL**: 2024-01-01 through 2025-12-31 only. 2017-2023 NOT covered — disclosed gap, not fabricated. | 23 | bls.gov per-year schedule archive pages (`/schedule/2024/home.htm`, `/schedule/2025/home.htm`) |
| `EMPLOYMENT_SITUATION_NFP` | BLS | **PARTIAL**: 2024-01-01 through 2025-12-31 only. 2017-2023 NOT covered — disclosed gap, not fabricated. | 23 | same BLS per-year schedule archive pages |
| `PCE_RELEASE` | BEA | **PARTIAL**: 2025-01-01 through 2025-12-31 only (10 of the year's normal ~12 releases; two fell after the cutoff — see anomaly note below). 2017-2024 NOT covered — disclosed gap, not fabricated. | 10 | BEA's official 2025 News Release Schedule PDF, plus BEA schedule-update announcements for the shutdown-driven reschedules |

**Total scheduled-event candidate dates before same-day dedup: 125** (69+23+23+10). After
deduplicating one genuine same-day collision (2024-06-12: both a CPI release and an FOMC
decision — real, correctly sourced), the scheduled-event stratum contains **124 unique
sessions** from a real run of the algorithm (see the WO final report for the exact number
from the actual committed manifest).

This intentionally OVERSHOOTS the WO brief's indicative ~80-session target. That is a
disclosed, reasoned deviation: FOMC alone, fully covered for the entire corpus range,
already contributes 69 real dates; truncating to hit an arbitrary target would require an
extra, undeclared selection rule that this checkpoint does not invent. The full, honestly
sourced set is used as-is.

**Known real-world anomaly, disclosed in the snapshot's own metadata:** the October-November
2025 U.S. federal government shutdown caused BLS to skip/delay several 2025 CPI and
Employment Situation releases (no discrete October-2025-reference-month report was published
for either series) and caused BEA to delay and combine several 2025 Personal Income and
Outlays (PCE) releases (September-2025 data pushed from October 31 to December 5, 2025;
October and November 2025 data combined and pushed to January 22, 2026 — outside this
corpus's cutoff; December 2025 data pushed to February 20, 2026 — also outside cutoff). The
snapshot records the **actual** release calendar, including these gaps, not the
originally-scheduled-but-never-published dates.

**Explicit scope decision:** only *regularly scheduled* FOMC meetings are included.
Unscheduled/emergency FOMC actions (notably the two emergency meetings in March 2020) are
out of scope for this snapshot.

Every record in the snapshot carries: `event_class`, `date`, `authority`, `source_ref`,
`retrieval_utc`, `event_taxonomy_version`, and an optional `coverage_note`. The snapshot's
own metadata block carries a `snapshot_sha256` over its canonical serialization.

## 4. Coarse volatility/compression reference series — GENUINELY UNAVAILABLE in this checkpoint

File: `research/hmt2/volatility-compression-reference-series-v1.json`
Status: **`PENDING_REFERENCE_SERIES_DATA`**

HMT-2A has no live/real market-data source of any kind (no Databento access, no real feed,
no provider adapter — none is in scope for this checkpoint). A live web-research pass on
2026-09-21 for a legitimately citable, freely redistributable, license-clean daily GC/gold
reference-price dataset found none meeting that bar: the readily-available free sources
(Investing.com, Barchart.com) are ToS-restricted against scraping/redistribution, and
third-party CSVs found (e.g. on GitHub) have no verifiable provenance, licence, or quality
guarantee suitable for a governed research corpus.

Per this whole engagement's standing evidence discipline ("PENDING_EVIDENCE, never invent a
number"), this checkpoint does **not** fabricate a plausible-looking historical volatility or
compression value. Instead it freezes:
- the intended `frozen_source` schema (data source / instrument / resolution / price basis /
  date range / version-hash fields), all explicitly marked `PENDING_REFERENCE_SERIES_DATA`;
- the full transformation-formula and normalization methodology (coarse high/low/close range,
  robust 63-session median/MAD z-score — see the JSON file for the exact formula);
- the classification rule that would assign `HIGH_VOL_NON_EVENT` / `COMPRESSION` once real
  data exists;
- the code interfaces (`compute_coarse_range_series`, `classify_high_vol_and_compression_sessions`
  in `market_truth/acquisition/corpus_selection.py`) — the former fails closed with
  `ReferenceSeriesUnavailableError` if ever called against a real series that still doesn't
  exist; the latter is what the actual selection pipeline calls, and it returns an explicit
  PENDING result (zero sessions, `status="PENDING_REFERENCE_SERIES_DATA"`) rather than raising,
  so a full corpus-selection run can still complete and honestly report zero sessions in these
  two strata.

**Consequence:** the `HIGH_VOL_NON_EVENT` and `COMPRESSION` strata are populated with **zero**
sessions in the HMT-2A manifest. No `volatility_compression_metric` value anywhere in the
manifest is a real computed number. This is flagged as this checkpoint's most significant,
expected, and honestly-disclosed incompleteness — see the WO final report.

## 5. Deterministic randomness

Domain-separated SHA-256 seeds, exactly as specified in the WO brief:

```
SHA256("HERMES|HMT-2|GC-MBP1|corpus-selection-v1|03d700a463ada61d472c64ad52265d09d82c60ea|DEVELOPMENT_RANDOM")
SHA256("HERMES|HMT-2|GC-MBP1|corpus-selection-v1|03d700a463ada61d472c64ad52265d09d82c60ea|PROTECTED_HOLDOUT")
```

Exact resulting hex digests (from `market_truth.acquisition.corpus_selection.domain_seed_hex`,
also recorded in every generated manifest's metadata and asserted in
`tests/hmt2/test_corpus_selection.py::test_deterministic_seed_digests_are_stable_and_documented`):

- `DEVELOPMENT_RANDOM`: `f0a08806a405bacd052059a3129548741a563149ea5e56bf72dac71b582021a3`
- `PROTECTED_HOLDOUT`: `df554685c2c9d063a0e1f4a783fad07657847ca2f47059571befde71fc8b61a8`

Each digest is converted to a big integer and used to seed a single `random.Random` instance,
which draws once, deterministically, over a pool built in a fixed (ascending-date) order
before shuffling. The selection is implemented as a single deterministic function
(`select_corpus`) of the frozen inputs — run once, and whatever it produces is used; no
manual reseeding or retrying occurs anywhere in this codebase.

`SELECTION_ALGORITHM_VERSION = "hmt2-corpus-selection-v1"`.

## 6. Strata, planning targets, and actual composition

Indicative targets from the WO brief (§6) — **not mandatory quotas**:

| Stratum | Indicative target | Actual (this checkpoint) |
|---|---|---|
| `SCHEDULED_EVENT` | ~80 | 124 unique sessions (see §3 for why this overshoots) |
| `MATCHED_CONTROL` | ~1 per event | 124 (0 unmatched in a real run — see §6.2) |
| `HIGH_VOL_NON_EVENT` | ~70 | **0 — PENDING_REFERENCE_SERIES_DATA (§4)** |
| `COMPRESSION` | ~70 | **0 — PENDING_REFERENCE_SERIES_DATA (§4)** |
| `RANDOM_DEVELOPMENT` | ~50 | 50 |
| `PROTECTED_HOLDOUT` | ~100 | 100 |

Exact counts from the actual committed manifest are in the WO final report and in the
manifest's own `metadata` block.

### 6.1 Matched-control algorithm

Pre-declared, calendar/event-metadata-only deterministic scoring hierarchy (never inspects
price/volatility behaviour — there is none to inspect in this checkpoint anyway):

1. **Same weekday** — enforced structurally: only `event_date ± 7*k` days (k = 1, 2, ...) are
   ever considered as candidates, since adding/subtracting whole weeks preserves weekday.
2. **Nearest calendar distance within the same broad period** — enforced by searching
   `k = 1 .. MATCHED_CONTROL_MAX_WEEKS` (= 8 weeks) in ascending order and taking the first
   qualifying hit.
3. **No selected catalyst present** — enforced by excluding every date already in the
   scheduled-event set and every date already claimed by an earlier match.
4. **Deterministic tie-break by session date** — at a given distance `k`, the prior week
   (`event_date - 7k`) is always tried before the subsequent week (`event_date + 7k`).

An event that cannot find any qualifying control within the 8-week search radius is logged
as `NO_QUALIFYING_CONTROL_WITHIN_SEARCH_RADIUS` (printed by the generator script), not
silently dropped or fabricated. See the WO final report for whether any occurred in the real
run.

### 6.2 Random development baseline and protected holdout

Both drawn via `random.Random(int(seed_hex, 16)).shuffle()` over a pool built in fixed
ascending-date order, then truncated to the target count, then re-sorted by date for stable
manifest ordering (the sort happens strictly AFTER the seeded draw — it does not affect which
sessions are chosen, only their presentation order).

### 6.3 Deduplication / precedence — a disclosed implementation choice

The WO brief's precedence order is:

```
PROTECTED_HOLDOUT > SCHEDULED_EVENT > MATCHED_CONTROL > HIGH_VOL_NON_EVENT
    > COMPRESSION > RANDOM_DEVELOPMENT
```

`SCHEDULED_EVENT` and `MATCHED_CONTROL` are fixed facts (a historical FOMC/CPI/NFP/PCE date is
what it is; a matched control is a deterministic function of an event date) — neither can be
"redrawn" on collision. `RANDOM_DEVELOPMENT` and `PROTECTED_HOLDOUT` are the only two strata
assigned by seeded random draw. This checkpoint honours the brief's explicit
non-destructive-collision instruction (stated for the holdout-vs-development case:
"replaced by drawing the next deterministic holdout candidate, not by
stealing/reassigning the other stratum's session") by generalising it uniformly:

1. `SCHEDULED_EVENT` assigned first (fixed real dates).
2. `MATCHED_CONTROL` assigned second, excluding all `SCHEDULED_EVENT` dates.
3. `HIGH_VOL_NON_EVENT` / `COMPRESSION` attempted third — both PENDING, contribute nothing,
   never collide with anything.
4. `RANDOM_DEVELOPMENT` drawn fourth, excluding everything assigned above.
5. `PROTECTED_HOLDOUT` drawn LAST, excluding everything assigned above. On encountering an
   already-assigned candidate mid-draw, the shuffle simply continues to its next candidate —
   nothing is stolen or reassigned.

Drawing `PROTECTED_HOLDOUT` last with a full exclusion set is operationally equivalent to
"holdout wins any collision, non-destructively" (nothing assigned after holdout could ever
collide with it, since nothing is assigned after it), which is what the brief's stated
precedence order requires, and it trivially guarantees that holdout sessions never appear as
any other stratum — exercised directly by
`tests/hmt2/test_corpus_selection.py::test_holdout_sessions_never_appear_as_a_non_holdout_stratum`.

This ordering is flagged as a disclosed engineering judgment call in the WO final report,
since the brief's literal precedence list (which ranks `PROTECTED_HOLDOUT` above
`SCHEDULED_EVENT`) is not directly executable for two strata that are fixed real-world facts
rather than redrawable candidates.

## 7. Exclusions

- No P0 microstructure fact, feature, threshold, or strategy logic anywhere in this
  checkpoint.
- No actual MBP-1/TBBO/trade data of any kind, real or fabricated.
- No HMT-3, DARWIN, ATHENA, Vantage, MBO, PROD, or legacy-retirement work.
- No `market_truth/contracts.py`, `identity.py`, `futures.py`, `provider.py`,
  `canonicaliser.py`, `partition.py`, `evidence.py`, `replay.py`, `derived_fact_identity.py`,
  `providers/fixture.py` modified — HMT-1's canonical machinery is untouched.

## 8. Versions (summary)

| Artifact | Version |
|---|---|
| Calendar | `gc-session-calendar-v1` |
| Event taxonomy | `hmt2-event-taxonomy-v1` |
| Scheduled-event snapshot | `hmt2-scheduled-macro-event-snapshot-v1` |
| Volatility/compression reference series | `hmt2-vol-compression-reference-series-v1` (PENDING) |
| Selection algorithm | `hmt2-corpus-selection-v1` |
| Manifest schema | `hmt2-corpus-selection-manifest-v1` |
| Corpus | `hmt2-corpus-v1` |
| Base SHA this checkpoint was built from | `03d700a463ada61d472c64ad52265d09d82c60ea` |
