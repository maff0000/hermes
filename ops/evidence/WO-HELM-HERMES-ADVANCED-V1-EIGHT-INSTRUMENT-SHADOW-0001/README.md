# Isolated Eight-Instrument Advanced-v1 Shadow Exercise — Evidence

**WO:** `WO-HELM-HERMES-ADVANCED-V1-EIGHT-INSTRUMENT-SHADOW-0001`
**Base:** canonical `main` `40a29f6c` (PR #131 merge).
**Verdict:** `GREEN` — the previous AMBER market-hours-policy metadata gap is CORRECTED (see §Correction). All families GREEN.

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

## Correction (was AMBER → now GREEN) — metadata-driven market-hours policy
New reusable authority `utils/hermes_market_hours_policy_v1.py`: four governed policies as **data instances** —
`fx_24x5`, `metals`, `index_cash`, `energy` — resolved by the registry `market_hours_policy` key (`resolve_policy`,
fail-closed on unknown/missing, **no** uniform default, **no** ticker lookup). DST-correct via explicit
`ZoneInfo("America/New_York")`; UTC is the sole internal authority. `fx`/`metals` mirror the governed
`config/market_hours_schedule.v1.json` (config_version 3); `index_cash`/`energy` are explicit **documented** governed
assumptions (no in-repo named schedule) to validate against the exchange calendar before production activation.

The generic gap path now consumes it: `market_phase(dt, policy)`, `_period_fully_open(open_epoch, tf, policy)`,
`classify_timeframe(..., policy)`, `analyze_gaps(..., policy)`; `GapsPublisher` resolves each instrument's policy from
registry metadata and records the key in the contract (`market_hours_policy` + `calendar_source`). Results:
- **fx_24x5** continuous week, no daily break; **XAU/FX parity preserved** (metals Sun-18:00/Fri-17:00 NY coincide with
  the legacy fixed-UTC window in EDT — existing gaps tests unchanged, 3012-test suite green).
- **metals/index_cash/energy** model the daily 17:00–18:00 NY halt as `CLOSED_SESSION` (expected closure, **not** an
  outage), DST-correct (21:30Z summer / 22:30Z winter).
- Open-period gaps still detected; expected-closure slots never fabricate `GAPS_FOUND`; per-policy isolation holds.
- **No ticker branch, no ticker→policy dict, no host-local time, no env-driven selection** (static scan enforced).

Named results: `MARKET_HOURS_POLICY_ISOLATION_GREEN`, `EIGHT_INSTRUMENT_MARKET_HOURS_POLICY_GREEN`. Pinned by
`tests/test_market_hours_policy_v1.py` (21 tests) and the shadow suite.

## Held (not performed)
Production migration 025, deployment, restart, seven-new production activation, production Redis keys, OANDA
subscription changes, second live stream, backfill execution, consumer/order activation. Production verified identical
(`e9e72efd4ddd`, image `sha256:9171fb67…`, restarts=0, started 2026-08-03) before and after.
