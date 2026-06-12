-- ============================================
-- Migration 021: register CANDLE_M30 + CANDLE_H4 in hermes_recovery_library
-- WO: WO-HELM-HERMES-CANDLE-H4-M30-PIPELINE-BUILD-0001
-- Database: tradingSignals
-- Date (UTC): 2026-06-12
-- Append-only INSERT (no UPSERT). Re-apply fails loud on PRIMARY KEY artifact_code.
--
-- WHY: M5/M15/H1/D1 already recover via DERIVE_FROM_CANONICAL_M1. M30 and H4 are new v1
--   artifacts (Architect ruling) and must be recoverable by the same governed pattern.
--   rebuild_order slots them into the existing chain by timeframe granularity:
--     M1=10, M5=20, M15=30, [M30=33], H1=40, [H4=43], D1=45, signals=50/60.
--   validation_strategy=CONTINUITY (same as the other derived candles).
-- ============================================

INSERT INTO hermes_recovery_library
  (artifact_code, artifact_type, producer_name, instrument_scope, timeframe, target_table,
   rebuild_strategy, required_lookback_bars, validation_strategy, is_enabled, rebuild_order,
   version, description, llm_reasoning)
VALUES
('CANDLE_M30', 'CANDLE', 'signal_service.m1_deriver', 'PER_INSTRUMENT', 'M30', 'candles_M30',
 'DERIVE_FROM_CANONICAL_M1', 0, 'CONTINUITY', 1, 33, '1.0',
 'M30 OHLCV candles. Recovery: derived from canonical M1 (30 consecutive M1 candles per M30 bucket, UTC-aligned :00/:30).',
 '{"rationale": "M30 added to v1 HERMES scope (Architect ruling). Same DERIVE_FROM_CANONICAL_M1 pattern as M5/M15/H1; UTC-aligned 30m buckets via TIMEFRAME_SECONDS[M30]=1800. rebuild_order 33 = after M15(30), before H1(40).", "source": "WO-HELM-HERMES-CANDLE-H4-M30-PIPELINE-BUILD-0001"}'),

('CANDLE_H4', 'CANDLE', 'signal_service.m1_deriver', 'PER_INSTRUMENT', 'H4', 'candles_H4',
 'DERIVE_FROM_CANONICAL_M1', 0, 'CONTINUITY', 1, 43, '1.0',
 'H4 OHLCV candles. Recovery: derived from canonical M1 (240 consecutive M1 candles per H4 bucket, UTC-aligned 00/04/08/12/16/20).',
 '{"rationale": "H4 added to v1 HERMES scope (Architect ruling) — higher-timeframe context for trader/Falcon signal review. UTC-aligned 4h buckets via TIMEFRAME_SECONDS[H4]=14400. rebuild_order 43 = after H1(40), before D1(45). NOTE: D1 uses 22:00 forex boundary; UTC-anchored H4 does not tile D1 cleanly (recorded open question).", "source": "WO-HELM-HERMES-CANDLE-H4-M30-PIPELINE-BUILD-0001"}');
