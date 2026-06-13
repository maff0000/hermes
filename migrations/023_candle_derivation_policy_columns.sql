-- ============================================
-- Migration 023: candles_M30 + candles_H4 — derivation-policy metadata columns
-- WO: WO-HELM-HERMES-CANDLE-DERIVATION-POLICY-ANNOTATION-0001
-- Database: tradingSignals
-- Date (UTC): 2026-06-13
--
-- WHY (R2D2 B-DIV): Phase-2 historical M30/H4 backfill used HISTORICAL_ALL_M1_COUNTED semantics
--   (counted ALL M1 rows regardless of each M1's complete flag). The new forward runner uses
--   FORWARD_COMPLETE_M1_ONLY (complete=1 source only). Without policy metadata, complete=1 means
--   different things across historical vs forward rows. This migration makes the policy
--   MACHINE-VISIBLE per row so no M30/H4 row is semantically ambiguous.
--
--   APPEND-ONLY / NON-DESTRUCTIVE: adds NULLABLE columns only (ALGORITHM=INSTANT — candles_M30
--   ~36k rows / candles_H4 ~4.7k rows, metadata-only). Existing OHLC/complete/timestamp are NOT
--   touched. Existing rows get NULL policy until the GOVERNED annotation step (separate, gated:
--   scripts/annotate_candle_derivation_policy.py) sets HISTORICAL_ALL_M1_COUNTED. Forward rows are
--   written with FORWARD_COMPLETE_M1_ONLY by the runner. CREATE-only/inert: NOT applied live in
--   this PR. UTC only. Idempotent via IF NOT EXISTS on each column.
-- ============================================

ALTER TABLE candles_M30
  ADD COLUMN IF NOT EXISTS derivation_policy            VARCHAR(48)  NULL COMMENT 'HISTORICAL_ALL_M1_COUNTED | FORWARD_COMPLETE_M1_ONLY',
  ADD COLUMN IF NOT EXISTS source_complete_policy       VARCHAR(32)  NULL COMMENT 'ALL_M1_COUNTED | COMPLETE_ONLY',
  ADD COLUMN IF NOT EXISTS source_policy_epoch          VARCHAR(48)  NULL COMMENT 'PHASE2_BACKFILL_PRE_STRICT_COMPLETE_POLICY | FORWARD_STRICT_COMPLETE_POLICY_V1',
  ADD COLUMN IF NOT EXISTS source_expected_candle_count INT UNSIGNED NULL,
  ADD COLUMN IF NOT EXISTS source_actual_candle_count   INT UNSIGNED NULL,
  ADD COLUMN IF NOT EXISTS source_complete_candle_count INT UNSIGNED NULL,
  ADD COLUMN IF NOT EXISTS source_incomplete_candle_count INT UNSIGNED NULL,
  ADD COLUMN IF NOT EXISTS source_missing_candle_count  INT UNSIGNED NULL,
  ADD COLUMN IF NOT EXISTS derivation_run_id            VARCHAR(64)  NULL,
  ADD COLUMN IF NOT EXISTS derivation_generated_at_utc  DATETIME(3)  NULL,
  ADD COLUMN IF NOT EXISTS derivation_policy_note       TEXT         NULL,
  ALGORITHM=INSTANT;

ALTER TABLE candles_H4
  ADD COLUMN IF NOT EXISTS derivation_policy            VARCHAR(48)  NULL COMMENT 'HISTORICAL_ALL_M1_COUNTED | FORWARD_COMPLETE_M1_ONLY',
  ADD COLUMN IF NOT EXISTS source_complete_policy       VARCHAR(32)  NULL COMMENT 'ALL_M1_COUNTED | COMPLETE_ONLY',
  ADD COLUMN IF NOT EXISTS source_policy_epoch          VARCHAR(48)  NULL COMMENT 'PHASE2_BACKFILL_PRE_STRICT_COMPLETE_POLICY | FORWARD_STRICT_COMPLETE_POLICY_V1',
  ADD COLUMN IF NOT EXISTS source_expected_candle_count INT UNSIGNED NULL,
  ADD COLUMN IF NOT EXISTS source_actual_candle_count   INT UNSIGNED NULL,
  ADD COLUMN IF NOT EXISTS source_complete_candle_count INT UNSIGNED NULL,
  ADD COLUMN IF NOT EXISTS source_incomplete_candle_count INT UNSIGNED NULL,
  ADD COLUMN IF NOT EXISTS source_missing_candle_count  INT UNSIGNED NULL,
  ADD COLUMN IF NOT EXISTS derivation_run_id            VARCHAR(64)  NULL,
  ADD COLUMN IF NOT EXISTS derivation_generated_at_utc  DATETIME(3)  NULL,
  ADD COLUMN IF NOT EXISTS derivation_policy_note       TEXT         NULL,
  ALGORITHM=INSTANT;
