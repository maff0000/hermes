# Final Report — D1 H4-Seal Runtime Hook (code-only PR)
**WO-HELM-HERMES-GOLD-D1-H4-SEAL-RUNTIME-HOOK-0001 · HELM/HERMES**
**Expected verdict: GREEN_PR_OPEN_GOLD_D1_H4_SEAL_RUNTIME_HOOK_CODE_ONLY**

## Delivered (code-only, base d67d7582)
- `utils/candle_h4_publish_wire_v1.py` — `CanonicalH4Producer` gains an optional `d1_producer`; after the H4
  latest write + forward-history hook, `_offer_to_d1(env)` offers the sealed H4 (via `_SealedH4View` adapter)
  to the governed D1 producer. `build_h4_producer_from_env` lazy-builds + passes the D1 producer (default
  DisabledD1Producer). d1_hook metrics + status.
- `tests/test_candle_d1_h4_seal_runtime_hook_v1.py` — 16 tests (incl. a full-day integration that publishes a 6/6 D1 through the hook).

## Behaviour
EACH sealed H4 is offered to the D1 producer, which publishes a D1 latest ONLY when a complete 6xH4 day
(22:00Z->22:00Z) exists; an incomplete day never publishes OK. Source = sealed H4 only (no 24xH1, no direct
candles_D1, no synthesis). XAU_USD only. D1 history not written here. Default DISABLED -> the seal hook is a
pure no-op; H4 latest + forward-history behaviour is unchanged.

## Fault isolation (R2D2-noted)
H4 latest is written first and returned unconditionally; the D1 offer runs after and is fault-isolated
(counted via d1_hook_fail, surfaced in the seal result + status(), never propagated). A D1 fault cannot undo
or block the H4 latest write.

## Tests
16 new pass; full candle suite **340 passed** (no regression: existing H4 latest/history + forward-history tests green).

## Exclusions honoured
No deploy/restart/activation/Redis write/SQL write/D1 history/direct candles_D1/24xH1 shortcut/regime/XAUUSD
output/shadow/consumer cutover/Proteus/structure_engine/opportunistic refactor.

## Risks / notes for R2D2
1. Still INERT — activation (set HERMES_CANDLE_D1_PUBLISH_* + deploy) is a separate WO. With D1 gates unset the
   hook is a no-op and the H4 runtime is identical to before.
2. EACH sealed H4 is offered (per WO) — including a SOURCE_INCOMPLETE H4 (a real but H1-gapped H4). The D1 day
   is "complete" when all 6 H4 buckets are present; each H4's own coverage is carried at the H4 layer. A day
   missing an H4 bucket stays <6 -> D1 not published. Flag if you prefer offering only status-OK H4.
3. Fault policy is log/count, not raise (H4 latest authoritative) — a D1 fault surfaces as a metric, not a stop.
