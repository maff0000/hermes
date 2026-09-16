-- ============================================
-- Rollback for Migration 027: DARWIN canonical historical candle authority
-- WO: WO-HELM-HERMES-DEV-DARWIN-DURABLE-CANONICAL-HISTORICAL-AUTHORITY-0001
--
-- Drops the unified + per-timeframe views first (dependents), then the two new physical tables.
-- NEVER touches candles_M1/M5/M15/H1 (the views' underlying tables) or the legacy candles_H4/candles_D1 —
-- this migration never wrote to any of those.
-- ============================================

DROP VIEW IF EXISTS canonical_candles;
DROP VIEW IF EXISTS canonical_candles_h1;
DROP VIEW IF EXISTS canonical_candles_m15;
DROP VIEW IF EXISTS canonical_candles_m5;
DROP VIEW IF EXISTS canonical_candles_m1;
DROP TABLE IF EXISTS canonical_candles_d1;
DROP TABLE IF EXISTS canonical_candles_h4;
