# 02 — Current D1 policy confirmed from code

From `utils/candle_d1_derivation_v1.py` + `candle_d1_publish_wire_v1.py` (verbatim, unchanged by this WO):

| Policy | Code evidence |
|---|---|
| Canonical instrument XAU_USD only | `GOV-CANDLE-D1-001` / `parse_d1_instruments` reject `XAUUSD` + non-XAU |
| Fixed **22:00:00 UTC** anchor | `D1_ANCHOR_HOUR_UTC = 22`; `assert_d1_open_anchor` raises `GOV-CANDLE-D1-002` unless open == 22:00:00 UTC |
| NY-5PM close, no DST | `_ANCHOR_SHIFT_SECONDS = (24-22)*3600`; fixed UTC grid, `SOURCE_POLICY_EPOCH="D1_FROM_H4_NY1700_FIXED_UTC_V1"` |
| Derives ONLY from six complete OK H4 | `D1_EXPECTED_CHILDREN = 6`; `h4_child_is_complete` requires status OK + closed + source_count==expected + coverage==1.0 + gap NONE + grid hour |
| Child grid hours | `D1_CHILD_H4_OPEN_HOURS_UTC = (22, 2, 6, 10, 14, 18)` |
| No direct `candles_D1` source | derivation docstring: "the dead, midnight-anchored `candles_D1` table is rejected as a source" |
| No 24×H1 shortcut | `assert_d1_source_timeframe` requires `H4`; `GOV-CANDLE-D1-WIRE-003` rejects anything else |
| Publish only complete closed 6/6 (status OK) | `_seal_and_publish`: a sealed <6 bucket is honestly skipped, never published |
| Publication is roll-over only | `on_h4_close` seals the **previous** bucket when a new D1 day's first H4 arrives |

**The warm-start preserves every one of these.** It reuses `d1_bucket_open` / `assert_d1_open_anchor` /
`h4_child_is_complete` / `D1_CHILD_H4_OPEN_HOURS_UTC` directly and never publishes — the only producible D1 latest
is still the existing live roll-over seal.
