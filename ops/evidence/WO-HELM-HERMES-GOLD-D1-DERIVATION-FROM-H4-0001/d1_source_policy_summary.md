# D1 Source Policy Summary
**WO-HELM-HERMES-GOLD-D1-DERIVATION-FROM-H4-0001 · code-only · HELM/HERMES**

- PRIMARY source = **6 x H4** (the NY-5PM H4 grid 22/02/06/10/14/18 UTC nests exactly into one D1 day).
- Daily boundary is **FIXED 22:00 UTC**: a D1 bucket OPENS at 22:00Z and spans 22:00Z -> next 22:00Z.
- **No UTC-midnight D1** (the midnight-anchored `candles_D1` table is rejected as a source).
- **No 24 x H1 production shortcut** — H1 may appear only as an audit/cross-check note, never as a source path.
- No DST-shifting / broker-local opaque anchor (fixed UTC grid).
- provenance = DERIVED_FROM_LOWER_TIMEFRAME; policy = `DERIVED_D1_FROM_H4`; epoch = `D1_FROM_H4_NY1700_FIXED_UTC_V1`.
- Instrument: XAU_USD only (alias XAUUSD / non-XAU rejected GOV-CANDLE-D1-001).
