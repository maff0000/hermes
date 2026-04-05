"""
Signal Service - Main FastAPI Application
EPIC-D002: Standalone Signal Service
GOV-ENV-001: Environment-aware configuration
WO-0030: Unified logging integration (GOV-LOG-001, GOV-LOG-002, GOV-LOG-011)

Single source of truth for market data.
OANDA primary, IBKR backup, Redis pub/sub distribution.
"""
import asyncio
import os
import sys
import time
import requests
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Dict, Optional, List

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
import uvicorn

# Environment-aware config (loads .env automatically, no hardcoded paths)
from env_config import BASE_DIR, ENV, get_env, get_env_int

# Ensure module path is set (using relative path)
sys.path.insert(0, str(BASE_DIR))

# WO-0030: Add path to tradingProteus for shared logging module
sys.path.insert(0, '/srv-dev/tradingProteus' if ENV == 'DEV' else '/srv/tradingProteus')

from config import load_config, ServiceConfig
from models.tick import SignalTick, TickSource
from adapters.base import AdapterState, AdapterHealth
from adapters.oanda import OANDAAdapter
from utils.redis_publisher import RedisPublisher
from utils.healthcheck import init_healthcheck, record_tick, record_candle, record_signal
try:
    from utils.iris_client import send_alert, send_audit
except ImportError:
    def send_alert(**kwargs): return False
    def send_audit(**kwargs): return False
from signal_builder import CandleAggregator, SignalComputer, SignalPublisher
from utils.level_engine import LevelEngine
from utils.watchdog import (
    HermesWatchdog, HealthPersistence, FaultCode,
    StreamState, HealthState, RecoveryState, DataFlowState,
)
from utils.trading_hours import is_market_open

# WO-0030: Unified logging with GELF streaming (GOV-LOG-001, GOV-LOG-011)
from shared.logging import get_logger, LogLevel

# Initialize structured logger with GELF enabled for Graylog streaming
# Service name includes environment for clear identification in Graylog
logger = get_logger(
    service=f'hermes-{ENV.lower()}',
    enable_gelf=True,
    gelf_host='192.168.11.10',
    gelf_port=12201
)


# Global state
class ServiceState:
    """Global service state"""
    config: ServiceConfig = None
    started_at: datetime = None

    # Adapters (initialized later)
    oanda_adapter = None
    ibkr_adapter = None

    # Current active source
    active_source: TickSource = TickSource.OANDA

    # Latest ticks per instrument
    latest_ticks: Dict[str, SignalTick] = {}

    # Redis publisher (initialized later)
    redis_publisher = None

    # Signal builder components (initialized later)
    candle_aggregator = None
    signal_computer = None
    signal_publisher = None

    # EPIC-D027: Level Engine for Phase 3
    level_engine = None

    # Background tasks
    adapter_tasks: Dict[str, asyncio.Task] = {}

    # Healthcheck reporter (initialized later)
    healthcheck_reporter = None

    # WO-HERMES-STREAM-WATCHDOG-0001: Runtime watchdog
    watchdog: HermesWatchdog = None


state = ServiceState()


def get_zeusv4_config(key: str, value_type: str = 'str'):
    """
    Load config value from zeusv4_config table in tradingProteus database.
    GOV-CFG-001: Fail-loud if key not found.

    Args:
        key: Config key name
        value_type: 'str', 'int', 'float', 'bool'

    Returns:
        Config value cast to requested type

    Raises:
        ValueError: If key not found (GOV-CFG-001)
    """
    import pymysql
    from env_config import get_db_config, ENV

    db_cfg = get_db_config()
    # zeusv4_config is in tradingProteus database, not tradingSignals
    conn = pymysql.connect(
        host=db_cfg['host'],
        port=db_cfg['port'],
        user=db_cfg['user'],
        password=db_cfg['password'],
        database='tradingProteus',  # zeusv4_config lives here
    )
    cursor = conn.cursor()
    cursor.execute(
        "SELECT config_value FROM zeusv4_config WHERE config_key = %s",
        (key,)
    )
    row = cursor.fetchone()
    cursor.close()
    conn.close()

    if row is None:
        raise ValueError(f"GOV-CFG-001: Config key '{key}' not found in zeusv4_config")

    value = row[0]
    if value_type == 'int':
        return int(value)
    elif value_type == 'float':
        return float(value)
    elif value_type == 'bool':
        return value.lower() in ('true', '1', 'yes')
    return value


def get_hermes_config(key: str, value_type: str = 'str'):
    """
    Load config value from hermes_config table in tradingSignals database.
    GOV-CFG-001: Fail-loud if key not found.
    WO-HERMES-STREAM-WATCHDOG-0001: HERMES-owned config domain.

    Args:
        key: Config key name
        value_type: 'str', 'int', 'float', 'bool'

    Returns:
        Config value cast to requested type

    Raises:
        ValueError: If key not found (GOV-CFG-001)
    """
    import pymysql
    from env_config import get_db_config

    db_cfg = get_db_config()
    conn = pymysql.connect(
        host=db_cfg['host'],
        port=db_cfg['port'],
        user=db_cfg['user'],
        password=db_cfg['password'],
        database=db_cfg['database'],  # tradingSignals — HERMES domain
    )
    cursor = conn.cursor()
    cursor.execute(
        "SELECT config_value FROM hermes_config WHERE config_key = %s AND enabled = 1",
        (key,)
    )
    row = cursor.fetchone()
    cursor.close()
    conn.close()

    if row is None:
        raise ValueError(f"GOV-CFG-001: Config key '{key}' not found in hermes_config")

    value = row[0]
    if value_type == 'int':
        return int(value)
    elif value_type == 'float':
        return float(value)
    elif value_type == 'bool':
        return value.lower() in ('true', '1', 'yes')
    return value


