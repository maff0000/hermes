# Final Report — Wire Forward History into Runtime (code-only PR)
**WO-HELM-HERMES-GOLD-MTF-FORWARD-HISTORY-WIRE-RUNTIME-0001 · HELM/HERMES**
**Expected verdict: GREEN_PR_OPEN_GOLD_MTF_FORWARD_HISTORY_WIRE_RUNTIME_CODE_ONLY**

## Delivered (code-only, base b95e111)
- `utils/candle_runtime_seam_v1.py` — CanonicalCandleForwardSeam gains an optional forward-history writer;
  post-publish `_forward_history` hook (CLOSED only; forming skipped before the call). build_canonical_seam +
  build_candle_forward_seam_from_env pass it through (lazy-built, default disabled).
- `utils/candle_h4_publish_wire_v1.py` — CanonicalH4Producer gains the writer; post-seal hook fires ONLY for
  COMPLETE 4/4 (status OK); build_h4_producer_from_env passes it through (default disabled).
- `tests/test_candle_forward_history_wire_runtime_v1.py` — 19 tests.

## Behaviour
Latest write happens first and is returned unconditionally; history append runs after and is fault-isolated
(counted + logged, never propagated). Default disabled -> latest-only, identical to pre-WO runtime. XAU_USD
only; M1/M5/M15/H1/H4; no D1; no regime; no XAUUSD output; no shadow. All history guards still run.

## Tests (19 new, all green; full candle suite 286 passed, no regression)
disabled default -> no history calls (seam + H4) · closed M1/M5/M15/H1 -> writer called + history written +
TTL + target-guard · forming -> writer NOT called · H4 complete 4/4 -> writer called + DERIVED history written ·
H4 partial/warmup -> not written as OK · history fault never breaks latest (seam + H4) · written key validates,
targets history not latest, no XAUUSD · no regime/D1 in any key/payload · factory fail-loud: enabled-unauthorised
(002), D1 timeframe (005), non-XAU (006); disabled-default builds latest-only seam.

## Exclusions honoured
No deploy/restart/activation/Redis write/SQL write/backfill/D1/regime/XAUUSD output/shadow/consumer cutover/
Proteus/structure_engine/opportunistic refactor. Changes are additive hooks only; latest publication untouched.

## Risks / notes for R2D2
1. Still INERT — no activation. Forward writes begin only when HERMES_CANDLE_HISTORY_FORWARD_* gates are set +
   deployed (next WO). This PR proves the callers exist and are correct, default-off.
2. Fault policy: a history fault (incl. same-epoch conflict) is logged + counted, NOT raised, so latest stays
   authoritative. A genuine history conflict will therefore surface as a metric/log, not a hard stop — by design.
3. The seam hook calls the writer for every CLOSED emitted candle; the writer is idempotent, so repeated emits
   of the same closed candle are safe no-ops (TTL refresh).
