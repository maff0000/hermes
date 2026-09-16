-- ============================================
-- Migration 027: DARWIN canonical historical candle authority (M1/M5/M15/H1/H4/D1)
-- WO: WO-HELM-HERMES-DEV-DARWIN-DURABLE-CANONICAL-HISTORICAL-AUTHORITY-0001
-- Database: tradingSignals
-- Date (UTC): 2026-09-16
--
-- WHY: DARWIN needs ONE coherent, versioned, SELECT-only SQL surface spanning the full historical past
--   through every newly-completed live candle, for M1/M5/M15/H1/H4/D1, XAU_USD. HERMES owns market truth;
--   this migration is that surface.
--
-- ARCHITECTURE:
--   * M1/M5/M15/H1 are ALREADY durable + trustworthy (candles_M1/M5/M15/H1, fed continuously by the
--     existing live collection path). This migration does NOT copy that data — it exposes it unchanged via
--     four thin VIEWS (canonical_candles_m1/m5/m15/h1) in the SAME uniform row shape as H4/D1 below, so a
--     row written by the existing live path is visible to DARWIN the instant it commits (no separate
--     "backfill ends here / live lives elsewhere" boundary — the view IS the live table).
--   * H4/D1 have NO existing trustworthy durable table (legacy `candles_H4`/`candles_D1` are explicitly
--     NON-AUTHORITATIVE — see below) — so this migration creates two NEW physical tables,
--     canonical_candles_h4 / canonical_candles_d1, populated ONLY by the governed derivation
--     (candle_h4_derivation_v1.derive_h4 / candle_d1_derivation_v1.derive_d1 — the SAME functions the live
--     producer uses), via both a one-time historical backfill and the live seal-path hook
--     (candle_durable_sql_writer_v1). Only complete, status-OK derived candles are ever stored here.
--   * `canonical_candles` UNIONs all six into ONE queryable object so DARWIN can issue the SAME range-query
--     shape (instrument=X, timeframe=<TF>, open_time>=FROM, open_time<TO, ORDER BY open_time ASC) for any
--     of the 6 timeframes. Per-timeframe objects remain individually queryable too.
--
-- LEGACY NON-AUTHORITATIVE (documented, untouched, never a source or a target here):
--   * candles_H4 — UTC-00:00-anchored (00/04/08/12/16/20), NOT the NY-5PM 22:00-anchored governed grid;
--     stale since 2026-06-15 (246 rows, 2026-04-01..2026-06-15 only). A DIFFERENT boundary convention
--     entirely from canonical_candles_h4 — never conflate the two.
--   * candles_D1 — UTC-midnight-anchored, materially gapped (119 gaps >1 day, max gap 5 days). NOT the
--     22:00Z NY-5PM governed daily boundary canonical_candles_d1 uses.
--
-- Identity: (instrument, timeframe, open_time) — UNIQUE. open_time = candle OPEN time, UTC (naive DATETIME
--   column, exactly like every other candle table in this schema — UTC by contract, never mixed).
--
-- CREATE-only / inert: no historical rows written here (see utils/candle_h4_durable_sql_backfill_v1.py and
--   utils/candle_d1_durable_sql_backfill_v1.py, both dark/dry-run-by-default). Views are idempotent
--   (CREATE OR REPLACE); tables are idempotent (IF NOT EXISTS).
-- ============================================

