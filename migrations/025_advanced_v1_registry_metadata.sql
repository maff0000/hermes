-- ============================================================================
-- Migration 025: Advanced-v1 reusable instrument-registry metadata
-- WO: WO-HELM-HERMES-ADVANCED-V1-REGISTRY-FOUNDATION-0001
-- Database: tradingSignals
-- Append-only + idempotent (ADD COLUMN IF NOT EXISTS). PREPARED + TESTED IN ISOLATION.
-- Purpose: make tradingSignals.instruments the SOLE reusable authority for the Advanced-v1 engine —
--   instrument discovery, metadata and per-capability policy — so a supported instrument is added by
--   ONE registry record with zero application-code change. Policy/data only; no per-ticker table; no
--   business logic in the DB. Does NOT activate the 7 new instruments (capability flags default 0) and
--   does NOT change XAU_USD runtime behaviour (the pilot is still gated by existing env flags until the
--   next pipeline-generalisation stage rewires consumers onto these flags).
-- Rollback: 025_advanced_v1_registry_metadata_rollback.sql
-- ============================================================================

-- 1) Governed asset-category authority gains 'energy' (for WTICO_USD and future energy instruments).
ALTER TABLE instruments MODIFY category
  ENUM('precious_metals','base_metals','forex_major','forex_minor','indices','crypto','energy') NOT NULL;

-- 2) Reusable metadata columns (data/policy only; selected by a reusable policy class, never a ticker branch).
ALTER TABLE instruments
  ADD COLUMN IF NOT EXISTS price_precision            TINYINT       NULL COMMENT 'decimal places for OHLC/indicator quantisation (validate vs OANDA before activation)',
  ADD COLUMN IF NOT EXISTS tick_size                  DECIMAL(18,8) NULL COMMENT 'minimum price increment (validate vs OANDA before activation)',
  ADD COLUMN IF NOT EXISTS price_authority            ENUM('mid','bid','ask') NOT NULL DEFAULT 'mid' COMMENT 'governed OHLC price source (uniform MID)',
  ADD COLUMN IF NOT EXISTS market_hours_policy        VARCHAR(32)   NULL COMMENT 'reusable MarketHoursPolicy key: fx_24x5|metals|index_cash|energy',
  ADD COLUMN IF NOT EXISTS expected_freshness_sec     INT           NULL COMMENT 'max seconds between ticks during valid hours before STALE',
  ADD COLUMN IF NOT EXISTS enabled_timeframes         JSON          NULL COMMENT 'candle+indicator timeframes, e.g. ["M1","M5","M15","H1","H4","D1"]',
  ADD COLUMN IF NOT EXISTS indicator_profile          VARCHAR(32)   NOT NULL DEFAULT 'standard_v1' COMMENT 'reusable indicator profile key',
  ADD COLUMN IF NOT EXISTS tick_contract_enabled      TINYINT(1)    NOT NULL DEFAULT 0 COMMENT 'advanced tick contract publication capability',
  ADD COLUMN IF NOT EXISTS indicator_contract_enabled TINYINT(1)    NOT NULL DEFAULT 0 COMMENT 'indicator contract publication capability',
  ADD COLUMN IF NOT EXISTS gap_detection_enabled      TINYINT(1)    NOT NULL DEFAULT 0 COMMENT 'gap-detection capability',
  ADD COLUMN IF NOT EXISTS backfill_policy            VARCHAR(16)   NOT NULL DEFAULT 'status_only' COMMENT 'disabled|status_only|bounded|full',
  ADD COLUMN IF NOT EXISTS retention_policy           VARCHAR(32)   NOT NULL DEFAULT 'default_v1',
  ADD COLUMN IF NOT EXISTS metadata_version           VARCHAR(8)    NOT NULL DEFAULT 'v1',
  ADD COLUMN IF NOT EXISTS registry_updated_at        TIMESTAMP     NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT 'UTC last metadata mutation',
  ADD COLUMN IF NOT EXISTS registry_provenance        VARCHAR(128)  NULL COMMENT 'WO/source that last set advanced-v1 metadata';

-- 3) WTICO_USD governed metadata correction: base_metals -> energy. Canonical symbol + candle/tick history preserved.
UPDATE instruments
   SET category='energy',
       registry_provenance='WO-HELM-HERMES-ADVANCED-V1-REGISTRY-FOUNDATION-0001'
 WHERE symbol='WTICO_USD';

-- 4) Seed reusable metadata for the 8-instrument rollout set. Capability flags stay 0 (NOT_ENABLED) for the
--    7 new instruments; XAU_USD (the existing active pilot) is marked enabled at the REGISTRY level so the
--    next stage can rewire runtime onto these flags without changing behaviour. price_precision/tick_size
--    are OANDA-plausible seeds and MUST be validated/discovered from OANDA before any activation.
-- metals
UPDATE instruments SET price_precision=3, tick_size=0.001,  price_authority='mid', market_hours_policy='metals',     expected_freshness_sec=120, enabled_timeframes='["M1","M5","M15","H1","H4","D1"]', indicator_profile='standard_v1', backfill_policy='status_only', metadata_version='v1', registry_provenance='WO-...-REGISTRY-FOUNDATION-0001', tick_contract_enabled=1, indicator_contract_enabled=1, gap_detection_enabled=1 WHERE symbol='XAU_USD';
UPDATE instruments SET price_precision=4, tick_size=0.0001, price_authority='mid', market_hours_policy='metals',     expected_freshness_sec=120, enabled_timeframes='["M1","M5","M15","H1","H4","D1"]', indicator_profile='standard_v1', backfill_policy='status_only', metadata_version='v1', registry_provenance='WO-...-REGISTRY-FOUNDATION-0001' WHERE symbol='XAG_USD';
-- standard FX (5dp)
UPDATE instruments SET price_precision=5, tick_size=0.00001, price_authority='mid', market_hours_policy='fx_24x5',   expected_freshness_sec=120, enabled_timeframes='["M1","M5","M15","H1","H4","D1"]', indicator_profile='standard_v1', backfill_policy='status_only', metadata_version='v1', registry_provenance='WO-...-REGISTRY-FOUNDATION-0001' WHERE symbol IN ('EUR_USD','GBP_USD','AUD_USD');
-- JPY precision (3dp)
UPDATE instruments SET price_precision=3, tick_size=0.001,  price_authority='mid', market_hours_policy='fx_24x5',    expected_freshness_sec=120, enabled_timeframes='["M1","M5","M15","H1","H4","D1"]', indicator_profile='standard_v1', backfill_policy='status_only', metadata_version='v1', registry_provenance='WO-...-REGISTRY-FOUNDATION-0001' WHERE symbol='USD_JPY';
-- equity index cash
UPDATE instruments SET price_precision=1, tick_size=0.1,    price_authority='mid', market_hours_policy='index_cash',  expected_freshness_sec=300, enabled_timeframes='["M1","M5","M15","H1","H4","D1"]', indicator_profile='standard_v1', backfill_policy='status_only', metadata_version='v1', registry_provenance='WO-...-REGISTRY-FOUNDATION-0001' WHERE symbol='SPX500_USD';
-- energy
UPDATE instruments SET price_precision=3, tick_size=0.001,  price_authority='mid', market_hours_policy='energy',      expected_freshness_sec=300, enabled_timeframes='["M1","M5","M15","H1","H4","D1"]', indicator_profile='standard_v1', backfill_policy='status_only', metadata_version='v1', registry_provenance='WO-...-REGISTRY-FOUNDATION-0001' WHERE symbol='WTICO_USD';
