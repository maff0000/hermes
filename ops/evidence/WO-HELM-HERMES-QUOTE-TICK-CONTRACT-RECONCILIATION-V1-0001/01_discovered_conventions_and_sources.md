# 01 — Discovered quote/tick source candidates + legacy quote-field locations

## KEY FINDING — a governed TICK contract already exists (do NOT rebuild)
`utils/tick_contract_v1.py` (WO-HELM-HERMES-REDIS-TICK-CONTRACT-DESIGN-0001) already defines the governed tick
surface: `build_tick_contract` → key **`hermes:ticks:XAU_USD:latest:v1`** (+ aggregate `hermes:ticks:latest:v1`),
data `{instrument, received_at_utc, bid, ask, mid, spread, source, seq}`, statuses OK/WARN/BLOCK/ERROR/UNAVAILABLE,
fail-loud on missing/inverted bid/ask. Also `tick_publisher_v1.py`, `tick_runtime_shadow_adapter_v1.py` (SHADOW).

**Decision:** this WO does NOT build a new `hermes:tick:*` key (that would DUPLICATE the existing tick surface).
It builds the genuinely-missing **QUOTE** contract, REFERENCES the existing tick surface
(`governed_tick_surface_reference`), and reconciles the existing tick envelope + legacy surfaces into the quote schema.

**Reconciliation finding (for a future catalog-reconcile WO — not changed here):** the instrument-catalog (PR #67)
declares `tick = NOT_IMPLEMENTED`; the tick contract is actually CODE_PRESENT (shadow/dark). The catalog default
snapshot should later represent tick as CODE_PRESENT_DARK referencing `hermes:ticks:XAU_USD:latest:v1`.

## Legacy quote-field locations
- `utils/redis_publisher.py` writes bid/ask/mid/spread (string + float) into legacy `hermes:signals:*` hashes.
- Legacy market_map-style payloads carry quote-ish fields alongside interpretation (session bias / liquidity / order-block).
- These are LEGACY, preserved (not deleted/mutated). Consumers can migrate to `hermes:quote:XAU_USD:v1` later.

## Conventions followed
Key style `hermes:<family>:XAU_USD:v1`; `candle_contract_v1` UTC helpers `_fmt`/`normalise_utc`/`_UTC_MS`; feed-health/
instrument-catalog status + forbidden-field-KEY scan + gated publisher (Disabled default; enabled-without-authorised →
SystemExit(101)); short self-expiring TTL for a hot surface; deterministic arithmetic (mid/spread) with fail-loud
inverted-quote guard (mirrors `tick_contract_v1` GOV-TICK-CONTRACT-003).
