# WO-HELM-HERMES-D1-CATALOG-CANDLES-TRUTH-0001 — HELM verdict (CODE-ONLY FIX PR)

## GREEN_D1_CATALOG_CANDLES_TRUTH_PR_READY
Tracking: HELM_HERMES_D1_CATALOG_CANDLES_TRUTH_PR::2026-07-12T10:00Z::GREEN_D1_CATALOG_CANDLES_TRUTH_PR_READY

Authority: R2D2 combined deploy-dark audit GREEN (…PR86_PR87_DEPLOY_DARK_AUDIT_APPROVED) — ruled catalog:candles D1 the last blocker to PH1 D1 closeout.
Branch: wo/WO-HELM-HERMES-D1-CATALOG-CANDLES-TRUTH-0001   Base: 0908cfc7c78f4c112542fcd0f7b66ecd6663ee88
Files changed (3): utils/hermes_control_plane_v1.py (+13/-4), utils/hermes_runtime_publisher_steps_v1.py (+37), tests/test_d1_catalog_candles_truth_v1.py (NEW)

## Root cause
build_candle_catalog (_catalog_entry, pure) + validate_candle_catalog HARDCODED the D1 entry:
latest_status=PENDING_FIRST_DAILY_SEAL, history_status + forward_history_status=BLOCKED_UNTIL_D1_LATEST_GREEN, and required them.

## Exact code change (validated runtime truth; no hardcoding; no overclaim)
- steps._d1_latest_active (REUSED from PR#87): validated sealed D1 latest -> latest_status ACTIVE.
- steps._d1_history_active (NEW): D1 history index exists + depth>0 + newest member re-validates via _read_d1_history_validated
  (assert_sealed_complete_d1 + 22:00 anchor + no XAUUSD) -> history_status ACTIVE. Missing/empty/malformed -> BLOCKED.
- steps._d1_forward_writer_active (NEW): HERMES_CANDLE_D1_HISTORY_ENABLED AND _AUTHORISED gates (governed config truth,
  NOT code-presence; enabled-without-authorised -> not active) -> forward_history_status ACTIVE.
- control_plane_step: after b.candle_catalog(...), overrides D1 latest/history/forward statuses to ACTIVE ONLY when each
  truth holds; clears the D1 caveat note when all three active; cp.validate_candle_catalog re-validates before publish.
- validate_candle_catalog relaxed: D1 latest_status in {PENDING, ACTIVE}; history_status in {BLOCKED, ACTIVE};
  forward_history_status in {BLOCKED, ACTIVE} (GOV-HERMES-CP-035/036/037). M1-H4 status assertions unchanged.

## Catalog truth findings (all tested)
- D1 latest: ACTIVE only when sealed-6/6-22:00-XAU_USD valid; missing/invalid/partial(<6)/00:00-anchor/XAUUSD -> PENDING.
- D1 history: ACTIVE only when index depth>0 AND newest member valid; missing index / invalid member -> BLOCKED.
- D1 forward-history: ACTIVE only when writer gates enabled+authorised; gates off / enabled-without-authorised -> BLOCKED.
- fully live -> all three ACTIVE, caveat note cleared, validate OK.

## No overclaim / no source drift
Missing/invalid/unsealed/wrong-anchor/XAUUSD/gates-off can NEVER become ACTIVE (fail-closed). D1 latest is read ONLY for
catalog:candles truth about D1 latest — NOT used as a fallback source for derived indicators/features/levels (those still
source governed D1 history only; _read_d1_history_validated has no :D1:latest: fallback). No SQL/pymysql/get_db_config/market_map/fake.

## Compatibility
M1-H4 catalog latest/history unchanged. PR#86/#87 manifest truth unchanged (indicators.D1/candle_latest.D1 ACTIVE, D1 gated removed).
consumer_live untouched (catalog has no consumer_live field). No Falcon/downstream logic. No regime/risk/strategy/signal/trade.

## Tests
+10 focused (fully-live all ACTIVE + note cleared; latest missing/invalid/partial/wrong-anchor/XAUUSD -> PENDING; history
missing/invalid -> BLOCKED; forward gates off/on -> BLOCKED/ACTIVE + enabled-without-authorised not active; M1-H4 catalog +
PR#86/#87 manifest unchanged + no deletes; no forbidden semantics/source in helpers). Compat (control-plane + manifest-truth +
candle-latest + D1 + publisher-runtime) 70 passed. FULL SUITE: branch == pristine main 0908cfc (51 failed + 4 collection errors,
identical pre-existing env/plugin noise) -> 0 NEW failures; +10 passing (1157 vs 1147).

## Boundaries (CODE-ONLY)
No runtime mutation. No deploy. No activation. No Redis writes (fake-client tests) or deletes. No SQL. No backfill. No market_map.
No Falcon/consumer-live. No D1 derived-source drift. No D1 latest derived-fallback. No secrets.
