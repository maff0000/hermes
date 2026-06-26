# Fix Summary

Two activation blockers fixed (code-only; nothing deployed/activated).

## Fix 1 — fail-closed canonical instrument allowlist (`utils/candle_runtime_seam_v1.py`)
- New governed env `HERMES_CANDLE_CANONICAL_INSTRUMENTS` (comma list). `parse_canonical_allowlist`
  returns a frozenset of **canonical** ids; **missing → fail loud, empty → fail loud**
  (`GOV-CANDLE-FWD-SEAM-007`). Alias entries (XAUUSD) canonicalise to XAU_USD, so the set is
  canonical-only and an alias can never become an output key.
- `CanonicalCandleForwardSeam` now requires a non-empty allowlist; `emit()` canonicalises the candle
  instrument and **skips** non-allowlisted ones (`INSTRUMENT_NOT_ALLOWLISTED`, per-instrument counter,
  no log spam, no write). `build_canonical_seam(..., allowed_instruments=...)` and the env factory
  (`HERMES_CANDLE_CANONICAL_INSTRUMENTS` required when `SINK=canonical`) thread it through.
- Result: no default fan-out to all instruments; for this WO only **XAU_USD** publishes.

## Fix 2 — geometry/precision consistency (`utils/candle_contract_v1.py`)
- `_PRICE_DP=6` + `_q()` quantise OHLC to the governed precision **before** the GOV-006 bounds check,
  and `_candle_geometry` derives from the same quantised OHLC. Rounding is monotonic, so
  `high>=max(open,close)`, `low<=min(open,close)`, `body_high<=high`, `body_low>=low` hold exactly —
  the float-hair (high a few ulps under close) can no longer trip GOV-006/032.
- Hard invariants preserved: wick sizes (not aliases), `range_size>=body_size`, `wick_*>=0`,
  UP/DOWN/FLAT unchanged. No validator tolerance was loosened — genuine geometry errors still fail loud.

## Key topology (unchanged, still enforced)
Only `hermes:candles:XAU_USD:{M1|M5|M15|H1}:latest:v1`. H4/D1/D rejected; unversioned rejected; XAUUSD
alias keys never written; no regime.
