-- ============================================
-- Migration 015: tradingSignals.ticks — add seq + contract_version
-- WO: WO-HELM-HERMES-TICK-CONTRACT-PUBLISH-0001
-- Database: tradingSignals
-- Date (UTC): 2026-06-10
-- Purpose (D-SCHEMA): evolve the HERMES-owned raw-tick truth table to carry a
--   HERMES-owned per-instrument monotonic sequence (seq) and an explicit
--   contract_version. APPEND-ONLY/ADDITIVE columns. Existing ~22M rows keep
--   NULL seq/contract_version until the governed backfill populates them
--   (scripts/backfill_tick_seq.py). NOT applied by this WO.
--
-- Ordering note: a UNIQUE(instrument, seq) constraint is DEFERRED to a
--   post-backfill migration so the backfill can populate deterministically
--   first. Here we add only the columns + a non-unique helper index.
-- ============================================

ALTER TABLE ticks
  ADD COLUMN seq BIGINT UNSIGNED NULL COMMENT 'HERMES-owned per-instrument monotonic sequence (backfilled; NULL until populated)' AFTER received_at_utc,
  ADD COLUMN contract_version VARCHAR(32) NULL COMMENT 'HERMES tick contract version, e.g. hermes.tick.v1 (NULL until backfilled/written)' AFTER seq,
  ADD INDEX idx_ticks_instrument_seq (instrument, seq);
