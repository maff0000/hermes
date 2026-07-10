# WO-HELM-HERMES-D1-MANIFEST-TRUTH-0001 — HELM verdict (CODE-ONLY FIX PR)

## GREEN_D1_MANIFEST_TRUTH_PR_READY
Tracking: HELM_HERMES_D1_MANIFEST_TRUTH_PR::2026-07-10T17:10Z::GREEN_D1_MANIFEST_TRUTH_PR_READY

R2D2 activation audit consumed:
R2D2_HERMES_D1_SURFACES_ACTIVATION_AUDIT::2026-07-10T16:48:21Z::GREEN_D1_INDICATORS_FEATURES_LEVELS_ACTIVATION_AUDIT_APPROVED
(ruling: D1 surfaces live+accepted but PH1 D1 not runtime-complete until manifest reflects live D1 truth)

Branch: wo/WO-HELM-HERMES-D1-MANIFEST-TRUTH-0001   Base: 492b2ee33386cb0204921f47a6e701b938b4c3ec
Files changed (2): utils/hermes_runtime_publisher_steps_v1.py (+41/-10), tests/test_d1_manifest_truth_v1.py (NEW)

## Root cause
control_plane_step live-family detection used _live_tfs (hardcoded LATEST_TFS = M1-H4) and ALWAYS added
indicators_d1/candle_features_d1/levels_d1 to gated_families when the M1-H4 families were live — so the manifest
listed D1 as gated even after D1 surfaces went live.

## Exact code change (gate + live-state derived; no assumption; no hardcoded-true)
New helpers:
- _d1_authorised(env) -> env bool (HERMES_INDICATOR/CANDLE_FEATURE/LEVEL_D1_AUTHORISED), no hidden default (false unless set)
- _d1_family_active(client, family, env) -> gate true AND hermes:{family}:XAU_USD:D1:v1 exists
- _daily_levels_active(client, env) -> HERMES_LEVEL_D1_AUTHORISED true AND hermes:levels:XAU_USD:daily:v1 exists
control_plane_step:
- ind_live/feat_live = M1-H4 (_live_tfs) + ["D1"] iff D1 family active; lvl_live = session/intraday + ["daily"] iff daily active
- active_families reflects D1/daily as ACTIVE when active
- gated_families indicators_d1/candle_features_d1/levels_d1 added ONLY when NOT active (removed when active)
- health per_family_health.levels_d1 = ACTIVE when daily active else GATED
- ind_active/feat_active (health completeness) still computed on M1-H4 (_live_tfs) — UNCHANGED

## Manifest truth before/after (expected once deployed)
- indicators authorised+live -> active_families.indicators includes D1=ACTIVE; indicators_d1 not in gated_families
- candle_features authorised+live -> D1=ACTIVE; candle_features_d1 not gated
- daily authorised+live -> active_families.levels includes daily=ACTIVE; levels_d1 not gated
- any D1 gate false OR key absent (warm-up/residual) -> D1 NOT active, stays gated (NO overclaim)

## No overclaim / no unsafe logic
Gate false + residual D1 key present -> NOT active (test). Gate true + key absent (warm-up) -> NOT active (test).
Only both -> active. consumer_live not touched (manifest has no consumer_live field). No Falcon/downstream logic added.

## Source safety (no drift)
Helpers read ONLY env bool + Redis key existence. No candle source path introduced. Grep of added lines: no pymysql,
no get_db_config, no candles_H4/M30, no :D1:latest: fallback, no market_map, no XAUUSD, no regime/risk/strategy/signal/trade.
D1 derived surfaces still derive from governed D1 history (that logic — _d1_tf_if_ready/_read_d1_history_validated — is untouched).

## Tests (6 focused, all pass)
active when authorised+live (D1 in active_families, out of gated); gated when gate false even with residual key (no overclaim);
gated when authorised but key absent (warm-up); per-family independence; health levels_d1 ACTIVE + no Redis deletes + helpers
add no forbidden semantics; no source-path drift. Touched-area (publisher_runtime + D1 + sessions_levels) 65 passed.
FULL SUITE: branch == pristine main 492b2ee (51 failed + 4 collection errors, identical pre-existing env/plugin noise) -> 0 NEW failures; +6 passing (1136 vs 1130).

## Boundaries (CODE-ONLY)
No runtime mutation. No deploy. No activation. No Redis writes (fake-client tests only) or deletes. No SQL. No backfill.
No market_map. No Falcon/consumer-live. No D1 source drift. No secrets. M1-H4 manifest/health behaviour unchanged.
