# HERMES M30/H4 Forward Derivation + Candle-Feature Foundation

**WO:** `WO-HELM-HERMES-CANDLE-H4-M30-FORWARD-DERIVATION-AND-FEATURES-0001`
**Status:** PR-ready (code/tests/docs/evidence). No publisher activation, no live Redis writes,
no cron/systemd. **All timestamps UTC. HERMES owns market truth only.**

## Modules

| Module | Purpose |
|---|---|
| `utils/forward_derivation_runner.py` | Forward M30/H4 derivation from canonical **complete** M1 (DB-only) |
| `utils/candle_features.py` | Deterministic candle **facts** (geometry, wick/body/range, previous, swing) |
| `utils/candle_payload_builders.py` | **Inert** Redis-ready payload builders (no writes) |

## A. Forward derivation runner

- **Source:** `canonical_m1` rows with `complete=1` only. A derived M30/H4 is `complete=1`
  **only** when all expected complete-M1 are present (M30=30, H4=240). Missing/incomplete →
  `complete=false` / `INCOMPLETE`; current bucket → `FORMING`; no source → `UNAVAILABLE`.
  *(Stricter than the historical backfill, which counted all M1 rows — documented divergence.)*
- **Timeframes:** M30 (UTC `:00`/`:30`), H4 (UTC anchor `00/04/08/12/16/20`, `H4_ANCHOR_UTC`).
- **Idempotent:** upsert on `UNIQUE(instrument,timestamp)`; a COMPLETE derivation refreshes the
  row; a partial/forming derivation **never downgrades** an existing complete candle
  (`complete=GREATEST(complete,VALUES(complete))`, OHLC updated only when `VALUES(complete)=1`).
- **Multi-instrument:** instruments from the governed `instruments` registry (`enabled=1`);
  empty/missing → fail loud (`GOV-FWD-INSTR-001`). No hardcoded XAU-only.
- **Modes:** `dry-run` (default, no writes), `single-cycle` (last closed bucket), `catch-up`
  (bounded N, cap 96). Mutation requires `--execute --confirm`.
- **Config:** DB target via `env_config` (fail-loud, no hardcoded host/port/db, no fallback).
  **DB-only — no Redis.**

## B. Candle features (deterministic market facts)

`build_candle_feature(...)` produces: OHLC; `candle_high/low`, `wick_high/low`,
`body_high/low`; `total_range`, `body_size`, `upper/lower_wick_size`, `upper/lower_wick_ratio`,
`body_to_range_ratio`, `close/open_position_in_range`; `candle_direction`
(BULLISH/BEARISH/DOJI) + `is_bullish/bearish/doji/full_body/long_upper_wick/long_lower_wick/
pin_bar_candidate`; previous-candle facts (`breaks/closes_*previous_*`, `inside/outside_bar_candidate`);
deterministic **swing candidates** (`is_local_swing_high/low_candidate`, `swing_status`
CONFIRMED/CANDIDATE/UNAVAILABLE); completeness block (`complete`, `complete_state`,
`source_complete*`, expected/actual/missing counts, `status`, `reason_codes`); and H4 anchor
metadata (`anchor_type=UTC`, `anchor_status=RATIFIED_FOR_HERMES_V1`, `not_session_interpretive=true`).

**Zero-range** candles → ratios `None`, classified DOJI (no division-by-zero). **Malformed**
candles (None OHLC / high<low) → fail loud (`GOV-FEAT-001/002`).

**These are FACTS, not strategy.** No buy/sell/setup/permission/confidence/signal, no
trend/regime, no session interpretation.

## C. Inert Redis-ready payload builders

All wrap the HERMES universal envelope (`schema_version, service=HERMES, domain,
generated_at_utc, valid_until_utc, ttl_seconds, freshness_state, status, reason_codes,
provenance, data`). They assert keys are `hermes:*` and reject `falcon:/solo:/neo:/matt:`
keys and Falcon-shaped field tokens (`GOV-PAY-001/002/003`). **No Redis I/O.**

