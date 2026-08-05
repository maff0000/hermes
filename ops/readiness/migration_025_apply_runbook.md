# Migration 025 — Production Apply Runbook (HELD — not executed)

**WO:** WO-HELM-HERMES-ADVANCED-V1-PRODUCTION-READINESS-0001
**Status:** PREPARED, NOT EXECUTED. Requires explicit Chief Architect migration authority (activation gate step 3).

## Identity
- **Operator:** named HELM operator at execution time (record actual name + UTC).
- **Target:** production `instruments` table (SSOT), DB reached via the production container's own governed DB config.
- **Source commit:** the merged canonical main containing migration 025 (`migrations/025_advanced_v1_registry_metadata.sql`).

## Preconditions (all must hold)
1. Calendar authority gate GREEN for every policy to be *activated* (note: `index_cash`/`energy` are BLOCKED — `ASSUMPTION_REQUIRES_PROVIDER`; activating SPX500/WTICO market-hours closure is NOT permitted until provider evidence).
2. `migration_025_production_preflight.sh` run GREEN: 14 rows, `energy` absent, WTICO=`base_metals`, 0 metadata columns, no column-name conflicts.
3. Production-like rehearsal GREEN (`production_rehearsal_harness.sh` → `ADVANCED_V1_PRODUCTION_REHEARSAL_GREEN`).
4. R2D2 assurance of this readiness package.
5. Explicit written migration authority.

## Determination
- **Maintenance window:** not required for correctness (additive `ADD COLUMN IF NOT EXISTS` + ENUM MODIFY + targeted UPDATEs on a 14-row InnoDB table; expected sub-second metadata lock). Prefer a low-traffic window to minimise the brief ALTER lock.

## Steps
1. **Backup proof:** `mariadb-dump <db> instruments > prod_instruments_pre025_<UTC>.sql`; verify row count = 14 in the dump. Store off the target host.
2. **Pre-migration snapshot:** capture DDL (`SHOW CREATE TABLE instruments`), row count, WTICO category, category ENUM, column list.
3. **Apply (single transaction where the engine permits DDL transactionality; otherwise ordered statements):** run `migrations/025_advanced_v1_registry_metadata.sql`.
4. **Lock monitoring:** watch `SHOW PROCESSLIST` / `information_schema.metadata_lock_info` during the ALTER.
5. **Row-count check:** 14 before == 14 after (no INSERT/DELETE).
6. **Schema checks:** 15 metadata columns added; `category` ENUM now includes `energy`.
7. **WTICO correction:** `WTICO_USD.category` == `energy`.
8. **14-row preservation:** all original symbols present.
9. **Capability flags after:** XAU_USD tick/indicator/gap = 1/1/1; the seven new = 0/0/0 (NOT_ENABLED — migration does NOT activate them).
10. **Readiness-guard verification:** `utils/hermes_advanced_v1_readiness_v1.readiness(column_names=<information_schema list>)` → `ready:true`.
11. **Post-migration smoke:** registry loads; `capability_instruments(...)` returns exactly `{XAU_USD}` (production capability unchanged); no publication (all env gates still off).
12. **Evidence capture + Memory Fabric record (helm:\*), UTC timestamps.**

## Abort criteria
- Any row-count change, ENUM/category damage, column-name conflict, lock exceeding the agreed bound, or readiness-guard failure → STOP, do not proceed to deployment, invoke the rollback runbook, preserve evidence, declare incident.

## Rollback decision point
- After step 5 (row-count) and step 6/7 (schema/WTICO). If any check fails → rollback immediately.

**This migration does NOT authorise deployment, publication, or activation. Those are separate, later, individually-authorised gates.**
