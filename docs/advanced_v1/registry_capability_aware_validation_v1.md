# Registry capability-aware validation + production-shape compatibility

**WO:** WO-HELM-HERMES-REGISTRY-INACTIVE-ROW-VALIDATION-AND-PRODUCTION-SHAPE-0001
**Base:** canonical main `c8c196ce`.

## Problem corrected
Migration 025 seeded Advanced-v1 metadata (`price_precision`, `tick_size`, `market_hours_policy`,
`expected_freshness_sec`, `enabled_timeframes`) only for the 8 rollout instruments. The 6 non-cohort rows
(`XPT_USD, XCU_USD, USD_CHF, USD_CAD, NZD_USD, EUR_GBP`) retain **NULL** for those fields. The old `validate_record`
required them for **every** row, so `load_from_db()` raised `RegistryError` on the live 14-row registry — which would
fail-close all five publishers and **stop the authorised XAU pilot** on the new image (the dark-deployment abort).

## Validation model (capability-driven, no ticker logic)
- **Universal fields** — validated for every row: `symbol`, `category`, `enabled`, `oanda_compatible`, the three
  capability flags, `metadata_version`; plus the NOT-NULL-default fields `price_authority`, `indicator_profile`,
  `backfill_policy`, `retention_policy`.
- **Capability-specific fields** — `price_precision`, `tick_size`, `market_hours_policy`, `expected_freshness_sec`,
  `enabled_timeframes`. These are:
  - **validated whenever present** (a present value must be valid, even on an inactive row); and
  - **required** (present + valid) when a capability that needs them is enabled.
- **Capability → required-field matrix** (`_CAPABILITY_REQUIRED_FIELDS`; the required set for a row is the UNION over
  its enabled capabilities):
  | Capability | Requires |
  |------------|----------|
  | `tick_contract_enabled` | price_precision, tick_size, price_authority, market_hours_policy, expected_freshness_sec |
  | `indicator_contract_enabled` | price_precision, price_authority, market_hours_policy, expected_freshness_sec, enabled_timeframes, indicator_profile |
  | `gap_detection_enabled` | price_precision, tick_size, market_hours_policy, expected_freshness_sec, enabled_timeframes |
  (backfill-status follows the gap capability; feed-health follows the tick capability — no separate flags.)

## Capability flags are STRICT authority fields
Capability flags (`tick_contract_enabled`, `indicator_contract_enabled`, `gap_detection_enabled`) are strict authority
and are parsed by `_parse_capability_flag`, **not** the permissive `_as_bool`:
- **Accepted** (governed loader contract): int `0`, int `1`, bool `False`, bool `True` (the SQL `tinyint(1)` driver forms;
  booleans are checked before ints since `bool` subclasses `int`).
- **Rejected** → `RegistryError: GOV-HERMES-REG-INVALID-CAPABILITY-FLAG` (state `CONFIGURATION_INVALID`): `None`, any
  string (incl. `"0"`, `"1"`, `"true"`, `"false"`, `"yes"`, `"no"`, `"on"`, `"off"`, `"maybe"`, `"2"`), int `2`/`-1`,
  floats (`0.0`/`1.0`), lists, dicts, bytes, arbitrary objects.
- A malformed flag is a **configuration fault** — it is **never** coerced to `False` and **never** interpreted as
  `NOT_ENABLED`. The inactive-metadata tolerance below applies only **after** the capability flags parse validly as
  false. The SQL constraint (`tinyint(1) NOT NULL DEFAULT 0`) and this loader validation are **complementary** controls;
  the database constraint does not replace loader validation, and malformed values are not assumed unreachable.

## Row semantics
- **Fully inactive row** (all Advanced-v1 capabilities false): loads with NULL capability-specific metadata,
  classified `effective_state = NOT_ENABLED`. **No defaults are invented** (fields stay `None`, not `0`/`""`/`mid`).
  It is not selected, not publishable, not keyed, not instantiated into a family state engine, and cannot be activated
  by master/mode alone or by pilot-scope membership. The general `enabled` flag is **not** an Advanced-v1 capability.
- **Capability-active row** (≥1 capability true): every field required by its enabled capabilities must be present and
  valid, else `RegistryError: GOV-HERMES-REG-INCOMPLETE-ACTIVE` (instrument + active capabilities + missing field +
  metadata_version). No partial operation, no downgrade to NOT_ENABLED.
- The typed `InstrumentRecord` marks the capability-specific fields `Optional[...]`; `require_complete()` is a guarded
  accessor so capability-active code never consumes `None`.

## Two complementary boundaries
1. **Selectors are the primary cohort boundary** — `selection_for(...)`/`backfill_status_instruments(...)` return only
   capability-enabled instruments, so inactive rows never reach a family.
2. **`require_complete()` at every family boundary is defence-in-depth** — each of the five publishers
   (tick/indicators/gaps/backfill-status/feed-health) calls `reg.assert_records_complete(records, selected_symbols)` at
   construction, which invokes the central `require_complete()` policy. This catches a directly-injected, constructed,
   or stale `InstrumentRecord` that is capability-active but has `None` required metadata (or a `NOT_ENABLED` record
   reaching a publisher). The guard is load-bearing: removing it makes a per-family boundary test fail. It does not
   duplicate the validation matrix.

## Effective-state preflight
`registry_effective_summary(records)` reports `{registry_rows, advanced_v1_active, not_enabled, not_enabled_count,
selection}` — distinguishing registry membership (14) from activation. A future effective-state deployment preflight
must: read all 14 rows; report inactive rows explicitly; validate active rows strictly; **not** abort because inactive
rows lack capability metadata; **do** abort if any selected/active row is incomplete. The whole registry loads only
when every active row is valid (active-row defects are never skipped/hidden).

## Production shape (post-025, unchanged by this WO)
14 rows; XAU_USD active (caps 1/1/1, complete metadata) = the pilot; the other 7 rollout instruments inactive
(caps 0) but metadata-complete; the 6 non-cohort inactive with NULL capability metadata. WTICO `energy`. No seeding of
the 6 rows was performed (Chief Architect ruling: correct the validator, not the data).
