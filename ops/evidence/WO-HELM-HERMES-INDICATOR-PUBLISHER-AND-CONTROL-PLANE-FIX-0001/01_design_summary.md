# Design Summary — HERMES Indicator Publisher + Control-Plane Health Fix
**WO-HELM-HERMES-INDICATOR-PUBLISHER-AND-CONTROL-PLANE-FIX-0001 · code-only · HELM/HERMES**

## Part A — control-plane health self-listing fix (utils/hermes_control_plane_v1.py)
build_health_summary gains control_plane_active (default False) + indicators_built (default False). When
control_plane_active=True: contract_manifest/publisher_heartbeat/candle_catalog/health_summary report ACTIVE +
control_plane_health=ACTIVE and are NOT listed under missing_but_expected_families; genuinely-missing surfaces
(indicators, candle_features, feed_health, instrument_catalog, sessions, candle_history_d1, ticks) stay listed.
indicators_built=True -> BUILT_NOT_ACTIVE (never falsely ACTIVE). New status BUILT_NOT_ACTIVE.
Heartbeat TTL policy: heartbeat_ttl_policy() — refresh 60s / TTL 180s (TTL>refresh, liveness); manifest/catalog/
health persistent (no TTL), refreshed + timestamped. Code-only; no live TTL change here.

## Parts C+D — indicator contract + disabled publisher (utils/hermes_indicators_v1.py)
Per-timeframe versioned key hermes:indicators:XAU_USD:{TF}:v1 (cleaner than one blob). build_indicator_contract:
publisher HERMES, contract_version v1, instrument XAU_USD only, generated_at_utc + value_open_time_utc (UTC),
source_candle_contract v1, source_timeframes, indicator_set_version, indicators{}, freshness_state,
deterministic_only=true. validate rejects regime/regime_confidence/risk/order_block/liquidity/decision/trade
field keys. Publisher foundation DISABLED by default; ENABLED-without-AUTHORISED -> SystemExit(101); explicit
INSTRUMENTS (XAU_USD only) + TIMEFRAMES; D1 indicators GATED until HERMES_INDICATOR_D1_AUTHORISED (D1 latest
GREEN). Zero Redis/network/SQL/file I/O at import or anywhere. No auth.
