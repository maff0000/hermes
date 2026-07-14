# HERMES Recovery Planner — Design Blueprint

**WO:** `WO-HELM-HERMES-PH2-RECOVERY-PLANNER-DESIGN-0001`
**Module:** `utils/hermes_recovery_planner_v1.py` (naming follows the established `hermes_*_v1.py` convention used by every
governed HERMES surface — `hermes_gaps_v1.py`, `hermes_backfill_status_v1.py`, etc. — rather than the bare
`utils/recovery_planner.py`, for packaging/versioning/testability consistency).
**Mode:** CODE-ONLY / DRY-RUN-ONLY.

## Purpose
The recovery planner converts validated in-memory recovery inputs into a **bounded, prioritised, serialisable
`ProposedWorkload`** describing *what may require recovery*. **It produces proposals only. It does not fetch, write,
publish, queue, backfill, repair or execute.**

## Ownership boundary
Reasons ONLY about: candle/history coverage gaps, market-closure exclusions, existing local coverage, timeframe priority,
bounded recovery segments, workload estimates, provenance requirements, deterministic planning faults.
NEVER about: regime, risk, strategy, trade signals, position sizing, execution, Falcon, ARES, HELIOS, NEO, SOLO.

## Execution isolation (structural)
No Redis / SQL / HTTP / broker / vendor / subprocess / filesystem / Docker / systemd / cron client is imported or
instantiated anywhere in the production module. There is no `.set/.delete/.zadd`, no seed/backfill import or invocation,
no job/queue/publish path. All output carries `planning_mode=DRY_RUN_ONLY` and
`execution_enabled=backfill_executed=repair_executed=consumer_live=false` (enforced by `validate_proposed_workload`).
The planner operates exclusively on explicit caller-supplied in-memory objects. Clock and policy are dependency-injected.

## Input contracts (frozen, validated, versioned)
- **`GapSurfaceSnapshot`** — contract_version, instrument, source_key, source_generated_at_utc, source_observed_at_utc?,
  overall_gap_state, gap_records (per-tf missing intervals + state), d1_boundary_state, d1_anchor_hour_utc.
- **`ExistingCoverageSnapshot`** — contract_version, instrument, timeframe, covered_intervals, snapshot_at_utc, provenance.
  Overlap clipping is computed against this caller-supplied coverage; **the planner never fetches local Redis history.**
- **`MarketClosureSnapshot`** — contract_version, instrument, closure_interval, classification, provenance, authority,
  effective_at_utc. Holiday closures require an accepted authority; unverified closures are never silently excluded.
- **`RecoveryPlanningPolicy`** (+ `CostModel`) — instrument, allowed_timeframes, timeframe_priority, priority_tiers,
  max_segments, max_candles_per_segment, max_candles_per_proposal, max_lookback_seconds, max_request_units,
  merge_adjacent_threshold_seconds, d1_history_floor_seconds, regular_weekend_closure_utc, accepted_closure_authorities,
  uncertain_to_unclassified, stale_gaps_max_age_seconds, allow_partial, cost_model. **All material policy is supplied
  explicitly; no hidden runtime defaults. Runtime config loading is out of scope for this WO.**

## Output contract — `ProposedWorkload`
contract_version, proposal_id, planner_version, target_instrument(=XAU_USD), generated_at_utc,
source_gaps_generated_at_utc, planning_mode(=DRY_RUN_ONLY), execution_enabled(=false), backfill_executed(=false),
repair_executed(=false), consumer_live(=false), overall_plan_status, estimated_request_units (primary, vendor-neutral),
estimated_api_cost_tokens (compat field; estimate only), prioritized_segments_list, intentionally_unavailable_segments,
excluded_existing_coverage_segments, unclassified_segments, deferred_segments, faults, warnings, input_provenance,
policy_digest. No executable command / API request / SQL / Redis instruction / callable recovery function is emitted.

## Interval semantics
Half-open `[start_utc, end_utc)`. All timestamps tz-aware, normalised to UTC, serialised `YYYY-MM-DDTHH:MM:SS.sssZ`.
Naive datetimes rejected; inverted/zero-length ranges rejected. All overlap/clip/adjacency/candle-count math uses the
same convention. `candles([s,e), tf) = floor((e-s)/period_tf)`.

## Priority policy
Tier 1: D1 history-floor segments. Tier 2: H4/H1 recent. Tier 3: M15/M5/M1 intraday. Represented explicitly in
`RecoveryPlanningPolicy.timeframe_priority`/`priority_tiers` (not an irreversible constant). Deterministic stable sort:
`(priority_rank, segment.start_utc, timeframe, segment_id)`. Identical inputs+policy+clock → identical output.

