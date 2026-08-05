# Advanced-v1 Controlled Production-Readiness Package

**WO:** WO-HELM-HERMES-ADVANCED-V1-PRODUCTION-READINESS-0001 · **Base:** canonical main `11b1e381` (PR #132 merge).
**Verdict:** GREEN readiness package — PLANNING/TOOLING/REHEARSAL ONLY. **No runtime mutation performed; all activation gates HELD.**

## Contents
| Artifact | § | Purpose |
|----------|---|---------|
| `config/advanced_v1/calendar_provenance_v1.json` | 6–8 | governed calendar provenance for the 4 policy keys + validation status |
| `config/advanced_v1/dark_deployment_v1.json` | 13 | dark-deployment control contract + fail-closed defaults |
| `config/advanced_v1/health_thresholds_v1.json` | 16 | measurable per-family promotion thresholds |
| `config/advanced_v1/activation_sequence_v1.json` | 18 | ordered gated activation sequence + promotion doctrine |
| `config/advanced_v1/rollback_matrix_v1.json` | 19 | rollback/abort triggers → actions |
| `utils/hermes_advanced_v1_readiness_package_v1.py` | — | fail-closed loaders/validators + production calendar gate |
| `ops/readiness/migration_025_production_preflight.sh` | 9 | READ-ONLY production preflight |
| `ops/readiness/production_rehearsal_harness.sh` | 12 | isolated production-like rehearsal (preflight→backup→migrate→readiness→dark→shadow→health→rollback→restore) |
| `ops/readiness/migration_025_apply_runbook.md` | 10 | migration apply runbook |
| `ops/readiness/migration_025_rollback_runbook.md` | 11 | migration rollback runbook |
| `tests/test_advanced_v1_production_readiness_v1.py` | 22 | package validation tests |

## Calendar validation result (§7)
- `fx_24x5` → **BROKER_CONFIRMED_WITH_HOLIDAY_LIMITATION** (governed config_version 3; holidays not modelled — deliberate).
- `metals` → **BROKER_CONFIRMED_WITH_HOLIDAY_LIMITATION** (governed config_version 3; XAU pilot).
- `index_cash` (SPX500_USD) → **ASSUMPTION_REQUIRES_PROVIDER** — OANDA index-CFD hours NOT validated; governed config marks it `SCHEDULE_AMBIGUOUS_FAIL_CLOSED`. **Production activation BLOCKED** until OANDA session evidence; the assumed daily halt must NOT be used to suppress gaps in production (fail loud).
- `energy` (WTICO_USD) → **ASSUMPTION_REQUIRES_PROVIDER** — same; **BLOCKED**.

The calendar gate (`calendar_policy_production_ready`) fails closed for `index_cash`/`energy` and for any expired/stale/unapproved policy.

## One-stream proof (§14)
HERMES keeps **one** canonical OANDA stream. `dark_deployment_v1.json` fixes `oanda_streams.max=1`; the five adopted downstream families construct no `PricingStream`/`oandapyV20`/per-instrument `Thread`/`multiprocessing` (test-enforced). Eight-instrument activation adds only generic downstream capability processing on the existing shared ingestion plane.

## Shadow soak design (§15)
Follows dark deployment. Initial state: no external consumers, no orders, no backfill execution, no publication to consumer-facing keys unless authorised; all eight capabilities by registry flags only. **Duration is evidence-driven**, not arbitrary: the soak completes only after it has OBSERVED each required condition at least once with thresholds (see `health_thresholds_v1.json`) held: normal weekday session; FX/metals open operation; a full weekend (or simulated) transition; **for SPX500/WTICO the scheduled-closure observations are DEFERRED until their calendar is provider-validated** (they stay BLOCKED); stale-source behaviour; restart recovery; Redis interruption; registry interruption; migration-readiness failure; one-instrument fault isolation. Minimum practical span ≥ one full weekly cycle to capture weekend open/close for the confirmed policies.

## Downstream compatibility inventory (§17)
Consumers of the five surfaces (`hermes:ticks|indicators|gaps|backfill:status|feed_health:<inst>:v1`):
- Existing **XAU** consumers remain byte-compatible (XAU keys unchanged; only additive `CLOSED_SESSION` phase + `market_hours_policy` field, both additive).
- No consumer receives seven-new production keys before activation authority (keys not written until capability flags flip in production — HELD).
- Aliases (`XAUUSD`) remain rejected; no dual publication; no consumer assumes XAU-only cardinality (registry-driven).
- **Falcon and all other applications are outside this lane and are NOT modified.**
- `consumer_live` remains false; no consumer is silently activated.

## Promotion doctrine (§20)
A readiness package is **not** production approval. Explicitly: source merge ≠ migration authority; migration ≠ deployment; deployment ≠ publication; publication ≠ consumer; consumer ≠ orders. `consumer_live` stays false; no order path exists.

## Held actions
Production migration 025, deployment, restart, seven-new activation, production Redis writes, OANDA change, second stream, backfill execution, consumer/order enablement, readiness-PR merge — ALL HELD.
