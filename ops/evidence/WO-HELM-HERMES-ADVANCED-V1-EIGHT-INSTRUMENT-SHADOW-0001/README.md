# Isolated Eight-Instrument Advanced-v1 Shadow Exercise — Evidence

**WO:** `WO-HELM-HERMES-ADVANCED-V1-EIGHT-INSTRUMENT-SHADOW-0001`
**Base:** canonical `main` `40a29f6c` (PR #131 merge).
**Verdict:** `AMBER` — one bounded instrument-policy metadata gap (see §Finding). Everything else GREEN.

## What this proves
The now-generic Advanced-v1 pipeline (tick, indicators, gaps, backfill-status, feed-health) operates across the full
initial eight-instrument cohort driven **only** by registry capability flags — no per-ticker code, no per-ticker
publisher/state/key/table. Onboarding is data-only. All outputs are **isolated shadow evidence**, never production-live.

- **Cohort (8, all Advanced-v1 caps):** XAU_USD, XAG_USD, EUR_USD, GBP_USD, AUD_USD, USD_JPY, SPX500_USD, WTICO_USD
- **Non-cohort (6, NOT_ENABLED):** XPT_USD, XCU_USD, USD_CHF, USD_CAD, NZD_USD, EUR_GBP

## Artifacts
| Path | Purpose |
|------|---------|
| `ops/shadow/seed_14_canonical_instruments.sql` | 14 canonical instruments, base columns (pre-025) |
| `ops/shadow/activate_eight_cohort_capabilities.sql` | data-only capability activation for the 7 new instruments |
| `ops/shadow/migration_025_isolation_harness.sh` | throwaway isolated MariaDB lifecycle (apply/reapply/activate/rollback) |
| `ops/evidence/.../migration_025_isolation_run.txt` | captured harness output (all invariants GREEN) |
| `tests/test_eight_instrument_advanced_v1_shadow_v1.py` | in-memory five-family shadow proof (CI infra-free) |

## Reproduce
```bash
# Isolated migration lifecycle (needs docker + mariadb:11.7; spins + tears down its own container):
bash ops/shadow/migration_025_isolation_harness.sh
# In-memory five-family shadow (no external deps):
python -m pytest -q tests/test_eight_instrument_advanced_v1_shadow_v1.py
```

## Migration-025 isolated lifecycle result (real MariaDB 11.7, throwaway)
14 rows before → apply (14 rows, `energy` ENUM added, WTICO→energy, XAU caps 1/1/1, 7-new caps 0) → reapply idempotent
(6 metadata cols, 14 rows) → data-only activation (8 cohort all-caps, 6 non-cohort all-caps-0, 14 preserved) →
rollback (14 rows, WTICO→base_metals, metadata cols dropped, `energy` removed). Container removed.

## Five-family shadow result (generic, per-instrument, isolated)
- **tick:** 8 distinct `hermes:ticks:<inst>:latest:v1`, per-instrument bid/ask/mid/spread, alias skipped, no collision.
- **indicators:** 8×5 = 40 distinct `hermes:indicators:<inst>:<tf>:v1`, per-instrument identity, contracts validate.
- **gaps:** 8 distinct `hermes:gaps:<inst>:v1`, per-instrument candle reads, no cross-contamination.
- **backfill-status:** 8 distinct `hermes:backfill:status:<inst>:v1`, reads only own gaps key; execution/backfill/repair false; no executor.
- **feed-health:** 14-instrument enumeration (8 ACTIVE, 6 NOT_ENABLED — not RED); per-instrument contracts validate; D1 GATED.
- **state isolation:** wide A/B/A interleave — no shared watermark/state; each key holds its own instrument's contract.
- **fault isolation:** stale/missing/registry-down isolated & fail-closed; unaffected instruments uncontaminated.
- **data-only activation:** same code, XAU-only → eight, purely by registry data.

## Finding (AMBER) — smallest correction
The generic gap classifier is registry-driven for **selection** but its **market-hours classification** still uses one
uniform weekly-weekend calendar (`gaps.market_phase`) and does **not** consume the per-instrument `market_hours_policy`
metadata (`fx_24x5|metals|index_cash|energy`). There is **no ticker branch** (hard anti-requirement holds), but
`index_cash` (SPX500_USD) and `energy` (WTICO_USD) expected daily closures are not modelled, so "expected closure vs
outage" cannot be distinguished for those policies.

**Smallest correction:** route `market_phase`/`classify_timeframe` through a reusable `MarketHoursPolicy` selected by the
registry `market_hours_policy` key (a policy-class lookup — still no ticker branch). Pinned by
`test_market_hours_policy_metadata_not_yet_consumed_by_gaps_AMBER_finding`.

## Held (not performed)
Production migration 025, deployment, restart, seven-new production activation, production Redis keys, OANDA
subscription changes, second live stream, backfill execution, consumer/order activation. Production verified identical
(`e9e72efd4ddd`, image `sha256:9171fb67…`, restarts=0, started 2026-08-03) before and after.
