-- ============================================
-- Migration 006: Canonical M1 Truth + Source Policy
-- WO: WO-HERMES-CANONICAL-M1-SCHEMA-A
-- Epic: EPIC-HERMES-SIGNAL-SOURCE-RESILIENCE-001
-- Database: tradingSignals
-- Date: 2026-03-31
--
-- Purpose: Foundation schema for canonical M1 truth with
--          explicit provenance and per-instrument source policy.
--          This migration is ADDITIVE ONLY — no runtime behavior
--          changes, no existing table modifications.
-- ============================================

-- ============================================================
-- canonical_m1
--
-- One row per instrument per UTC minute bucket.
-- First valid arrival wins. Provenance is explicit.
-- Repair/backfill is marked, not silent replacement.
--
-- This table is the future authoritative M1 truth.
-- During migration, it runs IN PARALLEL with candles_M1.
-- candles_M1 remains the live truth until cutover criteria met.
-- ============================================================
CREATE TABLE IF NOT EXISTS canonical_m1 (
    id                      BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    instrument              VARCHAR(20)     NOT NULL    COMMENT 'Instrument code (e.g. XAU_USD)',
    minute_bucket_utc       DATETIME        NOT NULL    COMMENT 'Canonical UTC minute (truncated to :00). This is the truth key.',
    open                    DECIMAL(12,5)   NOT NULL,
    high                    DECIMAL(12,5)   NOT NULL,
    low                     DECIMAL(12,5)   NOT NULL,
    close                   DECIMAL(12,5)   NOT NULL,
    volume                  INT UNSIGNED    NOT NULL DEFAULT 0 COMMENT 'Tick count (live) or broker volume (recovery). Semantics governed by ingest_mode.',
    source_id               VARCHAR(32)     NOT NULL    COMMENT 'Which source produced this candle (e.g. oanda_stream, oanda_rest, ibkr_stream)',
    ingest_mode             ENUM(
        'LIVE_FIRST_ACCEPTED',
        'REPAIR',
        'BACKFILL',
        'MANUAL'
    ) NOT NULL                                          COMMENT 'How this row entered the table. LIVE_FIRST_ACCEPTED = first arrival during live operation. REPAIR/BACKFILL = post-hoc fill from source. MANUAL = operator override.',
    arrival_utc             DATETIME(3)     NOT NULL    COMMENT 'UTC timestamp when the candidate arrived at the canonical engine',
    source_timestamp_utc    DATETIME(3)     NULL        COMMENT 'Original timestamp from the source system (if available)',
    complete                TINYINT(1)      NOT NULL DEFAULT 1 COMMENT 'Whether the candle period has fully closed',
    description             TEXT            NOT NULL    COMMENT 'Human-readable description',
    llm_reasoning           TEXT            NOT NULL    COMMENT 'LLM reasoning for schema design decisions',
    created_at              TIMESTAMP(3)    DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    UNIQUE KEY idx_instrument_minute (instrument, minute_bucket_utc),
    KEY idx_source_id (source_id),
    KEY idx_ingest_mode (ingest_mode),
    KEY idx_minute_bucket (minute_bucket_utc),
    KEY idx_arrival (arrival_utc)
) ENGINE=InnoDB
  COMMENT='Canonical M1 truth. One row per instrument per UTC minute. First valid arrival wins. Provenance explicit. EPIC-HERMES-SIGNAL-SOURCE-RESILIENCE-001.';


