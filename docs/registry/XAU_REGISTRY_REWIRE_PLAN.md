# XAU Advanced-v1 → canonical registry rewire
WO-HELM-HERMES-ADVANCED-V1-XAU-REGISTRY-REWIRE-0001

## Delivered this stage (additive; zero regression to live modules)
- `registry.capability_instruments(records, flag)` — registry-driven capability→instrument resolver.
- `utils/hermes_advanced_v1_selection_v1.py` — the SINGLE reusable authority the Advanced-v1 pipeline
  consults for WHICH instruments each capability (tick/indicator/gap/backfill-status) publishes for,
  plus a GENERIC key factory. PROVEN:
  - selection == {XAU_USD} for tick/indicator/gap (parity with today's env-parser output);
  - the 7 new instruments are absent from every capability set (NOT_ENABLED);
  - generic keys are BYTE-IDENTICAL to the existing module key strings
    (hermes_indicators_v1.indicator_key, hermes_gaps_v1.GAPS_KEY, hermes_backfill_status_v1.BACKFILL_STATUS_KEY,
     tick_contract_v1.canonical_key) — so a module adopting them changes no Redis contract;
  - synthetic enabled instrument appears by DATA only; disabling removes it; registry-unavailable fails closed;
  - the seam has zero XAU authority constant / `if instrument ==` / hard-coded rollout array (static scan).

## NOT yet done (the bounded next sub-stage) — deliberately staged to preserve byte-parity
The 15 modules each hold `CANONICAL_INSTRUMENT = "XAU_USD"` used in their key factory, payload builder,
parser AND validators, and are covered by ~40 governed tests asserting the current XAU-only enforcement.
Adopting the seam per module (parameterise the key factory by the passed instrument; replace the
constant/parser with `selection.selection_for(<cap>, records)`; update the module's tests to inject a
registry fixture) is byte-parity-preserving BY CONSTRUCTION (XAU is the only enabled instrument) but must be
done and parity-reviewed per module to avoid regressing governed publication code. Per-module disposition:
| module | capability | adoption |
|--------|-----------|----------|
| utils/hermes_indicators_v1.py | indicator | key→param by instrument; allowlist→selection_for('indicator') |
| utils/tick_live_emitter_v1.py | tick | allowlist→selection_for('tick'); key already generic |
| utils/hermes_gaps_v1.py | gap | GAPS_KEY→gaps_key(instrument); enable set→selection_for('gap') |
| utils/hermes_backfill_status_v1.py | backfill-status | key→backfill_status_key(inst); set→backfill_status_instruments |
| utils/hermes_feed_health_v1.py | health | registry_component_state + selection |
| (levels/sessions/catalog/quote/candle_d1/candle_history/candle_features/proposal_validator/recovery_planner/hydration) | out of §6 scope | leave unchanged; owner recorded; removed as each capability generalises |
Composition (main.py lifespan): resolve `selection.load_selection()` once at startup and pass the per-capability
instrument set to each publisher (replacing the env-parser inputs); env INSTRUMENTS becomes the fail-closed
consistency validator only.

## Guarantees held this stage
Production untouched; migration 025 NOT applied to production (WTICO still base_metals in prod); 7 new
instruments inactive; XAU key contracts byte-identical; no second OANDA stream; no backfill executor.
