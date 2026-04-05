-- ============================================
-- Migration 002: HERMES-owned config table
-- WO: WO-HERMES-STREAM-WATCHDOG-0001
-- Database: tradingSignals
-- Date: 2026-03-31
-- Purpose: HERMES runtime config must live in HERMES domain,
--          not in zeusv4_config (tradingProteus). This table
--          follows the same GOV-CFG-001 contract but is owned
--          by HERMES in the tradingSignals database.
-- ============================================

CREATE TABLE IF NOT EXISTS hermes_config (
    id              INT             NOT NULL AUTO_INCREMENT,
    config_key      VARCHAR(100)    NOT NULL,
    config_value    TEXT            NOT NULL,
    value_type      ENUM('string','int','float','bool','json') NOT NULL DEFAULT 'string',
    description     TEXT            NOT NULL    COMMENT 'Human-readable description of this config key',
    llm_reasoning   LONGTEXT        NOT NULL    COMMENT 'LLM reasoning for value choice',
    enabled         TINYINT(1)      NOT NULL DEFAULT 1,
    created_at      TIMESTAMP       DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP       DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY idx_config_key (config_key),
    KEY idx_enabled (enabled)
) ENGINE=InnoDB
  COMMENT='HERMES-owned runtime configuration. GOV-CFG-001 compliant. All keys require description and llm_reasoning.';

-- Seed watchdog config keys
INSERT INTO hermes_config (config_key, config_value, value_type, description, llm_reasoning) VALUES
('hermes_tick_staleness_threshold_sec', '120', 'int',
 'Maximum seconds without a fresh tick before declaring STALE during market hours',
 '{"rationale": "120s allows for brief OANDA stream pauses without false triggers. During active market hours OANDA sends ticks every 0.5-2s for XAU_USD. A 2-minute silence is a clear anomaly.", "source": "WO-HERMES-STREAM-WATCHDOG-0001"}'),

('hermes_candle_staleness_threshold_sec', '180', 'int',
 'Maximum seconds past expected M1 close before declaring candle progression stale',
 '{"rationale": "M1 candles close every 60s. Allow 180s grace -- 2 missed M1 closes. If aggregator has not produced an M1 candle for 3 minutes during active market hours the tick-to-candle pipeline is broken.", "source": "WO-HERMES-STREAM-WATCHDOG-0001"}'),

('hermes_recovery_proof_window_sec', '30', 'int',
 'Seconds after reconnect to wait for proof of fresh data flow before declaring false connect',
 '{"rationale": "After OANDA reconnect stream should deliver first tick within 1-5s during market hours. 30s is generous. If no tick arrives in 30s post-reconnect the stream is dead despite connected socket.", "source": "WO-HERMES-STREAM-WATCHDOG-0001"}'),

('hermes_max_recovery_attempts', '5', 'int',
 'Maximum consecutive recovery attempts before fatal exit',
 '{"rationale": "5 attempts with exponential backoff covers approx 2.5 minutes of retry. If OANDA is still unreachable after that a process restart via systemd is the correct escalation.", "source": "WO-HERMES-STREAM-WATCHDOG-0001"}'),

('hermes_watchdog_interval_sec', '15', 'int',
 'How often the health watchdog checks tick and candle freshness',
 '{"rationale": "15s gives fast fault detection without excessive DB polling. Combined with 120s tick threshold worst-case detection latency is 135s from stream death to RED state.", "source": "WO-HERMES-STREAM-WATCHDOG-0001"}');
