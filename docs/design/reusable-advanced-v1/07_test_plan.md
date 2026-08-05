# Parameterised test plan (§23) + add-one-instrument proof plan (§10)

## Principle
ONE parameterised suite over the canonical registry — never one file per ticker.
```python
@pytest.mark.parametrize("instrument", enabled_instruments())
def test_contracts_for_enabled_instrument(instrument):
    ...
```
`enabled_instruments()` reads the registry (test uses an injected in-memory registry fixture seeded with the
8 rollout instruments + policy metadata). An **independent calculation oracle** computes expected candle
geometry and indicators — the suite must NOT compare the implementation against itself.

## Coverage matrix (all 8 instruments × relevant TFs)
FX precision (EUR/GBP/AUD 5dp) · JPY precision (USD_JPY 3dp) · metals (XAU/XAG) · index hours (SPX500) ·
energy hours (WTICO) · sparse ticks · market closure · gaps · duplicate ticks · late ticks · out-of-order
ticks · zero-range candle · doji · upper wick · lower wick · indicator warm-up · Redis TTL · SQL persistence
· restart · failure isolation.

## Independent oracle
- Candle geometry oracle: pure-python recomputation of body/wick/range/direction from OHLC, asserting the
  §15 invariants exactly.
- Indicator oracle: independent EMA(2/(n+1),SMA-seed), RSI(Cutler-14), ATR(SMA-14), Bollinger(SMA20±2σ),
  ADX(Wilder-14) implemented separately from the engine; compare within 1e-6 relative tolerance; warm-up
  windows asserted.

## Add-one-instrument proof (§10) — harness outline
`tests/design/test_add_one_instrument_contract.py` (build phase, WO-5) will, in an isolated env:
seed registry with base 8 → snapshot → add one synthetic `TST_USD` row (registry only) → assert the
subscription builder, tick/candle/indicator/gap/backfill, SQL policy, health enumeration, and the
parameterised suite ALL include it with ZERO app-code change → disable the row → assert clean disappearance
+ no residual keys beyond retention. The onboarding git diff is asserted to be registry-only.

## This WO ships (design/fixture only)
- `tests/design/test_reusable_engine_contract_v1.py` — a **self-contained** demonstrative parameterised
  fixture (no DB/Redis/production dependency) proving: (a) the parametrize-over-registry pattern, (b) the
  candle-geometry invariants via an independent oracle across all 8 instruments with per-instrument
  precision, (c) an independent indicator oracle sanity check. It is a design artefact, not the production
  suite, and must not touch live state.
