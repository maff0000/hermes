# 01 — Catalog reconcile: quote/tick dark-state summary

## Discovered
- Catalog default (pre-WO) declared: `quote = NOT_IMPLEMENTED (key None)`, `tick = NOT_IMPLEMENTED (key None)`.
- Merged quote contract key (`utils/hermes_quote_tick_contract_v1.quote_key`): **`hermes:quote:XAU_USD:v1`** (PR #68, dark).
- Existing tick contract key (`utils/tick_contract_v1.canonical_key`): **`hermes:ticks:XAU_USD:latest:v1`** (code-present/shadow).

## Change (code-only declaration correction)
| Surface | Before | After |
|---|---|---|
| quote status | `NOT_IMPLEMENTED` | **`CODE_PRESENT_DARK`** |
| quote key | `None` | **`hermes:quote:XAU_USD:v1`** (referenced) |
| tick status | `NOT_IMPLEMENTED` | **`CODE_PRESENT_DARK`** |
| tick key | `None` | **`hermes:ticks:XAU_USD:latest:v1`** (EXISTING, referenced — no duplicate) |

Both carry `live: false` (key is a discovery fact, NOT a liveness/publication claim). Added quote+tick to
`contract_keys`. No duplicate `hermes:tick:XAU_USD:v1` introduced. Keys composed via the sibling builders
(`qt.quote_key`, `tickc.canonical_key`) through `_safe_key` — no hard-coded strings.

## Preserved truth (unchanged)
- **D1 default stays PENDING/gated** — NOT hard-coded ACTIVE despite the verified live seal; D1 goes ACTIVE only if
  a snapshot explicitly provides it (test proves both). D1 history/indicators/features/levels still gated.
- **feed_health** stays `CODE_PRESENT_DARK`. **control_plane/candles/indicators/features/sessions/levels** unchanged.
- **Legacy** `hermes:signals:*` = LEGACY, `hermes:market_map:*` = LEGACY_OR_PARTIAL — preserved, not deleted.
- Canonical `XAU_USD` only; XAUUSD inbound-alias-only; no output key contains `:XAUUSD:`. No ARES/interpretation.
