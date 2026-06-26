# Scope & Constraint Compliance

## In scope (done)
- `utils/candle_contract_v1.py` — added **M1 (60s)** and **M15 (900s)** to `TIMEFRAMES`, `TF_SECONDS`,
  `TTL_BUFFER_SECONDS`; added a deterministic geometry block to the `data` payload + `_DATA_KEYS`; added
  geometry validation (`GOV-CANDLE-CONTRACT-030..038`).
- `utils/candle_runtime_seam_v1.py` — extended `SUPPORTED_TF` to `("M1","M5","M15","H1")`; added
  `INSTRUMENT_ALIASES` + `canonical_instrument()` (XAUUSD→XAU_USD); docstrings updated.
- `ops/config/candle_forward_runtime_seam.md`, `.env.example` — doc updates to the M1/M5/M15/H1 grid.
- Tests — new `tests/test_candle_gold_mtf_contract_v1.py` (17 cases) + updates to 3 existing test files
  whose assertions assumed M1/M15 were unsupported.

## Wick semantics (fixed — exactly the WO formulas)
```
body_high  = max(open, close)
body_low   = min(open, close)
body_size  = abs(close - open)
range_size = high - low
wick_high  = high - body_high      # UPPER WICK SIZE — never the high price
wick_low   = body_low - low        # LOWER WICK SIZE — never the low price
```
Validation enforces `wick_high>=0`, `wick_low>=0`, `range_size>=0`, `body_high<=high`, `body_low>=low`,
`body_size<=range_size`, no high/low aliasing (via recomputation: `GOV-CANDLE-CONTRACT-035`).
`candle_direction` = `UP` (close>open) / `DOWN` (close<open) / `FLAT` (equal) — pure sign, no thresholds.

> The repo's `utils/candle_features.candle_geometry()` carried a redundant price-level alias
> (`"wick_high": high`). Per the WO remap rule (`wick_high = upper_wick_size`), the **contract** computes
> wick sizes inline and never sources that alias; `candle_features.py` is left untouched (its
> `upper_wick_size`/`lower_wick_size` already match the WO and its tests stay green).

## Forbidden list — NONE done (verified)
| Forbidden | Status |
|-----------|--------|
| Deploy / restart / activate | NOT done — no container/service touched |
| Write Redis | NOT done — all tests use in-memory fakes |
| Write SQL / DML / migrations | NOT done |
| Backfill | NOT done |
| Edit live `.env` / enable `HERMES_CANDLE_FORWARD_ENABLED` | NOT done — only `.env.example` comment |
| Activate shadow mode | NOT done — `shadow_authorised` only in test fixtures |
| Publish canonical `hermes:candles:*` | NOT done — canonical stays dark |
| Publish unversioned keys | NOT done — every shadow key ends `:latest:v1` |
| Publish `signals:candle:*` | NOT done — `GOV-CANDLE-CONTRACT-028` still guards it |
| Add H4/D1/D as supported | NOT done — still skipped `UNSUPPORTED_TIMEFRAME` |
| Add `regime`/`regime_confidence` | NOT done — `regime` is a forbidden token (`GOV-...-029`) |
| Touch Falcon/SOLO/NEO/ARES/HELIOS/Proteus | NOT done — see `git diff --name-only` (all HERMES candle lane) |
| Dual-publish `XAUUSD` | NOT done — alias map is one-way XAUUSD→XAU_USD, canonical id only |
