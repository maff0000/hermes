-- ============================================
-- Migration 015: tradingSignals.ticks — add seq + contract_version  (CORRECTED)
-- WO: WO-HELM-HERMES-TICK-CONTRACT-PUBLISH-0001 (corrected by
--     WO-HELM-HERMES-MIGRATION-AND-BACKFILL-HARDENING-0001)
-- Database: tradingSignals
-- Date (UTC): 2026-06-10
--
-- PROVENANCE / WHY CORRECTED:
--   Migration 015 was merged (PR #14, main d9e9880) but PROVABLY NEVER APPLIED:
--   re-verified read-only 2026-06-10 — tradingSignals.ticks has no seq column,
--   no contract_version column, no idx_ticks_instrument_seq, no uq_ticks_instrument_seq,
--   and there is no migration ledger. Per Architect ruling, an unapplied migration
--   may be corrected by PR. The original 015 bundled INSTANT-capable column adds with
--   an index add in ONE ALTER, which forces the whole statement to run INPLACE and
--   loses the instant fast-path. This correction SPLITS the work:
--     Step 1 — INSTANT nullable column adds (metadata-only on ~22M rows);
--     Step 2 — online INPLACE index build (LOCK=NONE, concurrent DML allowed).
--   Columns are added at the END of the row (no positional AFTER) to guarantee INSTANT.
--   UNIQUE(instrument,seq) is NOT here — it is deferred to migration 017 (post-backfill).
--   Existing ~22M rows keep NULL seq/contract_version until the governed backfill.
--   APPLY ORDER: run Step 1, then Step 2. Apply in a low-activity window; monitor
--   innodb_online_alter_log_max_size during the index build.
-- ============================================

-- Step 1: INSTANT nullable column adds (no table rebuild)
ALTER TABLE ticks
  ADD COLUMN seq BIGINT UNSIGNED NULL
      COMMENT 'HERMES-owned per-instrument monotonic sequence (backfilled; NULL until populated)',
  ADD COLUMN contract_version VARCHAR(32) NULL
      COMMENT 'HERMES tick contract version, e.g. hermes.tick.v1 (NULL until backfilled/written)',
  ALGORITHM=INSTANT;

-- Step 2: online (INPLACE) helper index build — NON-unique. LOCK=NONE allows concurrent writes.
ALTER TABLE ticks
  ADD INDEX idx_ticks_instrument_seq (instrument, seq),
  ALGORITHM=INPLACE, LOCK=NONE;
