#!/usr/bin/env python3
"""
ATR Calculator
ADR-040: ATR-Based Dynamic Stop-Loss and Take-Profit
GOV-ENV-001: Environment-aware configuration

Calculates Average True Range from candle data.
Used for dynamic SL/TP sizing that adapts to current volatility.

True Range = max(high-low, |high-prev_close|, |low-prev_close|)
ATR = SMA(True Range, period)
"""

import sys
import logging
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timezone
from pathlib import Path

import pymysql
import pymysql.cursors

# Setup - use relative path from module location
UTILS_DIR = Path(__file__).parent.absolute()
BASE_DIR = UTILS_DIR.parent
sys.path.insert(0, str(BASE_DIR))

from env_config import get_env_bool, get_db_config

logger = logging.getLogger(__name__)

# Debug mode - set via environment or trading_config
DEBUG_ATR = get_env_bool('DEBUG_ATR', False)


def set_debug(enabled: bool):
    """Enable/disable ATR debug output."""
    global DEBUG_ATR
    DEBUG_ATR = enabled


def debug_print(msg: str):
    """Print debug message if debug mode enabled."""
    if DEBUG_ATR:
        print(f"[ATR DEBUG] {msg}")


def calculate_true_range(high: float, low: float, prev_close: float) -> float:
    """
    Calculate True Range for a single candle.

    True Range = max(
        high - low,
        |high - prev_close|,
        |low - prev_close|
    )

    Args:
        high: Current candle high
        low: Current candle low
        prev_close: Previous candle close

    Returns:
        True Range value
    """
    return max(
        high - low,
        abs(high - prev_close),
        abs(low - prev_close)
    )


def calculate_atr(candles: List[Dict[str, Any]], period: int = 14) -> Optional[float]:
    """
    Calculate ATR from a list of candles.

    Args:
        candles: List of dicts with 'high', 'low', 'close' keys
                 MUST be in chronological order (oldest first)
        period: ATR period (default 14)

    Returns:
        ATR value, or None if insufficient data
    """
    debug_print(f"calculate_atr: {len(candles)} candles, period={period}")

    if len(candles) < period + 1:
        logger.warning(f"Insufficient candles for ATR({period}): got {len(candles)}, need {period + 1}")
        return None

    # Calculate True Range for each candle (except first)
    true_ranges = []
    for i in range(1, len(candles)):
        tr = calculate_true_range(
            high=float(candles[i]['high']),
            low=float(candles[i]['low']),
            prev_close=float(candles[i - 1]['close'])
        )
        true_ranges.append(tr)
        if DEBUG_ATR and i <= 3:  # Show first few TRs
            debug_print(f"  TR[{i}]: H={candles[i]['high']}, L={candles[i]['low']}, "
                       f"prev_C={candles[i-1]['close']} -> TR={tr:.4f}")

    # Use last 'period' true ranges for ATR
    recent_tr = true_ranges[-period:]
    atr = sum(recent_tr) / len(recent_tr)

    debug_print(f"  ATR({period}) = {atr:.4f} (avg of last {len(recent_tr)} TRs)")
    debug_print(f"  TR range: min={min(recent_tr):.4f}, max={max(recent_tr):.4f}")

    return round(atr, 5)


