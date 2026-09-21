# HMT-0 — `darwin_ro` provenance chronology

**Initiative:** `HMT-0 — Market Truth v2 Architecture Closure`
**Status:** DOCUMENTATION / ARCHITECTURE ONLY. See [`hmt0-closure-report.md`](hmt0-closure-report.md).
This document is a **sanitised closure narrative** of already-completed, already-closed state — it
records history, it does not propose or authorise anything new. No credential, password, or hash of any
kind appears anywhere in this document.

## Purpose

`darwin_ro` is a real principal that already exists, provisioned against the canonical historical
authority surface shipped in PR #166 (`migrations/027_darwin_canonical_historical_authority.sql` /
`migrations/027_darwin_readonly_principal_grants.template.sql`, both read in full while preparing this
pack). Its lifecycle produced four sequential, non-contradictory observations across four dates. This
document exists so a future reader encountering PR #166's own contemporaneous "unresolved" evidence does
not mistake it for a currently-open defect — it is a closed, historical snapshot from a principal that was
provisioned later the same day.

## §1 — Sequential states (not contradictory)

1. **PR #166** (merged to HERMES `main` at `3f90e640c9c9c1f4a22ba4ac586a1d478f35a997` — this pack's own
   base commit — on 2026-09-16) shipped `migrations/027_darwin_readonly_principal_grants.template.sql`,
   the migration that creates the role. At PR-close time, the principal itself had not yet been
   provisioned live: two earlier provisioning attempts were **blocked** because the MariaDB root
   credential could not be located (only container-env and `.env` root variables were checked, missing
   the dedicated secret file left by an unrelated earlier recovery WO). PR #166's own contemporaneous
   evidence therefore correctly recorded `darwin_ro` as unresolved at that point in time.
2. **2026-09-16, later the same day:** the correct root-credential path was found, `darwin_ro` was
   provisioned, and a real connect-as-`darwin_ro` SELECT proof plus an INSERT/UPDATE/DELETE denial proof
   was recorded.
3. **2026-09-17:** the credential was rotated as a precaution ahead of DARWIN PID-001's deploy gate; the
   old credential was confirmed denied, the new one confirmed working, and the grants were re-proven
   identical — still exactly the same 7 SELECT-only grants.
4. **2026-09-20 (this closure re-verification):** independently re-verified from scratch, using
   `information_schema` privilege views (not `SHOW GRANTS`, to avoid exposing the password hash that
   `SHOW GRANTS` embeds on this MariaDB version — see §3), plus a fresh live connect-as-`darwin_ro` proof.

These are four sequential, consistent snapshots of the same principal's lifecycle. **"Unresolved at
PR-close" and "provisioned and working days later" are not in tension** — they describe two different
points in the same, honestly-recorded timeline. Nothing about this chronology represents PR #166's
evidence as having been wrong; it was an accurate record of that moment, superseded by real, later
provisioning.

## §2 — Current state (verified 2026-09-20T20:44:23Z)

- **Database host identity (no secrets):** MariaDB 11.4.2, container `proteus-mariadb-dev`, reachable at
  `192.168.11.10:3307` — a shared multi-tenant instance also hosting other unrelated databases;
  `darwin_ro` is scoped to exactly one database within it.
- **Database:** `tradingSignals`.
- **Principal:** `darwin_ro@%`, auth plugin `mysql_native_password`, account not locked.
- **Exact grants** (`information_schema.TABLE_PRIVILEGES`, `GRANTEE='darwin_ro'@'%'`) — exactly 7 rows,
  all `SELECT`, all `IS_GRANTABLE=NO`: `tradingSignals.canonical_candles`, `.canonical_candles_d1`,
  `.canonical_candles_h1`, `.canonical_candles_h4`, `.canonical_candles_m1`, `.canonical_candles_m15`,
  `.canonical_candles_m5`. This matches exactly the 7 grants specified in
  `migrations/027_darwin_readonly_principal_grants.template.sql`.
- `information_schema.SCHEMA_PRIVILEGES` for this grantee: **empty** — no database-wide privilege of any
  kind. `information_schema.USER_PRIVILEGES`: exactly one row, `USAGE`/`IS_GRANTABLE=NO` (the
  "account exists" marker, confers nothing).
- **Explicit absence of mutation/admin privileges:** `INSERT`, `UPDATE`, `DELETE`, `CREATE`, `DROP`,
  `ALTER`, `INDEX`, `TRIGGER`, `GRANT OPTION`, `REFERENCES`, `EXECUTE`, `SUPER`, `CREATE USER`, `RELOAD`,
  `SHUTDOWN`, `PROCESS`, `FILE` — none of these appear anywhere across the three privilege views for this
  grantee.
- **Successful SELECT proof** against the approved canonical historical surface (live, as `darwin_ro`,
  not root): `canonical_candles_h4` → 2467 rows; `canonical_candles_d1` → 128 rows; per-instrument/
  timeframe counts for `XAU_USD`: D1=128, H1=10973, H4=2467, M1=625330, M15=43827, M5=132651 (H4/D1
  counts match independent root-connection counts obtained during the earlier HMT-0 archaeology exactly —
  cross-session consistency confirmed).
- **Scope-tightness proof beyond the stated ask:** an attempt to `SELECT COUNT(*) FROM mysql.user` as
  `darwin_ro` was correctly denied (`ERROR 1142: SELECT command denied to user 'darwin_ro'@'172.24.0.1'
  for table mysql.user`).

## §3 — Self-caught incident during evidence-gathering (disclosed transparently)

An initial `SHOW GRANTS FOR 'darwin_ro'@'%'` query (run as root) returned MariaDB's standard output
format, which embeds the account's password hash in the base
`GRANT USAGE ... IDENTIFIED BY PASSWORD '...'` line. That hash was displayed in a tool-output stream
during the evidence-gathering session but was **never re-displayed, logged, or committed anywhere
afterward**; all subsequent verification used `information_schema.*_PRIVILEGES` views, which report
privilege types without any credential material.

**Lesson, recorded here for future role-verification work:** prefer `information_schema` privilege views
(`TABLE_PRIVILEGES`, `SCHEMA_PRIVILEGES`, `USER_PRIVILEGES`) over `SHOW GRANTS` on this MariaDB version
for any future role-verification task, precisely because `SHOW GRANTS`'s output format on this version
embeds credential-shaped material that a privilege-only view does not. This lesson is recorded
narratively; no hash, password, or credential-shaped string appears anywhere in this document, in
compliance with this pack's absolute security discipline.

## §4 — What this document does not do

Does not re-derive this evidence from a live database connection — this pack has no need to, and no
instruction to, touch any live database. Every fact in §2 and §3 is a narrative summary of already-
sanitised evidence supplied for this closure pack, not a fresh query run by this pack. Does not propose
any change to `darwin_ro`'s grants, rotation schedule, or lifecycle — this is closed, historical state,
recorded for completeness alongside the forward-looking GC architecture in the rest of this pack.
