-- ============================================
-- Migration 008: Volume Semantics Governance
-- WO: WO-HERMES-CLEANUP-F
-- Epic: EPIC-HERMES-SIGNAL-SOURCE-RESILIENCE-001
-- Database: tradingSignals
-- Date: 2026-03-31
--
-- Purpose: Explicitly govern volume semantics across
--          live, recovery, and derived candle paths.
--          Volume is NOT normalized — provenance distinguishes.
-- ============================================

-- Config key documenting the volume semantic contract
INSERT INTO hermes_config (config_key, config_value, value_type, description, llm_reasoning)
VALUES
('hermes_volume_semantic_contract', 'provenance_tagged', 'string',
 'Volume semantics: live M1 volume = tick count. Recovery M1 volume = broker-reported volume. Derived higher-TF volume = SUM of constituent M1 volumes. Volume is NOT normalized across ingest modes. The ingest_mode field in canonical_m1 distinguishes the semantic. No trading logic should depend on absolute volume values without checking ingest_mode.',
 '{"rationale": "Normalizing volume would require choosing a canonical definition (tick count vs broker volume) and converting historical data. This is not justified because: (1) no current trading logic uses absolute volume, (2) volume_ratio in signals uses relative 20-bar average which is internally consistent within an ingest_mode, (3) provenance tagging via ingest_mode already makes the semantic queryable. Decision: document, tag, do not normalize.", "source": "WO-HERMES-CLEANUP-F", "alternatives_considered": ["normalize_to_tick_count", "normalize_to_broker_volume", "drop_volume_entirely"], "chosen": "provenance_tagged"}')
ON DUPLICATE KEY UPDATE
  config_value = VALUES(config_value),
  description = VALUES(description),
  llm_reasoning = VALUES(llm_reasoning);