-- ---------------------------------------------------------------------------------------------------------
-- Physical tables: H4 and D1 (no existing durable source — new canonical storage)
-- ---------------------------------------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS canonical_candles_h4 (
  id                           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  instrument                   VARCHAR(20)   NOT NULL,
  timeframe                    VARCHAR(8)    NOT NULL DEFAULT 'H4',
  open_time                    DATETIME      NOT NULL COMMENT 'Candle OPEN time, UTC (NY-5PM 22:00-anchored grid)',
  open                         DECIMAL(12,5) NOT NULL,
  high                         DECIMAL(12,5) NOT NULL,
  low                          DECIMAL(12,5) NOT NULL,
  close                        DECIMAL(12,5) NOT NULL,
  volume                       INT UNSIGNED  NOT NULL DEFAULT 0,
  is_closed                    TINYINT(1)    NOT NULL DEFAULT 1,
  status                       VARCHAR(24)   NOT NULL COMMENT 'Always OK — incomplete windows are never stored here',
  source_timeframe             VARCHAR(8)    NOT NULL DEFAULT 'H1',
  derivation_policy            VARCHAR(48)   NOT NULL,
  source_policy_epoch          VARCHAR(48)   NOT NULL,
  source_count                 SMALLINT UNSIGNED NOT NULL,
  expected_source_count        SMALLINT UNSIGNED NOT NULL,
  source_coverage              DECIMAL(5,4)  NOT NULL,
  gap_state                    VARCHAR(24)   NOT NULL,
  derivation_run_id            VARCHAR(64)   NOT NULL COMMENT 'Which backfill/live run produced this row (provenance only, never part of identity)',
  derivation_generated_at_utc  DATETIME(3)   NOT NULL,
  created_at                   TIMESTAMP     NULL DEFAULT current_timestamp(),
  PRIMARY KEY (id),
  UNIQUE KEY uq_identity (instrument, timeframe, open_time),
  KEY idx_instrument_open (instrument, open_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Durable canonical H4, derived EXCLUSIVELY via candle_h4_derivation_v1.derive_h4() from canonical H1. NOT candles_H4 (legacy, different anchor, non-authoritative).';

CREATE TABLE IF NOT EXISTS canonical_candles_d1 (
  id                           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  instrument                   VARCHAR(20)   NOT NULL,
  timeframe                    VARCHAR(8)    NOT NULL DEFAULT 'D1',
  open_time                    DATETIME      NOT NULL COMMENT 'Candle OPEN time, UTC (22:00Z NY-5PM fixed daily anchor)',
  open                         DECIMAL(12,5) NOT NULL,
  high                         DECIMAL(12,5) NOT NULL,
  low                          DECIMAL(12,5) NOT NULL,
  close                        DECIMAL(12,5) NOT NULL,
  volume                       INT UNSIGNED  NOT NULL DEFAULT 0,
  is_closed                    TINYINT(1)    NOT NULL DEFAULT 1,
  status                       VARCHAR(24)   NOT NULL COMMENT 'Always OK — incomplete windows are never stored here',
  source_timeframe             VARCHAR(8)    NOT NULL DEFAULT 'H4',
  derivation_policy            VARCHAR(48)   NOT NULL,
  source_policy_epoch          VARCHAR(48)   NOT NULL,
  source_count                 SMALLINT UNSIGNED NOT NULL,
  expected_source_count        SMALLINT UNSIGNED NOT NULL,
  source_coverage              DECIMAL(5,4)  NOT NULL,
  gap_state                    VARCHAR(24)   NOT NULL,
  derivation_run_id            VARCHAR(64)   NOT NULL COMMENT 'Which backfill/live run produced this row (provenance only, never part of identity)',
  derivation_generated_at_utc  DATETIME(3)   NOT NULL,
  created_at                   TIMESTAMP     NULL DEFAULT current_timestamp(),
  PRIMARY KEY (id),
  UNIQUE KEY uq_identity (instrument, timeframe, open_time),
  KEY idx_instrument_open (instrument, open_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Durable canonical D1, derived EXCLUSIVELY via candle_d1_derivation_v1.derive_d1() from canonical H4 (never candles_D1, never 24xH1).';

-- ---------------------------------------------------------------------------------------------------------
-- Views: M1/M5/M15/H1 — uniform shape over the EXISTING trustworthy tables (no data duplication)
-- ---------------------------------------------------------------------------------------------------------

-- NOTE: every string literal/derived-string expression below is explicitly COLLATE utf8mb4_unicode_ci —
-- matching the canonical_candles_h4/d1 physical tables' column collation exactly — because MariaDB assigns
-- bare string literals the CONNECTION's default collation, which can differ from a table's declared column
-- collation; without this, the UNION ALL in `canonical_candles` below fails with
-- "Illegal mix of collations for operation 'UNION'". Proven empirically while applying this migration.

CREATE OR REPLACE VIEW canonical_candles_m1 AS
SELECT
  id, instrument, 'M1' COLLATE utf8mb4_unicode_ci AS timeframe, `timestamp` AS open_time,
  open, high, low, close, volume,
  complete AS is_closed,
  'OK' COLLATE utf8mb4_unicode_ci AS status,
  'M1' COLLATE utf8mb4_unicode_ci AS source_timeframe,
  'NONE_DIRECT' COLLATE utf8mb4_unicode_ci AS derivation_policy,
  'DIRECT_NATIVE_V1' COLLATE utf8mb4_unicode_ci AS source_policy_epoch,
  1 AS source_count, 1 AS expected_source_count, 1.0000 AS source_coverage,
  'NONE' COLLATE utf8mb4_unicode_ci AS gap_state,
  CONCAT('DIRECT_M1:', id) COLLATE utf8mb4_unicode_ci AS derivation_run_id,
  CAST(created_at AS DATETIME(3)) AS derivation_generated_at_utc,
  created_at
FROM candles_M1
WHERE instrument = 'XAU_USD' AND complete = 1;

CREATE OR REPLACE VIEW canonical_candles_m5 AS
SELECT
  id, instrument, 'M5' COLLATE utf8mb4_unicode_ci AS timeframe, `timestamp` AS open_time,
  open, high, low, close, volume,
  complete AS is_closed,
  'OK' COLLATE utf8mb4_unicode_ci AS status,
  'M5' COLLATE utf8mb4_unicode_ci AS source_timeframe,
  'NONE_DIRECT' COLLATE utf8mb4_unicode_ci AS derivation_policy,
  'DIRECT_NATIVE_V1' COLLATE utf8mb4_unicode_ci AS source_policy_epoch,
  1 AS source_count, 1 AS expected_source_count, 1.0000 AS source_coverage,
  'NONE' COLLATE utf8mb4_unicode_ci AS gap_state,
  CONCAT('DIRECT_M5:', id) COLLATE utf8mb4_unicode_ci AS derivation_run_id,
  CAST(created_at AS DATETIME(3)) AS derivation_generated_at_utc,
  created_at
FROM candles_M5
WHERE instrument = 'XAU_USD' AND complete = 1;

CREATE OR REPLACE VIEW canonical_candles_m15 AS
SELECT
  id, instrument, 'M15' COLLATE utf8mb4_unicode_ci AS timeframe, `timestamp` AS open_time,
  open, high, low, close, volume,
  complete AS is_closed,
  'OK' COLLATE utf8mb4_unicode_ci AS status,
  'M15' COLLATE utf8mb4_unicode_ci AS source_timeframe,
  'NONE_DIRECT' COLLATE utf8mb4_unicode_ci AS derivation_policy,
  'DIRECT_NATIVE_V1' COLLATE utf8mb4_unicode_ci AS source_policy_epoch,
  1 AS source_count, 1 AS expected_source_count, 1.0000 AS source_coverage,
  'NONE' COLLATE utf8mb4_unicode_ci AS gap_state,
  CONCAT('DIRECT_M15:', id) COLLATE utf8mb4_unicode_ci AS derivation_run_id,
  -- Architect review correction: candles_M15 has no created_at column (unlike M1/M5/H1) — HERMES
  -- genuinely does not know when this row was generated/inserted, so this is honestly NULL, never
  -- fabricated from the candle's own market-open timestamp (a market fact is not a provenance fact).
  -- Documented explicitly in the SQL contract doc as an M15-specific column note.
  CAST(NULL AS DATETIME(3)) AS derivation_generated_at_utc,
  CAST(NULL AS DATETIME) AS created_at
FROM candles_M15
WHERE instrument = 'XAU_USD' AND complete = 1;

CREATE OR REPLACE VIEW canonical_candles_h1 AS
SELECT
  id, instrument, 'H1' COLLATE utf8mb4_unicode_ci AS timeframe, `timestamp` AS open_time,
  open, high, low, close, volume,
  complete AS is_closed,
  'OK' COLLATE utf8mb4_unicode_ci AS status,
  'H1' COLLATE utf8mb4_unicode_ci AS source_timeframe,
  'NONE_DIRECT' COLLATE utf8mb4_unicode_ci AS derivation_policy,
  'DIRECT_NATIVE_V1' COLLATE utf8mb4_unicode_ci AS source_policy_epoch,
  1 AS source_count, 1 AS expected_source_count, 1.0000 AS source_coverage,
  'NONE' COLLATE utf8mb4_unicode_ci AS gap_state,
  CONCAT('DIRECT_H1:', id) COLLATE utf8mb4_unicode_ci AS derivation_run_id,
  CAST(created_at AS DATETIME(3)) AS derivation_generated_at_utc,
  created_at
FROM candles_H1
WHERE instrument = 'XAU_USD' AND complete = 1;

-- ---------------------------------------------------------------------------------------------------------
-- Unified view: ONE object, all 6 timeframes, uniform shape, the Phase-7 supported query surface.
-- (`id` intentionally OMITTED here — it is table-local and not globally unique across the UNION; the
--  contracted identity is instrument+timeframe+open_time, present in every branch.)
-- ---------------------------------------------------------------------------------------------------------

-- Every text column is re-collated explicitly at this level too (COLLATE utf8mb4_unicode_ci) — a nested
-- view-of-views loses an underlying view's own explicit COLLATE on a literal-derived column, so it must be
-- re-asserted here or MariaDB raises "Illegal mix of collations for operation 'UNION'" (proven empirically).
CREATE OR REPLACE VIEW canonical_candles AS
SELECT instrument COLLATE utf8mb4_unicode_ci AS instrument, timeframe COLLATE utf8mb4_unicode_ci AS timeframe,
       open_time, open, high, low, close, volume, is_closed, status COLLATE utf8mb4_unicode_ci AS status,
       source_timeframe COLLATE utf8mb4_unicode_ci AS source_timeframe,
       derivation_policy COLLATE utf8mb4_unicode_ci AS derivation_policy,
       source_policy_epoch COLLATE utf8mb4_unicode_ci AS source_policy_epoch,
       source_count, expected_source_count, source_coverage,
       gap_state COLLATE utf8mb4_unicode_ci AS gap_state,
       derivation_run_id COLLATE utf8mb4_unicode_ci AS derivation_run_id,
       derivation_generated_at_utc, created_at
FROM canonical_candles_m1
UNION ALL
SELECT instrument COLLATE utf8mb4_unicode_ci, timeframe COLLATE utf8mb4_unicode_ci, open_time, open, high, low,
       close, volume, is_closed, status COLLATE utf8mb4_unicode_ci,
       source_timeframe COLLATE utf8mb4_unicode_ci, derivation_policy COLLATE utf8mb4_unicode_ci,
       source_policy_epoch COLLATE utf8mb4_unicode_ci, source_count, expected_source_count, source_coverage,
       gap_state COLLATE utf8mb4_unicode_ci, derivation_run_id COLLATE utf8mb4_unicode_ci,
       derivation_generated_at_utc, created_at
FROM canonical_candles_m5
UNION ALL
SELECT instrument COLLATE utf8mb4_unicode_ci, timeframe COLLATE utf8mb4_unicode_ci, open_time, open, high, low,
       close, volume, is_closed, status COLLATE utf8mb4_unicode_ci,
       source_timeframe COLLATE utf8mb4_unicode_ci, derivation_policy COLLATE utf8mb4_unicode_ci,
       source_policy_epoch COLLATE utf8mb4_unicode_ci, source_count, expected_source_count, source_coverage,
       gap_state COLLATE utf8mb4_unicode_ci, derivation_run_id COLLATE utf8mb4_unicode_ci,
       derivation_generated_at_utc, created_at
FROM canonical_candles_m15
UNION ALL
SELECT instrument COLLATE utf8mb4_unicode_ci, timeframe COLLATE utf8mb4_unicode_ci, open_time, open, high, low,
       close, volume, is_closed, status COLLATE utf8mb4_unicode_ci,
       source_timeframe COLLATE utf8mb4_unicode_ci, derivation_policy COLLATE utf8mb4_unicode_ci,
       source_policy_epoch COLLATE utf8mb4_unicode_ci, source_count, expected_source_count, source_coverage,
       gap_state COLLATE utf8mb4_unicode_ci, derivation_run_id COLLATE utf8mb4_unicode_ci,
       derivation_generated_at_utc, created_at
FROM canonical_candles_h1
UNION ALL
SELECT instrument COLLATE utf8mb4_unicode_ci, timeframe COLLATE utf8mb4_unicode_ci, open_time, open, high, low,
       close, volume, is_closed, status COLLATE utf8mb4_unicode_ci,
       source_timeframe COLLATE utf8mb4_unicode_ci, derivation_policy COLLATE utf8mb4_unicode_ci,
       source_policy_epoch COLLATE utf8mb4_unicode_ci, source_count, expected_source_count, source_coverage,
       gap_state COLLATE utf8mb4_unicode_ci, derivation_run_id COLLATE utf8mb4_unicode_ci,
       derivation_generated_at_utc, created_at
FROM canonical_candles_h4
UNION ALL
SELECT instrument COLLATE utf8mb4_unicode_ci, timeframe COLLATE utf8mb4_unicode_ci, open_time, open, high, low,
       close, volume, is_closed, status COLLATE utf8mb4_unicode_ci,
       source_timeframe COLLATE utf8mb4_unicode_ci, derivation_policy COLLATE utf8mb4_unicode_ci,
       source_policy_epoch COLLATE utf8mb4_unicode_ci, source_count, expected_source_count, source_coverage,
       gap_state COLLATE utf8mb4_unicode_ci, derivation_run_id COLLATE utf8mb4_unicode_ci,
       derivation_generated_at_utc, created_at
FROM canonical_candles_d1;