def fetch_candle_history(
    instrument: str,
    lookback_hours: int = None,
    limit: int = None,
    timeframe: str = 'M5',
    min_candles_floor: int = None
) -> list:
    """
    Fetch recent candle history from database for indicator computation.

    ADR-0033: Use time-based lookback to ensure consistent horizon across timeframes.
    GOV-CFG-001: lookback_hours and min_candles_floor MUST come from config - fail-loud if missing.

    Args:
        instrument: Trading instrument
        lookback_hours: Hours of history to fetch (from hermes_lookback_hours config)
        limit: Candle count (deprecated, for backwards compatibility)
        timeframe: Candle timeframe (M1, M5, M15, H1, D1)
        min_candles_floor: Minimum candles regardless of lookback (from hermes_min_candles_floor config)

    Returns:
        List of candle dicts in chronological order (oldest first)

    Raises:
        ValueError: If lookback_hours not provided (GOV-CFG-001)
    """
    # GOV-CFG-001: Fail-loud if required config not passed
    if lookback_hours is None and limit is None:
        raise ValueError(
            "GOV-CFG-001: lookback_hours is required. "
            "Load from hermes_lookback_hours config and pass explicitly."
        )
    if min_candles_floor is None and limit is None:
        raise ValueError(
            "GOV-CFG-001: min_candles_floor is required when using lookback_hours. "
            "Load from hermes_min_candles_floor config and pass explicitly."
        )
    import pymysql
    from env_config import get_db_config
    from signal_builder import TIMEFRAMES

    # Get table name and timeframe config
    tf_config = TIMEFRAMES.get(timeframe)
    if not tf_config:
        logger.warning(f"Unknown timeframe: {timeframe}")
        return []
    table_name = tf_config['table']
    tf_seconds = tf_config['seconds']
    tf_minutes = tf_seconds // 60

    # ADR-0033: Calculate limit from lookback_hours if limit not explicitly provided
    if limit is None:
        computed_limit = (lookback_hours * 60) // tf_minutes
        # Safety floor: never less than EMA200 requires (from hermes_min_candles_floor config)
        limit = max(computed_limit, min_candles_floor)

    try:
        db_cfg = get_db_config()
        conn = pymysql.connect(
            host=db_cfg['host'],
            port=db_cfg['port'],
            user=db_cfg['user'],
            password=db_cfg['password'],
            database=db_cfg['database'],
        )
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        # Table name is safe - comes from TIMEFRAMES dict, not user input
        cursor.execute(f"""
            SELECT timestamp, open, high, low, close, volume
            FROM {table_name}
            WHERE instrument = %s
            ORDER BY timestamp DESC
            LIMIT %s
        """, (instrument, limit))
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        # Return in chronological order (oldest first)
        return list(reversed(rows))
    except Exception as e:
        logger.warning(f"Failed to fetch {timeframe} candle history for {instrument}: {e}")
        return []


def load_and_publish_instruments(redis_pub: RedisPublisher) -> int:
    """
    Load instrument configs from DB and publish to Redis.

    Called at startup to make instrument config available to all services.
    Source: tradingSignals.instruments table (SSOT for instrument config)

    Args:
        redis_pub: RedisPublisher instance

    Returns:
        Number of instruments published
    """
    import pymysql
    from env_config import get_db_config

    try:
        db_cfg = get_db_config()
        conn = pymysql.connect(
            host=db_cfg['host'],
            port=db_cfg['port'],
            user=db_cfg['user'],
            password=db_cfg['password'],
            database=db_cfg['database'],
        )
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        # Load all enabled instruments
        cursor.execute("""
            SELECT symbol, pip_value_per_lot, contract_size,
                   default_sl_distance, default_tp_multiplier,
                   min_lot_size, max_lot_size,
                   trading_hours_start, trading_hours_end,
                   oanda_compatible, ibkr_compatible
            FROM instruments
            WHERE enabled = 1
        """)
        instruments = cursor.fetchall()
        cursor.close()
        conn.close()

        if not instruments:
            logger.warning("No enabled instruments found in database")
            return 0

        # Publish each instrument config to Redis
        enabled_symbols = []
        for inst in instruments:
            symbol = inst['symbol']
            config = {
                'pip_value_per_lot': inst['pip_value_per_lot'],
                'contract_size': inst['contract_size'],
                'default_sl_distance': inst['default_sl_distance'],
                'default_tp_multiplier': inst['default_tp_multiplier'],
                'min_lot_size': inst['min_lot_size'],
                'max_lot_size': inst['max_lot_size'],
                'trading_hours_start': str(inst['trading_hours_start']),
                'trading_hours_end': str(inst['trading_hours_end']),
                'oanda_compatible': inst['oanda_compatible'],
                'ibkr_compatible': inst['ibkr_compatible'],
            }
            if redis_pub.publish_instrument_config(symbol, config):
                enabled_symbols.append(symbol)

        # Publish list of enabled instruments
        redis_pub.publish_instruments_list(enabled_symbols)

        logger.info(f"Published {len(enabled_symbols)} instrument configs to Redis")
        return len(enabled_symbols)

    except Exception as e:
        logger.error(f"Failed to load/publish instruments: {e}")
        return 0


# WO-HERMES-CANDLE-PERSISTENCE-DIVERGENCE-0009: shared candle writer connection
_candle_writer_conn = None


