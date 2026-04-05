-- ============================================
-- Migration 009: Market Hours Policy
-- WO: WO-HERMES-CANARY-MARKET-HOURS-0002
-- Database: tradingSignals
-- Date: 2026-04-01
--
-- Purpose: Per-instrument governed market-hours policy for
--          canary, watchdog, and proof program. UTC only.
--          Determines whether fresh data is expected right now.
-- ============================================

CREATE TABLE IF NOT EXISTS hermes_market_hours (
    instrument          VARCHAR(20)     NOT NULL    COMMENT 'Instrument code or DEFAULT for fallback policy',
    market_type         VARCHAR(32)     NOT NULL    COMMENT 'Market category (FOREX, METALS, COMMODITIES)',
    open_day_utc        TINYINT UNSIGNED NOT NULL   COMMENT 'Day market opens. 0=Monday, 6=Sunday.',
    open_time_utc       TIME            NOT NULL    COMMENT 'UTC time market opens on open_day',
    close_day_utc       TINYINT UNSIGNED NOT NULL   COMMENT 'Day market closes. 0=Monday, 4=Friday.',
    close_time_utc      TIME            NOT NULL    COMMENT 'UTC time market closes on close_day',
    maintenance_start_utc TIME          NULL        COMMENT 'Daily maintenance window start (UTC), NULL if none',
    maintenance_end_utc TIME            NULL        COMMENT 'Daily maintenance window end (UTC), NULL if none',
    is_enabled          TINYINT(1)      NOT NULL DEFAULT 1,
    description         TEXT            NOT NULL,
    llm_reasoning       TEXT            NOT NULL,
    created_at          TIMESTAMP       DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP       DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (instrument)
) ENGINE=InnoDB
  COMMENT='Per-instrument market hours policy. UTC only. Governs when fresh data is expected. Used by canary, watchdog, proof.';

-- ============================================================
-- Seed: Forex and metals market hours
-- All current instruments are forex or metals on OANDA.
-- Forex/metals: Sunday 22:00 UTC → Friday 22:00 UTC (continuous).
-- No per-instrument differences for current set.
-- If instruments are added with different hours, add explicit rows.
-- ============================================================

INSERT INTO hermes_market_hours
(instrument, market_type, open_day_utc, open_time_utc, close_day_utc, close_time_utc,
 maintenance_start_utc, maintenance_end_utc, is_enabled, description, llm_reasoning)
VALUES
('XAU_USD', 'METALS', 6, '22:00:00', 4, '22:00:00', NULL, NULL, 1,
 'XAU/USD (Gold). Forex metals hours: Sunday 22:00 UTC to Friday 22:00 UTC continuous.',
 '{"rationale": "OANDA trades XAU/USD continuously from Sunday 22:00 UTC open to Friday 22:00 UTC close. No daily maintenance window on OANDA for this instrument.", "source": "WO-HERMES-CANARY-MARKET-HOURS-0002"}'),
('XAG_USD', 'METALS', 6, '22:00:00', 4, '22:00:00', NULL, NULL, 1,
 'XAG/USD (Silver). Same hours as XAU/USD.',
 '{"rationale": "Same forex metals schedule.", "source": "WO-HERMES-CANARY-MARKET-HOURS-0002"}'),
('XPT_USD', 'METALS', 6, '22:00:00', 4, '22:00:00', NULL, NULL, 1,
 'XPT/USD (Platinum). Same hours as XAU/USD.',
 '{"rationale": "Same forex metals schedule.", "source": "WO-HERMES-CANARY-MARKET-HOURS-0002"}'),
('XCU_USD', 'METALS', 6, '22:00:00', 4, '22:00:00', NULL, NULL, 1,
 'XCU/USD (Copper). Same hours as XAU/USD.',
 '{"rationale": "Same forex metals schedule.", "source": "WO-HERMES-CANARY-MARKET-HOURS-0002"}'),
('EUR_USD', 'FOREX', 6, '22:00:00', 4, '22:00:00', NULL, NULL, 1,
 'EUR/USD. Standard forex hours.',
 '{"rationale": "Standard forex schedule.", "source": "WO-HERMES-CANARY-MARKET-HOURS-0002"}'),
('GBP_USD', 'FOREX', 6, '22:00:00', 4, '22:00:00', NULL, NULL, 1,
 'GBP/USD. Standard forex hours.',
 '{"rationale": "Standard forex schedule.", "source": "WO-HERMES-CANARY-MARKET-HOURS-0002"}'),
('USD_JPY', 'FOREX', 6, '22:00:00', 4, '22:00:00', NULL, NULL, 1,
 'USD/JPY. Standard forex hours.',
 '{"rationale": "Standard forex schedule.", "source": "WO-HERMES-CANARY-MARKET-HOURS-0002"}'),
('AUD_USD', 'FOREX', 6, '22:00:00', 4, '22:00:00', NULL, NULL, 1,
 'AUD/USD. Standard forex hours.',
 '{"rationale": "Standard forex schedule.", "source": "WO-HERMES-CANARY-MARKET-HOURS-0002"}'),
('NZD_USD', 'FOREX', 6, '22:00:00', 4, '22:00:00', NULL, NULL, 1,
 'NZD/USD. Standard forex hours.',
 '{"rationale": "Standard forex schedule.", "source": "WO-HERMES-CANARY-MARKET-HOURS-0002"}'),
('USD_CAD', 'FOREX', 6, '22:00:00', 4, '22:00:00', NULL, NULL, 1,
 'USD/CAD. Standard forex hours.',
 '{"rationale": "Standard forex schedule.", "source": "WO-HERMES-CANARY-MARKET-HOURS-0002"}'),
('USD_CHF', 'FOREX', 6, '22:00:00', 4, '22:00:00', NULL, NULL, 1,
 'USD/CHF. Standard forex hours.',
 '{"rationale": "Standard forex schedule.", "source": "WO-HERMES-CANARY-MARKET-HOURS-0002"}'),
('EUR_GBP', 'FOREX', 6, '22:00:00', 4, '22:00:00', NULL, NULL, 1,
 'EUR/GBP. Standard forex hours.',
 '{"rationale": "Standard forex schedule.", "source": "WO-HERMES-CANARY-MARKET-HOURS-0002"}');
