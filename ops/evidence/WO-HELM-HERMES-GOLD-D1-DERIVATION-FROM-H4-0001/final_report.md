# Final Report — D1 Derivation from 6xH4 (code-only PR)
**WO-HELM-HERMES-GOLD-D1-DERIVATION-FROM-H4-0001 · HELM/HERMES**
**Expected verdict: GREEN_PR_OPEN_GOLD_D1_DERIVATION_FROM_H4_CODE_ONLY**

## Delivered (code-only, base b0585af8)
- `utils/candle_d1_derivation_v1.py` — `derive_d1` (6xH4 -> D1, fixed 22:00 UTC), `d1_bucket_open`,
  `d1_child_h4_opens`, `h4_children_in_bucket`, `assert_d1_open_anchor`. No Redis I/O, no publication.
- `utils/candle_contract_v1.py` — added D1 to TIMEFRAMES/TF_SECONDS(86400)/TTL_BUFFER(3600) + policy
  `DERIVED_D1_FROM_H4`. Derivation/validation support ONLY; publish/history/seam grids unchanged (still exclude D1).
- `tests/test_candle_d1_derivation_v1.py` (17 tests) + ttl-policy test update.

## Anchor / source / completeness
Fixed 22:00 UTC day (22:00Z->22:00Z); children 22/02/06/10/14/18; UTC-midnight rejected (GOV-CANDLE-D1-002).
OHLCV: open=first, high=max, low=min, close=last, vol=sum. 6/6 closed -> OK; <6 -> SOURCE_INCOMPLETE/NO_SOURCE_DATA
(never OK, coverage<1, gap surfaced); forming -> FORMING; no synthetic children. Source = H4 only (no candles_D1,
no 24xH1, H1 audit-note only). XAU_USD only (alias/non-XAU rejected GOV-CANDLE-D1-001).

## D1 remains unpublishable/unwritable (this WO is derivation only)
assert_canonical_key rejects D1 (PUB-CANON-KEY-005); assert_history_target rejects D1 (HIST-TGT-006); D1 not in
SUPPORTED_TF/DERIVED_TF. Contract recognises D1 for derivation/validation but all write grids still exclude it.

## Tests
17 new pass; full candle suite **303 passed** (no regression). Pre-existing env-dependent collection errors
(REDIS_HOST/starlette) unrelated and untouched.

## Exclusions honoured
No deploy/restart/activation/Redis write/SQL write/D1 publication/D1 history backfill/direct candles_D1/
24xH1 shortcut/regime/XAUUSD output/shadow/consumer cutover/Proteus/structure_engine/opportunistic refactor.

## Risks / notes for R2D2
1. Contract now recognises BOTH legacy "D" and new "D1" (86400). "D1" is the governed NY-5PM daily token used by
   keys/guards; "D" retained for back-compat. All write-path guards still reject D1 (proven by tests).
2. Derivation is not wired into any runtime path (no producer). Future guarded D1 publication + history are
   separate WOs; this PR only makes the derivation + contract recognition exist and be validated.
3. H1 cross-check is documented as audit-only and intentionally NOT a source path.
