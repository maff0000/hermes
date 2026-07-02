# 02 — Current H4 policy confirmed from code (preserved)

From `candle_h4_derivation_v1.py` + `candle_h4_publish_wire_v1.py` (unchanged by this WO):

| Policy | Code evidence |
|---|---|
| Canonical instrument XAU_USD only | `GOV-CANDLE-H4-001` + allowlist reject XAUUSD/non-XAU |
| H4 = four complete OK H1 children | `H4_EXPECTED_CHILDREN=4`; derive_h4 <4 → never OK (SOURCE_INCOMPLETE) |
| Fixed NY-5PM 22:00-UTC grid, no DST | `H4_ANCHOR_HOURS_UTC=(22,2,6,10,14,18)`, fixed epoch shift, no DST |
| H1-only source | derive_h4 source_timeframe=H1; no direct `candles_H4`, no Proteus, no M15 fallback |
| No synthesis / no gap-laundering | "Missing H1 are NEVER synthesised"; honest SOURCE_INCOMPLETE |
| Seal on live roll-over | `on_h1_close` seals the previous bucket when the next bucket's first H1 arrives |
| Deterministic market-data only | no regime/risk/decision fields anywhere |

**Warm-start preserves all of it:** it reuses `h4_bucket_open` / `H4_ANCHOR_HOURS_UTC` / `H4_EXPECTED_CHILDREN` /
`H1_TIMEFRAME`, accepts only complete-OK on-the-hour H1 of the active block, and NEVER publishes/seals — the only
producible H4 is still the existing live roll-over seal.
