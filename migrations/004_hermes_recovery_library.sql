-- ============================================
-- Migration 004: HERMES Recovery Library
-- WO: WO-HERMES-RECOVERY-LIBRARY-0003
-- Database: tradingSignals
-- Date: 2026-03-31
-- Purpose: Governed metadata registry for rebuildable artifacts.
--          Defines what can be recovered, in what order, with what
--          dependencies and strategies. The planner reads this,
--          not code lists.
-- ============================================

-- ============================================================
-- hermes_recovery_library
-- Master registry of recoverable artifacts.
-- ============================================================
CREATE TABLE IF NOT EXISTS hermes_recovery_library (
    artifact_code       VARCHAR(64)     NOT NULL    COMMENT 'Stable unique code (e.g. CANDLE_M1, SIGNAL_M5)',
    artifact_type       ENUM('CANDLE','SIGNAL')
                        NOT NULL                    COMMENT 'Artifact category',
    producer_name       VARCHAR(64)     NOT NULL    COMMENT 'Module/process that produces this artifact',
    instrument_scope    ENUM('GLOBAL','PER_INSTRUMENT')
                        NOT NULL DEFAULT 'PER_INSTRUMENT' COMMENT 'Whether this artifact is per-instrument or global',
    timeframe           VARCHAR(10)     NOT NULL    COMMENT 'Timeframe (M1, M5, M15, H1)',
    target_table        VARCHAR(64)     NOT NULL    COMMENT 'DB table this artifact writes to',
    rebuild_strategy    ENUM(
        'BROKER_FETCH',
        'DERIVE_FROM_LOWER_TIMEFRAME',
        'WINDOW_PLUS_LOOKBACK',
        'FULL_WINDOW',
        'PARTITION_REBUILD'
    ) NOT NULL                                      COMMENT 'How this artifact is rebuilt during recovery',
    required_lookback_bars INT UNSIGNED NOT NULL DEFAULT 0 COMMENT 'Bars of history needed before window start for correct computation',
    validation_strategy ENUM(
        'CONTINUITY',
        'ROW_COUNT',
        'DEPENDENCY_FRESHNESS',
        'SEMANTIC'
    ) NOT NULL DEFAULT 'CONTINUITY'                 COMMENT 'How recovery is verified',
    is_enabled          TINYINT(1)      NOT NULL DEFAULT 1 COMMENT 'Whether this artifact is active for recovery',
    rebuild_order       INT UNSIGNED    NOT NULL    COMMENT 'Explicit execution order (lower = earlier). Must be consistent with dependency graph.',
    version             VARCHAR(16)     NOT NULL DEFAULT '1.0' COMMENT 'Schema/logic version',
    description         TEXT            NOT NULL    COMMENT 'Human-readable description',
    llm_reasoning       TEXT            NOT NULL    COMMENT 'LLM reasoning for design decisions',
    created_at          TIMESTAMP       DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP       DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (artifact_code),
    KEY idx_type (artifact_type),
    KEY idx_enabled (is_enabled),
    KEY idx_rebuild_order (rebuild_order)
) ENGINE=InnoDB
  COMMENT='Governed registry of recoverable HERMES artifacts. The planner reads this, not code lists.';

-- ============================================================
-- hermes_recovery_dependencies
-- Normalized dependency graph between artifacts.
-- ============================================================
CREATE TABLE IF NOT EXISTS hermes_recovery_dependencies (
    artifact_code           VARCHAR(64) NOT NULL COMMENT 'The artifact that has a dependency',
    depends_on_artifact_code VARCHAR(64) NOT NULL COMMENT 'The upstream artifact it depends on',
    dependency_kind         ENUM('REQUIRED','OPTIONAL')
                            NOT NULL DEFAULT 'REQUIRED' COMMENT 'Whether this dependency is hard or soft',
    description             TEXT        NOT NULL COMMENT 'Human-readable description of this dependency',
    llm_reasoning           TEXT        NOT NULL COMMENT 'LLM reasoning for dependency relationship',
    PRIMARY KEY (artifact_code, depends_on_artifact_code),
    KEY idx_depends_on (depends_on_artifact_code),
    CONSTRAINT fk_dep_artifact FOREIGN KEY (artifact_code) REFERENCES hermes_recovery_library(artifact_code),
    CONSTRAINT fk_dep_upstream FOREIGN KEY (depends_on_artifact_code) REFERENCES hermes_recovery_library(artifact_code)
) ENGINE=InnoDB
  COMMENT='Normalized dependency graph for recovery artifacts. FK-enforced referential integrity.';

-- ============================================================
-- Seed: current real HERMES artifacts
-- ============================================================

INSERT INTO hermes_recovery_library
(artifact_code, artifact_type, producer_name, instrument_scope, timeframe, target_table,
 rebuild_strategy, required_lookback_bars, validation_strategy, is_enabled, rebuild_order,
 version, description, llm_reasoning)
