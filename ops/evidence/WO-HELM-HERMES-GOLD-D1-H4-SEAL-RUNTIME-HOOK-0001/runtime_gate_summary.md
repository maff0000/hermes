# Runtime Gate Summary
The D1 hook inherits the D1 producer's gates (built by build_d1_producer_from_env, default DISABLED):
| Env var | Default | Rule |
|---|---|---|
| HERMES_CANDLE_D1_PUBLISH_ENABLED | false | false -> DisabledD1Producer -> seal hook no-op |
| HERMES_CANDLE_D1_PUBLISH_AUTHORISED | false | enabled w/o authorised -> FAIL LOUD (GOV-CANDLE-D1-WIRE-004) |
| HERMES_CANDLE_D1_INSTRUMENTS | (none) | explicit XAU_USD only; missing/empty -> 005; XAUUSD/non-XAU -> 006 |
| HERMES_CANDLE_D1_SOURCE_TIMEFRAME | H4 | must be H4 (6xH4); else (e.g. H1/24xH1) -> 003 |
| canonical bus controls (publish enabled+authorised, redis host/port/db, H4 publish enabled) | (none) | preserved/required |
Enabling D1 without authorisation / allowlist / source=H4 fails loud at build_h4_producer_from_env (the hook path).
