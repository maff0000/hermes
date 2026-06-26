# Timeframe Grid — M1 / M5 / M15 / H1

DIRECT-NATIVE grid (`source_count=expected=1`, `coverage=1.0`, `DIRECT_FROM_SOURCE`, `NONE_DIRECT`,
`source_policy_epoch=DIRECT_NATIVE_V1`).

| TF | period (s) `TF_SECONDS` | EX buffer (s) `TTL_BUFFER_SECONDS` | `redis_ex_seconds` | shadow key |
|----|----|----|----|----|
| **M1**  | 60   | 30  | 90   | `hermes:shadow:candles:{instrument}:M1:latest:v1`  |
| **M5**  | 300  | 60  | 360  | `hermes:shadow:candles:{instrument}:M5:latest:v1`  |
| **M15** | 900  | 180 | 1080 | `hermes:shadow:candles:{instrument}:M15:latest:v1` |
| **H1**  | 3600 | 300 | 3900 | `hermes:shadow:candles:{instrument}:H1:latest:v1`  |

`ttl_seconds` in the envelope == `TF_SECONDS[tf]`; `valid_until_utc == generated_at_utc + ttl_seconds`
(enforced by `GOV-CANDLE-CONTRACT-016/018`). Redis `EX` = `TF_SECONDS + TTL_BUFFER_SECONDS` (the grace
window; consumers freshness-gate on `valid_until_utc`, not on EX).

## Deferred (still skipped `UNSUPPORTED_TIMEFRAME`)
`H4` (14400s) and `D` (86400s) remain recognised by the contract for the future derived path but are
**not** in the shadow seam's `SUPPORTED_TF`; `D1` is likewise skipped. Proof: `12_test_results.txt` →
`test_seam_skips_deferred_timeframes`, `test_no_silent_remap_of_unsupported`.

Verified values: see `test_candle_gold_mtf_contract_v1.py::test_direct_native_grid_recognised_and_ttld`
and `test_candle_contract_v1.py::test_ttl_policy_per_timeframe`.
