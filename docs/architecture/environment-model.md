# HERMES Environment Model (post-MVP canonical doctrine)

Status: CANONICAL. HERMES has reached MVP (R2D2 GREEN / clean-v2 CLOSED). From MVP onward, material
architecture/operational decisions are recorded durably here (Git = intended architecture), reconciled into
the R2D2 service blueprint (verified reality), with Memory Fabric holding current operational state.

## 1. HERMES core boundary
HERMES does exactly: `OANDA -> calculate candles + HERMES-owned technical indicators/signals -> persist durable
state to MariaDB -> publish fast/current state to Redis -> expose /health, /readiness, /buildinfo`.
HERMES does NOT own downstream application internals.

## 2. Explicitly outside HERMES
ARES, HELIOS, FALCON, NEO, SOLO, TRADER internals; downstream consumer credential migrations; `trinity@'%'`
estate cleanup; other applications' canaries. These do not re-enter HERMES WOs unless a direct HERMES
interface contract requires clarification.

## 3. DEV / PROD split (canonical)
- **DEV HERMES** (dell, `/srv-dev/tradingSignals`): development, testing, research, historical analysis,
  backtesting data source. Data policy `FULL_HISTORY_RETAINED`. Remains running. NOT replaced by cloud PROD.
  Must NOT be truncated by any PROD retention policy.
- **PROD HERMES** (dedicated cloud VPS): live OANDA ingestion, live production calculations, durable production
  SQL, production Redis publication, operational health/readiness. Independent of DEV.

## 4. PROD cloud topology
Standalone private three-container stack on the dedicated HERMES VPS:
`hermes-signal` (app) + `hermes-db` (MariaDB) + `hermes-cache` (Redis), on a private Docker network.
Database and Redis remain internal/private (no public ports). No other trading-platform application on this host.
Inbound firewall is the IONOS per-server managed service; no host firewall.

## 5. Configuration principle
Same core HERMES application source for DEV and PROD. Environment differences are supplied EXTERNALLY
(`ENVIRONMENT=DEV|PROD` + external config/secrets). No PROD/DEV behaviour hardcoded in core signal code.

## 6. PROD retention doctrine (NOT yet implemented)
PROD may later use bounded historical retention (candidate: ~1 year of production signal/indicator history).
Not hardcoded now. If implemented it MUST be: PROD-only; outside the core signal engine; environment-bound;
separately configurable/testable; technically incapable of acting on DEV; observable/logged; fail-safe
(bound to PROD DB identity + endpoint + environment + container/network boundary; no shared housekeeping;
no cross-environment DB access; no dangerous host/IP fallback). DEV retains full depth regardless.

## 7. No full-DEV-clone assumption
Cloud PROD is not required to contain the entire DEV historical estate. Initial PROD data is determined later
from warm-start calculation requirements, operational continuity, the selected PROD retention horizon, and disk
runway. No destructive move from DEV.

## 8. Provenance (current MVP)
Assured source SHA `00ff3091bccff1315c5b6b4e720ee9061877f685`; image digest
`sha256:7af73aa8703927e6d1d48b378dd89447febdc0e039571ccb31d754c64ab0d857`. Newer R2D2-verified truth supersedes.

## 9. Change-management rule (post-MVP)
Every material decision affecting environment purpose, deployment topology, data lifecycle, persistence,
retention, secrets, configuration, operational boundaries, interfaces, disaster recovery, or cloud deployment
must produce a durable Git artefact here (or the repository-conventional location) — not chat/Fabric alone.

## 10. PROD data initialisation (bounded seed, not a DEV clone)
PROD is seeded with the minimum data live HERMES requires — NOT the DEV historical estate:
- **Include:** HERMES config/reference tables (instruments, market_hours, source_policy, hermes_config, regime_config,
  recovery_library, etc.); warm-start higher-timeframe candles (D1/H4/H1/M30/M15, full for the active instrument,
  satisfying the ~100–200 bar max indicator lookback); and ~90 days of lower-timeframe continuity (M5/M1, signals,
  canonical_m1, hermes_levels) for the currently active PROD instrument set.
- **Exclude (schema present, zero rows):** `ticks`, `tick_seq_backfill_staging`, and DEV operational-history logs
  (backfill provenance, recovery jobs, incidents, data gaps). Raw ticks are not required for PROD (indicators derive
  from candles; DEV retains ticks for backtesting).
- **Redis:** starts CLEAN (noeviction, appendonly). No DEV RDB/AOF copy — PROD Redis warms from SQL + live flow.
- **Method:** logical HERMES-only `mariadb-dump`/import, MariaDB 11.4 → 11.4, table- and time-filtered, run online
  (`--single-transaction`) read-only against DEV. No physical datadir copy. No DEV stop/mutation.
- **Instrument scope:** the currently authorised PROD instrument set (initially `XAU_USD`; canonical gold naming
  `XAU_USD`, never `XAUUSD`). Multi-instrument capability is retained; extra instruments are enabled by config only.
- **Retention:** still NOT implemented; remains a separate PROD-only, environment-bound, DEV-incapable future control.
- **Signal stays dark** until a separately authorised OANDA cutover — exactly one live authoritative stream (DEV).