- `hermes:candle_features:latest:v1` — **Approach A**: serves the latest COMPLETE candle; if the
  most-recent closed bucket is incomplete → `latest_closed_incomplete=true` + `status=DEGRADED`;
  never serves incomplete as complete (`GOV-PAY-004`).
- `hermes:candle_context:current:v1` — `active_h4_candle` (always FORMING, `candle_id=null`,
  provisional OHLC marked) + `last_closed_h4_candle` (COMPLETE only if `candles_H4.complete=1`,
  else INCOMPLETE + `status=DEGRADED`); explicit H4 UTC anchor.
- `hermes:indicators:latest:v1` — INERT scaffold; `source_complete_policy=COMPLETE_ONLY`
  (incomplete source → degraded, no indicators); deterministic EMA comparison state only
  (`ABOVE/BELOW/CROSS_UP/CROSS_DOWN`, validated) — no trend/regime.

## D. Staleness / wrongness protection

Fail loud on: missing config (`env_config`), unknown timeframe (`GOV-FWD-TF-001`), malformed
rows (`GOV-FEAT-001/002`), empty instrument registry (`GOV-FWD-INSTR-001`). Degrade on
incomplete source; mark STALE where source is stale (weekend/market-closed staleness IS shown
STALE — never laundered to FRESH; HERMES does not interpret market hours, that is ARES' lane).
`complete` ≠ `freshness` (independent axes). UTC only. `reason_codes` always exposed.

## Container-readiness (no host cron/systemd)

`forward_derivation_runner.main()` is a container entry point (`--mode/--timeframe/
--lookback-buckets/--execute/--confirm`). A future container would invoke `run_cycle()` on a
**container-internal** schedule. **This WO creates no cron/systemd**; a temporary host scheduler,
if ever needed, must be explicitly authorised and documented for container absorption.

## Governed classification config (R2D2 **B-CONST**, fixed pre-merge)

Candle classification thresholds (doji / full-body / long-wick / pin-bar / `swing_lookback`)
**change classification output and are trading-sensitive — not mathematical invariants**, so they
are **governed config, never module constants**:

- `migrations/022_hermes_candle_feature_config.sql` — `hermes_candle_feature_config` table + a
  v1 row (`candle_feature_classification:v1`) holding the 6 thresholds as JSON (append-only;
  CREATE-only / **not applied live in this PR**).
- `utils/candle_features.load_config(get_conn, key)` reads the enabled row and **fails loud** if
  missing/disabled (`GOV-FEAT-CFG-001`) / malformed (`GOV-FEAT-CFG-002`) / out-of-range
  (`GOV-FEAT-CFG-003`) / bad `swing_lookback` (`GOV-FEAT-CFG-004`). **No module-constant
  fallback** — geometry is config-free, *classification REQUIRES an explicit
  `CandleFeatureConfig`*. Every feature carries `classification_config` provenance
  (`config_key`/`config_id`/`config_version`). The old hidden constants are removed (asserted by test).

## Completeness-policy divergence (R2D2 **B-DIV**, made explicit + live-gated)

- **Historical** Phase-2 M30/H4 backfill counted *all* M1 rows → `HISTORICAL_ALL_M1_COUNTED`.
- **Forward** runner counts **only `complete=1`** M1 → `FORWARD_COMPLETE_M1_ONLY` (stricter).
- **Risk:** historical and forward `complete=1` rows may mean different things until reconciled.
- Runner output + run evidence expose `derivation_policy`, `source_complete_policy`,
  `expected/actual/complete/incomplete/missing_source_candle_count`, and a
  `historical_policy_note`. Live `--execute` is **machine-blocked** (`GOV-FWD-BDIV-001`) until
  `reconciliation_ack=True`, which requires one of: **(1)** historical rows re-derived with strict
  COMPLETE_ONLY, **(2)** historical rows annotated with policy, or **(3)** an architect-approved
  epoch cutover recorded. Dry-run is always allowed.

## Live-apply note (Phase 2, gated)

This WO ships code + dry-run evidence only. Running the runner in `--execute --confirm` against
live `candles_M30/H4`, and any Redis publisher build, are separate gated WOs requiring explicit
Architect authorisation (publisher target ratification + C5 H4-anchor decision still pending).
