# Runtime Gate Summary — D1 publish (default DISABLED -> no-op)
| Env var | Default | Rule |
|---|---|---|
| HERMES_CANDLE_FORWARD_ENABLED + HERMES_CANDLE_FORWARD_SINK=canonical | off | required for the canonical bus; else DisabledD1Producer |
| HERMES_CANDLE_D1_PUBLISH_ENABLED | false | master D1 enable; false -> DisabledD1Producer (no-op) |
| HERMES_CANDLE_D1_PUBLISH_AUTHORISED | false | enabled w/o authorised -> FAIL LOUD (GOV-CANDLE-D1-WIRE-004) |
| HERMES_CANDLE_D1_INSTRUMENTS | (none) | explicit XAU_USD only; missing/empty -> 005; XAUUSD/non-XAU -> 006 |
| HERMES_CANDLE_D1_SOURCE_TIMEFRAME | H4 | must be H4 (6xH4); anything else (e.g. H1/24xH1) -> 003 |
| HERMES_CANDLE_CANONICAL_* (publish enabled+authorised, redis host/port/db) | (none) | canonical bus target, required when enabled |
Deploying this code with the D1 flag unset keeps D1 dark. Activation is a separate WO.
