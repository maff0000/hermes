-- ============================================
-- Migration 007: Recovery Library — M1 Canonical Migration
-- WO: WO-HERMES-RECOVERY-MIGRATION-E
-- Epic: EPIC-HERMES-SIGNAL-SOURCE-RESILIENCE-001
-- Database: tradingSignals
-- Date: 2026-03-31
--
-- Purpose: Migrate recovery strategies so only M1 is fetched
--          from broker. Higher TFs are derived from canonical M1.
--          Add D1 recovery (was missing).
--          Add DERIVE_FROM_CANONICAL_M1 to rebuild_strategy enum.
-- ============================================

-- Add new rebuild strategy to enum
ALTER TABLE hermes_recovery_library
  MODIFY COLUMN rebuild_strategy ENUM(
    'BROKER_FETCH',
    'DERIVE_FROM_LOWER_TIMEFRAME',
    'DERIVE_FROM_CANONICAL_M1',
    'WINDOW_PLUS_LOOKBACK',
    'FULL_WINDOW',
    'PARTITION_REBUILD'
  ) NOT NULL COMMENT 'How this artifact is rebuilt during recovery';

-- Update M5: BROKER_FETCH → DERIVE_FROM_CANONICAL_M1
UPDATE hermes_recovery_library
SET rebuild_strategy = 'DERIVE_FROM_CANONICAL_M1',
    description = 'M5 OHLCV candles. Live: aggregated from ticks. Recovery: derived from canonical M1 (5 consecutive M1 candles per M5 bucket).',
    llm_reasoning = '{"rationale": "M5 candles are now derived from canonical M1 during recovery instead of fetched independently from OANDA. This reduces broker coupling from 4 granularity fetches to 1 (M1 only). Derivation proven equivalent: 12/12 M5 buckets matched legacy at 100%.", "source": "WO-HERMES-RECOVERY-MIGRATION-E", "migration_from": "BROKER_FETCH"}'
WHERE artifact_code = 'CANDLE_M5';

-- Update M15: BROKER_FETCH → DERIVE_FROM_CANONICAL_M1
UPDATE hermes_recovery_library
SET rebuild_strategy = 'DERIVE_FROM_CANONICAL_M1',
    description = 'M15 OHLCV candles. Live: aggregated from ticks. Recovery: derived from canonical M1 (15 consecutive M1 candles per M15 bucket).',
    llm_reasoning = '{"rationale": "Same migration as M5. M15 derived from canonical M1 during recovery. Broader equivalence proof pending in WO-H.", "source": "WO-HERMES-RECOVERY-MIGRATION-E", "migration_from": "BROKER_FETCH"}'
WHERE artifact_code = 'CANDLE_M15';

-- Update H1: BROKER_FETCH → DERIVE_FROM_CANONICAL_M1
UPDATE hermes_recovery_library
SET rebuild_strategy = 'DERIVE_FROM_CANONICAL_M1',
    description = 'H1 OHLCV candles. Live: aggregated from ticks. Recovery: derived from canonical M1 (60 consecutive M1 candles per H1 bucket).',
    llm_reasoning = '{"rationale": "Same migration as M5/M15. H1 derived from canonical M1. Broader equivalence proof pending in WO-H.", "source": "WO-HERMES-RECOVERY-MIGRATION-E", "migration_from": "BROKER_FETCH"}'
WHERE artifact_code = 'CANDLE_H1';

-- Add D1 (was missing from recovery library)
INSERT INTO hermes_recovery_library
(artifact_code, artifact_type, producer_name, instrument_scope, timeframe, target_table,
 rebuild_strategy, required_lookback_bars, validation_strategy, is_enabled, rebuild_order,
 version, description, llm_reasoning)
VALUES
('CANDLE_D1', 'CANDLE', 'signal_service.m1_deriver', 'PER_INSTRUMENT', 'D1', 'candles_D1',
 'DERIVE_FROM_CANONICAL_M1', 0, 'CONTINUITY', 1, 45,
 '1.0',
 'D1 OHLCV candles. Live: aggregated from ticks. Recovery: derived from canonical M1 (up to 1440 M1 candles per forex day, 22:00-22:00 UTC boundary).',
 '{"rationale": "D1 was missing from recovery library entirely. Now recoverable via M1 derivation. Forex day boundary = 22:00 UTC (21:00 during US DST). D1 rebuild order 45 = after H1 (40) but before signals (50).", "source": "WO-HERMES-RECOVERY-MIGRATION-E"}')
ON DUPLICATE KEY UPDATE
  rebuild_strategy = VALUES(rebuild_strategy),
  description = VALUES(description),
  llm_reasoning = VALUES(llm_reasoning);

-- Add D1 dependency on CANDLE_M1
INSERT INTO hermes_recovery_dependencies
(artifact_code, depends_on_artifact_code, dependency_kind, description, llm_reasoning)
VALUES
('CANDLE_D1', 'CANDLE_M1', 'REQUIRED',
 'D1 candles require M1 candle data for derivation',
 '{"rationale": "D1 is derived from up to 1440 M1 candles spanning a forex day. Without M1 data, D1 cannot be derived.", "source": "WO-HERMES-RECOVERY-MIGRATION-E"}')
ON DUPLICATE KEY UPDATE
  description = VALUES(description),
  llm_reasoning = VALUES(llm_reasoning);

-- Update existing higher-TF dependencies to also depend on CANDLE_M1
INSERT INTO hermes_recovery_dependencies
(artifact_code, depends_on_artifact_code, dependency_kind, description, llm_reasoning)
VALUES
('CANDLE_M5', 'CANDLE_M1', 'REQUIRED',
 'M5 candles are now derived from canonical M1 during recovery',
 '{"rationale": "Recovery migration: M5 BROKER_FETCH replaced by DERIVE_FROM_CANONICAL_M1. M5 depends on M1.", "source": "WO-HERMES-RECOVERY-MIGRATION-E"}'),
('CANDLE_M15', 'CANDLE_M1', 'REQUIRED',
 'M15 candles are now derived from canonical M1 during recovery',
 '{"rationale": "Recovery migration: M15 BROKER_FETCH replaced by DERIVE_FROM_CANONICAL_M1. M15 depends on M1.", "source": "WO-HERMES-RECOVERY-MIGRATION-E"}'),
('CANDLE_H1', 'CANDLE_M1', 'REQUIRED',
 'H1 candles are now derived from canonical M1 during recovery',
 '{"rationale": "Recovery migration: H1 BROKER_FETCH replaced by DERIVE_FROM_CANONICAL_M1. H1 depends on M1.", "source": "WO-HERMES-RECOVERY-MIGRATION-E"}')
ON DUPLICATE KEY UPDATE
  description = VALUES(description),
  llm_reasoning = VALUES(llm_reasoning);
