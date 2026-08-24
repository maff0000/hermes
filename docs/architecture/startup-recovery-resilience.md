# HERMES startup / recovery resilience and health truth

**WO:** `WO-HELM-HERMES-DEV-STARTUP-RECOVERY-RESILIENCE-AND-HEALTH-TRUTHFIX-0001`
**Root cause event:** Dell (DEV host) reboot 2026-08-20T08:20Z — HERMES DEV stayed
alive-but-dead (no stream, frozen SQL, expired Redis freshness) for four days behind
green surfaces.

## North star

> A temporary dependency failure must never leave HERMES silently alive-but-dead.
> HERMES tells the truth, keeps retrying safely, recovers automatically, and returns
> to FLOWING without human intervention.

## The proven defect chain (before this WO)

1. Boot #1 raced MariaDB: the first unprotected lifespan DB read raised pymysql 2013
   and aborted startup ("Application startup failed. Exiting.").
2. Boot #2 (container restart) reached the OANDA connect while host DNS was still
   down. `connect()` returned False once and the lifespan logged
   "service will start without streaming" — the stream task, watchdog arming and
   recovery lived ONLY in the success branch, so nothing ever retried.
3. Truth surfaces lied: the compose healthcheck was a bare TCP connect (container
   "healthy" forever), the publisher heartbeat hardcoded `status: OK`, and
   `/readiness` reported DB/Redis connectivity as a copy of the health colour
   (claiming FAILED even when live probes succeeded).

## Startup state machine (after this WO)

```
STARTING
   │  config load (env only)
   ▼
WAITING_FOR_DB          bounded wait-for-DB gate (utils/hermes_startup_gate_v1)
   │  ready ────────────► continue     timeout ► DbStartupTimeout: VISIBLE exit
   ▼
CONNECTING_OANDA        one initial connect attempt
   │  success ► CONNECTED_UNPROVEN + proof window
   │  failure ► StreamState.NEVER_CONNECTED (first-class, RED, not-ready)
   ▼
STARTED                 oanda_stream_task + level_update_task run in BOTH cases
```

`startup_phase` (STARTING / WAITING_FOR_DB / CONNECTING_OANDA / STARTED) is
surfaced on `/health` and in structured logs. It is a startup-only marker: stream
truth (NEVER_CONNECTED / RECOVERING / CONNECTED_UNPROVEN / FLOWING / STALE /
PARTIAL_FLOWING / FAILED) remains owned by the watchdog — one truth surface.

## DB startup semantics

- Bounded, observable retry: `SELECT 1` probes with exponential backoff
  (`DB_STARTUP_RETRY_INITIAL_DELAY` → ×`DB_STARTUP_RETRY_BACKOFF_MULTIPLIER`,
  capped at `DB_STARTUP_RETRY_MAX_DELAY`), total budget
  `DB_STARTUP_WAIT_TIMEOUT_SECONDS`. No tight loop; every wait logged with UTC,
  attempt count and delay; no secret ever logged.
- A DB that needs a short time after host reboot ⇒ HERMES waits and proceeds.
- A DB that never comes up ⇒ `DbStartupTimeout` propagates, the process exits
  loudly, the container restart policy owns the next attempt. HERMES never
  pretends to be healthy without a database.

## Initial OANDA connect / never-connected recovery

- The initial connect is no longer one-shot-fatal for streaming. On failure the
  watchdog stream state becomes **`NEVER_CONNECTED`** (migration 026 widens the
  `hermes_service_health.stream_state` enum, append-only, ordinal-safe).
- `oanda_stream_task()` starts in BOTH branches. Its existing governed reconnect
  loop (`STREAM_RETRY_*` bounded exponential backoff, watchdog RECOVERING /
  recovery-attempt accounting / proof window / `HERMES_RECOVERY_EXHAUSTED`
  fatal-exit doctrine) now owns BOTH failure classes:
  - never-connected startup state, and
  - previously-connected stream loss.
  One recovery mechanism. No second competing loop. No new REST polling, no
  extra OANDA load beyond the existing recovery attempts, shared-account
  discipline and the PROD/DEV scheduled REST phase-offset doctrine unchanged.
- Recovery path: `NEVER_CONNECTED → RECOVERING → CONNECTED_UNPROVEN (proof
  window) → FLOWING` on the first real tick.

## Health / readiness / heartbeat truth rules

- **Container health = application health.** The compose healthcheck runs the
  governed probe (`utils/hermes_healthcheck_probe_v1`): GET `/health`;
  GREEN/AMBER healthy (AMBER tolerated to avoid flap during short recovery);
  RED, no-contract-body, or connection-refused ⇒ unhealthy. The Dockerfile
  HEALTHCHECK and compose now agree — no bare TCP test anywhere.
- **/health** continues to report the watchdog truth and adds `startup_phase`.
  No stream while the market should be flowing ⇒ RED (503).
- **/readiness** `database_connectivity` / `redis_connectivity` are live bounded
  probes (`SELECT 1` / `PING`, fail-closed, per evaluation): current runtime
  truth — a past failure can never poison a recovered runtime, and a dead
  dependency reads FAILED regardless of the health colour.
  `NEVER_CONNECTED`/`FAILED` project as determinate "disconnected" (not-ready),
  never indeterminate.
- **Publisher heartbeat + `hermes:health:v1`**: top-level status is DERIVED from
  the per-timeframe latest-key freshness (`derive_publisher_status`): all FRESH
  ⇒ OK; mixed ⇒ WARN; none fresh ⇒ FAIL. Stale market data can never publish an
  OK operational status. Market calculations are untouched.
- **feed_health** (already honest) remains authoritative for per-instrument feed
  truth; the surfaces above no longer materially contradict it.

## Expected behaviour after reboot / transient dependency loss

DB and/or DNS/OANDA temporarily unavailable at boot ⇒ HERMES starts (or waits)
visibly degraded/not-ready, retries with bounded backoff inside OANDA connection
limits, reconnects automatically when the dependency returns, resumes ticks, SQL
and Redis publication advance again, and health/readiness return GREEN — with no
manual restart.

## Config

See `docs/config/hermes-configuration-reference.md` (startup gate keys) and
`ops/staging/staging.env.example`. One schema, DEV and PROD identical keys —
values external only. No config in code.

## Deploy order note

Apply `migrations/026_stream_state_never_connected.sql` to the target database
BEFORE starting an application revision that can emit `NEVER_CONNECTED`
(error-1265 class guard). DEV first; PROD only via the governed exact-promotion
WO after R2D2 assurance.