def _get_candle_writer_conn():
    """Get or reuse the candle writer DB connection. Reconnect on failure."""
    global _candle_writer_conn
    import pymysql
    from env_config import get_db_config

    try:
        if _candle_writer_conn and _candle_writer_conn.open:
            _candle_writer_conn.ping(reconnect=True)
            return _candle_writer_conn
    except Exception:
        _candle_writer_conn = None

    try:
        db_cfg = get_db_config()
        _candle_writer_conn = pymysql.connect(
            host=db_cfg['host'],
            port=db_cfg['port'],
            user=db_cfg['user'],
            password=db_cfg['password'],
            database=db_cfg['database'],
            autocommit=True,
            connect_timeout=5,
        )
        return _candle_writer_conn
    except Exception as e:
        logger.error(f"[HERMES_CANDLE_WRITE_CONN_FAILED] DB connection for candle writer failed: {e}")
        _candle_writer_conn = None
        raise


def save_candle(candle) -> bool:
    """
    Save completed candle to database. Reuses a shared connection.

    WO-HERMES-CANDLE-PERSISTENCE-DIVERGENCE-0009:
    Changed from per-call connection creation to shared connection with
    ping/reconnect. Failures logged through service logger (not swallowed).
    Returns True on success, False on failure.
    """
    from signal_builder import TIMEFRAMES

    tf_config = TIMEFRAMES.get(candle.timeframe)
    if not tf_config:
        logger.error(f"Unknown timeframe: {candle.timeframe}")
        return False
    table_name = tf_config['table']

    try:
        conn = _get_candle_writer_conn()
        with conn.cursor() as cursor:
            cursor.execute(f"""
                INSERT INTO {table_name} (instrument, timestamp, open, high, low, close, volume)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    open = VALUES(open),
                    high = VALUES(high),
                    low = VALUES(low),
                    close = VALUES(close),
                    volume = VALUES(volume)
            """, (
                candle.instrument, candle.timestamp, candle.open,
                candle.high, candle.low, candle.close, candle.volume
            ))
        return True
    except Exception as e:
        global _candle_writer_conn
        _candle_writer_conn = None  # Force reconnect next time
        logger.error(f"[HERMES_CANDLE_WRITE_FAILED] Failed to save {candle.timeframe} candle "
                     f"{candle.instrument} {candle.timestamp}: {e}")
        return False


def get_last_candle_timestamp(instrument: str) -> Optional[datetime]:
    """Get timestamp of most recent M5 candle for instrument."""
    import pymysql
    from env_config import get_db_config
    try:
        db_cfg = get_db_config()
        conn = pymysql.connect(
            host=db_cfg['host'],
            port=db_cfg['port'],
            user=db_cfg['user'],
            password=db_cfg['password'],
            database=db_cfg['database'],
        )
        cursor = conn.cursor()
        cursor.execute("""
            SELECT MAX(timestamp) FROM candles_M5 WHERE instrument = %s
        """, (instrument,))
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        return result[0] if result and result[0] else None
    except Exception as e:
        logger.warning(f"Failed to get last candle timestamp: {e}")
        return None


def fetch_oanda_candles(instrument: str, from_time: datetime, to_time: datetime) -> List[Dict]:
    """Fetch M5 candles from OANDA API for backfill.

    DEPRECATED (WO-HERMES-CLEANUP-F):
    This function is hardcoded to M5 granularity and is a legacy path.
    The canonical recovery path is utils/recovery_executor.py which
    fetches M1 only and derives higher TFs (EPIC-HERMES-SIGNAL-SOURCE-RESILIENCE-001).
    This function remains in use for startup backfill until the canonical
    M1 engine is wired into the live service path (post-cutover).
    Do NOT add new callers. Remove after cutover criteria met.
    """
    oanda_key = get_env('OANDA_API_KEY', required=True)
    oanda_env = get_env('OANDA_ENVIRONMENT', 'practice')
    base_url = 'https://api-fxtrade.oanda.com' if oanda_env == 'live' else 'https://api-fxpractice.oanda.com'

    url = f"{base_url}/v3/instruments/{instrument}/candles"
    headers = {"Authorization": f"Bearer {oanda_key}"}
    params = {
        "granularity": "M5",
        "price": "M",
        "from": from_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "to": to_time.strftime("%Y-%m-%dT%H:%M:%SZ")
    }

    try:
        response = requests.get(url, headers=headers, params=params, timeout=60)
        response.raise_for_status()
        candles = response.json().get("candles", [])
        return [
            {
                'timestamp': datetime.fromisoformat(c['time'].replace('Z', '+00:00')).replace(tzinfo=None),
                'open': float(c['mid']['o']),
                'high': float(c['mid']['h']),
                'low': float(c['mid']['l']),
                'close': float(c['mid']['c']),
                'volume': c['volume']
            }
            for c in candles if c.get('complete', False)
        ]
    except Exception as e:
        logger.error(f"Failed to fetch OANDA candles: {e}")
        return []


