# HERMES Shared Bus — Downstream Consumer Contract (ARES / HELIOS)

HERMES publishes its verified market telemetry to the **shared dev/staging Redis bus**. Downstream
services consume it READ-ONLY from the bus — they **must never request a direct database lease** against
the candle store. The Redis bus is the integration contract; the SQL DB is HERMES-private.

## Immutable connection endpoints
| Target | Value |
|--------|-------|
| Redis staging bus | **`192.168.11.10:6379`** (`proteus-redis`) |
| Key prefix | `hermes:` |

## Read scopes (per consumer)
| Consumer | Allowed read scope | Pattern |
|----------|--------------------|---------|
| **ARES** | latest computed signals | `hermes:signals:latest:*` |
| **HELIOS** | instrument configs + price | `hermes:instrument:*` and `hermes:price` |

## Rules
1. **No direct DB leases.** ARES/HELIOS connect to the Redis bus ONLY — never to `proteus-mariadb-dev`.
2. **Read-only.** Consumers never write to the `hermes:*` keyspace.
3. **Bus is canonical for integration.** Code coupling is forbidden (service independence doctrine);
   the Redis live-state contract IS the integration layer.
4. **Health is observable** via `scripts/hermes_monitor.py` (14-instrument keyspace audit → Graylog/Discord).

Producer: HERMES (Helm lane). Consumers must validate freshness (key TTL / `updated_at`) and fail-loud on
stale/missing keys rather than silently proceeding.
