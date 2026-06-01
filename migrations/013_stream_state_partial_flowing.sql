-- ============================================================================
-- WO-HELM-HERMES-STREAM-STATE-SCHEMA-FIX-0001
-- Migration 013: Add PARTIAL_FLOWING to hermes_service_health.stream_state enum
-- ============================================================================
-- DEFECT
--   utils/watchdog.py StreamState defines PARTIAL_FLOWING (introduced by
--   WO-HERMES-SIGNAL-SERVICE-DEV-PER-INSTRUMENT-RESUBSCRIBE-REPAIR-0001) and the
--   watchdog persists it via HealthPersistence.update_health(stream_state=...),
--   but the hermes_service_health.stream_state ENUM (defined in
--   migrations/001_hermes_health_and_incidents.sql) never included it. MariaDB
--   therefore rejects every such write with error 1265 "Data truncated for
--   column stream_state". As a result hermes_service_health UPDATEs fail and the
--   table stays empty (0 rows) — the process-level HERMES health contract is
--   silently non-functional.
--
-- FIX
--   Widen the ENUM to admit PARTIAL_FLOWING. Append-only and ordinal-safe:
--     * All 7 original values are kept in their original order (ordinals 1..7
--       unchanged) so any already-stored row keeps its exact meaning.
--     * PARTIAL_FLOWING is APPENDED as the 8th value (ordinal 8). ENUM storage
--       order is independent of the semantic order in StreamState; appending at
--       the end is the zero-risk way to add a value.
--     * No value is removed or reordered. No row data is modified. Only the
--       column type definition changes.
--
-- APPEND-ONLY / RE-APPLY
--   This is a NEW migration file (013). Migrations 001-012 are not edited.
--   The ALTER ... MODIFY is idempotent: re-applying it sets the same ENUM
--   definition again with no error and no data change.
--
-- COVERAGE AFTER THIS MIGRATION
--   The enum admits all 8 values emitted by utils/watchdog.py StreamState:
--     DISCONNECTED, CONNECTING, CONNECTED_UNPROVEN, FLOWING, STALE,
--     RECOVERING, FAILED, PARTIAL_FLOWING
--
-- SCOPE GUARD
--   Touches ONLY hermes_service_health.stream_state. No market data, candles,
--   canonical_m1, backfill, provenance, signals, levels, instrument health,
--   config, feed mode, Redis, ARES, Falcon, or strategy tables are touched.
--
-- VALIDATION NOTE
--   This WO produces and statically validates the migration only. It is NOT
--   applied to any live or scratch database here — applying it is a runtime
--   action gated behind R2D2-HERMES audit + Architect authorisation.
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
        'PARTIAL_FLOWING'
    ) NOT NULL DEFAULT 'DISCONNECTED'
      COMMENT 'Current stream lifecycle state (PARTIAL_FLOWING added by WO-HELM-HERMES-STREAM-STATE-SCHEMA-FIX-0001)';
