# Migration 025 — Production Rollback Runbook (HELD — not executed)

**WO:** WO-HELM-HERMES-ADVANCED-V1-PRODUCTION-READINESS-0001
**Status:** PREPARED + rehearsed in isolation (`production_rehearsal_harness.sh` STEP 8/9). Requires explicit authority to run in production.

## Script
`migrations/025_advanced_v1_registry_metadata_rollback.sql` — reverts WTICO `energy`→`base_metals` FIRST, then drops the 15 added metadata columns (idempotent `DROP COLUMN IF EXISTS`), then removes `energy` from the category ENUM.

## Verified in isolation (rehearsal + isolation harness)
- WTICO returns to `base_metals`.
- Introduced columns removed only when authorised (this script).
- All 14 rows remain; pre-existing columns/data intact.
- No candle or tick history change (script touches only `instruments`).
- No unrelated ENUM/category damage (order matters: revert WTICO before removing `energy`).
- Backup-restore fidelity independently proven (STEP 9): drop + restore from the pre-migration dump → 14 rows, WTICO base_metals.

## Rollback invocability by gate
| Prior state | Rollback action | Data-loss note |
|-------------|-----------------|----------------|
| Migration only | run rollback SQL | Drops the added metadata columns → any values written to them are LOST. At this gate the only values are the migration seeds (reproducible from the migration) → loss ACCEPTABLE. |
| Migration + dark deployment | disable app / redeploy prior image, then rollback SQL | Dark app wrote no Advanced-v1 data → no additional loss. ACCEPTABLE. |
| Migration + partial shadow publication | disable capability flags (data), stop publication, preserve published Redis keys as evidence, then rollback SQL | Redis Advanced-v1 keys are shadow/dark and non-authoritative; dropping metadata columns loses only registry seed/capability data (reproducible). Capture the capability-flag values before rollback so activation can be re-derived. ACCEPTABLE with capture. |

## Data-loss statement
Dropping the migration-introduced metadata columns loses per-instrument Advanced-v1 metadata (precision/tick_size/policy/capability flags/provenance). At every gate above these are either migration seeds or data-only activation values that are **reproducible from governed sources** (migration file + activation SQL), so the loss is acceptable provided the current capability-flag values are captured to evidence first.

## Post-rollback
- Verify 14 rows, WTICO=`base_metals`, `energy` absent from ENUM, metadata columns absent.
- Readiness guard now reports MIGRATION_REQUIRED (correct — pipeline stays dark).
- Memory Fabric (helm:\*) incident + rollback record with UTC.
