# Final Report — D1 Canonical Publish Wiring (6xH4, code-only PR)
**WO-HELM-HERMES-GOLD-D1-CANONICAL-PUBLISH-WIRE-0001 · HELM/HERMES**
**Expected verdict: GREEN_PR_OPEN_GOLD_D1_CANONICAL_PUBLISH_WIRE_CODE_ONLY**

## Delivered (code-only, base 498d0679)
- `utils/candle_d1_publish_wire_v1.py` — `CanonicalD1Producer` (sealed H4 -> 6/6 D1 on 22:00Z rollover,
  publishes ONLY complete 6/6 OK), `DisabledD1Producer`, `build_d1_producer_from_env`, `parse_d1_instruments`,
  `assert_d1_source_timeframe`. No Redis I/O at import; disabled -> no-op.
- `utils/candle_publisher_v1.py` — D1 added to `CANONICAL_PUBLISH_TIMEFRAMES`; `assert_canonical_key` now
  accepts the D1 latest key (governed path); legacy "D" still rejected.
- `tests/test_candle_d1_publish_wire_v1.py` (21 tests) + posture updates to the D1-derivation and H4-wire tests.

## Behaviour
6xH4 -> D1 on fixed 22:00 UTC (22:00Z->22:00Z; children 22/02/06/10/14/18). Publishes a D1 latest ONLY when the
sealed bucket is complete 6/6 (status OK); <6 is never published (D1_INCOMPLETE_NOT_PUBLISHED). Source = H4 only
(non-H4 ignored; source timeframe must be H4, else fail loud); no direct candles_D1; no 24xH1; no synthesis.
XAU_USD only (config allowlist rejects XAUUSD/non-XAU; input alias canonicalised so no XAUUSD key emitted).
D1 publishable ONLY via this governed producer — the direct seam still refuses D1; D1 history still blocked.

## Gating (default DISABLED)
HERMES_CANDLE_D1_PUBLISH_ENABLED(false) + _AUTHORISED(false, enabled-unauth -> 004) + _INSTRUMENTS(XAU_USD, missing
-> 005, XAUUSD/non-XAU -> 006) + _SOURCE_TIMEFRAME(H4, else -> 003) + canonical bus controls.

## Tests
21 new pass; full candle suite **324 passed** (no regression). Pre-existing env-dependent collection errors
(REDIS_HOST/starlette) unrelated and untouched.

## Exclusions honoured
No deploy/restart/activation/Redis write/SQL write/D1 history backfill/direct candles_D1/24xH1 shortcut/regime/
XAUUSD output/shadow/consumer cutover/Proteus/structure_engine/opportunistic refactor.

## Risks / notes for R2D2
1. assert_canonical_key now accepts D1 latest keys (added D1 to CANONICAL_PUBLISH_TIMEFRAMES). Generic/direct D1
   publication is still impossible because the DIRECT seam refuses D1 and only the D1 producer emits D1. Two
   prior tests (D1-derivation, H4-wire) updated to the new posture; D1 history remains blocked.
2. Producer is NOT wired into the runtime loop here (no on_h4_close caller). Wiring it to the H4 seal stream +
   activation is a separate WO (build->wire->activate cadence).
3. SAFEST rule: incomplete D1 is never published (not even SOURCE_INCOMPLETE), unlike H4 which publishes
   incomplete honestly. This is deliberate for the daily candle.
