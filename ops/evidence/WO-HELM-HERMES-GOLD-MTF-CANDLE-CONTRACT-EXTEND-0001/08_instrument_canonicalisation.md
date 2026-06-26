# Instrument Canonicalisation — XAUUSD → XAU_USD

## Rule
Publish **only** the canonical instrument id. The seam maps known broker aliases one-way before building
the contract; the alias form is never published and never dual-written.

```python
# utils/candle_runtime_seam_v1.py
INSTRUMENT_ALIASES = {"XAUUSD": "XAU_USD"}

def canonical_instrument(instrument):
    return INSTRUMENT_ALIASES.get(instrument, instrument)
```

`runtime_candle_to_contract` builds with `instrument=canonical_instrument(candle.instrument)`, so the
contract `key`, the shadow key, and the payload `data.instrument` all carry `XAU_USD`.

## Proof
`test_xauusd_alias_canonicalised_no_dual_publish`:
- `canonical_instrument("XAUUSD") == "XAU_USD"` and `canonical_instrument("XAU_USD") == "XAU_USD"`
- emitting an `XAUUSD` candle writes exactly **one** key: `hermes:shadow:candles:XAU_USD:M5:latest:v1`
- **no** key contains the substring `XAUUSD` (no dual-publish)
- `len(store) == 1`

## Design notes
- The map is **explicit and deliberate** — one entry per proven alias, no fuzzy/regex normalisation, so a
  typo can never be silently "corrected" into a wrong instrument.
- Canonical instruments (the 14 live: `AUD_USD, EUR_GBP, EUR_USD, GBP_USD, NZD_USD, SPX500_USD, USD_CAD,
  USD_CHF, USD_JPY, WTICO_USD, XAG_USD, XAU_USD, XCU_USD, XPT_USD`) pass through unchanged.
