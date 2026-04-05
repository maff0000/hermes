-- ============================================
-- Migration 001: HERMES Health Truth + Incident Persistence
-- WO: WO-HERMES-STREAM-WATCHDOG-0001
-- Database: tradingSignals
-- Date: 2026-03-31
-- Purpose: Eliminate zombie-stream states by persisting
--          authoritative health truth and incident records.
-- ============================================

-- ============================================
-- hermes_service_health
-- Authoritative runtime health state for HERMES.
-- Single row per service/environment, updated by watchdog.
-- This is truth, not a log.
-- ============================================
CREATE TABLE IF NOT EXISTS hermes_service_health (
    service_name        VARCHAR(64)     NOT NULL,
    environment         VARCHAR(16)     NOT NULL,
    last_tick_utc       DATETIME(3)     NULL        COMMENT 'UTC timestamp of last received tick',
    last_stream_msg_utc DATETIME(3)     NULL        COMMENT 'UTC timestamp of last stream message (tick or heartbeat)',
    last_candle_m1_utc  DATETIME        NULL        COMMENT 'UTC timestamp of last completed M1 candle in DB',
    last_signal_utc     DATETIME        NULL        COMMENT 'UTC timestamp of last published signal',
    stream_state        ENUM(
        'DISCONNECTED',
        'CONNECTING',
        'CONNECTED_UNPROVEN',
        'FLOWING',
        'STALE',
        'RECOVERING',
        'FAILED'
    ) NOT NULL DEFAULT 'DISCONNECTED'               COMMENT 'Current stream lifecycle state',
    data_flow_state     ENUM('ACTIVE','STALE','DEAD','UNKNOWN')
                        NOT NULL DEFAULT 'UNKNOWN'   COMMENT 'Is data actually flowing?',
    health_state        ENUM('GREEN','AMBER','RED')
                        NOT NULL DEFAULT 'RED'       COMMENT 'Overall health assessment',
    recovery_state      ENUM('IDLE','IN_PROGRESS','PROOF_WINDOW','FAILED','EXHAUSTED')
                        NOT NULL DEFAULT 'IDLE'      COMMENT 'Current recovery attempt state',
    fault_code          VARCHAR(64)     NULL         COMMENT 'Active fault code if unhealthy',
    fault_detail_json   JSON            NULL         COMMENT 'Structured diagnostic detail for active fault',
    recovery_attempt_no INT UNSIGNED    NOT NULL DEFAULT 0 COMMENT 'Current recovery attempt number',
    updated_at          TIMESTAMP(3)    NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    description         TEXT            NOT NULL     COMMENT 'Human-readable description of this row purpose',
    llm_reasoning       TEXT            NOT NULL     COMMENT 'LLM reasoning for schema design decisions',
    PRIMARY KEY (service_name, environment)
) ENGINE=InnoDB
  COMMENT='Authoritative runtime health state for HERMES. Updated by watchdog. Single row per service/env.';

-- ============================================
-- hermes_incidents
-- Persistent incident record. One row per fault event.
-- Incidents open when faults detected, close when resolved.
-- This is the audit trail for operational failures.
-- ============================================
CREATE TABLE IF NOT EXISTS hermes_incidents (
    incident_id         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    service_name        VARCHAR(64)     NOT NULL,
    environment         VARCHAR(16)     NOT NULL,
    opened_at           DATETIME(3)     NOT NULL     COMMENT 'UTC timestamp when incident was detected',
    closed_at           DATETIME(3)     NULL         COMMENT 'UTC timestamp when incident was resolved (NULL = open)',
    severity            ENUM('INFO','WARNING','CRITICAL','FATAL')
                        NOT NULL                     COMMENT 'Incident severity level',
    fault_code          VARCHAR(64)     NOT NULL     COMMENT 'Stable fault code from HERMES fault contract',
    fault_summary       VARCHAR(255)    NOT NULL     COMMENT 'Human-readable one-line summary',
    diagnostic_json     JSON            NULL         COMMENT 'Structured diagnostic context at time of detection',
    resolution_json     JSON            NULL         COMMENT 'Resolution context when closed',
    status              ENUM('OPEN','RESOLVING','RESOLVED','ESCALATED')
                        NOT NULL DEFAULT 'OPEN'      COMMENT 'Incident lifecycle status',
    recovery_attempts   INT UNSIGNED    NOT NULL DEFAULT 0 COMMENT 'Number of recovery attempts made',
    description         TEXT            NOT NULL     COMMENT 'Human-readable description',
    llm_reasoning       TEXT            NOT NULL     COMMENT 'LLM reasoning for incident classification',
    PRIMARY KEY (incident_id),
    KEY idx_service_env (service_name, environment),
    KEY idx_status (status),
    KEY idx_fault_code (fault_code),
    KEY idx_opened_at (opened_at)
) ENGINE=InnoDB
  COMMENT='Persistent incident record for HERMES operational failures. Opened by watchdog, closed on resolution.';

-- ============================================
-- Seed the initial health row for hermes-dev
-- Starts as RED/DISCONNECTED — must be proven healthy by watchdog.
-- ============================================
INSERT INTO hermes_service_health (
    service_name, environment, stream_state, data_flow_state,
    health_state, recovery_state, description, llm_reasoning
) VALUES (
    'hermes', 'DEV', 'DISCONNECTED', 'UNKNOWN', 'RED', 'IDLE',
    'HERMES DEV service health state. Updated by runtime watchdog. Single authoritative row.',
    'Seeded at migration time as RED/DISCONNECTED because no service has proven health yet. Watchdog must promote to GREEN only after observing fresh data flow during market hours. This prevents any false-healthy startup state.'
) ON DUPLICATE KEY UPDATE
    description = VALUES(description),
    llm_reasoning = VALUES(llm_reasoning);
