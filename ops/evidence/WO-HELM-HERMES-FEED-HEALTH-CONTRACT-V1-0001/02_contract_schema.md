# 02 — Feed-health contract v1 schema (proposed)

**Key:** `hermes:feed_health:XAU_USD:v1` (versioned; XAU_USD only; no XAUUSD).
**Default behaviour:** builder is PURE (payloads only). Publisher foundation DISABLED by default
(`HERMES_FEED_HEALTH_PUBLISH_ENABLED` unset → `DisabledFeedHealthPublisher`); enabled-without-authorised →
`SystemExit(101)`. **Nothing is published in this WO.**

## Payload
| field | meaning |
|---|---|
| `schema_version` / `contract` | `"v1"` / `"feed_health:v1"` |
| `instrument` / `canonical_instrument` | `"XAU_USD"` |
| `generated_at_utc` | UTC ISO Z |
| `source` | `{name, connectivity}` — connectivity ∈ CONNECTED/DEGRADED/OFFLINE/UNKNOWN |
| `status` | GREEN / AMBER_STALE / RED_MISSING / GATED / UNKNOWN_NEEDS_PROBE |
| `fault` | `{state: NONE|FAULTS_PRESENT, counters: {...}}` |
| `freshness` | FRESH / STALE / UNAVAILABLE |
| `last_tick_utc` / `last_quote_utc` | UTC Z or null |
| `last_candle_utc_by_tf` / `age_seconds_by_tf` | per-TF last CLOSED-candle close + age |
| `timeframes` / `missing_timeframes` / `stale_timeframes` / `healthy_timeframes` / `gated_timeframes` | explicit classification |
| `per_timeframe_health` | per-TF `{timeframe, last_candle_utc, age_seconds, status}` |
| `source_dependencies` | governed upstream dependency list |
| `publisher` / `publisher_identity` | `"HERMES"` / `"hermes-signal"` |
| `ttl_seconds` | 180 (liveness self-expiry) |
| `provenance` | `{deterministic_only, source_timeframes, method_config: {stale_tf_multiplier, stale_threshold_seconds_by_tf, d1_latest_green}}` |
| `notes` | free list |
| `deterministic_only` | `true` |

## Deterministic status logic (fail-loud; missing/stale explicit)
- per-TF: D1 & not d1_latest_green → **GATED**; last close None → **RED_MISSING**; age > `STALE_TF_MULTIPLIER(=2.0)×TF_SECONDS` → **AMBER_STALE**; else **GREEN**.
- aggregate: connectivity UNKNOWN → **UNKNOWN_NEEDS_PROBE**; OFFLINE or any non-gated RED → **RED_MISSING**; DEGRADED or any AMBER → **AMBER_STALE**; else **GREEN**. GATED (D1) alone never forces RED/AMBER.