## D1 history-floor doctrine
Distinguishes: missing coverage within the governed D1 history floor (recovery target, tier 1) vs beyond floor (excluded,
not required) vs D1 boundary defects (blocking) vs intentionally unavailable vs existing coverage. Anchor is **22:00 /
NY-5PM**; a `00:00` anchor or a non-OK `d1_boundary_state` produces a **`BLOCKED_D1_BOUNDARY`** fault with an empty segment
list — a boundary defect is never silently converted into a recovery segment. The planner never imports/invokes the D1
seed/backfill engine.

## Weekend & holiday doctrine
Regular XAU_USD weekend closure (policy `regular_weekend_closure_utc`, default Fri 22:00 → Sun 22:00 UTC) and closures with
an accepted authority are classified `INTENTIONALLY_UNAVAILABLE` and clipped out of recovery residuals. No exhaustive
holiday calendar is hardcoded; no holiday is guessed from a date; no calendar vendor is contacted. Closures lacking
sufficient authority are **not silently discarded** — they are surfaced as `UNCLASSIFIED_MARKET_STATE` (per policy) plus a
warning. Ambiguous boundaries are represented honestly in faults/warnings.

## Overlap clipping / idempotency
Supplied coverage is normalised + merged (sorted, overlapping/adjacent coalesced) before clipping; inputs are never
mutated. Residual = requested gap minus merged coverage, handling no/full/left/right/contained/multiple/adjacent/
duplicate/unsorted overlaps. Running twice with identical inputs yields the same segment set. `proposal_id` is a
deterministic SHA of canonical input+policy content, excluding the volatile clock.

## Segment coalescing
Adjacent same-instrument/same-tf/same-source/same-priority residuals merge only when the inter-gap ≤
`merge_adjacent_threshold_seconds`, **no governed closure lies between them**, and the merged candle count stays within
`max_candles_per_segment`. Never merges across weekends, closures, D1 boundaries or policy limits.

## Bounded workload
Enforces max_segments, max_candles_per_segment (splits oversize residuals into aligned chunks), max_candles_per_proposal,
max_lookback, max_request_units. Overflow is placed in `deferred_segments` (class `DEFERRED_BOUND`) — **never silently
truncated**. If `allow_partial` → `PARTIAL_BOUNDED_PROPOSAL` (+ reason); else a blocking status with no executable-looking
segment list.

## Cost estimation
Deterministic, vendor-neutral, from the explicit `CostModel` (candles_per_request, request_units_per_call,
cost_tokens_per_request, fixed_overhead_units, timeframe_multipliers). No credentials/runtime/vendor values. Labelled
**estimates**, not actual charges. `estimated_request_units` is primary; `estimated_api_cost_tokens` retained for compat.

## Gate truth table
| ENABLED | AUTHORISED | Result |
|---|---|---|
| false | false | `DISABLED` (no invocation, no writes) |
| false | true  | `DISABLED` (honest disabled state) |
| true  | false | fail-closed → `SystemExit(105)` |
| true  | true  | `ENABLED_AUTHORISED_PLAN_ONLY` — permits a future caller to PLAN; **never** implies execution/publish/mutation |
Env names (constants only; NOT wired into boot in this WO): `HERMES_RECOVERY_PLANNER_ENABLED`,
`HERMES_RECOVERY_PLANNER_AUTHORISED`.

## Fault codes
INVALID_CONTRACT_VERSION, INVALID_INSTRUMENT, XAUUSD_REJECTED, NAIVE_TIMESTAMP, INVALID_INTERVAL, STALE_GAPS_SNAPSHOT,
MISSING_GAPS_PAYLOAD, INVALID_GAPS_STATE, D1_BOUNDARY_FAILURE, UNSUPPORTED_TIMEFRAME, MISSING_CLOSURE_PROVENANCE,
UNCERTAIN_MARKET_STATE, EXISTING_COVERAGE_MISMATCH, WORKLOAD_BOUND_EXCEEDED, INVALID_COST_MODEL,
NON_DETERMINISTIC_SEGMENT_IDENTITY, SOURCE_POLICY_INSTRUMENT_MISMATCH.
High-level statuses: NO_RECOVERY_REQUIRED, PROPOSAL_READY, PARTIAL_BOUNDED_PROPOSAL, BLOCKED_INVALID_INPUT,
BLOCKED_D1_BOUNDARY, BLOCKED_STALE_GAPS, BLOCKED_POLICY, BLOCKED_UNCLASSIFIED_MARKET_STATE. **`OK` is never a status.**

## Future wiring / executor boundary (explicit non-goals of this WO)
- A **separate governed wiring WO** will decide whether/how the gate enters container boot and whether a caller invokes
  the planner and where any proposal is stored. This WO adds NO boot wiring, NO supervisor runner, NO Redis publisher,
  NO compose/env change.
- A **separate governed executor** (not this module) would be required to act on any proposal. The planner deliberately
  cannot execute, and emits no callable/executable artefact.

## Non-goals
No recovery execution, no data fetch, no Redis/SQL/vendor access, no job scheduling, no publication, no regime/risk/
strategy/signal/trade semantics, no cross-application dependency.
