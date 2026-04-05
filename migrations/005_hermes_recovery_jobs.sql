-- ============================================
-- Migration 005: HERMES Recovery Jobs
-- WO: WO-HERMES-BACKFILL-ENGINE-0004
-- Database: tradingSignals
-- Date: 2026-03-31
-- Purpose: Governed execution truth for recovery operations.
--          Every rebuild attempt is recorded with full
--          artifact-level detail, row counts, and fault states.
-- ============================================

CREATE TABLE IF NOT EXISTS hermes_recovery_jobs (
    job_id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    job_type            ENUM('BACKFILL','REBUILD','RECOVERY','VERIFY')
                        NOT NULL                    COMMENT 'Type of recovery operation',
    trigger_source      ENUM('OPERATOR','WATCHDOG','SCHEDULED','GAP_SCAN')
                        NOT NULL                    COMMENT 'What initiated this job',
    instrument          VARCHAR(20)     NOT NULL    COMMENT 'Target instrument',
    window_start_utc    DATETIME        NOT NULL    COMMENT 'Recovery window start',
    window_end_utc      DATETIME        NOT NULL    COMMENT 'Recovery window end',
    artifact_scope      VARCHAR(255)    NOT NULL    COMMENT 'Comma-separated artifact codes or ALL_ENABLED',
    status              ENUM('PENDING','RUNNING','COMPLETED','FAILED','PARTIAL')
                        NOT NULL DEFAULT 'PENDING'  COMMENT 'Job lifecycle status',
    fault_code          VARCHAR(64)     NULL        COMMENT 'Fault code if failed',
    total_steps         INT UNSIGNED    NOT NULL DEFAULT 0,
    completed_steps     INT UNSIGNED    NOT NULL DEFAULT 0,
    failed_steps        INT UNSIGNED    NOT NULL DEFAULT 0,
    total_rows_written  INT UNSIGNED    NOT NULL DEFAULT 0,
    attempt_no          INT UNSIGNED    NOT NULL DEFAULT 1,
    started_at          DATETIME(3)     NULL,
    finished_at         DATETIME(3)     NULL,
    duration_seconds    DECIMAL(10,3)   NULL,
    diagnostic_json     JSON            NULL        COMMENT 'Structured diagnostic context',
    description         TEXT            NOT NULL,
    llm_reasoning       TEXT            NOT NULL,
    PRIMARY KEY (job_id),
    KEY idx_instrument (instrument),
    KEY idx_status (status),
    KEY idx_window (window_start_utc, window_end_utc)
) ENGINE=InnoDB
  COMMENT='Recovery job ledger. Every rebuild attempt is tracked with full execution truth.';

CREATE TABLE IF NOT EXISTS hermes_recovery_job_items (
    job_item_id         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    job_id              BIGINT UNSIGNED NOT NULL,
    artifact_code       VARCHAR(64)     NOT NULL    COMMENT 'Recovery library artifact code',
    execution_order     INT UNSIGNED    NOT NULL    COMMENT 'Order this step ran in',
    status              ENUM('PENDING','RUNNING','COMPLETED','FAILED','SKIPPED')
                        NOT NULL DEFAULT 'PENDING',
    rebuild_strategy    VARCHAR(64)     NOT NULL    COMMENT 'Strategy used (from recovery library)',
    rows_expected       INT UNSIGNED    NULL        COMMENT 'Expected row count for this artifact/window',
    rows_written        INT UNSIGNED    NOT NULL DEFAULT 0,
    rows_validated      INT UNSIGNED    NULL        COMMENT 'Row count confirmed by post-repair check',
    fault_code          VARCHAR(64)     NULL,
    started_at          DATETIME(3)     NULL,
    finished_at         DATETIME(3)     NULL,
    duration_seconds    DECIMAL(10,3)   NULL,
    diagnostic_json     JSON            NULL,
    description         TEXT            NOT NULL,
    llm_reasoning       TEXT            NOT NULL,
    PRIMARY KEY (job_item_id),
    KEY idx_job_id (job_id),
    KEY idx_artifact (artifact_code),
    KEY idx_status (status),
    CONSTRAINT fk_job_item_job FOREIGN KEY (job_id) REFERENCES hermes_recovery_jobs(job_id)
) ENGINE=InnoDB
  COMMENT='Per-artifact execution detail within a recovery job. One row per step.';
