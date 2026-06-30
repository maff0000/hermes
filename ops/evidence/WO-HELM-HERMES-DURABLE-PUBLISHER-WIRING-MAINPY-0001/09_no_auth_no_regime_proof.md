# 09 — No-auth / no-regime proof

## No authentication / ACL / credential / NOAUTH / new secrets
- No Redis AUTH/ACL/credential gate added; no change to Redis security posture; no new secret introduced.
- `utils/hermes_publisher_runtime_v1.py` — scanned for `password= / requirepass / .auth( / acl setuser /
  username= / ssl= / noauth` → **none**. Test: `test_runtime_module_no_auth_markers`.
- `utils/hermes_runtime_publisher_steps_v1.py` — scanned for Redis-auth markers `requirepass / .auth( /
  acl setuser / noauth / ssl=` → **none**. Test: `test_steps_module_no_redis_auth_markers`. (The only
  `password=` in the steps module is the **pre-existing governed DB read** `pymysql.connect(... password=
  c["password"] ...)` sourced from `env_config.get_db_config()` — reading existing config for `trading_windows` /
  classification config, exactly as the prior detached scripts did. No NEW credential, no Redis auth.)
- The Redis client is built only from existing `HERMES_CANDLE_CANONICAL_REDIS_*` host/port/db env (no password
  field) — unchanged posture.

## No regime / risk / decision / trade / signal / ARES interpretation
- HERMES publishes deterministic facts only. Interpretive **field keys** (regime/regime_confidence/risk/decision/
  trade/signal/smart_money/order_block/liquidity/bias/setup/go_no_go) are rejected at build/validate time by the
  contract modules every step calls (`hermes_levels_v1`, `hermes_candle_features_v1`, `hermes_indicators_v1`,
  `hermes_sessions_v1`, `hermes_control_plane_v1`). No new field is added by the steps beyond the deterministic
  facts the detached scripts already published (+ the closed-H1 `level_semantics` metadata).
- No ARES interpretation logic / legacy mutation implemented: scanned both new modules for `regime_detector /
  .detect_regime / hermes:signals / set_market_map / import market_map / order_block_meaning / smart_money_zone /
  go_no_go / decision_gate` → **none**. Test: `test_no_regime_or_ares_interpretation_logic`.
- Control-plane health/heartbeat/manifest reflect only family presence/freshness/state — no regime/risk fields
  (covered by existing `test_health_no_regime_or_risk_fields`).
