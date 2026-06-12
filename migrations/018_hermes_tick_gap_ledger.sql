-- ============================================
-- Migration 018: hermes_tick_gap_ledger — tick-aware accepted/unrecoverable gap ledger
-- WO: WO-HELM-HERMES-TICK-GAP-SEMANTIC-AND-LEDGER-0001
-- Database: tradingSignals
-- Date (UTC): 2026-06-12
--
-- WHY:
--   hermes_data_gaps.artifact_type is ENUM('CANDLE','SIGNAL') — it has NO concept of a
--   raw-TICK gap, so the 2026-06-10/11 power-outage tick dropouts (~9-11h cumulative per
--   instrument over two days, all instruments synchronized) are structurally invisible to
--   the existing ledger. Architect ratified (post R2D2 GREEN of the outage gap assessment):
--     1. Raw tick gaps 2026-06-10/11 are ACCEPTED as UNRECOVERABLE under current governed
--        HERMES sources (OANDA has no historical raw-tick endpoint; no fallback/archive).
--     2. HERMES tick `seq` = per-instrument monotonic sequence over STORED tick rows
--        (NOT an assertion of complete market-time tick coverage).
--   A dedicated tick-gap ledger is preferred over mutating the hermes_data_gaps ENUM:
--   cleaner, no risk to the existing candle/signal scanner's assumptions, semantically
--   separate (tick truth vs candle/signal truth).
--
--   This migration is INERT capability: it only CREATES the ledger table. It does NOT seed
--   records (see scripts/seed_tick_gap_ledger.py) and changes no existing data. Append-only.
--   Idempotency: UNIQUE(scan_hash) so a re-run of the seed never double-inserts a window.
--   ALL timestamps UTC.
-- ============================================

CREATE TABLE IF NOT EXISTS hermes_tick_gap_ledger (
  id                BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  instrument        VARCHAR(20)  NOT NULL COMMENT 'Instrument code (e.g. XAU_USD)',
  gap_start_utc     DATETIME(3)  NOT NULL COMMENT 'First missing tick boundary (UTC) — timestamp of last tick before the gap',
  gap_end_utc       DATETIME(3)  NOT NULL COMMENT 'Resume boundary (UTC) — timestamp of first tick after the gap',
  duration_seconds  INT UNSIGNED NOT NULL COMMENT 'gap_end_utc - gap_start_utc in whole seconds',
  status            ENUM('ACCEPTED','OPEN','REPAIRED','SUPERSEDED') NOT NULL DEFAULT 'OPEN'
                       COMMENT 'Lifecycle: OPEN=newly recorded; ACCEPTED=governed acceptance of unrecoverable loss; REPAIRED=data restored; SUPERSEDED=replaced by a corrected record',
  recoverability    ENUM('UNRECOVERABLE','RECOVERABLE','UNKNOWN') NOT NULL DEFAULT 'UNKNOWN'
                       COMMENT 'UNRECOVERABLE=no governed source can restore these ticks; RECOVERABLE=a source exists; UNKNOWN=not yet assessed',
  source_cause      VARCHAR(64)  NOT NULL COMMENT 'Root cause class, e.g. POWER_OUTAGE_INGEST_DROPOUT',
  detection_method  VARCHAR(64)  NOT NULL COMMENT 'How the gap was found, e.g. TICK_INTERVAL_SCAN',
  accepted_policy   VARCHAR(64)  NULL     COMMENT 'Governed policy applied, e.g. STORED_ROW_SEQUENCE',
  semantic_version  VARCHAR(64)  NULL     COMMENT 'Seq semantic version this acceptance is bound to, e.g. hermes.tick.seq.stored_row.v1',
  r2d2_finding_key  VARCHAR(128) NULL     COMMENT 'Memory Fabric key of the R2D2 finding backing this record',
  architect_ruling  TEXT         NULL     COMMENT 'Human-readable Architect ruling text backing the acceptance',
  notes             TEXT         NULL     COMMENT 'Free-text operational notes',
  diagnostic_json   LONGTEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin DEFAULT NULL
                       COMMENT 'Structured diagnostic context (prev/next tick ids, session, source)'
                       CHECK (diagnostic_json IS NULL OR json_valid(diagnostic_json)),
  scan_hash         VARCHAR(64)  NOT NULL COMMENT 'Idempotency hash of (instrument|gap_start_utc|gap_end_utc)',
  created_at_utc    DATETIME(3)  NOT NULL COMMENT 'UTC time this record was written',
  created_by        VARCHAR(64)  NOT NULL COMMENT 'Author, e.g. WO-HELM-HERMES-TICK-GAP-SEMANTIC-AND-LEDGER-0001',
  updated_at_utc    DATETIME(3)  NULL     COMMENT 'UTC time of last status transition',
  PRIMARY KEY (id),
  UNIQUE KEY uq_tick_gap_scan_hash (scan_hash),
  KEY idx_instrument (instrument),
  KEY idx_status (status),
  KEY idx_recoverability (recoverability),
  KEY idx_gap_window (gap_start_utc, gap_end_utc)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='HERMES tick-gap ledger. Records raw-tick gaps and their governed recoverability/acceptance. seq is sequenced over STORED rows; consumers needing complete market-time coverage MUST consult this ledger. Idempotent via UNIQUE(scan_hash).';
