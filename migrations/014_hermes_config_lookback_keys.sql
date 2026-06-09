-- ============================================
-- Migration 014: HERMES-owned lookback config keys
-- WO: WO-HELM-HERMES-CONFIG-OWN-DB-0001
-- Database: tradingSignals
-- Date (UTC): 2026-06-09
-- Purpose: Sever DEP-4. hermes_lookback_hours and hermes_min_candles_floor
--          were read from the RETIRED zeusv4_config table (tradingProteus DB).
--          Move them into the HERMES-owned hermes_config table (GOV-CFG-001).
--          Values migrated VERBATIM from zeusv4_config (captured read-only 2026-06-09):
--            hermes_lookback_hours      = 24
--            hermes_min_candles_floor   = 210
--          Append-only INSERT (no UPSERT). Re-apply must fail loud on UNIQUE config_key.
-- ============================================

INSERT INTO hermes_config (config_key, config_value, value_type, description, llm_reasoning) VALUES
('hermes_lookback_hours', '24', 'int',
 'Hours of candle history to fetch for indicator computation / gap recovery (ADR-0033 time-based lookback).',
 '{"rationale": "24h time-based lookback (ADR-0033) gives a consistent horizon across timeframes; at M5 that is ~288 bars, comfortably above the EMA_200 warm-up of 200 bars. Migrated verbatim from the retired zeusv4_config.hermes_lookback_hours.", "migrated_from": "zeusv4_config.hermes_lookback_hours (tradingProteus, retired)", "captured_value": "24", "captured_utc": "2026-06-09", "source": "WO-HELM-HERMES-CONFIG-OWN-DB-0001"}'),

('hermes_min_candles_floor', '210', 'int',
 'Minimum candles to fetch regardless of lookback, ensuring indicator warm-up (EMA_200 needs 200+ bars).',
 '{"rationale": "Minimum candle floor regardless of lookback; 210 sits just above the EMA_200 warm-up floor of 200 bars so all indicators have stable warm-up even on short/sparse windows. Migrated verbatim from the retired zeusv4_config.hermes_min_candles_floor.", "migrated_from": "zeusv4_config.hermes_min_candles_floor (tradingProteus, retired)", "captured_value": "210", "captured_utc": "2026-06-09", "source": "WO-HELM-HERMES-CONFIG-OWN-DB-0001"}');