-- ============================================================
-- hermes_source_policy
--
-- Per-instrument source configuration. Governs which sources
-- are authoritative, fallback order, and acceptance windows.
-- No instrument inherits silently from another.
-- ============================================================
CREATE TABLE IF NOT EXISTS hermes_source_policy (
    instrument              VARCHAR(20)     NOT NULL    COMMENT 'Instrument code. One row per instrument. No inheritance.',
    primary_source          VARCHAR(32)     NOT NULL    COMMENT 'Primary source_id for this instrument (e.g. oanda_stream)',
    fallback_sources_json   JSON            NULL        COMMENT 'Ordered list of fallback source_ids. NULL = no fallback in v1.',
    acceptance_window_sec   INT UNSIGNED    NOT NULL    COMMENT 'Maximum seconds after minute_bucket_utc that a candidate is accepted. Per-instrument, no default inheritance.',
    is_enabled              TINYINT(1)      NOT NULL DEFAULT 1,
    description             TEXT            NOT NULL,
    llm_reasoning           TEXT            NOT NULL,
    created_at              TIMESTAMP       DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMP       DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (instrument)
) ENGINE=InnoDB
  COMMENT='Per-instrument source policy. Governs primary source, fallback, acceptance window. No silent inheritance between instruments.';


-- ============================================================
-- hermes_candidate_log
--
-- Structured log of rejected candidates. Not a truth table —
-- an audit trail for understanding source behavior and
-- debugging multi-source disagreement in future phases.
-- ============================================================
CREATE TABLE IF NOT EXISTS hermes_candidate_log (
    log_id                  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    instrument              VARCHAR(20)     NOT NULL,
    minute_bucket_utc       DATETIME        NOT NULL,
    source_id               VARCHAR(32)     NOT NULL,
    ingest_mode             VARCHAR(32)     NOT NULL,
    rejection_reason        VARCHAR(64)     NULL        COMMENT 'Fault code if rejected, NULL if accepted',
    open                    DECIMAL(12,5)   NULL,
    high                    DECIMAL(12,5)   NULL,
    low                     DECIMAL(12,5)   NULL,
    close                   DECIMAL(12,5)   NULL,
    arrival_utc             DATETIME(3)     NOT NULL,
    logged_at               TIMESTAMP(3)    DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (log_id),
    KEY idx_instrument_minute (instrument, minute_bucket_utc),
    KEY idx_rejection (rejection_reason)
) ENGINE=InnoDB
  COMMENT='Audit log of all candidate M1 submissions (accepted and rejected). For debugging and future multi-source analysis.';


-- ============================================================
-- Seed: source policy for all 12 active instruments
-- V1: all use oanda_stream as primary, no fallback.
-- Each instrument has its own explicit row — no inheritance.
-- ============================================================
INSERT INTO hermes_source_policy
(instrument, primary_source, fallback_sources_json, acceptance_window_sec, is_enabled, description, llm_reasoning)
VALUES
('XAU_USD', 'oanda_stream', NULL, 120, 1,
 'XAU_USD source policy: OANDA primary, no fallback in v1.',
 '{"rationale": "XAU_USD is the primary trading instrument. 120s acceptance window matches watchdog tick staleness threshold. No fallback source available in v1.", "source": "WO-HERMES-CANONICAL-M1-SCHEMA-A"}'),
('XAG_USD', 'oanda_stream', NULL, 120, 1,
 'XAG_USD source policy: OANDA primary, no fallback in v1.',
 '{"rationale": "Precious metals group. Same acceptance window as XAU_USD.", "source": "WO-HERMES-CANONICAL-M1-SCHEMA-A"}'),
('XPT_USD', 'oanda_stream', NULL, 120, 1,
 'XPT_USD source policy: OANDA primary, no fallback in v1.',
 '{"rationale": "Precious metals group.", "source": "WO-HERMES-CANONICAL-M1-SCHEMA-A"}'),
('XCU_USD', 'oanda_stream', NULL, 120, 1,
 'XCU_USD source policy: OANDA primary, no fallback in v1.',
 '{"rationale": "Commodities group.", "source": "WO-HERMES-CANONICAL-M1-SCHEMA-A"}'),
('EUR_USD', 'oanda_stream', NULL, 120, 1,
 'EUR_USD source policy: OANDA primary, no fallback in v1.',
 '{"rationale": "Major FX pair. Same acceptance window.", "source": "WO-HERMES-CANONICAL-M1-SCHEMA-A"}'),
