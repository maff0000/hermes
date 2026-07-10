# WO-HELM-HERMES-D1-DAILY-LEVELS-ASOF-TYPE-FIX-0001 — HELM verdict (CODE-ONLY FIX PR)

## GREEN_D1_DAILY_LEVELS_ASOF_TYPE_FIX_PR_READY
Tracking: HELM_HERMES_D1_DAILY_LEVELS_ASOF_TYPE_FIX_PR::2026-07-10T14:00Z::GREEN_D1_DAILY_LEVELS_ASOF_TYPE_FIX_PR_READY

Blocker consumed: HELM_HERMES_D1_SURFACES_ACTIVATION::2026-07-10T13:32Z::AMBER_D1_SURFACES_ACTIVATION_BLOCKED_DAILY_LEVELS_ASOF_TYPE_BUG

Branch: wo/WO-HELM-HERMES-D1-DAILY-LEVELS-ASOF-TYPE-FIX-0001   Base: 61fc42453094b1b52497ea67a182d45bd9118037
Files changed (2): utils/hermes_runtime_publisher_steps_v1.py (1 line), tests/test_d1_daily_levels_asof_fix_v1.py (NEW)

## Exact fix (narrow, 1 line)
sessions_levels_step daily block:
-   as_of_candle_close_utc=cc._fmt(d1_close)     # STRING -> GOV-HERMES-LVL-001
+   as_of_candle_close_utc=d1_close              # DATETIME (build_level_contract/_assert_utc requires it)
No other semantics changed. (session/intraday already pass a datetime; this makes daily consistent.)

## Regression test (closes the gap)
tests/test_d1_daily_levels_asof_fix_v1.py — 8 tests, all pass:
- test_daily_path_publishes_valid_contract_no_lvl001: drives the FULL sessions_levels_step daily path (via _win_cache
  injection to skip SQL _load_windows) -> hermes:levels:XAU_USD:daily:v1 PUBLISHES, validate_level_contract passes,
  d1_derived=True, PDH/PDL/PDC/adr_20 present, level_source_granularity=D1, no XAUUSD, no deletes.
- test_asof_is_datetime_into_build_level_contract: spies build_level_contract -> daily as_of type == 'datetime'.
- test_old_string_asof_would_have_raised: proves build_level_contract genuinely rejects a STRING as_of (fix is load-bearing).
- daily dark when scope unauthorised; daily blocked below depth 26; source is D1 history only (no SQL/market_map/D1-latest);
  safeguards still present; XAUUSD denied in D1 source.

## Safeguards preserved (unchanged)
_d1_tf_if_ready, _read_d1_history_validated, _d1_derived_ready, _d1_daily_levels, assert_sealed_complete_d1, XAUUSD deny,
22:00 NY-5PM anchor, depth>=26 — all present (test_safeguards_still_present + source_path_proof.txt).

## Source path / no forbidden source (see source_path_proof.txt)
D1 source = governed sealed D1 history ONLY (D1_TF:history:v1). _read_d1_history_validated has NO :D1:latest: fallback,
NO pymysql, NO get_db_config, NO market_map. No fake/generated/unsealed fallback. Canonical XAU_USD; XAUUSD denied.

## Tests
focused 8 passed; touched-area (d1 indicators/features/levels + sessions_levels + candle_features + publisher_runtime +
d1_history) 104 passed; FULL SUITE branch == pristine main 61fc424 (51 failed + 4 collection errors, identical pre-existing
env/plugin noise) -> 0 NEW failures; +8 passing (1130 vs 1122).

## Forbidden-token/semantics
The diff only removes a cc._fmt() wrap — introduces no new token. No regime/risk/strategy/signal/trade semantics.
(Test file uses 'XAUUSD' only in an explicit negative deny test.)

## Boundaries (CODE-ONLY)
No runtime mutation. No activation. No backfill. No Redis writes (fake-client tests only) or deletes. No SQL. No market_map.
No consumer-live. Falcon held. No secrets. Runtime untouched (still rolled-back dark state, container 927619012e4b).
