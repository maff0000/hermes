-- ============================================
-- Migration 017: tradingSignals.ticks — add UNIQUE(instrument, seq)
-- WO: WO-HELM-HERMES-MIGRATION-AND-BACKFILL-HARDENING-0001 (scaffold; apply deferred)
-- Database: tradingSignals
-- Date (UTC): 2026-06-10
--
-- *** DO NOT APPLY YET ***
-- Apply ONLY after ALL of the following are satisfied and R2D2 validates the evidence:
--   1. migration 015 applied (seq + contract_version columns + idx_ticks_instrument_seq present);
--   2. migration 016 applied (hermes_config tunables present);
--   3. governed seq backfill COMPLETED (scripts/backfill_tick_seq.py);
--   4. no NULL seq among historical rows intended for uniqueness;
--   5. per-instrument seq is dense + monotonic in (timestamp,id) order;
--   6. duplicate (instrument,seq) check passes (zero duplicates);
--   7. R2D2 validates the backfill evidence.
--
-- NULLs note: a UNIQUE index permits multiple NULLs in MariaDB, so rows still written
-- by structure_engine (seq NULL) until the native-writer cutover do NOT block this
-- constraint. The HERMES writer's fail-loud seq-collision protection depends on this
-- constraint being live BEFORE native-writer activation.
--
-- Online DDL: INPLACE, LOCK=NONE (concurrent DML allowed); run in a low-activity window.
-- ============================================

ALTER TABLE ticks
  ADD UNIQUE INDEX uq_ticks_instrument_seq (instrument, seq),
  ALGORITHM=INPLACE, LOCK=NONE;