def backfill_gap(instruments: List[str], gap_threshold_minutes: int = 10) -> int:
    """
    Check for gaps and backfill missing candles/signals.

    Called on reconnect to recover data lost during disconnect.
    GOV-ENV-001: All config from environment.

    DEPRECATED (WO-HERMES-CLEANUP-F):
    This is a legacy M5-only startup backfill. The canonical recovery path
    is utils/recovery_executor.py which uses the governed recovery library
    (BROKER_FETCH M1 → DERIVE higher TFs). This function remains active
    for startup/reconnect until canonical M1 engine is wired into live path.
    Do NOT add new callers. Remove after cutover criteria met.

    Args:
        instruments: List of instruments to check
        gap_threshold_minutes: Only backfill if gap > this (default 10 min)

    Returns:
        Number of signals backfilled
    """
    import pymysql
    from env_config import get_db_config

    now = datetime.utcnow()
    total_signals = 0

    for instrument in instruments:
        last_ts = get_last_candle_timestamp(instrument)
        if not last_ts:
            logger.info(f"[{instrument}] No existing candles - skipping backfill")
            continue

        gap_minutes = (now - last_ts).total_seconds() / 60
        if gap_minutes <= gap_threshold_minutes:
            logger.debug("No backfill needed", extra={
                'instrument': instrument,
                'gap_minutes': round(gap_minutes, 1),
                'threshold_minutes': gap_threshold_minutes
            })
            continue

        # WO-0030: Structured gap detection logging (GOV-LOG-002)
        logger.warning("Gap detected", extra={
            'instrument': instrument,
            'gap_minutes': round(gap_minutes, 1),
            'backfill_required': True
        })

        # Fetch missing candles (from last_ts + 5min to now)
        from_time = last_ts + timedelta(minutes=5)
        to_time = now

        # Rate limit: max 4 REST requests/second to Oanda (WO-HERMES-MACRO-001)
        time.sleep(0.3)

        candles = fetch_oanda_candles(instrument, from_time, to_time)
        if not candles:
            logger.warning(f"[{instrument}] No candles fetched for backfill")
            continue

        logger.info(f"[{instrument}] Fetched {len(candles)} candles for backfill")

        # Save candles to DB
        try:
            db_cfg = get_db_config()
            conn = pymysql.connect(
                host=db_cfg['host'],
                port=db_cfg['port'],
                user=db_cfg['user'],
                password=db_cfg['password'],
                database=db_cfg['database'],
                autocommit=True
            )
            cursor = conn.cursor()

            for candle in candles:
                cursor.execute("""
                    INSERT INTO candles_M5 (instrument, timestamp, open, high, low, close, volume)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        open = VALUES(open), high = VALUES(high),
                        low = VALUES(low), close = VALUES(close), volume = VALUES(volume)
                """, (instrument, candle['timestamp'], candle['open'],
                      candle['high'], candle['low'], candle['close'], candle['volume']))

            cursor.close()
            logger.info(f"[{instrument}] Saved {len(candles)} candles")

            # Compute signals for backfilled candles
            # ADR-0033: Use time-based lookback for consistent horizon
            # GOV-CFG-001: Load config from database, no hardcoded defaults
            lookback_hours = get_zeusv4_config('hermes_lookback_hours', 'int')
            min_candles = get_zeusv4_config('hermes_min_candles_floor', 'int')
            history = fetch_candle_history(instrument, lookback_hours=lookback_hours, min_candles_floor=min_candles)
            if len(history) >= 50 and state.signal_computer and state.signal_publisher:
                signals_computed = 0

                for candle in candles:
                    # Find position in history for this candle
                    candle_ts = candle['timestamp']

                    # Create candle object for signal computer
                    class BackfillCandle:
                        def __init__(self, data, instr):
                            self.instrument = instr
                            self.timestamp = data['timestamp']
                            self.open = float(data['open'])
                            self.high = float(data['high'])
                            self.low = float(data['low'])
                            self.close = float(data['close'])
                            self.volume = data['volume']
                            self.timeframe = 'M5'

                    candle_obj = BackfillCandle(candle, instrument)

                    try:
                        signal = state.signal_computer.compute_signal(candle_obj, history)
                        if signal:
                            state.signal_publisher.publish_signal(signal)
                            signals_computed += 1
                    except Exception as e:
                        logger.debug(f"Signal compute error at {candle_ts}: {e}")

                logger.info(f"[{instrument}] Computed {signals_computed} signals")
                total_signals += signals_computed

            conn.close()

        except Exception as e:
            logger.error(f"[{instrument}] Backfill DB error: {e}")

    return total_signals


