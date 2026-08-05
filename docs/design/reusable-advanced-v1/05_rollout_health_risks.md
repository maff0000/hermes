# Rollout stages, cohorts, rollback, health, risks (§22, §25, §26, §28)

## Health & observability (§22) — reusable aggregation, auto-enumerated
The health aggregator enumerates instruments from the **registry** (already true today: `/health`, `/status`
read `state.config.instruments`; extend `/metrics` and per-contract coverage to iterate the registry).
Adding a registry row automatically adds: health status, readiness, metrics, freshness, contract coverage
(tick/candle/indicator/gap/backfill present+fresh), fault reporting. **No manual per-ticker endpoint edit.**
Planes aggregated: ingestion, tick contract, candle plane, indicator plane, gaps, backfill, SQL, Redis.

## Rollout stages (§25) — bounded, design-only here
- **Stage 0 — authority freeze:** ratify registry authority, schemas, formula conventions, timeframes,
  health semantics, tests (this WO's deliverables).
- **Stage 1 — generalise XAU pilot:** lift constant/parser in the 15 modules; one shared pipeline; key
  strings unchanged; **no production activation**; XAU behaviour byte-identical.
- **Stage 2 — add-one-instrument proof:** registry-only onboarding, zero code change, isolated env (§10).
- **Stage 3 — isolated 8-instrument proof:** XAU + 7, deterministic replay, shadow Redis, ephemeral SQL,
  all contracts + endpoints; **no production route** (WP3 pattern).
- **Stage 4 — shadow-live observation:** consume the EXISTING live feed via the running production runtime;
  publish ONLY to a shadow keyspace; **no canonical activation; no second OANDA connection** unless a
  governed feed-sharing method exists (the running runtime is the only OANDA-live owner).
- **Stage 5 — staged canonical activation:** one shared codebase; activation by **registry capability
  flags**; rollback by capability/cohort; **no code fork**.
- **Stage 6 — backfill executor:** separately gated; historical-mutation authority required.
- **Stage 7 — independent R2D2 compliance audit:** requirement denominator; independent recomputation.

## Activation cohorts (§26) — risk control only, same code
| Cohort | Instruments | Justification |
|--------|-------------|---------------|
| A | XAU_USD, XAG_USD | metals; XAU is the proven pilot; XAG nearest-neighbour (same precision/hours) |
| B | EUR_USD, GBP_USD, AUD_USD, USD_JPY | standard FX + JPY-precision edge case (3dp) — proves precision generalisation |
| C | SPX500_USD, WTICO_USD | equity-index + energy — distinct market hours/maintenance/volatility |
All cohorts run the **same code**; only registry enablement changes. Rollback = disable the cohort's
registry flags (per-capability or per-instrument) — no redeploy, no code change.

## Rollback plan (§25/§26)
Capability/cohort rollback is a registry flag flip. Codebase rollback (if Stage 1/5 build regresses) reuses
the proven WP4 image-tag revert doctrine (rollback image + compose tag). No per-instrument rollback logic.

## Risk register (§28)
| ID | Risk | Sev | Mitigation |
|----|------|-----|------------|
| R-1 | Constant→allowlist lift regresses XAU behaviour | High | golden-master XAU parity test (byte-identical keys+payloads) in Stage 1 |
| R-2 | Second instrument list survives (hidden duplicate) | High | REQ-REG-01 static scan; the 17-list inventory closed to zero |
| R-3 | Price authority mismatch (mid vs bid/ask) | Med | validation V-1 confirm aggregator source before ratify |
| R-4 | Raw-tick SQL volume/backpressure | Med | bounded retention + batched writes + kill switch; separate gated WO |
| R-5 | JPY/index precision errors | Med | registry price_precision + independent oracle per instrument (Cohort B/C tests) |
| R-6 | Shadow-live needs 2nd OANDA stream | High | Stage 4 forbids a 2nd stream; consume the running runtime's data only |
| R-7 | RSI Cutler vs Wilder ambiguity | Low | explicitly ratified as Cutler; change is deliberate future WO |
| R-8 | Backfill executor mutates history | High | separate WO + historical-mutation authority + idempotency + kill switch |
| R-9 | Gap/backfill single-key → per-key migration breaks consumers | Med | additive per-instrument keys; keep XAU key string stable; version bump if shape changes |

## Held-actions register (§28)
No production activation · no 2nd OANDA stream · no Redis/SQL mutation · no migration execution · no backfill
execution · no consumer/order · no label rebuild · no other-app change · no directing R2D2. All are future
separately-authorised WOs (see EPIC WO-1..WO-10).
