-- ============================================
-- Migration 019: candles_M30 — durable M30 OHLCV candle table
-- WO: WO-HELM-HERMES-CANDLE-H4-M30-PIPELINE-BUILD-0001
-- Database: tradingSignals
-- Date (UTC): 2026-06-12
--
-- WHY: M30 is IN v1 HERMES scope (Architect ruling) but candles_M30 did not exist.
--   Schema mirrors candles_H1/M5/M15/D1 exactly (same OHLCV contract, UNIQUE(instrument,timestamp)
--   for idempotent derivation upsert). M30 is derived from canonical M1 (DERIVE_FROM_CANONICAL_M1)
--   on UTC-aligned 30-minute boundaries (:00 / :30), via utils/m1_deriver (TIMEFRAME_SECONDS['M30']=1800).
--   `timestamp` = bucket start (UTC). `complete`=1 only when all 30 constituent M1 candles present.
--   CREATE-only / inert: this migration creates the table; no rows are written here (see
--   scripts/backfill_candles_h4_m30.py). Append-only; idempotent via IF NOT EXISTS.
-- ============================================

CREATE TABLE IF NOT EXISTS candles_M30 (
  id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  instrument  VARCHAR(20)  NOT NULL,
  timestamp   DATETIME     NOT NULL COMMENT 'Bucket start (UTC), 30m-aligned (:00/:30)',
  open        DECIMAL(12,5) NOT NULL,
  high        DECIMAL(12,5) NOT NULL,
  low         DECIMAL(12,5) NOT NULL,
  close       DECIMAL(12,5) NOT NULL,
  volume      INT UNSIGNED DEFAULT 0 COMMENT 'Sum of constituent M1 volumes',
  complete    TINYINT(1)   DEFAULT 1 COMMENT '1 only when all 30 expected M1 candles present',
  source      VARCHAR(64)  DEFAULT 'signal_service',
  created_at  TIMESTAMP    NULL DEFAULT current_timestamp(),
  PRIMARY KEY (id),
  UNIQUE KEY idx_instrument_timestamp (instrument, timestamp),
  KEY idx_timestamp (timestamp)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