async def oanda_stream_task():
    """
    Background task to stream OANDA prices, aggregate candles, compute signals.

    Self-healing: Automatically reconnects on stream failure with exponential backoff.
    GOV-ENV-001: All retry config from .env
    """
    # Load retry config from environment
    retry_initial = get_env_int('STREAM_RETRY_INITIAL_DELAY', 5)
    retry_max = get_env_int('STREAM_RETRY_MAX_DELAY', 300)
    retry_multiplier = get_env_int('STREAM_RETRY_BACKOFF_MULTIPLIER', 2)
    max_retries = get_env_int('STREAM_MAX_RETRIES', 0)  # 0 = infinite

    retry_count = 0
    current_delay = retry_initial

    while True:
        try:
            logger.info("OANDA stream task starting...")
            retry_count = 0  # Reset on successful connection
            current_delay = retry_initial

            # WO-HERMES-STREAM-WATCHDOG-0001: Watchdog handles state promotion
            # on first tick receipt via record_tick() -> _promote_to_flowing()

            async for tick in state.oanda_adapter.stream():
                # Update latest ticks
                state.latest_ticks[tick.instrument] = tick

                # Record tick for healthcheck metrics
                record_tick()

                # WO-HERMES-STREAM-WATCHDOG-0001 + WO-0010: Notify watchdog of fresh tick (per-instrument)
                if state.watchdog:
                    state.watchdog.record_tick(tick.timestamp, instrument=tick.instrument)

                # Publish raw tick to Redis
                tick_data = tick.to_dict()
                subscribers = state.redis_publisher.publish_tick(tick_data)

                # Process tick through candle aggregator
                if state.candle_aggregator:
                    completed_candles = state.candle_aggregator.process_tick(
                        instrument=tick.instrument,
                        bid=tick.bid,
                        ask=tick.ask,
                        timestamp=tick.timestamp
                    )

                    # For each completed candle, save and optionally compute signal
                    for candle in completed_candles:
                        # WO-0030: Structured logging with instrument correlation (GOV-LOG-002)
                        logger.info("Candle aggregation complete", extra={
                            'instrument': candle.instrument,
                            'timeframe': candle.timeframe,
                            'open': candle.open,
                            'high': candle.high,
                            'low': candle.low,
                            'close': candle.close,
                            'volume': candle.volume
                        })

                        # Save candle to database (all timeframes)
                        # WO-HERMES-CANDLE-PERSISTENCE-DIVERGENCE-0009:
                        # Only update watchdog M1 truth AFTER successful DB write.
                        # This prevents divergence between in-memory and persisted truth.
                        candle_saved = save_candle(candle)

                        if candle_saved:
                            record_candle()
                            if candle.timeframe == 'M1' and state.watchdog:
                                state.watchdog.record_candle_m1(candle.timestamp, instrument=candle.instrument)
                        else:
                            logger.warning(
                                f"[HERMES_CANDLE_WRITE_FAILED] Candle NOT persisted: "
                                f"{candle.instrument} {candle.timeframe} {candle.timestamp}"
                            )

                        # Compute signals for M1, M5 and M15 candles (signal timeframes)
                        if candle.timeframe in ('M1', 'M5', 'M15'):  # M1 added 2026-04-02 (Matt-directed, scalping ADX/DI/RSI)
                            # ADR-0033: Use time-based lookback for consistent horizon across timeframes
                            # GOV-CFG-001: Load config from database, no hardcoded defaults
                            compute_start = time.time()
                            lookback_hours = get_zeusv4_config('hermes_lookback_hours', 'int')
                            min_candles = get_zeusv4_config('hermes_min_candles_floor', 'int')
                            history = fetch_candle_history(candle.instrument, lookback_hours=lookback_hours, timeframe=candle.timeframe, min_candles_floor=min_candles)

                            if state.signal_computer and len(history) >= 50:
                                # Compute complete signal
                                signal = state.signal_computer.compute_signal(candle, history)
                                compute_duration_ms = int((time.time() - compute_start) * 1000)

                                if signal and state.signal_publisher:
                                    # Publish to DB and Redis
                                    state.signal_publisher.publish_signal(signal)
                                    record_signal()

                                    # WO-HERMES-STREAM-WATCHDOG-0001: Track signal production
                                    if state.watchdog:
                                        state.watchdog.record_signal(candle.timestamp)

                                    # WO-0030: Structured logging for signal published (GOV-LOG-002)
                                    logger.info("Signal published", extra={
                                        'instrument': candle.instrument,
                                        'timeframe': candle.timeframe,
                                        'price_close': candle.close,
                                        'regime': getattr(signal, 'regime', None),
                                        'rsi_14': getattr(signal, 'rsi_14', None),
                                        'duration_ms': compute_duration_ms
                                    })

                # Log periodically (every 20th tick per instrument) - DEBUG level for high volume
                if not hasattr(state, '_tick_counts'):
                    state._tick_counts = {}
                count = state._tick_counts.get(tick.instrument, 0) + 1
                state._tick_counts[tick.instrument] = count

                if count % 20 == 1:
                    # WO-0030: Structured tick logging (DEBUG for high volume, GOV-LOG-002)
                    logger.debug("Tick received", extra={
                        'instrument': tick.instrument,
                        'bid': tick.bid,
                        'ask': tick.ask,
                        'spread': tick.spread,
                        'subscribers': subscribers,
                        'tick_count': count
                    })

        except asyncio.CancelledError:
            logger.info("OANDA stream task cancelled")
            raise
        except Exception as e:
            retry_count += 1
            # WO-0030: Structured error logging (GOV-LOG-002)
            logger.warning("OANDA stream disconnected", extra={
                'error': str(e),
                'retry_count': retry_count,
                'reconnect_attempt': retry_count
            })

            # WO-HERMES-STREAM-WATCHDOG-0001: Notify watchdog of recovery attempt
            if state.watchdog:
                state.watchdog.set_stream_state(StreamState.RECOVERING)
                state.watchdog.record_recovery_attempt()

                # Check exhaustion via watchdog (governed threshold from DB)
                if state.watchdog.is_recovery_exhausted:
                    logger.critical(
                        f"[{FaultCode.RECOVERY_EXHAUSTED}] "
                        f"Recovery exhausted after {state.watchdog.recovery_attempt_no} attempts"
                    )
                    # Watchdog will handle fatal exit on next evaluation cycle
                    # But also raise to exit the stream loop
                    raise RuntimeError(f"{FaultCode.RECOVERY_EXHAUSTED}: max attempts reached")

            # Reconnect with exponential backoff
            logger.warning(f"Reconnecting in {current_delay}s...")
            await asyncio.sleep(current_delay)

            # Increase delay for next retry (capped at max)
            current_delay = min(current_delay * retry_multiplier, retry_max)

            # Attempt to reconnect adapter
            try:
                await state.oanda_adapter.disconnect()
                if await state.oanda_adapter.connect():
                    # WO-HERMES-STREAM-WATCHDOG-0001: connect() only proves auth/API reachability.
                    # Do NOT log as stream recovery success. Enter proof window instead.
                    logger.info("OANDA control plane connected — entering proof window (data flow NOT yet proven)")

                    if state.watchdog:
                        state.watchdog.enter_proof_window()

                    # Auto-backfill any gaps from the disconnect period
                    if state.config and state.config.instruments:
                        gap_threshold = get_env_int('BACKFILL_GAP_THRESHOLD_MINUTES', 10)
                        logger.info(f"Checking for gaps (threshold: {gap_threshold}min)...")
                        backfilled = backfill_gap(state.config.instruments, gap_threshold)
                        if backfilled > 0:
                            logger.info(f"Backfill complete: {backfilled} signals recovered")
                else:
                    logger.warning("OANDA reconnection failed - will retry")
                    if state.watchdog:
                        state.watchdog.set_stream_state(StreamState.FAILED)
                        state.watchdog._set_recovery_state(RecoveryState.FAILED)
            except Exception as reconnect_error:
                logger.error(f"Reconnection error: {reconnect_error}")


