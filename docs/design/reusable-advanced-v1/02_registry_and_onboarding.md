# Canonical registry authority + data-only onboarding + add-one-instrument proof

## Registry authority (§9)
**One authority: `tradingSignals.instruments` (governed SQL).** Selected because it already is the
persistent SSOT (loaded at startup `config.py:load_and_publish_instruments`), migration-append-only
audited, HERMES-owned, and honours "config in DB not files". Not `/etc`, not hard-coded Python.

Machine-readable column contract: `instrument_registry_schema.yaml` (this dir). New advanced-contract
policy columns (precision, tick_size, price_authority, market_hours_policy, expected_freshness,
enabled_timeframes, indicator_profile, capability flags, backfill/retention policy) are added by a **future
append-only migration** — designed here, **not executed** under this WO.

Runtime derivation: the reusable **registry loader** computes `enabled_instruments()` from
`SELECT symbol,... FROM instruments WHERE enabled=1`. The current `INSTRUMENTS` env var is **demoted** to an
optional override/allowlist intersection and ultimately removed (it is duplicate list #1). Every stage —
subscription, tick, candle, indicator, gap, backfill, key generation, SQL policy, health, tests, evidence —
derives from this loader. **No second list.**

## Data-only onboarding contract (§2, §3)
```
ADD ONE REGISTRY RECORD (or set enabled=1)  ->  ALL ENABLED HERMES CAPABILITIES DISCOVER IT
```
Onboarding MUST NOT require: a new module/script/service/container/SQL-table/Redis-schema/publisher/
indicator/gap-detector/backfill impl, copied tests, multi-file edits, or an `if instrument == ...` branch.
Instrument-specific facts live as registry metadata; unknowable-from-broker policy stays in the registry;
broker facts (tick_size/precision) are discovered+validated from OANDA where safe.

## Add-one-instrument acceptance test (§10 — BLOCKING gate)
Executed in an **isolated** environment (WP3 pattern: dedicated docker net + ephemeral MariaDB + shadow
Redis + mock/replay OANDA). Steps, each an evidence artefact:
1. clean isolated HERMES; 2. add ONE synthetic/authorised test instrument to the registry ONLY;
3. make NO app-code change; 4. start isolated runtime; 5. prove subscription-builder auto-discovery;
6. tick normalisation; 7. Redis tick publication; 8. candle construction; 9. high/low + wick calc;
10. SQL persistence per policy; 11. indicator calc; 12. indicator Redis publication; 13. gap telemetry;
14. backfill status; 15. health/status/metrics inclusion; 16. parameterised tests auto-include it;
17. disable/remove the registry record; 18. instrument disappears cleanly; 19. no stale keys/residual state
beyond governed retention.

**Onboarding git diff acceptance:** exactly one registry record change + optionally *generated* artefacts
that are never hand-maintained; **zero application-code changes**. Any app-code change to onboard = **RED**.

## Removal of the 17 duplicate lists
See `04_inventory.md`. Each is dispositioned delete / registry-driven / test-parameterised. Fallback lists
(`config.py` legacy `load_instruments_from_db`, `adapters/oanda.py:260`, script defaults, mock price tables)
are removed or sourced from the loader; test fixtures move to `@pytest.mark.parametrize("instrument",
enabled_instruments())`.