class ATRProvider:
    """
    Provides ATR values for instruments from candle data.

    Caches ATR calculations to avoid repeated database queries.
    """

    def __init__(self, db_config: Dict[str, Any] = None):
        """
        Initialize ATR provider.

        Args:
            db_config: Database connection config (uses env_config if not provided)
        """
        if db_config:
            self._db_config = db_config
        else:
            # Use environment-aware config (GOV-ENV-001)
            cfg = get_db_config()
            self._db_config = {
                'host': cfg['host'],
                'port': cfg['port'],
                'user': cfg['user'],
                'password': cfg['password'],
                'db': cfg['database'],
            }
        self._cache: Dict[str, Tuple[float, datetime]] = {}
        self._cache_ttl_seconds = 300  # 5 minute cache

        # Load instrument config from database
        self._instrument_config = self._load_instrument_config()

    def _load_instrument_config(self) -> Dict[str, Dict[str, Any]]:
        """Load instrument-specific config from trading_config table."""
        config = {}

        # Known suffixes for instrument config keys
        SUFFIXES = {
            '_pip_size': 'pip_size',
            '_pip_value': 'pip_value',
            '_min_spread': 'min_spread',
            '_atr_period': 'atr_period',
        }

        try:
            conn = pymysql.connect(**self._db_config)
            cursor = conn.cursor(pymysql.cursors.DictCursor)
            cursor.execute("""
                SELECT config_key, config_value, value_type
                FROM trading_config
                WHERE config_key LIKE '%_pip_size'
                   OR config_key LIKE '%_pip_value'
                   OR config_key LIKE '%_min_spread'
                   OR config_key LIKE '%_atr_period'
            """)

            for row in cursor.fetchall():
                # Parse key: xau_usd_pip_size -> instrument=XAU_USD, param=pip_size
                key = row['config_key']

                # Find which suffix matches
                param = None
                instrument = None
                for suffix, param_name in SUFFIXES.items():
                    if key.endswith(suffix):
                        param = param_name
                        instrument = key[:-len(suffix)].upper()
                        break

                if not instrument or not param:
                    continue

                if instrument not in config:
                    config[instrument] = {}

                # Parse value based on type
                if row['value_type'] == 'integer':
                    config[instrument][param] = int(row['config_value'])
                elif row['value_type'] == 'decimal':
                    config[instrument][param] = float(row['config_value'])
                else:
                    config[instrument][param] = row['config_value']

            cursor.close()
            conn.close()
            logger.info(f"Loaded instrument config: {list(config.keys())}")

        except Exception as e:
            logger.error(f"Failed to load instrument config: {e}")

        return config

    def get_instrument_config(self, instrument: str) -> Dict[str, Any]:
        """
        Get config for an instrument.

        Args:
            instrument: Instrument code (e.g., 'XAU_USD')

        Returns:
            Dict with pip_size, pip_value, min_spread, atr_period
        """
        # Normalize instrument name
        inst = instrument.upper().replace('/', '_')
        if len(inst) == 6 and '_' not in inst:
            inst = inst[:3] + '_' + inst[3:]

        # Return config or defaults
        if inst in self._instrument_config:
            return self._instrument_config[inst]

        # Default values (should be overridden by database config)
        logger.warning(f"No config for {inst}, using defaults")
        return {
            'pip_size': 0.10,
            'pip_value': 10.0,
            'min_spread': 3.0,
            'atr_period': 14
        }

    def get_atr(self, instrument: str, timestamp: datetime = None) -> Optional[float]:
        """
        Get ATR for an instrument at a specific time.

        For backtesting, pass the timestamp to get historical ATR.
        For live trading, pass None for current ATR.

        Args:
            instrument: Instrument code (e.g., 'XAU_USD')
            timestamp: Optional timestamp for historical ATR

        Returns:
            ATR value, or None if unavailable
        """
        inst_config = self.get_instrument_config(instrument)
        period = inst_config.get('atr_period', 14)

        # Check cache for recent values (live only)
        cache_key = f"{instrument}:{period}"
        if timestamp is None and cache_key in self._cache:
            cached_atr, cached_time = self._cache[cache_key]
            if (datetime.now(timezone.utc) - cached_time).total_seconds() < self._cache_ttl_seconds:
                return cached_atr

        # Query candles from database
        try:
            conn = pymysql.connect(**self._db_config)
            cursor = conn.cursor(pymysql.cursors.DictCursor)

            # Need period + 1 candles to calculate period true ranges
            limit = period + 5  # Extra buffer

            if timestamp:
                # Historical: get candles before timestamp
                cursor.execute("""
                    SELECT timestamp, open, high, low, close
                    FROM candles_M5
                    WHERE instrument = %s AND timestamp <= %s
                    ORDER BY timestamp DESC
                    LIMIT %s
                """, (instrument, timestamp, limit))
            else:
                # Current: get most recent candles
                cursor.execute("""
                    SELECT timestamp, open, high, low, close
                    FROM candles_M5
                    WHERE instrument = %s
                    ORDER BY timestamp DESC
                    LIMIT %s
                """, (instrument, limit))

            rows = cursor.fetchall()
            cursor.close()
            conn.close()

            if len(rows) < period + 1:
                logger.warning(f"Insufficient candles for {instrument} ATR: {len(rows)}")
                return None

            # Reverse to chronological order (oldest first)
            candles = list(reversed(rows))
            atr = calculate_atr(candles, period)

            # Cache result (live only)
            if timestamp is None and atr is not None:
                self._cache[cache_key] = (atr, datetime.now(timezone.utc))

            return atr

        except Exception as e:
            logger.error(f"Failed to get ATR for {instrument}: {e}")
            return None

    def calculate_dynamic_sl_tp(
        self,
        instrument: str,
        atr: float,
        sl_atr_multiple: float = 1.5,
        tp_atr_multiple: float = 3.0,
        sl_min_pips: float = 5.0,
        sl_max_pips: float = 100.0
    ) -> Tuple[float, float]:
        """
        Calculate dynamic SL/TP in pips based on ATR.

        Formula:
            sl_pips = clamp(ATR × sl_atr_multiple / pip_size, min, max)
            tp_pips = ATR × tp_atr_multiple / pip_size

        Args:
            instrument: Instrument code
            atr: Current ATR value
            sl_atr_multiple: SL as ATR multiple (default 1.5)
            tp_atr_multiple: TP as ATR multiple (default 3.0)
            sl_min_pips: Minimum SL floor (default 5.0)
            sl_max_pips: Maximum SL ceiling (default 100.0)

        Returns:
            (sl_pips, tp_pips) tuple
        """
        debug_print(f"calculate_dynamic_sl_tp: instrument={instrument}, ATR={atr:.4f}")

        inst_config = self.get_instrument_config(instrument)
        pip_size = inst_config.get('pip_size', 0.10)

        debug_print(f"  pip_size={pip_size}, sl_atr_mult={sl_atr_multiple}, tp_atr_mult={tp_atr_multiple}")
        debug_print(f"  sl_min={sl_min_pips}, sl_max={sl_max_pips}")

        if pip_size <= 0:
            logger.error(f"Invalid pip_size for {instrument}: {pip_size}")
            return sl_min_pips, sl_min_pips * 2  # Fallback

        # Calculate raw pips from ATR
        raw_sl_pips = (atr * sl_atr_multiple) / pip_size
        raw_tp_pips = (atr * tp_atr_multiple) / pip_size

        debug_print(f"  raw_sl = ({atr:.4f} × {sl_atr_multiple}) / {pip_size} = {raw_sl_pips:.1f} pips")
        debug_print(f"  raw_tp = ({atr:.4f} × {tp_atr_multiple}) / {pip_size} = {raw_tp_pips:.1f} pips")

        # Apply SL floor and ceiling
        sl_pips = max(sl_min_pips, min(sl_max_pips, raw_sl_pips))
        tp_pips = raw_tp_pips

        if sl_pips != raw_sl_pips:
            debug_print(f"  SL clamped: {raw_sl_pips:.1f} -> {sl_pips:.1f} (min={sl_min_pips}, max={sl_max_pips})")

        # Calculate price distance for reference
        sl_price_dist = sl_pips * pip_size
        tp_price_dist = tp_pips * pip_size
        debug_print(f"  FINAL: SL={sl_pips:.1f} pips (${sl_price_dist:.2f}), TP={tp_pips:.1f} pips (${tp_price_dist:.2f})")
        debug_print(f"  R:R ratio = {tp_pips/sl_pips:.2f}:1")

        return round(sl_pips, 1), round(tp_pips, 1)


# Global singleton
_atr_provider: Optional[ATRProvider] = None


def get_atr_provider() -> ATRProvider:
    """Get or create the global ATR provider."""
    global _atr_provider
    if _atr_provider is None:
        _atr_provider = ATRProvider()
    return _atr_provider
