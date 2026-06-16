# HERMES Redis Tick Contract v1 (for Falcon / Falcon-structure)

**WO:** `WO-HELM-HERMES-REDIS-TICK-CONTRACT-DESIGN-0001`
**Status:** DESIGN_ONLY_CONTRACT_DEFINED_RUNTIME_BLOCKED — **no live Redis writes, no publisher
activation** in this WO. **All timestamps UTC (millisecond precision).**

## Ownership

HERMES owns **market truth only** and publishes **raw ticks**. HERMES does **not** own and this
contract does **not** carry: structure_engine, interpretive structure truth, Falcon structure
events, HELIOS structures, risk, regime, strategy signals, or cockpit projections (enforced by
`validate_tick_contract` — `GOV-TICK-CONTRACT-022`).

Falcon / Falcon-structure are **standalone read-only consumers** of this Redis contract. They must
**not** read `tradingSignals.ticks` / HERMES SQL / HERMES internals / `/srv-dev/tradingProteus` /
structure_engine / tradingProteus DB tables, and must not use filesystem wrappers, cross-repo
imports, legacy module calls, or hidden SQL joins. The Redis contract is the **only** boundary.

## Canonical keys

| Key | Role |
|---|---|
| `hermes:ticks:{instrument}:latest:v1` | **canonical hot-path** consumer key (per instrument) |
| `hermes:ticks:latest:v1` | **discovery/dashboard/cockpit support only** (catalog of instruments; NOT a price tick) |

Consumers hot-path read the **per-instrument** key; the aggregate is for discovery only.

## Envelope (per-instrument tick)

```json
{
  "schema_version": "v1", "service": "HERMES", "domain": "ticks",
  "contract": "hermes.ticks.latest", "contract_version": "v1",
  "key": "hermes:ticks:XAU_USD:latest:v1",
  "generated_at_utc": "YYYY-MM-DDTHH:MM:SS.sssZ",
  "valid_until_utc": "YYYY-MM-DDTHH:MM:SS.sssZ",
  "ttl_seconds": 5,
  "freshness_state": "FRESH|STALE|DEGRADED|UNAVAILABLE",
  "status": "OK|WARN|BLOCK|ERROR|UNAVAILABLE",
  "reason_codes": [],
  "provenance": {"publisher": "HERMES", "source": "oanda",
    "source_contract": "HERMES_TICK_SOURCE_V1",
    "source_received_at_utc": "...Z", "published_at_utc": "...Z",
    "instrument_registry_version": "v1", "runtime_instance": null, "derivation": "NONE_RAW_TICK"},
  "data": {"instrument": "XAU_USD", "received_at_utc": "...Z",
    "bid": 0.0, "ask": 0.0, "mid": 0.0, "spread": 0.0, "source": "oanda",
    "seq": null, "contract_version": "v1"}
}
```

`mid = (bid+ask)/2`, `spread = ask-bid` (validated). `valid_until_utc = generated_at_utc + ttl_seconds`.

## TTL / freshness policy

- `ttl_seconds = 5`. **Redis `EX` is documented as 10 seconds** for the later publisher (grace
  window; consumers still freshness-gate on `valid_until_utc`, not on key existence).
- **FRESH:** tick age ≤ 5s. **DEGRADED:** age > 5s and ≤ 15s, **or** a source warning is present.
  **STALE:** age > 15s. **UNAVAILABLE:** no valid source tick, malformed tick, Redis write
  impossible, or instrument missing/disabled.
- Freshness is **independent of completeness**; stale is never laundered to fresh.

## seq rule (explicit)

**`seq` is `null` in v1.** HERMES does **not** have a reliable real-time monotonic tick sequence on
the live publish path. The stored-row `tradingSignals.ticks.seq` is a **stored-row index** (tick
gaps are unrecoverable; it is **not** a market-completeness proof) and is **not** exposed as a
real-time tick seq. **Consumers MUST NOT infer tick completeness from `seq`** (enforced:
`GOV-TICK-CONTRACT-019`).

## UNAVAILABLE / aggregate

- **UNAVAILABLE** envelope: `freshness_state=status=UNAVAILABLE`, price fields `null`, `reason_codes`
  explain (e.g. `INSTRUMENT_DISABLED`, `NO_VALID_SOURCE_TICK`).
- **Aggregate** (`hermes:ticks:latest:v1`): discovery catalog — `data.instruments[]`, `count`,
  `per_instrument_key_pattern`, `purpose=DISCOVERY_DASHBOARD_COCKPIT_ONLY`; **no** price fields.

## Reference implementation (design-only, no I/O)

`utils/tick_contract_v1.py` — `canonical_key`, `aggregate_key`, `classify_freshness`,
`build_tick_contract`, `build_unavailable`, `build_aggregate_discovery`, `validate_tick_contract`
(fail-loud `GOV-TICK-CONTRACT-0xx`). Fixtures in `tests/fixtures/tick_contract/`.

## Later inert-publisher build (separate gated WO — NOT this WO)

A future publisher would `SET hermes:ticks:{instrument}:latest:v1 <envelope> EX 10` on each accepted
tick, from the governed HERMES publish surface (no hardcoded host/port/db, fail-loud on missing
target — see the publisher-target evidence). It is **not** built or activated here.
