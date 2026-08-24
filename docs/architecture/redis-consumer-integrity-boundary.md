# HERMES Redis consumer-integrity boundary — PERMANENT DOCTRINE

**WO:** `WO-HELM-HERMES-DEV-REDIS-CONSUMER-INTEGRITY-READONLY-BOUNDARY-0001`
**Change:** `CHG-HERMES-2026-08-24-REDIS-CONSUMER-INTEGRITY-READONLY-BOUNDARY`
**Defect closed:** `OPEN_HERMES_REDIS_CONSUMER_INTEGRITY_DEFECT` (remote consumers held
default-user `+@all ~*` authority — full mutation/admin over HERMES Redis).

## Doctrine

> HERMES Redis is a **published read interface**. Consumers may read HERMES
> market truth but may not mutate HERMES operational state.

Two independent layers, both required:

1. **Network perimeter** (unchanged by this WO): source-IP allowlisting at the
   host/provider firewall decides *who can connect*.
2. **Redis operation authority** (this WO): ACLs decide *what a connection may
   do*.

## Endpoint / authority model

```
REMOTE CONSUMER (authorised IP)
    └─ consumer authority: read-only
         ~hermes:*  &hermes:*           keys + pub/sub channels
         +@read +@connection            reads, ping/hello/select
         +subscribe +psubscribe         live tick/signal channels
         -@dangerous  (+info re-added)  no KEYS/FLUSH*/CONFIG/ACL/DEBUG/...
         no EVAL/SCRIPT, no @write      mechanically unable to mutate

HERMES PUBLISHER (hermes-signal)
    └─ writer authority: ACL user `REDIS_USERNAME` (e.g. hermes-writer)
         ~hermes:*  &hermes:*
         +@read +@write +@keyspace +@transaction +@connection +@pubsub
         -@dangerous                    no flush/config/acl/admin even as writer
         secret: `REDIS_PASSWORD`, environment-owned 0600 env file only
```

- **Consumer authentication:** none required where the deployment applies the
  consumer ruleset to the Redis `default` user (PROD `hermes-cache`): an
  authorised-IP client simply connects — the preferred zero-ceremony model.
- **Named ruleset:** the same consumer ruleset is also maintained as the ACL
  user `hermes-consumer` for environments where `default` cannot be restricted
  (see DEV constraint below) and for consumer-equivalent testing.

## Consumer key/command scope

- Keys and channels: `hermes:*` only — the entire canonical consumer contract
  lives under this namespace (verified against the published contract
  inventory). No `~*` grants.
- `+info` is re-added deliberately: consumers (ARES identity gate) pin the
  server `run_id` from `INFO`. `KEYS` stays denied (`SCAN` remains available
  via `@read`).
- Everything mutating or administrative is mechanically denied and covered by
  a live denial matrix (SET/HSET/DEL/EXPIRE/FLUSHDB/FLUSHALL/CONFIG/ACL/
  EVAL/SCRIPT/SHUTDOWN/DEBUG/...).

## Publisher fail-safe (no hidden fallback)

`utils/hermes_redis_auth_v1.redis_auth_kwargs()` is the single auth seam used
by **every** HERMES runtime redis client constructor. If `REDIS_USERNAME` is
set but the secret is missing/empty it **raises** — HERMES fails loudly and
health/readiness reflect the publication failure. It never silently falls back
to default authority and never relaxes Redis security to recover.

## Config contract (shared schema; values environment-owned)

| Key | Meaning |
|-----|---------|
| `REDIS_USERNAME` | writer ACL user; empty ⇒ legacy open model |
| `REDIS_PASSWORD` | writer secret (0600 env file only; never Git/image/logs/Fabric) |

## ACL persistence

ACL users live in the Redis instance's `users.acl` (aclfile) with **hashed**
passwords (`#sha256`), mounted alongside the instance's redis.conf — durable
across restarts. Applied per environment by its deployment owner.

## DEV constraint (recorded)

DEV Redis (`proteus-redis`, dell `:6379`) is a **shared platform bus** with
active non-HERMES writers on the default user (`prod:*`, `r2d2:*`, `trader:*`,
`candles:*`, …). Restricting `default` there would break out-of-lane legacy
applications, so on DEV: `default` remains open (bus constraint), the writer
boundary is fully active for HERMES, and the consumer ruleset is enforced and
proven via `hermes-consumer`. On PROD (`hermes-cache`, HERMES-only) the
consumer ruleset is applied to `default` itself at promotion — full enforcement
with zero consumer ceremony.

## Future consumers

New consumers connect from an authorised IP and read `hermes:*` — no per-app
Redis users, no redesign. If a consumer genuinely needs a command outside the
ruleset, it is added to the governed ruleset here, with justification.