('GBP_USD', 'oanda_stream', NULL, 120, 1,
 'GBP_USD source policy: OANDA primary, no fallback in v1.',
 '{"rationale": "Major FX pair.", "source": "WO-HERMES-CANONICAL-M1-SCHEMA-A"}'),
('USD_JPY', 'oanda_stream', NULL, 120, 1,
 'USD_JPY source policy: OANDA primary, no fallback in v1.',
 '{"rationale": "Major FX pair.", "source": "WO-HERMES-CANONICAL-M1-SCHEMA-A"}'),
('AUD_USD', 'oanda_stream', NULL, 120, 1,
 'AUD_USD source policy: OANDA primary, no fallback in v1.',
 '{"rationale": "Minor FX pair.", "source": "WO-HERMES-CANONICAL-M1-SCHEMA-A"}'),
('NZD_USD', 'oanda_stream', NULL, 120, 1,
 'NZD_USD source policy: OANDA primary, no fallback in v1.',
 '{"rationale": "Minor FX pair.", "source": "WO-HERMES-CANONICAL-M1-SCHEMA-A"}'),
('USD_CAD', 'oanda_stream', NULL, 120, 1,
 'USD_CAD source policy: OANDA primary, no fallback in v1.',
 '{"rationale": "Minor FX pair.", "source": "WO-HERMES-CANONICAL-M1-SCHEMA-A"}'),
('USD_CHF', 'oanda_stream', NULL, 120, 1,
 'USD_CHF source policy: OANDA primary, no fallback in v1.',
 '{"rationale": "Minor FX pair.", "source": "WO-HERMES-CANONICAL-M1-SCHEMA-A"}'),
('EUR_GBP', 'oanda_stream', NULL, 120, 1,
 'EUR_GBP source policy: OANDA primary, no fallback in v1.',
 '{"rationale": "Cross pair.", "source": "WO-HERMES-CANONICAL-M1-SCHEMA-A"}');


-- ============================================================
-- Config keys in hermes_config for canonical M1 engine
-- ============================================================
INSERT INTO hermes_config (config_key, config_value, value_type, description, llm_reasoning)
VALUES
('canonical_m1_default_acceptance_window_sec', '120', 'int',
 'Default acceptance window for canonical M1 candidates if instrument has no specific policy. Used as safety fallback only — every instrument SHOULD have an explicit policy row.',
 '{"rationale": "120s matches watchdog tick staleness threshold. This default exists only as a safety net — the engine should read from hermes_source_policy per instrument. If an instrument has no policy row, the engine should log a warning and use this default rather than silently failing.", "source": "WO-HERMES-CANONICAL-M1-SCHEMA-A"}'),

('canonical_m1_reject_if_exists', 'true', 'bool',
 'If true, reject candidate M1 when canonical row already exists for that instrument/minute bucket. V1 rule: first valid arrival wins.',
 '{"rationale": "First-valid-arrival-wins is the v1 ingestion rule. Setting this to false would allow last-write-wins, which is explicitly NOT the v1 design. This config exists so the behavior is governed and auditable, not hardcoded.", "source": "WO-HERMES-CANONICAL-M1-SCHEMA-A"}'),

('canonical_m1_log_rejected_candidates', 'true', 'bool',
 'If true, log rejected candidates to hermes_candidate_log table for audit.',
 '{"rationale": "In v1 with a single source this will rarely fire. But the logging infrastructure must exist before v2 multi-source so we can debug disagreement. Cost is minimal — one INSERT per rejection.", "source": "WO-HERMES-CANONICAL-M1-SCHEMA-A"}')

ON DUPLICATE KEY UPDATE
  config_value = VALUES(config_value),
  description = VALUES(description),
  llm_reasoning = VALUES(llm_reasoning);
