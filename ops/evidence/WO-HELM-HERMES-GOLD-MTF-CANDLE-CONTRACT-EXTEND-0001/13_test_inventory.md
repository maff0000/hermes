# Test Inventory

Full run output: `12_test_results.txt` → **172 passed**.

## New file: `tests/test_candle_gold_mtf_contract_v1.py` (17 cases)
| Test | Proves |
|------|--------|
| `test_direct_native_grid_recognised_and_ttld` | M1/M5/M15/H1 in TIMEFRAMES; TF_SECONDS + redis_ex correct |
| `test_each_grid_tf_builds_and_validates` | each grid TF builds + validates; canonical key shape |
| `test_geometry_block_present_and_correct_bullish` | body/range/wick/direction exact values (UP) |
| `test_geometry_bearish_and_flat_directions` | DOWN + FLAT direction; zero body_size |
| `test_wick_high_is_not_the_high_price` | **no aliasing**: wick_high ≠ high, wick_low ≠ low; recomputable |
| `test_marubozu_zero_wicks_are_zero_not_aliased` | zero-wick candle → wicks 0.0, not aliased |
| `test_validator_rejects_wick_aliased_to_high` | `GOV-CANDLE-CONTRACT-035` on aliasing |
| `test_validator_rejects_negative_wick` | `GOV-CANDLE-CONTRACT-031` |
| `test_validator_rejects_body_bigger_than_range` | `GOV-CANDLE-CONTRACT-033` |
| `test_validator_rejects_bad_direction` | `GOV-CANDLE-CONTRACT-037/038` |
| `test_validator_rejects_missing_geometry_field` | `GOV-CANDLE-CONTRACT-012` (missing data field) |
| `test_market_closed_geometry_is_none_and_valid` | unpriced → geometry None, still valid |
| `test_no_forbidden_regime_field_can_leak` | `GOV-CANDLE-CONTRACT-029` on `regime` |
| `test_seam_supports_full_direct_grid` | seam emits + writes valid shadow keys for all 4 TFs |
| `test_seam_skips_deferred_timeframes` | D1/H4/D → `UNSUPPORTED_TIMEFRAME`, no write |
| `test_every_shadow_key_is_versioned_v1` | every key ends `:latest:v1`; no canonical |
| `test_xauusd_alias_canonicalised_no_dual_publish` | XAUUSD→XAU_USD, single key, no dual-publish |

## Updated existing tests (assertions that assumed M1/M15 unsupported)
| File | Change |
|------|--------|
| `tests/test_candle_contract_v1.py` | `test_bad_timeframe_fails_loud` uses M30/M3/W (M1 now valid); `test_ttl_policy_per_timeframe` asserts the full 6-TF map + M1/M15 redis_ex |
| `tests/test_candle_shadow_writer_wire_v1.py` | unsupported set → `(D1,H4,D)`; `test_no_silent_remap_of_unsupported` uses H4 |
| `tests/test_candle_utc_naive_fix_v1.py` | unsupported set → `(D1,D,H4)` |

## Regression guard
`models/candle.py` deliberately left unchanged — verified `tests/test_models.py` (incl.
`TestCandleAggregator::test_flush_all`) stays green (the live aggregator still produces M1/M5/H1/D1).
