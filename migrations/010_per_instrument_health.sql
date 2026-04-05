-- ============================================
-- Migration 010: Per-Instrument Health Truth
-- WO: WO-HERMES-PER-INSTRUMENT-HEALTH-0010
-- Database: tradingSignals
-- Date: 2026-04-01
--
-- Purpose: Per-instrument health persistence. One row per
--          enabled instrument. Updated by watchdog. Replaces
--          global-only health as the authoritative truth model.
-- ============================================

CREATE TABLE IF NOT EXISTS hermes_instrument_health (
    instrument          VARCHAR(20)     NOT NULL    COMMENT 'Instrument code',
    truth_expected      TINYINT(1)      NOT NULL DEFAULT 1 COMMENT 'Is fresh data expected right now (market hours aware)',
    last_tick_utc       DATETIME(3)     NULL        COMMENT 'Last tick received for this instrument',
    last_m1_persisted_utc DATETIME      NULL        COMMENT 'Last M1 candle confirmed written to DB for this instrument',
    tick_age_seconds    DECIMAL(10,1)   NULL        COMMENT 'Seconds since last tick (computed at update time)',
    m1_age_seconds      DECIMAL(10,1)   NULL        COMMENT 'Seconds since last persisted M1 (computed at update time)',
    health_state        ENUM('GREEN','AMBER','RED')
                        NOT NULL DEFAULT 'RED'      COMMENT 'Per-instrument health assessment',
    reason_code         VARCHAR(64)     NULL        COMMENT 'Why this state: MARKET_CLOSED, HERMES_INSTRUMENT_TICK_STALE, etc.',
    source_state        VARCHAR(32)     NULL        COMMENT 'Source adapter state if known (CONNECTED, HALTED, etc.)',
    updated_at_utc      DATETIME(3)     NOT NULL    COMMENT 'When this row was last evaluated',
    description         TEXT            NOT NULL,
    llm_reasoning       TEXT            NOT NULL,
    PRIMARY KEY (instrument),
    KEY idx_health_state (health_state)
) ENGINE=InnoDB
  COMMENT='Per-instrument health truth. One row per enabled instrument. Updated by watchdog every cycle.';

-- Seed all 12 instruments as RED — must be proven healthy by watchdog
INSERT INTO hermes_instrument_health
(instrument, truth_expected, health_state, reason_code, updated_at_utc, description, llm_reasoning)
VALUES
('XAU_USD', 1, 'RED', NULL, UTC_TIMESTAMP(3), 'XAU/USD instrument health', '{"rationale":"Seeded RED. Watchdog must prove healthy.","source":"WO-0010"}'),
('XAG_USD', 1, 'RED', NULL, UTC_TIMESTAMP(3), 'XAG/USD instrument health', '{"rationale":"Seeded RED.","source":"WO-0010"}'),
('XPT_USD', 1, 'RED', NULL, UTC_TIMESTAMP(3), 'XPT/USD instrument health', '{"rationale":"Seeded RED.","source":"WO-0010"}'),
('XCU_USD', 1, 'RED', NULL, UTC_TIMESTAMP(3), 'XCU/USD instrument health', '{"rationale":"Seeded RED.","source":"WO-0010"}'),
('EUR_USD', 1, 'RED', NULL, UTC_TIMESTAMP(3), 'EUR/USD instrument health', '{"rationale":"Seeded RED.","source":"WO-0010"}'),
('GBP_USD', 1, 'RED', NULL, UTC_TIMESTAMP(3), 'GBP/USD instrument health', '{"rationale":"Seeded RED.","source":"WO-0010"}'),
('USD_JPY', 1, 'RED', NULL, UTC_TIMESTAMP(3), 'USD/JPY instrument health', '{"rationale":"Seeded RED.","source":"WO-0010"}'),
('AUD_USD', 1, 'RED', NULL, UTC_TIMESTAMP(3), 'AUD/USD instrument health', '{"rationale":"Seeded RED.","source":"WO-0010"}'),
('NZD_USD', 1, 'RED', NULL, UTC_TIMESTAMP(3), 'NZD/USD instrument health', '{"rationale":"Seeded RED.","source":"WO-0010"}'),
('USD_CAD', 1, 'RED', NULL, UTC_TIMESTAMP(3), 'USD/CAD instrument health', '{"rationale":"Seeded RED.","source":"WO-0010"}'),
('USD_CHF', 1, 'RED', NULL, UTC_TIMESTAMP(3), 'USD/CHF instrument health', '{"rationale":"Seeded RED.","source":"WO-0010"}'),
('EUR_GBP', 1, 'RED', NULL, UTC_TIMESTAMP(3), 'EUR/GBP instrument health', '{"rationale":"Seeded RED.","source":"WO-0010"}')
ON DUPLICATE KEY UPDATE description=VALUES(description);
