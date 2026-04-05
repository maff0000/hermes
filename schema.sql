-- ============================================
-- Trading Signals Database Schema
-- EPIC-D002: Standalone Signal Service
-- Database: tradingSignals
-- ============================================

-- Candle tables (M1, M5, H1, D1)
CREATE TABLE IF NOT EXISTS candles_M1 (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    instrument VARCHAR(20) NOT NULL,
    timestamp DATETIME NOT NULL,
    open DECIMAL(12,5) NOT NULL,
    high DECIMAL(12,5) NOT NULL,
    low DECIMAL(12,5) NOT NULL,
    close DECIMAL(12,5) NOT NULL,
    volume INT UNSIGNED DEFAULT 0,
    complete TINYINT(1) DEFAULT 1,
    source VARCHAR(20) DEFAULT 'signal_service',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY idx_instrument_timestamp (instrument, timestamp),
    KEY idx_timestamp (timestamp)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS candles_M5 LIKE candles_M1;
CREATE TABLE IF NOT EXISTS candles_H1 LIKE candles_M1;
CREATE TABLE IF NOT EXISTS candles_D1 LIKE candles_M1;

-- Ticks table (optional high-resolution storage)
CREATE TABLE IF NOT EXISTS ticks (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    instrument VARCHAR(20) NOT NULL,
    timestamp DATETIME(3) NOT NULL,
    bid DECIMAL(12,5) NOT NULL,
    ask DECIMAL(12,5) NOT NULL,
    source VARCHAR(20) DEFAULT 'oanda',
    PRIMARY KEY (id),
    KEY idx_instrument_timestamp (instrument, timestamp)
) ENGINE=InnoDB;

-- Decisions table (trading signals for execution)
CREATE TABLE IF NOT EXISTS decisions (
    id BIGINT NOT NULL AUTO_INCREMENT,
    decision_id VARCHAR(36) NOT NULL,
    instrument VARCHAR(20) NOT NULL,
    timestamp DATETIME NOT NULL,
    direction ENUM('LONG','SHORT','NONE') NOT NULL,
    entry_price DECIMAL(12,5),
    stop_loss DECIMAL(12,5),
    take_profit DECIMAL(12,5),
    sl_distance DECIMAL(12,5),
    tp_distance DECIMAL(12,5),
    lot_size DECIMAL(10,4),
    confidence DECIMAL(5,4),
    template_id VARCHAR(32),
    regime VARCHAR(32),
    signal_data JSON,
    status ENUM('pending','executed','rejected','expired') DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY idx_decision_id (decision_id),
    KEY idx_instrument (instrument),
    KEY idx_timestamp (timestamp),
    KEY idx_status (status)
) ENGINE=InnoDB;

-- Computed signals/indicators
CREATE TABLE IF NOT EXISTS signals (
    id BIGINT NOT NULL AUTO_INCREMENT,
    instrument VARCHAR(20) NOT NULL,
    timestamp DATETIME NOT NULL,
    timeframe VARCHAR(10) NOT NULL DEFAULT 'M5',
    price_close DECIMAL(12,5),
    rsi_14 DECIMAL(5,2),
    ema_9 DECIMAL(12,5),
    ema_21 DECIMAL(12,5),
    ema_50 DECIMAL(12,5),
    ema_200 DECIMAL(12,5),
    atr_14 DECIMAL(12,5),
    regime VARCHAR(32),
    session VARCHAR(20),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY idx_instrument_tf_timestamp (instrument, timeframe, timestamp),
    KEY idx_timestamp (timestamp)
) ENGINE=InnoDB;

-- Instruments (copy from instruments table)
CREATE TABLE IF NOT EXISTS instruments (
    id INT NOT NULL AUTO_INCREMENT,
    symbol VARCHAR(20) NOT NULL,
    mt5_symbol VARCHAR(20),
    name VARCHAR(100) NOT NULL,
    category ENUM('precious_metals','base_metals','forex_major','forex_minor','indices','crypto') NOT NULL,
    pip_value_per_lot DECIMAL(10,4) NOT NULL,
    contract_size INT NOT NULL DEFAULT 1,
    default_sl_distance DECIMAL(10,4) NOT NULL,
    default_tp_multiplier DECIMAL(5,2) DEFAULT 1.5,
    min_lot_size DECIMAL(10,4) DEFAULT 0.01,
    max_lot_size DECIMAL(10,4) DEFAULT 100.0,
    trading_hours_start TIME DEFAULT '00:00:00',
    trading_hours_end TIME DEFAULT '23:59:59',
    enabled TINYINT(1) DEFAULT 1,
    oanda_compatible TINYINT(1) DEFAULT 1,
    ibkr_compatible TINYINT(1) DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY idx_symbol (symbol)
) ENGINE=InnoDB;

-- Service health/stats
CREATE TABLE IF NOT EXISTS service_stats (
    id INT NOT NULL AUTO_INCREMENT,
    timestamp DATETIME NOT NULL,
    ticks_received BIGINT DEFAULT 0,
    candles_written BIGINT DEFAULT 0,
    decisions_generated INT DEFAULT 0,
    active_source VARCHAR(20),
    latency_avg_ms DECIMAL(10,2),
    errors INT DEFAULT 0,
    PRIMARY KEY (id),
    KEY idx_timestamp (timestamp)
) ENGINE=InnoDB;
