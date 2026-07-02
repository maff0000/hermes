# 01 — Discovered existing HERMES catalog/control-plane conventions (followed)

| Convention | Source | Applied |
|---|---|---|
| Key style `hermes:<family>:XAU_USD:v1` | sibling contracts | `hermes:instrument_catalog:XAU_USD:v1` |
| Sibling contract KEY builders | `candle_contract_v1.canonical_key`, `candle_history_v1.history_index_key`, `hermes_indicators_v1.indicator_key`, `hermes_candle_features_v1.candle_features_key`, `hermes_sessions_v1.sessions_key`, `hermes_levels_v1.levels_key`, `hermes_feed_health_v1.feed_health_key` | composed via these (no hard-coded key strings) |
| Control-plane manifest/catalog keys | `hermes_control_plane_v1.KEY_*` | referenced as discovery facts (not mutated) |
| Candle catalog family/status shape | `hermes_control_plane_v1` manifest active/gated/not_implemented families | surface status vocab mirrors it |
| UTC helpers `_fmt`/`normalise_utc` | `candle_contract_v1` | all timestamps via `_utc()` → ISO `Z` |
| `TF_SECONDS` / supported TFs M1..H4/D1 | `candle_contract_v1` | `SUPPORTED_TIMEFRAMES` |
| D1 gating (`PENDING_FIRST_DAILY_SEAL`, `BLOCKED_UNTIL_D1_LATEST_GREEN`) | `hermes_control_plane_v1` | D1 latest PENDING; D1 history BLOCKED; D1 indicators/features/levels GATED |
| Feed-health contract (PR #66) | `hermes_feed_health_v1` | represented CODE_PRESENT_DARK (merged, not activated) |
| Legacy `hermes:signals:*` / `hermes:market_map:*` | prior WOs (FROZEN_PENDING_CONSUMER_CUTOVER) | represented LEGACY / LEGACY_OR_PARTIAL — preserved, not deleted |
| TTL / self-expiring liveness | `hermes_control_plane_v1` heartbeat TTL | `ttl_seconds=300` |
| Forbidden-field-KEY recursive scan | control-plane / feed-health | `_scan_no_forbidden_field_keys` |
| Gated publisher foundation (Disabled default; enabled-without-authorised → SystemExit(101); build-only) | sibling contracts | `build_instrument_catalog_publisher_from_env` |

Nothing invented; no unversioned keys; contract keys composed via governed sibling builders.