VALUES
('CANDLE_M1', 'CANDLE', 'signal_service.oanda_adapter', 'PER_INSTRUMENT', 'M1', 'candles_M1',
 'BROKER_FETCH', 0, 'CONTINUITY', 1, 10,
 '1.0',
 'M1 OHLCV candles from OANDA. Foundation layer — no HERMES dependencies. Recovery fetches directly from OANDA REST API.',
 '{"rationale": "M1 candles are the atomic data unit. They are produced by tick aggregation in real-time but recovered via OANDA /instruments/{}/candles endpoint with M1 granularity. Zero lookback needed because each candle is self-contained OHLCV.", "source": "WO-HERMES-RECOVERY-LIBRARY-0003"}'),

('CANDLE_M5', 'CANDLE', 'signal_service.oanda_adapter', 'PER_INSTRUMENT', 'M5', 'candles_M5',
 'BROKER_FETCH', 0, 'CONTINUITY', 1, 20,
 '1.0',
 'M5 OHLCV candles from OANDA. Recovery fetches from broker. Existing startup backfill already uses this path.',
 '{"rationale": "M5 candles are produced from tick aggregation but recovered from OANDA REST API with M5 granularity. The existing backfill_gap() function in main.py already fetches M5 from OANDA, proving this path works. Zero lookback — each candle is self-contained.", "source": "WO-HERMES-RECOVERY-LIBRARY-0003"}'),

('CANDLE_M15', 'CANDLE', 'signal_service.oanda_adapter', 'PER_INSTRUMENT', 'M15', 'candles_M15',
 'BROKER_FETCH', 0, 'CONTINUITY', 1, 30,
 '1.0',
 'M15 OHLCV candles from OANDA. Recovery fetches from broker with M15 granularity.',
 '{"rationale": "Same recovery pattern as M1/M5. OANDA provides M15 candles directly via REST API. No need to derive from lower timeframes when broker source is authoritative.", "source": "WO-HERMES-RECOVERY-LIBRARY-0003"}'),

('CANDLE_H1', 'CANDLE', 'signal_service.oanda_adapter', 'PER_INSTRUMENT', 'H1', 'candles_H1',
 'BROKER_FETCH', 0, 'CONTINUITY', 1, 40,
 '1.0',
 'H1 OHLCV candles from OANDA. Recovery fetches from broker with H1 granularity.',
 '{"rationale": "OANDA provides H1 candles directly. Broker fetch is the simplest and most authoritative recovery path for all candle timeframes.", "source": "WO-HERMES-RECOVERY-LIBRARY-0003"}'),

('SIGNAL_M5', 'SIGNAL', 'signal_service.signal_computer', 'PER_INSTRUMENT', 'M5', 'signals',
 'WINDOW_PLUS_LOOKBACK', 250, 'ROW_COUNT', 1, 50,
 '1.0',
 'M5 composite signal — RSI(14), EMA(9/12/20/21/26/50/200), ATR(14), ADX(14), Bollinger Bands, regime, compression, break detection. Depends on CANDLE_M5 history.',
 '{"rationale": "Signals are computed by SignalComputer.compute_signal() from candle history. EMA_200 requires 200+ bars of lookback. Using 250 bars to ensure all indicators have stable warm-up. The hermes_lookback_hours and hermes_min_candles_floor config keys govern the exact lookback in production. Rebuild strategy is WINDOW_PLUS_LOOKBACK: fetch candle history extending lookback bars before the gap start, then recompute signals for each candle in the gap window.", "source": "WO-HERMES-RECOVERY-LIBRARY-0003"}'),

('SIGNAL_M15', 'SIGNAL', 'signal_service.signal_computer', 'PER_INSTRUMENT', 'M15', 'signals',
 'WINDOW_PLUS_LOOKBACK', 250, 'ROW_COUNT', 1, 60,
 '1.0',
 'M15 composite signal — same indicator set as M5 but on M15 timeframe. Depends on CANDLE_M15 history.',
 '{"rationale": "Identical computation pipeline to SIGNAL_M5 but operating on M15 candle data. Same 250-bar lookback for EMA_200 stability. Rebuild order is after CANDLE_M15 (order 30) to ensure upstream candles exist before signal computation.", "source": "WO-HERMES-RECOVERY-LIBRARY-0003"}');

-- ============================================================
-- Seed: dependency graph
-- ============================================================

-- Candles have no HERMES dependencies (broker fetch)
-- Signals depend on their respective candle timeframe

INSERT INTO hermes_recovery_dependencies
(artifact_code, depends_on_artifact_code, dependency_kind, description, llm_reasoning)
VALUES
('SIGNAL_M5', 'CANDLE_M5', 'REQUIRED',
 'M5 signals require M5 candle history for indicator computation',
 '{"rationale": "SignalComputer.compute_signal() takes a candle and history array of M5 candles. Without CANDLE_M5 data, signal computation is impossible. This is a hard dependency.", "source": "WO-HERMES-RECOVERY-LIBRARY-0003"}'),

('SIGNAL_M15', 'CANDLE_M15', 'REQUIRED',
 'M15 signals require M15 candle history for indicator computation',
 '{"rationale": "Same relationship as SIGNAL_M5 to CANDLE_M5. M15 signals are computed from M15 candle history. Hard dependency.", "source": "WO-HERMES-RECOVERY-LIBRARY-0003"}');
