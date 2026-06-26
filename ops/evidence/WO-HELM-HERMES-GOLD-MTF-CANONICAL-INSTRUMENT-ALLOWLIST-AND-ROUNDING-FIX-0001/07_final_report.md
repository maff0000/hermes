# Final Report — WO-HELM-HERMES-GOLD-MTF-CANONICAL-INSTRUMENT-ALLOWLIST-AND-ROUNDING-FIX-0001

**Persona:** HELM · **Lane:** HERMES · **Type:** code-only, PR-gated
**Verdict sought:** `GREEN_PR_OPEN_GOLD_MTF_CANONICAL_ALLOWLIST_AND_ROUNDING_FIX_CODE_ONLY`

## Both activation blockers fixed
1. **Instrument over-publish** → fail-closed allowlist `HERMES_CANDLE_CANONICAL_INSTRUMENTS` (XAU_USD only
   here). Missing/empty → fail loud (`GOV-CANDLE-FWD-SEAM-007`); non-allowlisted instruments skipped
   (`INSTRUMENT_NOT_ALLOWLISTED`, counted, no write, no spam). No default fan-out.
2. **Geometry rounding** → OHLC quantised to `_PRICE_DP=6` before bounds/geometry validation; monotonic
   rounding makes `body_high<=high` / `body_low>=low` hold exactly. The exact EUR_GBP float-hair case now
   validates; 30-case fuzz passes; wick sizes/aliasing/direction unchanged; no tolerance loosened.

## Tests — 211 in candle lane, 0 new failures
New `tests/test_candle_canonical_allowlist_and_rounding_v1.py` (17 cases): allowlist publish/skip,
missing/empty fail-loud, XAUUSD→XAU_USD no alias key, grid-only, H4/D1 rejected, unversioned rejected,
from-env requires allowlist; rounding repro + fuzz + invariants + aliasing-still-fails + direction. Full
candle/seam/publisher/contract/model suites green. Pre-existing watchdog(async)/env-dependent failures
unchanged (fail on base).

## Risks / notes for R2D2
- OHLC are now published quantised to 6dp (was raw). 6dp ≥ all instrument quote precision (FX 5dp, metals/
  indices ≤3dp); chosen because geometry already rounded to 6dp. Flagged for awareness.
- Allowlist matching is on the **canonical** id; alias entries normalise in, alias keys never emit.
- `live`/`prod` sinks still rejected; only `canonical` selects the writer.

## No-activation
No deploy/restart/activation/Redis/SQL/backfill/env-change/consumer-cutover. `hermes-signal-dev` remains
merged-image + candle-forward DISABLED. Re-activation is a separate step after merge.
