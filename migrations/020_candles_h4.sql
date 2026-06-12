-- ============================================
-- Migration 020: candles_H4 — durable H4 OHLCV candle table
-- WO: WO-HELM-HERMES-CANDLE-H4-M30-PIPELINE-BUILD-0001
-- Database: tradingSignals
-- Date (UTC): 2026-06-12
--
-- WHY: H4 is IN v1 HERMES scope (Architect ruling) and is the higher-timeframe context
--   traders/Falcon require for signal review. candles_H4 did not exist. Schema mirrors
--   candles_H1/D1 exactly. H4 is derived from canonical M1 (DERIVE_FROM_CANONICAL_M1) on
--   UTC-ALIGNED 4-hour boundaries (00/04/08/12/16/20 UTC) per Architect ruling, via
--   utils/m1_deriver (TIMEFRAME_SECONDS['H4']=14400, epoch-truncated buckets).
--   `complete`=1 only when all 240 constituent M1 candles present.
--
--   BOUNDARY NOTE (open Architect question, recorded — not a blocker): D1 derivation uses the
--   22:00 UTC forex-day boundary, whereas H4 here is UTC-00:00-anchored per the ruling. As a
--   result UTC H4 buckets (e.g. 20:00-24:00) straddle the 22:00 D1 boundary and do NOT tile
--   D1 cleanly. M5/M15/H1 are likewise UTC-anchored, so H4 is consistent with them. If clean
--   H4->D1 nesting (forex-22:00-anchored H4) is later required, that is a separate ruling.
--
--   CREATE-only / inert: no rows written here (see scripts/backfill_candles_h4_m30.py).
--   Append-only; idempotent via IF NOT EXISTS.
-- ============================================

CREATE TABLE IF NOT EXISTS candles_H4 (
  id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  instrument  VARCHAR(20)  NOT NULL,
  timestamp   DATETIME     NOT NULL COMMENT 'Bucket start (UTC), 4h-aligned (00/04/08/12/16/20)',
  open        DECIMAL(12,5) NOT NULL,
  high        DECIMAL(12,5) NOT NULL,
  low         DECIMAL(12,5) NOT NULL,
  close       DECIMAL(12,5) NOT NULL,
  volume      INT UNSIGNED DEFAULT 0 COMMENT 'Sum of constituent M1 volumes',
  complete    TINYINT(1)   DEFAULT 1 COMMENT '1 only when all 240 expected M1 candles present',
  source      VARCHAR(64)  DEFAULT 'signal_service',
  created_at  TIMESTAMP    NULL DEFAULT current_timestamp(),
  PRIMARY KEY (id),
  UNIQUE KEY idx_instrument_timestamp (instrument, timestamp),
  KEY idx_timestamp (timestamp)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
