# WO-HELM-HERMES-D1-CANDLE-LATEST-MANIFEST-TRUTH-0001 — HELM verdict (CODE-ONLY FIX PR)

## GREEN_D1_CANDLE_LATEST_MANIFEST_TRUTH_PR_READY
Tracking: HELM_HERMES_D1_CANDLE_LATEST_MANIFEST_TRUTH_PR::2026-07-10T18:10Z::GREEN_D1_CANDLE_LATEST_MANIFEST_TRUTH_PR_READY

Consumed: R2D2 PR#86 residual (candle_latest_d1 PENDING while D1 latest live) + architect decision to fix before deploying PR#86 (combined deploy-dark).
Branch: wo/WO-HELM-HERMES-D1-CANDLE-LATEST-MANIFEST-TRUTH-0001   Base: 54c8eb4d9dc7b8c41f81850d3804f38d878bfb58
Files changed (3): utils/hermes_control_plane_v1.py (+13/-2), utils/hermes_runtime_publisher_steps_v1.py (+27/-2), tests/test_d1_candle_latest_manifest_truth_v1.py (NEW)

## Root cause
Both the manifest builder (build_contract_manifest, line ~175) and validate_manifest (GOV-HERMES-CP-015, line ~217) HARDCODED
gated_families.candle_latest_d1 = PENDING_FIRST_DAILY_SEAL — the manifest could never reflect a live D1 latest.

## Exact code change
- steps._d1_latest_active(client): reads hermes:candles:XAU_USD:D1:latest:v1; validates via assert_sealed_complete_d1
  (XAU_USD/D1, status OK, closed, 6/6, coverage 1.0, no gap) + 22:00 NY-5PM anchor + no XAUUSD; missing/invalid -> False (fail-closed).
- steps.control_plane_step: when _d1_latest_active -> active_families.candle_latest['D1']=ACTIVE and pop gated_families.candle_latest_d1;
  re-validate condition extended with d1_latest_active. steps._d1_state (heartbeat) tightened to the same validated check (was mere existence).
- hermes_control_plane_v1.validate_manifest: candle_latest_d1 truthful in EXACTLY ONE of — gated PENDING (default, D1 latest not live)
  OR active_families.candle_latest.D1=ACTIVE (D1 latest live+valid). Both -> GOV-HERMES-CP-015B (no split-brain). Active-marker absent -> require PENDING (GOV-HERMES-CP-015).

## candle_latest_d1 truth / no overclaim
missing key -> not active; invalid/unsealed -> not active; <6/6 source_count -> not active; wrong (non-22:00) anchor -> not active;
XAUUSD -> not active; valid sealed 22:00 D1 latest -> ACTIVE. (all six cases tested.) Builder default (pure, no I/O) stays PENDING and valid.

## PR #86 derived-family compatibility
Untouched. D1 indicators/candle_features/daily levels manifest logic (_d1_family_active/_daily_levels_active) unchanged; test_d1_manifest_truth_v1 still passes (part of the 67-test compat run).

## Scope discipline (candle_catalog left for follow-up)
Fixed the MANIFEST candle_latest_d1 only. The candle_catalog (hermes:catalog:candles:v1) still shows D1 latest_status=PENDING +
history_status/forward_history_status=BLOCKED_UNTIL_D1_LATEST_GREEN — a SEPARATE broader under-claim (D1 latest + D1 history + forward,
all conservative/safe-direction) NOT touched here because the WO forbids altering D1 history/backfill logic. Recommended as a follow-up
control-plane candle_catalog truth WO. No overclaim introduced anywhere; both artifacts remain in the safe (under-claim) direction pre-fix.

## consumer_live / Falcon / source
consumer_live untouched (manifest has no consumer_live field). No Falcon/downstream logic. Helpers read only the D1 latest key +
validate it — no SQL/pymysql/get_db_config/market_map, no D1-latest fallback for DERIVED surfaces (indicators/features/levels source
path untouched), no fake/shadow/vendor, no XAUUSD (except explicit negative test), no regime/risk/strategy/signal/trade.

## Tests
+11 focused (valid sealed D1 latest -> active + removed from gated; missing/invalid/partial/wrong-anchor/XAUUSD -> PENDING;
helper unit; validator forbids both active+gated; validator accepts active-when-not-gated; builder default still PENDING+valid;
no consumer/Falcon/source tokens in helper). PR#86 compat + control-plane + D1 + publisher-runtime = 67 passed.
FULL SUITE: branch == pristine main 54c8eb4 (51 failed + 4 collection errors, identical pre-existing env/plugin noise) -> 0 NEW failures; +11 passing (1147 vs 1136).

## Boundaries (CODE-ONLY)
No runtime mutation. No deploy. No activation. No Redis writes (fake-client tests) or deletes. No SQL. No backfill. No market_map.
No Falcon/consumer-live. No D1 derived-source drift. No D1 latest misuse (not used as derived fallback). No secrets. M1-H4 manifest unchanged.
