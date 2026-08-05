-- ============================================================================
-- Rollback for Migration 025 — Advanced-v1 reusable instrument-registry metadata
-- WO-HELM-HERMES-ADVANCED-V1-REGISTRY-FOUNDATION-0001. Database: tradingSignals.
-- Reverts the WTICO category, the added columns and the ENUM 'energy' member. Preserves candle/tick history.
-- Order matters: revert WTICO OFF 'energy' BEFORE removing 'energy' from the ENUM.
-- ============================================================================

-- 1) revert WTICO_USD energy -> base_metals (canonical symbol + history preserved)
UPDATE instruments SET category='base_metals' WHERE symbol='WTICO_USD';

-- 2) drop the reusable metadata columns (idempotent)
ALTER TABLE instruments
  DROP COLUMN IF EXISTS price_precision,
  DROP COLUMN IF EXISTS tick_size,
  DROP COLUMN IF EXISTS price_authority,
  DROP COLUMN IF EXISTS market_hours_policy,
  DROP COLUMN IF EXISTS expected_freshness_sec,
  DROP COLUMN IF EXISTS enabled_timeframes,
  DROP COLUMN IF EXISTS indicator_profile,
  DROP COLUMN IF EXISTS tick_contract_enabled,
  DROP COLUMN IF EXISTS indicator_contract_enabled,
  DROP COLUMN IF EXISTS gap_detection_enabled,
  DROP COLUMN IF EXISTS backfill_policy,
  DROP COLUMN IF EXISTS retention_policy,
  DROP COLUMN IF EXISTS metadata_version,
  DROP COLUMN IF EXISTS registry_updated_at,
  DROP COLUMN IF EXISTS registry_provenance;

-- 3) remove 'energy' from the governed category ENUM (safe now that no row uses it)
ALTER TABLE instruments MODIFY category
  ENUM('precious_metals','base_metals','forex_major','forex_minor','indices','crypto') NOT NULL;
