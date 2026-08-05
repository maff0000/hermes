# ADR-001 — One reusable, registry-driven HERMES pipeline

**Status:** Proposed (design-only). **Decision authority:** Chief Architect after R2D2 assurance.

## Context
Advanced contracts are an XAU pilot bound by a module constant in 15 files; schemas/keys are already
generic. We must onboard instruments by data alone.

## Decision
Adopt **one reusable pipeline** consuming **one canonical registry**. The mandated flow — every stage
reads the SAME registry:

```
CANONICAL INSTRUMENT REGISTRY (SQL instruments table)
  -> OANDA SUBSCRIPTION (built from enabled+oanda_compatible rows)
  -> TICK NORMALISATION -> TICK CONTRACT (hermes:ticks:<INST>:latest:v1)
  -> CANDLE ENGINE -> CANDLE/WICK GEOMETRY (hermes:candles:<INST>:<TF>:latest:v1 + SQL candles_<TF>)
  -> INDICATOR ENGINE (hermes:indicators:<INST>:<TF>:v1)
  -> GAP DETECTION (hermes:gaps:<INST>:v1)
  -> BACKFILL STATE (hermes:backfill:status:<INST>:v1)
  -> SQL/REDIS PUBLICATION (one key factory + one serializer)
  -> HEALTH/STATUS/METRICS (auto-enumerated from registry)
```

## Reusable components (exactly one implementation each — §8)
registry loader · metadata validator · OANDA subscription builder · tick normaliser · tick contract
publisher · candle engine · candle geometry calculator · wick calculator · indicator engine · indicator
publisher · gap detector · backfill state machine · **Redis key factory** · Redis serializer · SQL writer ·
freshness evaluator · health aggregator · evidence generator.

Every component signature accepts `(instrument_identity, instrument_metadata, timeframe?, governed_payload)`.
**No component may assume `XAU_USD`.** Instrument-specific behaviour is expressed as registry metadata or a
**reusable policy class selected by a metadata key** (e.g. `market_hours_policy`, `indicator_profile`,
`backfill_policy`) — never a ticker-name branch.

## Consequences
- The 15 modules drop their `CANONICAL_INSTRUMENT` constant; the allowlist becomes `enabled_instruments()`
  from the registry. Their fail-loud parsers keep the alias/unknown guards but accept the registry set.
- The scattered per-module `canonical_key()` functions are replaced by one `utils/hermes_redis_keys_v1.py`
  factory; existing key STRINGS are preserved byte-for-byte (no consumer break).
- Onboarding = one registry row. Capability flags on the row gate activation; the code path is identical.

## Prohibited (RED if present — §7)
`if instrument == "..."` branches · `calculate_xau_*` / `publish_eur_tick` / `build_wtico_candles` ·
`xau_indicator_engine.py` etc. · per-ticker SQL table / Redis schema / test file / health logic ·
manual per-file instrument enumeration · duplicate formula/serializer implementations · one deploy per ticker.

## Alternatives rejected
- *Copy XAU seven times* — violates the core ruling; unmaintainable; RED.
- *Per-instrument microservices/containers* — one-deploy-per-ticker; rejected.
- *Config in code* — rejected (registry is data in governed SQL).
