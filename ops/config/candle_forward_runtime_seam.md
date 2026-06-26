# Config Governance — HERMES candle-forward runtime seam (INERT)

**WO:** WO-HELM-HERMES-CANDLE-FORWARD-RUNTIME-WIRE-INERT-0001 · **Owner:** HELM (HERMES lane) · **Default:** DISABLED

| Key | Default | Description | Activation gate |
|-----|---------|-------------|-----------------|
| `HERMES_CANDLE_FORWARD_ENABLED` | `false` | Master gate for the runtime candle-forward emit seam. When false (default) the runtime constructs a no-op `DisabledCandleEmitter` — structurally aware, writes nothing, requires no sink config. | Architect-authorised activation only (and even enabled, this WO emits nothing). |
| `HERMES_CANDLE_FORWARD_SINK` | *(none — required iff enabled)* | Explicit forward sink mode. No hidden default — if `HERMES_CANDLE_FORWARD_ENABLED=true` and this is unset, the runtime FAILS LOUD. Modes: `none`/`inert` (governed `NoWriteCandleSink`, no write); `shadow` (dev `SerializingCandleShadowWriter` → dev Redis 6380, **M1/M5/M15/H1 only**, `hermes:shadow:candles:*`); `canonical`/`live`/`prod` → FAIL LOUD `GOV-CANDLE-FWD-SEAM-002`. | Shadow activation and canonical publish each require a separate, authorised, R2D2-audited WO. |

### Shadow sink (`HERMES_CANDLE_FORWARD_SINK=shadow`) — dev only, M1/M5/M15/H1

Wired by `WO-HELM-HERMES-CANDLE-FORWARD-SHADOW-WRITER-WIRE-DEV-0001`; the DIRECT-NATIVE grid was extended to **M1/M5/M15/H1** by `WO-HELM-HERMES-GOLD-MTF-CANDLE-CONTRACT-EXTEND-0001` (R2D2 ruling `GREEN_DESIGN_RULING_SHADOW_WRITER_DIRECT_NATIVE_ONLY`, GOLD-MTF extension). DIRECT-NATIVE means `source_count=expected=1`, `coverage=1.0`, `DIRECT_FROM_SOURCE`, `NONE_DIRECT`, `source_policy_epoch=DIRECT_NATIVE_V1`. Deferred runtime timeframes (`D1`/`H4`/`D`) are **skipped** with `UNSUPPORTED_TIMEFRAME` (counter+log), never remapped, never derived from stale SQL. Writes **only** `hermes:shadow:candles:{instrument}:{M1|M5|M15|H1}:latest:v1` to dev Redis 6380 (JSON string, `SET EX` per timeframe; M1=90s, M5=360s, M15=1080s, H1=3900s). Instruments are canonicalised (`XAUUSD`→`XAU_USD`), canonical id only, never dual-written. The contract also carries deterministic geometry (`body_high`/`body_low`/`body_size`/`range_size`/`wick_high`/`wick_low`/`candle_direction`) — raw market-truth only, **no regime**. Canonical stays dark. **Not enabled in this PR.**

> **M15 production note:** the contract + seam are M15-ready, but the live `CandleAggregator` (`models/candle.Timeframe`) is intentionally left unchanged in this PR — it produces M1/M5/H1/D1. Wiring M15 aggregation is a future activation WO; nothing here activates it.

| Key | Default | Required when shadow | Notes |
|-----|---------|----------------------|-------|
| `HERMES_CANDLE_FORWARD_SHADOW_REDIS_HOST` | *(none)* | yes (no default) | dev Redis host |
| `HERMES_CANDLE_FORWARD_SHADOW_REDIS_PORT` | *(none)* | yes | e.g. 6380 |
| `HERMES_CANDLE_FORWARD_SHADOW_REDIS_DB` | *(none)* | yes | e.g. 0 |
| `HERMES_CANDLE_FORWARD_SHADOW_AUTHORISED` | `false` | must be `true` | else `GOV-CANDLE-PUB-SHADOW-001` |
| `HERMES_CANDLE_FORWARD_SHADOW_TREAT_AS_PRODUCTION` | `false` | no | localhost-as-prod → fail loud |
| `HERMES_CANDLE_FORWARD_SHADOW_DEV_SHADOW` | `false` | when host is loopback | dev marker |

H4 derivation and D-anchor ratification (`NY_1700_FOREX`) are deferred to later WOs; canonical publish is a separate gate.

```json
{
  "llm_reasoning": "The candle-forward path is trading-sensitive market-truth publication and must never be enabled by a hidden default. The shadow sink is now wired (DIRECT-NATIVE M5/H1 only, dev Redis 6380, hermes:shadow:candles:* only) but remains disabled-by-default and is NOT activated by this build PR. Enabling without an explicit sink mode fails loud; shadow requires explicit host/port/db + authorisation (no defaults, no localhost-as-prod); canonical/live/prod fail loud. Unsupported timeframes are skipped, never remapped or stale-SQL-derived. No Proteus, no legacy candle pubsub, no canonical writer.",
  "owner": "HELM/HERMES",
  "default_disabled": true,
  "shadow_supported_timeframes": ["M1", "M5", "M15", "H1"],
  "deferred": ["M15_aggregator_production", "H4_derivation", "D_anchor_NY_1700_FOREX", "canonical_publish", "backfill", "falcon_consumer"],
  "fault_codes": ["GOV-CANDLE-FWD-SEAM-001", "GOV-CANDLE-FWD-SEAM-002", "GOV-CANDLE-FWD-SEAM-003", "GOV-CANDLE-FWD-SEAM-004", "GOV-CANDLE-PUB-SHADOW-001..004"]
}
```
