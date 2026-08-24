-- ============================================================================
-- WO-HELM-HERMES-DEV-STARTUP-RECOVERY-RESILIENCE-AND-HEALTH-TRUTHFIX-0001
-- Migration 026: Add NEVER_CONNECTED to hermes_service_health.stream_state enum
-- ============================================================================
-- DEFECT CONTEXT
--   After the 2026-08-20 Dell host reboot, the initial OANDA connect failed on a
--   transient DNS error and HERMES "started without streaming" with recovery
--   never armed. The fix makes "no successful stream connection has EVER been
--   established this process lifetime" a first-class, visible stream state:
--   utils/watchdog.py StreamState.NEVER_CONNECTED, set by the lifespan on a
--   failed initial connect and driven by the governed recovery loop
--   (NEVER_CONNECTED -> RECOVERING -> CONNECTED_UNPROVEN -> FLOWING).
--
--   HealthPersistence persists stream_state into hermes_service_health. Without
--   this migration MariaDB rejects the new value with error 1265 "Data truncated
--   for column stream_state" (exactly the migration-013 defect class), so the
--   enum MUST be widened BEFORE the fixed application is deployed.
--
-- FIX (append-only, ordinal-safe — identical pattern to migration 013)
--   * All 8 existing values keep their original order (ordinals 1..8 unchanged)
--     so every stored row keeps its exact meaning.
--   * NEVER_CONNECTED is APPENDED as the 9th value (ordinal 9). ENUM storage
--     order is independent of semantic order; appending at the end is the
--     zero-risk way to add a value.
--   * No value removed or reordered. No row data modified. Only the column type
--     definition changes.
--
-- APPLY ORDER (deploy discipline)
--   Apply this migration to the target database BEFORE starting the application
--   revision that can emit NEVER_CONNECTED. DEV first; PROD only under the
--   governed exact-promotion WO.
--
-- APPEND-ONLY / RE-APPLY
--   This is a NEW migration file (026). Migrations 001-025 are not edited. The
--   ALTER ... MODIFY is idempotent: re-applying sets the same ENUM definition
--   again with no error and no data change.
--
-- SCOPE GUARD
--   Touches ONLY hermes_service_health.stream_state. No market data, candles,
--   canonical_m1, backfill, provenance, signals, levels, instrument health,
--   config, feed mode, Redis, ARES, Falcon, or strategy tables are touched.
-- ============================================================================

ALTER TABLE hermes_service_health
    MODIFY COLUMN stream_state ENUM(
        'DISCONNECTED',
        'CONNECTING',
        'CONNECTED_UNPROVEN',
        'FLOWING',
        'STALE',
        'RECOVERING',
        'FAILED',
        'PARTIAL_FLOWING',
        'NEVER_CONNECTED'
    ) NOT NULL DEFAULT 'DISCONNECTED'
      COMMENT 'Current stream lifecycle state (NEVER_CONNECTED added by WO-HELM-HERMES-DEV-STARTUP-RECOVERY-RESILIENCE-AND-HEALTH-TRUTHFIX-0001)';
