# Part C — Ownership Classification (every market_map.py output)
| Output | Classification |
|---|---|
| current_session (UTC-window) | HERMES_DETERMINISTIC_SESSION_FACT |
| session open/close times | HERMES_DETERMINISTIC_SESSION_FACT |
| Asia/London/NY session range high/low | HERMES_DETERMINISTIC_LEVEL_FACT |
| prior day high/low (PDH/PDL) | HERMES_DETERMINISTIC_LEVEL_FACT (D1-derived -> GATED until D1 latest GREEN) |
| ADR(20) average daily range | HERMES_DETERMINISTIC_LEVEL_FACT (D1-derived -> GATED) |
| range midpoint | HERMES_DETERMINISTIC_LEVEL_FACT |
| current price | (market data; already in candle keys) |
| instrument list / windows | HERMES_INSTRUMENT_METADATA (governed config) |
| (none found) regime/risk/liquidity/order_block/smart_money/decision | ARES_INTERPRETIVE_CONTEXT — NONE present in market_map outputs |
No UNKNOWN_REQUIRES_ARCHITECT_RULING items: all outputs are deterministic HERMES-owned. The legacy hermes:market_map:* key shape is LEGACY_DEPRECATED (frozen).