async def level_update_task():
    """
    Background task to periodically update price levels.
    EPIC-D027: Phase 3 Trading Strategies

    Update schedule:
    - PDH/PDL: Checked every hour, updates at 00:00 UTC (forex day start)
    - Asia Range: Checked every hour, updates at 07:00 UTC (Asia close)
    - M15 Swings: Updated every 15 minutes
    """
    logger.info("Level update task starting...")

    # Track last update times
    last_pdh_update = None
    last_asia_update = None
    last_swing_update = None

    while True:
        try:
            now = datetime.utcnow()
            instruments = state.config.instruments if state.config else ['XAU_USD']

            for instrument in instruments:
                # Update M15 swings every 15 minutes
                if last_swing_update is None or (now - last_swing_update).total_seconds() >= 900:
                    try:
                        swings = state.level_engine.compute_m15_swings(instrument)
                        if swings:
                            state.level_engine.save_levels_to_db(instrument, swings)
                            logger.debug(f"[{instrument}] M15 swings updated")
                    except Exception as e:
                        logger.warning(f"[{instrument}] M15 swing update error: {e}")
                    last_swing_update = now

                # Update PDH/PDL at 00:xx UTC (once per day)
                if now.hour == 0 and (last_pdh_update is None or last_pdh_update.date() != now.date()):
                    try:
                        pdh_pdl = state.level_engine.compute_pdh_pdl(instrument)
                        if pdh_pdl:
                            state.level_engine.save_levels_to_db(instrument, pdh_pdl)
                            logger.info(f"[{instrument}] PDH/PDL updated: PDH={pdh_pdl.get('pdh', {}).get('level_price')}")
                    except Exception as e:
                        logger.warning(f"[{instrument}] PDH/PDL update error: {e}")
                    last_pdh_update = now

                # Update Asia range at 07:xx UTC (after Asia close)
                if now.hour == 7 and (last_asia_update is None or last_asia_update.date() != now.date()):
                    try:
                        asia = state.level_engine.compute_asia_range(instrument)
                        if asia:
                            state.level_engine.save_levels_to_db(instrument, asia)
                            logger.info(f"[{instrument}] Asia range updated: High={asia.get('asia_high', {}).get('level_price')}")
                    except Exception as e:
                        logger.warning(f"[{instrument}] Asia range update error: {e}")
                    last_asia_update = now

            # Sleep for 5 minutes before next check
            await asyncio.sleep(300)

        except asyncio.CancelledError:
            logger.info("Level update task cancelled")
            raise
        except Exception as e:
            logger.error(f"Level update task error: {e}")
            await asyncio.sleep(60)  # Wait before retry


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle"""
    # WO-0030: Structured service startup logging (GOV-LOG-002)
    logger.info("HERMES service starting", extra={
        'service': 'hermes',
        'environment': ENV,
        'version': '1.2.1'
    })

    # Load configuration
    state.config = load_config()
    state.started_at = datetime.utcnow()

    # CRITICAL: Detect production environment and block mock usage
    # NOTE: Mock functionality is DEPRECATED in this service.
    # Project Dolos (EPIC-TBD) will provide a fully separate mock trading
    # simulator. This check remains as a safety guard only.
    # GOV-ENV-001: Use ENVIRONMENT tag instead of path detection
    is_prod = (ENV == "PROD")

    if is_prod and state.config.oanda.use_mock:
        logger.critical("=" * 60)
        logger.critical("FATAL: USE_MOCK=true is FORBIDDEN in PRODUCTION!")
        logger.critical("Mock data would corrupt live trading signals.")
        logger.critical("Set USE_MOCK=false in .env and restart.")
        logger.critical("NOTE: Mock functionality deprecated - see Project Dolos")
        logger.critical("=" * 60)
        raise RuntimeError("MOCK SIGNALS BLOCKED IN PRODUCTION - SAFETY VIOLATION")

    # WO-0030: Structured configuration logging (GOV-LOG-002)
    logger.info("Configuration loaded", extra={
        'environment': ENV,
        'instruments': len(state.config.instruments),
        'primary_source': state.config.primary_source,
        'redis_host': state.config.redis.host,
        'redis_port': state.config.redis.port,
        'use_mock': state.config.oanda.use_mock
    })

    # Initialize Redis publisher
    state.redis_publisher = RedisPublisher()
    if state.redis_publisher.connect():
        logger.info("Redis publisher connected")

        # Publish instrument configs to Redis (SSOT for all services)
        num_instruments = load_and_publish_instruments(state.redis_publisher)
        logger.info(f"Instrument configs published: {num_instruments}")
    else:
        logger.warning("Redis publisher connection failed - will retry on publish")

    # Initialize signal builder components
    # GOV-ENV-001: Timeframes from .env (default M5 for signals, add H1/D1 for market-map)
    from env_config import get_env_list
    candle_timeframes = get_env_list('CANDLE_TIMEFRAMES', ['M5'])
    state.candle_aggregator = CandleAggregator(
        instruments=state.config.instruments,
        timeframes=candle_timeframes
    )
    logger.info(f"Candle aggregator initialized: {len(state.config.instruments)} instruments, timeframes={candle_timeframes}")

    state.signal_computer = SignalComputer()
    logger.info("Signal computer initialized")

    state.signal_publisher = SignalPublisher(redis_publisher=state.redis_publisher)
    logger.info("Signal publisher initialized")

    # EPIC-D027: Initialize Level Engine for Phase 3
    state.level_engine = LevelEngine()
    logger.info("Level Engine initialized")

    # Initial level update for all instruments
    for inst in state.config.instruments:
        try:
            state.level_engine.update_all_levels(inst)
            logger.info(f"[{inst}] Initial levels computed")
        except Exception as e:
            logger.warning(f"[{inst}] Initial level update error: {e}")


    # WO-HERMES-STREAM-WATCHDOG-0001: Initialize and start health watchdog
    from env_config import get_db_config as _get_db_cfg
    _watchdog_db = _get_db_cfg()
    _watchdog_config = {
        'tick_staleness_threshold_sec': get_hermes_config('hermes_tick_staleness_threshold_sec', 'int'),
        'candle_staleness_threshold_sec': get_hermes_config('hermes_candle_staleness_threshold_sec', 'int'),
        'recovery_proof_window_sec': get_hermes_config('hermes_recovery_proof_window_sec', 'int'),
        'max_recovery_attempts': get_hermes_config('hermes_max_recovery_attempts', 'int'),
        'watchdog_interval_sec': get_hermes_config('hermes_watchdog_interval_sec', 'int'),
    }
    _persistence = HealthPersistence(_watchdog_db, service_name='hermes', environment=ENV, logger=logger)
    state.watchdog = HermesWatchdog(
        service_state=state,
        persistence=_persistence,
        config=_watchdog_config,
        market_hours_checker=is_market_open,
        logger=logger,
    )

    # Fatal exit callback — clean async shutdown
    async def _fatal_exit():
        logger.critical("FATAL EXIT: Watchdog triggered deterministic shutdown.")
        # Give a moment for final log flush
        await asyncio.sleep(1)
        import os
        os._exit(1)  # Hard exit — systemd will restart

    state.watchdog.set_fatal_exit_callback(_fatal_exit)
    await state.watchdog.start()
    logger.info("Health watchdog started")

    # Initialize OANDA adapter
    state.oanda_adapter = OANDAAdapter(
        api_key=state.config.oanda.api_key,
        account_id=state.config.oanda.account_id,
        instruments=state.config.instruments,
        environment=state.config.oanda.environment,
        use_mock=state.config.oanda.use_mock,
        mock_url=state.config.oanda.mock_url
    )

    # Connect to OANDA
    if await state.oanda_adapter.connect():
        # WO-0030: Structured connection logging (GOV-LOG-002)
        logger.info("OANDA stream connected", extra={
            'instruments': len(state.config.instruments),
            'environment': state.config.oanda.environment
        })

        # Check for gaps and backfill on startup if enabled
        from env_config import get_env_bool
        if get_env_bool('BACKFILL_ON_STARTUP', True):
            gap_threshold = get_env_int('BACKFILL_GAP_THRESHOLD_MINUTES', 10)
            logger.info(f"Startup backfill check (threshold: {gap_threshold}min)...")
            backfilled = backfill_gap(state.config.instruments, gap_threshold)
            if backfilled > 0:
                logger.info(f"Startup backfill complete: {backfilled} signals recovered")
            else:
                logger.info("No gaps detected - data is current")

        # WO-HERMES-STREAM-WATCHDOG-0001: Mark stream as unproven until first tick
        if state.watchdog:
            state.watchdog.set_stream_state(StreamState.CONNECTED_UNPROVEN)
            state.watchdog.enter_proof_window()

        # Start streaming task
        state.adapter_tasks['oanda'] = asyncio.create_task(oanda_stream_task())
        logger.info("OANDA streaming task started")

        # EPIC-D027: Start level update task
        state.adapter_tasks['level_update'] = asyncio.create_task(level_update_task())
        logger.info("Level update task started")
    else:
        logger.error("Failed to connect to OANDA - service will start without streaming")

    # Initialize and start healthcheck reporter (sends to IRIS)
    state.healthcheck_reporter = init_healthcheck(state)
    state.healthcheck_reporter.start()
    logger.info("Healthcheck reporter started (reporting to IRIS)")

    # WO-0030: Structured service ready logging (GOV-LOG-002)
    logger.info("HERMES service ready", extra={
        'service': 'hermes',
        'environment': ENV,
        'instruments': len(state.config.instruments) if state.config else 0
    })

    yield

    # Shutdown
    logger.info("Signal Service shutting down...")

    # Stop watchdog
    if state.watchdog:
        await state.watchdog.stop()

    # Stop healthcheck reporter
    if state.healthcheck_reporter:
        await state.healthcheck_reporter.stop()

    # Cancel adapter tasks
    for name, task in state.adapter_tasks.items():
        logger.info(f"Cancelling {name} adapter...")
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # Disconnect adapters
    if state.oanda_adapter:
        await state.oanda_adapter.disconnect()

    # Close signal publisher
    if state.signal_publisher:
        state.signal_publisher.close()

    # Close Redis
    if state.redis_publisher:
        state.redis_publisher.close()

    logger.info("Signal Service stopped.")


# Create FastAPI app
app = FastAPI(
    title="Signal Service",
    description="Single source of truth for market data (EPIC-D002)",
    version="0.1.0",
    lifespan=lifespan
)


# ============================================================================
# Health Endpoints
# ============================================================================

@app.get("/health")
async def health():
    """
    Authoritative health truth — WO-HERMES-STREAM-WATCHDOG-0001.
    Reports real runtime state from watchdog. Never lies.
    """
    if not state.watchdog:
        # Service still starting up
        return JSONResponse(
            status_code=503,
            content={"health_state": "RED", "error": "Service initializing — watchdog not yet started"}
        )

    snapshot = state.watchdog.get_health_snapshot()
    health = snapshot.get("health_state", "RED")

    # HTTP status reflects health truth
    if health == "RED":
        return JSONResponse(status_code=503, content=snapshot)
    elif health == "AMBER":
        return JSONResponse(status_code=200, content=snapshot)
    else:
        return JSONResponse(status_code=200, content=snapshot)


@app.get("/ready")
async def ready():
    """
    Readiness check - are we receiving ticks?
    Returns 503 if not ready.
    """
    if not state.latest_ticks:
        raise HTTPException(status_code=503, detail="No ticks received yet")

    # Check if any tick is stale (older than 60s)
    now = datetime.utcnow()
    for instrument, tick in state.latest_ticks.items():
        age = (now - tick.timestamp).total_seconds()
        if age > 60:
            raise HTTPException(
                status_code=503,
                detail=f"Stale tick for {instrument}: {age:.1f}s old"
            )

    return {
        "status": "ready",
        "active_source": state.active_source.value,
        "instruments": list(state.latest_ticks.keys()),
        "tick_count": len(state.latest_ticks)
    }


@app.get("/metrics")
async def metrics():
    """Prometheus-style metrics"""
    lines = []

    # Uptime
    if state.started_at:
        uptime = (datetime.utcnow() - state.started_at).total_seconds()
        lines.append(f"signal_service_uptime_seconds {uptime:.0f}")

    # Active source
    lines.append(f'signal_service_active_source{{source="{state.active_source.value}"}} 1')

    # Tick ages per instrument
    now = datetime.utcnow()
    for instrument, tick in state.latest_ticks.items():
        age = (now - tick.timestamp).total_seconds()
        lines.append(f'signal_service_tick_age_seconds{{instrument="{instrument}"}} {age:.3f}')
        lines.append(f'signal_service_spread{{instrument="{instrument}"}} {tick.spread:.5f}')

    # Adapter health (when implemented)
    # lines.append(f'signal_adapter_connected{{source="oanda"}} {1 if connected else 0}')

    return JSONResponse(
        content="\n".join(lines),
        media_type="text/plain"
    )


# ============================================================================
# Price Endpoints
# ============================================================================

@app.get("/prices")
async def get_all_prices():
    """Get latest prices for all instruments"""
    return {
        "source": state.active_source.value,
        "prices": {
            inst: tick.to_dict()
            for inst, tick in state.latest_ticks.items()
        }
    }


@app.get("/prices/{instrument}")
async def get_price(instrument: str):
    """Get latest price for specific instrument"""
    # Normalize instrument (accept both XAU_USD and XAUUSD)
    normalized = instrument.upper().replace("_", "")
    for inst, tick in state.latest_ticks.items():
        if inst.replace("_", "") == normalized:
            return tick.to_dict()

    raise HTTPException(status_code=404, detail=f"Instrument {instrument} not found")


@app.get("/fx/{pair}")
async def get_fx_rate(pair: str):
    """
    Get FX rate for currency conversion.
    ADR-053: USD Base Currency with Phase 3 Conversion

    Example: /fx/GBP_USD returns rate for converting USD to GBP
    """
    # Normalize (accept GBP_USD or GBPUSD)
    normalized = pair.upper().replace("_", "")
    for inst, tick in state.latest_ticks.items():
        if inst.replace("_", "") == normalized:
            mid = (tick.bid + tick.ask) / 2
            return {
                "pair": inst,
                "rate": mid,
                "bid": tick.bid,
                "ask": tick.ask,
                "timestamp": tick.timestamp.isoformat()
            }

    raise HTTPException(status_code=404, detail=f"FX pair {pair} not found")


# ============================================================================
# Admin Endpoints
# ============================================================================

@app.get("/status")
async def status():
    """Detailed service status"""
    adapters = {}

    if state.oanda_adapter:
        adapters["oanda"] = state.oanda_adapter.health.to_dict()
    if state.ibkr_adapter:
        adapters["ibkr"] = state.ibkr_adapter.health.to_dict()

    return {
        "service": "signal-service",
        "version": "0.1.0",
        "started_at": state.started_at.isoformat() if state.started_at else None,
        "active_source": state.active_source.value,
        "instruments": state.config.instruments if state.config else [],
        "adapters": adapters,
        "tick_count": len(state.latest_ticks)
    }


@app.post("/failover/{source}")
async def force_failover(source: str):
    """Force failover to specific source (admin)"""
    try:
        new_source = TickSource(source.lower())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid source: {source}")

    old_source = state.active_source
    state.active_source = new_source
    logger.warning(f"Forced failover: {old_source.value} -> {new_source.value}")

    return {
        "status": "ok",
        "old_source": old_source.value,
        "new_source": new_source.value
    }


# ============================================================================
# Main
# ============================================================================

if __name__ == "__main__":
    # GOV-ENV-001: Use env_config for environment-aware port
    from env_config import get_env_int
    port = get_env_int("SIGNAL_PORT", required=True)  # Must be set in .env
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        reload=False,  # Disable for production stability
        log_level="info"
    )
