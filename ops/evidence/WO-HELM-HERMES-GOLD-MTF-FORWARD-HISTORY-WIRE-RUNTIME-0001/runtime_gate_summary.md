# Runtime Gate Summary
Wiring is inert until ALL of these are set (default DISABLED -> latest-only, unchanged runtime):

| Env var | Default | Rule |
|---|---|---|
| HERMES_CANDLE_HISTORY_FORWARD_ENABLED | false | false -> DisabledHistoryForwardWriter attached (no-op) |
| HERMES_CANDLE_HISTORY_FORWARD_AUTHORISED | false | enabled w/o authorised -> FAIL LOUD (GOV-CANDLE-HIST-FWD-002) |
| HERMES_CANDLE_HISTORY_FORWARD_TIMEFRAMES | (none) | explicit ⊆ {M1,M5,M15,H1,H4}; missing -> 003; D1/D -> 005; off-grid -> 007 |
| HERMES_CANDLE_HISTORY_FORWARD_INSTRUMENTS | (none) | explicit; missing -> 004; non-XAU -> 006; XAUUSD canonicalised to XAU_USD |
| HERMES_CANDLE_CANONICAL_REDIS_HOST/PORT/DB | (none) | explicit bus target, required when enabled |

The writer is only built on the canonical sink path (HERMES_CANDLE_FORWARD_ENABLED + SINK=canonical). With
history gates unset, the seam/producer behave exactly as before. Activation (setting these flags + deploy) is
a SEPARATE WO — NOT done here.
