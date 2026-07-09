# WO-HELM-HERMES-D1-INDICATORS-FEATURES-LEVELS-0002 — HELM verdict (CODE-ONLY PR)

## GREEN_D1_INDICATORS_FEATURES_LEVELS_PR_READY_FOR_R2D2_AUDIT
Tracking: HELM_HERMES_D1_INDICATORS_FEATURES_LEVELS_BUILD::2026-07-09T14:30Z::GREEN_D1_INDICATORS_FEATURES_LEVELS_PR_READY_FOR_R2D2_AUDIT

R2D2 backfill execution authority consumed:
R2D2_HERMES_D1_HISTORY_BACKFILL_EXECUTION_AUDIT::2026-07-09T14:13:06Z::GREEN_D1_HISTORY_BACKFILL_EXECUTION_AUDIT_APPROVED_DEPTH_GE_26

Branch: wo/WO-HELM-HERMES-D1-INDICATORS-FEATURES-LEVELS-0002   Base: 1dd1a8cdfe4969ca268924689c440a5a149c6e03
Files changed (2): utils/hermes_runtime_publisher_steps_v1.py (+109/-10), tests/test_d1_indicators_features_levels_v2.py (NEW)

## D1 depth proof
D1 history depth 30 (>=26) — blocker cleared by the audited backfill execution. Source range 2026-05-31..2026-07-07, sealed 6/6.

## Source path
D1 surfaces derive from hermes:candles:XAU_USD:D1:history:v1 (index + members) ONLY. NOT SQL H4/M30, NOT SQL D1, NOT
market_map, NOT vendor, NOT shadow, NOT the unsealed live D1 latest, NOT XAUUSD. _read_d1_history_validated re-asserts
each member sealed-6/6 (assert_sealed_complete_d1) + 22:00 NY-5PM anchor + no XAUUSD before compute.

## D1 indicator support
indicator_step now loops LATEST_TFS + _d1_tf_if_ready(pub, client). D1 reuses _compute_indicators (same calculate_ema/
rsi/atr as M1-H4) over the validated D1 history. Depth 30 -> ema_12/ema_26/rsi_14/atr_14 all present. Key
hermes:indicators:XAU_USD:D1:v1 (existing convention).

## D1 candle_feature support
candle_feature_step now includes D1 (same _features_for as M1-H4): body/wick/range/ratios/direction + inside/outside/
engulfing from the newest 2 sealed D1. Fail-loud (GOV-HERMES-D1DERIV-011) if a prior sealed D1 is unavailable. Key
hermes:candle_features:XAU_USD:D1:v1.

## D1 level support (daily)
sessions_levels_step publishes scope 'daily' when the level publisher authorised it AND depth ready: previous_day_high/
low/close + adr_20 (mean high-low over last 20 sealed D1). Fail-loud (GOV-HERMES-D1DERIV-010) if no prior D1. Deterministic
OHLC facts only. Key hermes:levels:XAU_USD:daily:v1 (D1-derived scope, d1_latest_green=True).

## Depth/source guard behaviour
_d1_tf_if_ready = (D1,) iff D1 in pub.timeframes AND d1_history_depth_sufficient (>=26); else () -> D1 governed SKIP
(blocked, never fabricated). ema_26 needs 26, rsi_14/atr_14 need >=15/14 (existing _compute_indicators length guards).
Source validation rejects unsealed/gapped/non-UTC/wrong-anchor/XAUUSD.

## Activation / default-dark behaviour
D1 is DARK by default: publishers ship without D1 in timeframes/scopes (HERMES_INDICATOR_D1_AUTHORISED /
HERMES_CANDLE_FEATURE_D1_AUTHORISED / HERMES_LEVEL_D1_AUTHORISED unset). No new gate introduced. LATEST_TFS never modified
=> M1-H4 unchanged. This PR does NOT set any D1 gate and does NOT activate D1.

## No catalog overclaim
No catalog/manifest changes. D1 indicators/features/levels are NOT marked RUNTIME_PUBLISHED.

## Tests (13 focused, all pass)
source validation accepts sealed / rejects non-OK + wrong-anchor + XAUUSD; _d1_tf_if_ready included only when authorised+deep;
dark when not authorised; indicator_step publishes D1 (ema_26 present) when ready; no D1 when dark; D1 blocked below depth 26
(M1-H4 unaffected); candle_feature publishes D1 from sealed history; dark by default; prev-unavailable fails loud; daily levels
from sealed D1 (PDH/PDL/PDC + adr_20); daily levels fail loud without prior; no forbidden tokens in D1 functions.
Touched-area: indicators + candle_features + sessions_levels + d1_history + publisher_runtime = 233 + 181 passed.
FULL SUITE: branch == pristine main 1dd1a8c (51 failed + 4 collection errors, identical pre-existing env/plugin noise) -> 0 NEW failures; +13 passing (1122 vs 1109).

## Forbidden-token scan (added lines, explained)
regime/risk/trade x1 -> _d1_daily_levels NEGATIVE-declaration docstring; market_map x1 -> block negative declaration
("never market_map"); XAUUSD x2 -> alias-DENY guard in _read_d1_history_validated (rejects, never produces). No score/signal/
entry/exit/buy/sell/go-no-go in added code. No interpretive logic entered HERMES.

## Boundaries (CODE-ONLY)
No runtime mutation. No deploy. No activation (no D1 gate set). No Redis writes (fake-client tests only) or deletes. No SQL.
No backfill execution. No market_map. No catalog overclaim. No consumer-live. No Falcon/consumer/cross-app. No secrets.
M1-H4 indicators/features/levels + tick/quote/feed-health/instrument_catalog/D1 latest untouched.
