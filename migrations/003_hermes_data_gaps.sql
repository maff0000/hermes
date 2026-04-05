-- ============================================
-- Migration 003: HERMES Data Gap Ledger
-- WO: WO-HERMES-GAP-TRUTH-0002
-- Database: tradingSignals
-- Date: 2026-03-31
-- Purpose: Persistent, authoritative truth for missing data
--          windows. Gaps are detected, tracked, and resolved
--          with deterministic status transitions.
-- ============================================

CREATE TABLE IF NOT EXISTS hermes_data_gaps (
    gap_id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    artifact_type       ENUM('CANDLE','SIGNAL')
                        NOT NULL                    COMMENT 'What kind of data is missing',
    instrument          VARCHAR(20)     NOT NULL    COMMENT 'Instrument code (e.g. XAU_USD)',
    timeframe           VARCHAR(10)     NOT NULL    COMMENT 'Timeframe (M1, M5, M15, H1, D1)',
    gap_start_utc       DATETIME        NOT NULL    COMMENT 'First missing expected timestamp (inclusive)',
    gap_end_utc         DATETIME        NOT NULL    COMMENT 'Last missing expected timestamp (inclusive)',
    expected_count      INT UNSIGNED    NOT NULL    COMMENT 'Number of expected rows in this window',
    actual_count        INT UNSIGNED    NOT NULL DEFAULT 0 COMMENT 'Number of actual rows found at detection time',
    missing_count       INT UNSIGNED    NOT NULL    COMMENT 'expected - actual at detection time',
    status              ENUM('DETECTED','CONFIRMED','IN_REPAIR','RESOLVED','INVALIDATED')
                        NOT NULL DEFAULT 'DETECTED' COMMENT 'Gap lifecycle status',
    detected_at         DATETIME(3)     NOT NULL    COMMENT 'UTC timestamp of first detection',
    confirmed_at        DATETIME(3)     NULL        COMMENT 'UTC timestamp of confirmation (rescan agreed)',
    resolved_at         DATETIME(3)     NULL        COMMENT 'UTC timestamp of resolution',
    resolution_job_id   BIGINT UNSIGNED NULL        COMMENT 'FK to hermes_recovery_jobs when WO-4 exists',
    scan_hash           VARCHAR(64)     NOT NULL    COMMENT 'Hash of (artifact_type, instrument, timeframe, gap_start, gap_end) for idempotent reconciliation',
    diagnostic_json     JSON            NULL        COMMENT 'Structured diagnostic context at detection',
    description         TEXT            NOT NULL    COMMENT 'Human-readable description',
    llm_reasoning       TEXT            NOT NULL    COMMENT 'LLM reasoning for gap classification',
    PRIMARY KEY (gap_id),
    UNIQUE KEY idx_scan_hash (scan_hash),
    KEY idx_instrument_tf (instrument, timeframe),
    KEY idx_status (status),
    KEY idx_gap_window (gap_start_utc, gap_end_utc),
    KEY idx_detected_at (detected_at)
) ENGINE=InnoDB
  COMMENT='Persistent gap ledger for HERMES. Tracks missing data windows with deterministic status transitions. Idempotent via scan_hash.';
