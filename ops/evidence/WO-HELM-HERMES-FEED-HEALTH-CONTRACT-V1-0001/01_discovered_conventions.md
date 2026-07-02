# 01 — Discovered existing HERMES health/control-plane conventions (followed)

| Convention | Source | Applied in feed-health |
|---|---|---|
| Key style `hermes:<family>:XAU_USD:v1` | hermes_indicators/candle_features/sessions/levels_v1 | `hermes:feed_health:XAU_USD:v1` |
| Control-plane keys (manifest/heartbeat/catalog/health) | `hermes_control_plane_v1` KEY_* | read-only inputs (heartbeat) — NOT mutated |
| UTC helpers `normalise_utc` / `_fmt` / `_UTC_MS` | `candle_contract_v1` | all timestamps via `_utc()` → ISO `Z` |
| `TF_SECONDS` M1..H4/D1 | `candle_contract_v1` L34 | staleness thresholds + close derivation |
| `FRESHNESS_STATES` (FRESH/STALE/UNAVAILABLE) | `candle_contract_v1` L52 | `freshness` field |
| Candle `status` OK + `is_closed` + `data.timestamp_utc` | candle latest envelope | collector derives last CLOSED close = open + span |
| Heartbeat TTL 180 / refresh 60 | `hermes_control_plane_v1` L67-68 | `ttl_seconds=180` (liveness self-expiry) |
| Forbidden-field-KEY recursive scan | `hermes_control_plane_v1` / `hermes_levels_v1` | `_scan_no_forbidden_field_keys` (regime/risk/decision/ARES + auth tokens) |
| `STATUS_GATED` + D1 gating (never RED until D1 GREEN) | `hermes_control_plane_v1` / `hermes_levels_v1` D1 rules | D1 tf → GATED until `d1_latest_green` |
| Gated publisher foundation (Disabled default; enabled-without-authorised → SystemExit(101); build-only, no Redis) | `hermes_levels_v1` / `hermes_candle_features_v1` | `build_feed_health_publisher_from_env` |
| Redis-target redaction | `hermes_control_plane_v1.redact_redis_target` | no host/port/db in payload; client injected |

Conclusion: feed-health follows the exact established governed-contract shape; nothing invented; no unversioned keys.
