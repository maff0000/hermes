# Config / Gate Summary
All explicit, fail-closed, no hidden defaults. DEFAULT DISABLED -> DisabledHistoryForwardWriter (no-op).

| Env var | Default | Rule |
|---|---|---|
| HERMES_CANDLE_HISTORY_FORWARD_ENABLED | false | master enable; false -> no-op writer |
| HERMES_CANDLE_HISTORY_FORWARD_AUTHORISED | false | enabled w/o authorised -> FAIL LOUD (GOV-CANDLE-HIST-FWD-002) |
| HERMES_CANDLE_HISTORY_FORWARD_TIMEFRAMES | (none) | explicit list ⊆ {M1,M5,M15,H1,H4}; missing/empty -> 003; D1/D -> 005; off-grid -> 007 |
| HERMES_CANDLE_HISTORY_FORWARD_INSTRUMENTS | (none) | explicit allowlist; missing/empty -> 004; non-XAU -> 006; XAUUSD canonicalised to XAU_USD |
| HERMES_CANDLE_CANONICAL_REDIS_HOST/PORT/DB | (none) | explicit bus target (reused from canonical contract), required when enabled |

Error-code namespace: GOV-CANDLE-HIST-FWD-001..020. Runtime skip (no raise): FORMING_NOT_HISTORY,
STATUS_NOT_OK, TF_NOT_CONFIGURED, INSTRUMENT_NOT_ALLOWLISTED. TTL = 3,024,000s (35d) from the history contract.
