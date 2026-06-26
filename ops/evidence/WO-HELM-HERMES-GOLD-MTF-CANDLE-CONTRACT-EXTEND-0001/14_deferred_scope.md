# Deferred Scope (deliberate, documented — no silent caps)

| Deferred item | Why | Where it lands |
|---------------|-----|----------------|
| **M15 aggregator production** | `models/candle.Timeframe` was intentionally **not** modified. Adding `M15` to that enum makes the live `CandleAggregator` start aggregating M15 (verified: `test_flush_all` would flush 5 not 4) — an unrequested runtime behaviour change bordering on activation. The contract + seam are M15-**ready** (string-keyed); production wiring is a separate activation WO. | Future activation WO |
| **H4 derivation** | The derived (lower-TF→higher-TF) path stays deferred; H4 is recognised by the contract but skipped by the seam (`UNSUPPORTED_TIMEFRAME`). | Future derived-path WO |
| **D / D1 anchor** | Daily anchor ratification (`NY_1700_FOREX`) deferred; skipped by the seam. | Future anchor WO |
| **Canonical publish** | `hermes:candles:*` stays dark; no canonical writer wired. | Separate gated publish WO |
| **`wick_profile` / `range_state` / `volatility_state`** | These need the governed classifier (`candle_features.classify`) + DB-loaded thresholds. Out of this PR's deterministic-basics scope; adding them without governed config would smuggle interpretation into HERMES. | Future governed-features WO |
| **Shadow activation** | Code wired + ready but disabled by default; needs explicit Redis host/port/db + authorisation. | Authorised activation WO |
| **Backfill** | Forbidden by this WO; not done. | n/a |
| **Falcon/consumer wiring** | Consumers PULL; no consumer touched. | Downstream WO |

Nothing above is silently dropped — each is recorded here and in `ops/config/candle_forward_runtime_seam.md`.
