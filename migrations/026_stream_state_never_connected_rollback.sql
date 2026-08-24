-- ============================================================================
-- WO-HELM-HERMES-DEV-STARTUP-RECOVERY-RESILIENCE-AND-HEALTH-TRUTHFIX-0001
-- Rollback for migration 026: remove NEVER_CONNECTED from stream_state enum
-- ============================================================================
-- PRECONDITION: no live row may still hold stream_state='NEVER_CONNECTED' and
-- the application revision emitting it must already be rolled back — otherwise
-- writes fail with error 1265 (the exact defect class 013/026 exist to prevent).
-- The remap below is defensive: any lingering NEVER_CONNECTED row becomes
-- DISCONNECTED (its closest pre-026 meaning) before the enum is narrowed.
-- ============================================================================

UPDATE hermes_service_health
   SET stream_state = 'DISCONNECTED'
 WHERE stream_state = 'NEVER_CONNECTED';

ALTER TABLE hermes_service_health
    MODIFY COLUMN stream_state ENUM(
        'DISCONNECTED',
        'CONNECTING',
        'CONNECTED_UNPROVEN',
        'FLOWING',
        'STALE',
        'RECOVERING',
        'FAILED',
        'PARTIAL_FLOWING'
    ) NOT NULL DEFAULT 'DISCONNECTED'
      COMMENT 'Current stream lifecycle state (PARTIAL_FLOWING added by WO-HELM-HERMES-STREAM-STATE-SCHEMA-FIX-0001)';
