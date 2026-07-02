# 02 — Quote contract v1 schema (+ tick reference)

**Quote key:** `hermes:quote:XAU_USD:v1` (versioned; XAU_USD output only; XAUUSD = inbound alias only).
**Tick key:** `hermes:ticks:XAU_USD:latest:v1` — EXISTING (`tick_contract_v1`), REFERENCED, not rebuilt/duplicated.
**Default:** builder PURE (payloads only); publisher foundation DISABLED (`HERMES_QUOTE_PUBLISH_ENABLED` unset →
`DisabledQuotePublisher`); enabled-without-authorised → `SystemExit(101)`. **Nothing published this WO.**

## Status: GREEN / AMBER_STALE / RED_MISSING / GATED / NOT_IMPLEMENTED / UNKNOWN_NEEDS_PROBE
- connectivity UNKNOWN → UNKNOWN_NEEDS_PROBE; bid/ask missing → RED_MISSING; bid/ask present but NO source
  timestamp → UNKNOWN_NEEDS_PROBE (no false GREEN); OFFLINE → RED_MISSING; age>threshold or DEGRADED → AMBER_STALE;
  else GREEN.

## Quote payload
schema_version, contract, instrument, canonical_instrument, generated_at_utc, source{name,connectivity}, status,
fault, freshness, bid, ask, mid, spread, spread_points (null unless governed point_size), spread_bps,
last_quote_utc, received_at_utc, source_timestamp_utc, age_seconds, ttl_seconds(15), source_dependencies,
publisher, provenance{method_config: stale_threshold_seconds, point_size}, notes, deterministic_only=true.

## Validation (deterministic, fail-loud)
bid/ask numeric when present; **ask≥bid else GOV-HERMES-QT-006/-018 fail loud**; spread=ask-bid; mid=(bid+ask)/2;
spread_bps=(spread/mid)·10000; UTC-only; governed stale threshold (default 10s); missing source ts → never GREEN;
unknown → loud; no output key may contain `:XAUUSD:`; forbidden-field-KEY scan (no regime/risk/decision/signal/
entry-exit/ARES).

## Reconciliation (deterministic quote fields ONLY — interpretation dropped)
- `reconcile_quote_from_tick_envelope(tick_env)` — existing tick → quote (bid/ask/timestamps; tick status recomputed).
- `reconcile_quote_from_legacy_snapshot(snap, origin=...)` — `hermes:signals:*` / legacy market_map → quote; extracts
  only bid/ask (+ timestamp); **mid/spread RECOMPUTED (legacy values never trusted)**; every signal/decision/regime/
  market-map interpretation field DROPPED; missing bid/ask stays explicit (RED_MISSING), never fabricated.
