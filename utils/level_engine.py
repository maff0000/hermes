#!/usr/bin/env python3
"""
HERMES Level Engine - Key Price Level Computation
EPIC-D027 / STORY-D027-01: Phase 3 Trading Strategies

Computes and maintains key price levels for breakout detection:
- PDH/PDL (Previous Day High/Low) - Updated at forex day start (00:00 UTC)
- Asia Range High/Low - Updated at Asia session close (08:00 UTC)
- M15 Swing High/Low - Rolling with each M15 bar

All levels stored in hermes_levels table with zones (level +/- ATR buffer).
"""

import logging
import sys
from datetime import datetime, timedelta, time as dt_time
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from decimal import Decimal

# Ensure parent directory is in path for imports
_this_dir = Path(__file__).parent
_parent_dir = _this_dir.parent
if str(_parent_dir) not in sys.path:
    sys.path.insert(0, str(_parent_dir))

logger = logging.getLogger("signal-service.level_engine")


class LevelEngine:
    """
    Computes and manages key price levels for Phase 3 breakout detection.

    Levels are computed from candle data and stored in hermes_levels table.
    Each level has a zone (level_price +/- zone_half_width).
    """

    # Default configuration (overridden by zeus_phase3_config)
    DEFAULT_CONFIG = {
        'level_zone_atr_mult': 0.15,
        'level_pdh_enabled': True,
        'level_asia_enabled': True,
        'level_m15_swing_enabled': True,
        'level_strength_touch_increment': 0.05,
        'asia_start_hour_utc': 22,  # Previous day
        'asia_end_hour_utc': 7,     # Current day
        'swing_lookback_bars': 20,
    }

    def __init__(self, db_connection_func=None, config: Dict = None):
        """
        Initialize Level Engine.

        Args:
            db_connection_func: Function returning database connection
            config: Configuration dict (uses defaults if not provided)
        """
        self._get_db = db_connection_func
        self.config = {**self.DEFAULT_CONFIG, **(config or {})}
        self._config_loaded_at = None

    def _get_db_connection(self):
        """Get database connection to tradingSignals PROD."""
        if self._get_db:
            return self._get_db()
        # Fallback to env_config for tradingSignals connection
        from env_config import get_db_config
        import pymysql
        cfg = get_db_config()
        return pymysql.connect(
            host=cfg['host'],
            port=cfg['port'],
            user=cfg['user'],
            password=cfg['password'],
            database=cfg['database'],
            cursorclass=pymysql.cursors.DictCursor
        )

    def _load_config_from_env(self):
        """Load configuration from environment variables (GOV-ENV-001 compliant)."""
        from env_config import get_env, get_env_int

        # Level Engine config from .env with defaults
        env_mappings = {
            'level_zone_atr_mult': ('LEVEL_ZONE_ATR_MULT', float, 0.15),
            'level_pdh_enabled': ('LEVEL_PDH_ENABLED', lambda x: x.lower() in ('true', '1'), True),
            'level_asia_enabled': ('LEVEL_ASIA_ENABLED', lambda x: x.lower() in ('true', '1'), True),
            'level_m15_swing_enabled': ('LEVEL_M15_SWING_ENABLED', lambda x: x.lower() in ('true', '1'), True),
            'level_strength_touch_increment': ('LEVEL_STRENGTH_TOUCH_INCREMENT', float, 0.05),
            'asia_start_hour_utc': ('ASIA_START_HOUR_UTC', int, 22),
            'asia_end_hour_utc': ('ASIA_END_HOUR_UTC', int, 7),
            'swing_lookback_bars': ('SWING_LOOKBACK_BARS', int, 20),
        }

        for config_key, (env_key, converter, default) in env_mappings.items():
            env_val = get_env(env_key)
            if env_val is not None:
                try:
                    self.config[config_key] = converter(env_val)
                except (ValueError, TypeError):
                    self.config[config_key] = default
            else:
                self.config[config_key] = default

        self._config_loaded_at = datetime.utcnow()
        logger.debug(f"Level Engine config loaded from env: {len(self.config)} keys")

    def compute_pdh_pdl(self, instrument: str, reference_date: datetime = None) -> Dict:
        """
        Compute Previous Day High/Low levels.

        Args:
            instrument: Trading instrument (e.g., 'XAU_USD')
            reference_date: Date to compute PDH/PDL for (default: today)

        Returns:
            Dict with 'pdh' and 'pdl' level data, or empty if no data
        """
        if reference_date is None:
            reference_date = datetime.utcnow().date()
        elif hasattr(reference_date, 'date'):
            reference_date = reference_date.date()

        # Previous forex day (forex day starts 17:00 EST = 22:00 UTC previous calendar day)
        prev_date = reference_date - timedelta(days=1)

        conn = self._get_db_connection()
        cursor = conn.cursor()

        try:
            # Get previous day's high/low from D1 candles
            cursor.execute("""
                SELECT high, low, close
                FROM candles_D1
                WHERE instrument = %s
                  AND DATE(timestamp) = %s
                LIMIT 1
            """, (instrument, prev_date))

            row = cursor.fetchone()
            if not row:
                logger.debug(f"No D1 candle for {instrument} on {prev_date}")
                return {}

            pdh = float(row['high'])
            pdl = float(row['low'])

            # Get current ATR for zone calculation
            atr = self._get_current_atr(instrument, cursor)
            zone_width = atr * self.config['level_zone_atr_mult']

            return {
                'pdh': {
                    'level_price': pdh,
                    'zone_upper': pdh + zone_width,
                    'zone_lower': pdh - zone_width,
                    'atr_at_creation': atr,
                    'valid_from': datetime.combine(reference_date, dt_time(0, 0)),
                    'valid_until': datetime.combine(reference_date + timedelta(days=1), dt_time(0, 0)),
                },
                'pdl': {
                    'level_price': pdl,
                    'zone_upper': pdl + zone_width,
                    'zone_lower': pdl - zone_width,
                    'atr_at_creation': atr,
                    'valid_from': datetime.combine(reference_date, dt_time(0, 0)),
                    'valid_until': datetime.combine(reference_date + timedelta(days=1), dt_time(0, 0)),
                }
            }

        finally:
            cursor.close()
            conn.close()

    def compute_asia_range(self, instrument: str, reference_date: datetime = None) -> Dict:
        """
        Compute Asia session range (high/low during Asia hours).

        Asia session: 22:00 UTC (previous day) to 07:00 UTC (current day)

        Args:
            instrument: Trading instrument
            reference_date: Date to compute Asia range for

        Returns:
            Dict with 'asia_high' and 'asia_low' level data
        """
        if reference_date is None:
            reference_date = datetime.utcnow().date()
        elif hasattr(reference_date, 'date'):
            reference_date = reference_date.date()

        asia_start = self.config.get('asia_start_hour_utc', 22)
        asia_end = self.config.get('asia_end_hour_utc', 7)

        # Asia starts previous day 22:00, ends current day 07:00
        start_time = datetime.combine(reference_date - timedelta(days=1), dt_time(asia_start, 0))
        end_time = datetime.combine(reference_date, dt_time(asia_end, 0))

        conn = self._get_db_connection()
        cursor = conn.cursor()

        try:
            # Get high/low from H1 candles during Asia session
            cursor.execute("""
                SELECT MAX(high) as asia_high, MIN(low) as asia_low
                FROM candles_H1
                WHERE instrument = %s
                  AND timestamp >= %s
                  AND timestamp < %s
            """, (instrument, start_time, end_time))

            row = cursor.fetchone()
            if not row:
                logger.debug(f"No H1 candles for {instrument} during Asia {start_time} - {end_time}")
                return {}

            if row['asia_high'] is None or row['asia_low'] is None:
                return {}

            asia_high = float(row['asia_high'])
            asia_low = float(row['asia_low'])

            # Get current ATR for zone calculation
            atr = self._get_current_atr(instrument, cursor)
            zone_width = atr * self.config['level_zone_atr_mult']

            return {
                'asia_high': {
                    'level_price': asia_high,
                    'zone_upper': asia_high + zone_width,
                    'zone_lower': asia_high - zone_width,
                    'atr_at_creation': atr,
                    'valid_from': end_time,  # Valid after Asia closes
                    'valid_until': datetime.combine(reference_date + timedelta(days=1), dt_time(asia_end, 0)),
                },
                'asia_low': {
                    'level_price': asia_low,
                    'zone_upper': asia_low + zone_width,
                    'zone_lower': asia_low - zone_width,
                    'atr_at_creation': atr,
                    'valid_from': end_time,
                    'valid_until': datetime.combine(reference_date + timedelta(days=1), dt_time(asia_end, 0)),
                }
            }

        finally:
            cursor.close()
            conn.close()

    def compute_m15_swings(self, instrument: str, lookback_bars: int = None) -> Dict:
        """
        Compute M15 swing high/low levels.

        Uses simple pivot detection: bar high > neighbors = swing high.

        Args:
            instrument: Trading instrument
            lookback_bars: Number of M15 bars to analyze (default from config)

        Returns:
            Dict with 'm15_swing_high' and 'm15_swing_low' level data
        """
        if lookback_bars is None:
            lookback_bars = self.config.get('swing_lookback_bars', 20)

        conn = self._get_db_connection()
        cursor = conn.cursor()

        try:
            # Get recent M15 candles
            cursor.execute("""
                SELECT timestamp, high, low, close
                FROM candles_M15
                WHERE instrument = %s
                ORDER BY timestamp DESC
                LIMIT %s
            """, (instrument, lookback_bars + 4))  # Extra bars for pivot detection

            rows = cursor.fetchall()
            if len(rows) < 5:
                logger.debug(f"Insufficient M15 candles for {instrument} swing detection")
                return {}

            # Normalize to float values
            rows = [{'timestamp': r['timestamp'], 'high': float(r['high']), 'low': float(r['low']), 'close': float(r['close'])} for r in rows]

            # Reverse to chronological order
            rows = list(reversed(rows))

            # Find swing high (local maximum)
            swing_high = None
            swing_high_time = None
            for i in range(2, len(rows) - 2):
                if (rows[i]['high'] > rows[i-1]['high'] and
                    rows[i]['high'] > rows[i-2]['high'] and
                    rows[i]['high'] > rows[i+1]['high'] and
                    rows[i]['high'] > rows[i+2]['high']):
                    swing_high = rows[i]['high']
                    swing_high_time = rows[i]['timestamp']

            # Find swing low (local minimum)
            swing_low = None
            swing_low_time = None
            for i in range(2, len(rows) - 2):
                if (rows[i]['low'] < rows[i-1]['low'] and
                    rows[i]['low'] < rows[i-2]['low'] and
                    rows[i]['low'] < rows[i+1]['low'] and
                    rows[i]['low'] < rows[i+2]['low']):
                    swing_low = rows[i]['low']
                    swing_low_time = rows[i]['timestamp']

            # Fallback to simple max/min if no pivots found
            if swing_high is None:
                swing_high = max(r['high'] for r in rows[-lookback_bars:])
                swing_high_time = next(r['timestamp'] for r in rows[-lookback_bars:] if r['high'] == swing_high)
            if swing_low is None:
                swing_low = min(r['low'] for r in rows[-lookback_bars:])
                swing_low_time = next(r['timestamp'] for r in rows[-lookback_bars:] if r['low'] == swing_low)

            # Get current ATR for zone calculation
            atr = self._get_current_atr(instrument, cursor)
            zone_width = atr * self.config['level_zone_atr_mult']

            now = datetime.utcnow()

            return {
                'm15_swing_high': {
                    'level_price': swing_high,
                    'zone_upper': swing_high + zone_width,
                    'zone_lower': swing_high - zone_width,
                    'atr_at_creation': atr,
                    'valid_from': now,
                    'valid_until': now + timedelta(hours=4),  # Rolling validity
                },
                'm15_swing_low': {
                    'level_price': swing_low,
                    'zone_upper': swing_low + zone_width,
                    'zone_lower': swing_low - zone_width,
                    'atr_at_creation': atr,
                    'valid_from': now,
                    'valid_until': now + timedelta(hours=4),
                }
            }

        finally:
            cursor.close()
            conn.close()

    def _get_current_atr(self, instrument: str, cursor) -> float:
        """Get current M15 ATR for the instrument."""
        cursor.execute("""
            SELECT atr_14
            FROM signals
            WHERE instrument = %s AND atr_14 IS NOT NULL
            ORDER BY timestamp DESC
            LIMIT 1
        """, (instrument,))

        row = cursor.fetchone()
        if row and row['atr_14']:
            return float(row['atr_14'])

        # Fallback: calculate from recent candles
        cursor.execute("""
            SELECT AVG(high - low) as avg_range
            FROM candles_M15
            WHERE instrument = %s
            ORDER BY timestamp DESC
            LIMIT 14
        """, (instrument,))

        row = cursor.fetchone()
        if row and row['avg_range']:
            return float(row['avg_range'])

        return 10.0  # Default ATR for XAU_USD

    def save_levels_to_db(self, instrument: str, levels: Dict) -> int:
        """
        Save computed levels to hermes_levels table.

        Args:
            instrument: Trading instrument
            levels: Dict of level type -> level data

        Returns:
            Number of levels saved
        """
        if not levels:
            return 0

        conn = self._get_db_connection()
        cursor = conn.cursor()

        level_type_map = {
            'pdh': 'PDH',
            'pdl': 'PDL',
            'asia_high': 'ASIA_HIGH',
            'asia_low': 'ASIA_LOW',
            'm15_swing_high': 'M15_SWING_HIGH',
            'm15_swing_low': 'M15_SWING_LOW',
        }

        saved = 0
        try:
            for key, data in levels.items():
                level_type = level_type_map.get(key)
                if not level_type or not data:
                    continue

                # Deactivate old levels of same type
                cursor.execute("""
                    UPDATE hermes_levels
                    SET is_active = FALSE
                    WHERE instrument = %s AND level_type = %s AND is_active = TRUE
                """, (instrument, level_type))

                # Insert new level
                cursor.execute("""
                    INSERT INTO hermes_levels
                    (instrument, level_type, level_price, zone_upper, zone_lower,
                     atr_at_creation, valid_from, valid_until, is_active)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE)
                """, (
                    instrument,
                    level_type,
                    data['level_price'],
                    data['zone_upper'],
                    data['zone_lower'],
                    data.get('atr_at_creation'),
                    data.get('valid_from'),
                    data.get('valid_until'),
                ))
                saved += 1

            conn.commit()
            logger.info(f"Saved {saved} levels for {instrument}")

        except Exception as e:
            logger.error(f"Error saving levels: {e}")
            conn.rollback()

        finally:
            cursor.close()
            conn.close()

        return saved

    def get_active_levels(self, instrument: str) -> List[Dict]:
        """
        Get all active levels for an instrument.

        Args:
            instrument: Trading instrument

        Returns:
            List of active level dicts
        """
        conn = self._get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT id, level_type, level_price, zone_upper, zone_lower,
                       atr_at_creation, strength_score, touch_count,
                       valid_from, valid_until
                FROM hermes_levels
                WHERE instrument = %s
                  AND is_active = TRUE
                  AND (valid_until IS NULL OR valid_until > NOW())
                ORDER BY level_type
            """, (instrument,))

            rows = cursor.fetchall()
            return list(rows) if rows else []

        finally:
            cursor.close()
            conn.close()

    def get_nearest_levels(self, instrument: str, current_price: float, atr: float) -> Dict:
        """
        Get nearest resistance and support levels with distances.

        Args:
            instrument: Trading instrument
            current_price: Current market price
            atr: Current ATR for distance calculation

        Returns:
            Dict with nearest_resistance and nearest_support data
        """
        levels = self.get_active_levels(instrument)

        if not levels:
            return {}

        nearest_resistance = None
        nearest_resistance_dist = float('inf')
        nearest_support = None
        nearest_support_dist = float('inf')

        for level in levels:
            price = float(level['level_price'])
            dist = abs(price - current_price)
            dist_atr = dist / atr if atr > 0 else float('inf')

            if price > current_price and dist < nearest_resistance_dist:
                nearest_resistance = level
                nearest_resistance_dist = dist
                nearest_resistance['dist_atr'] = round(dist_atr, 2)

            elif price < current_price and dist < nearest_support_dist:
                nearest_support = level
                nearest_support_dist = dist
                nearest_support['dist_atr'] = round(dist_atr, 2)

        result = {}
        if nearest_resistance:
            result['nearest_resistance'] = nearest_resistance
        if nearest_support:
            result['nearest_support'] = nearest_support

        return result

    def update_all_levels(self, instrument: str) -> Dict:
        """
        Compute and save all level types for an instrument.

        Called by cron job or on-demand.

        Args:
            instrument: Trading instrument

        Returns:
            Summary of levels computed
        """
        summary = {'instrument': instrument, 'levels': {}}

        # Load config if stale (GOV-ENV-001: config from .env)
        if self._config_loaded_at is None or (datetime.utcnow() - self._config_loaded_at).seconds > 300:
            self._load_config_from_env()

        # PDH/PDL
        if self.config.get('level_pdh_enabled', True):
            pdh_pdl = self.compute_pdh_pdl(instrument)
            if pdh_pdl:
                self.save_levels_to_db(instrument, pdh_pdl)
                summary['levels']['pdh'] = pdh_pdl.get('pdh', {}).get('level_price')
                summary['levels']['pdl'] = pdh_pdl.get('pdl', {}).get('level_price')

        # Asia Range
        if self.config.get('level_asia_enabled', True):
            asia = self.compute_asia_range(instrument)
            if asia:
                self.save_levels_to_db(instrument, asia)
                summary['levels']['asia_high'] = asia.get('asia_high', {}).get('level_price')
                summary['levels']['asia_low'] = asia.get('asia_low', {}).get('level_price')

        # M15 Swings
        if self.config.get('level_m15_swing_enabled', True):
            swings = self.compute_m15_swings(instrument)
            if swings:
                self.save_levels_to_db(instrument, swings)
                summary['levels']['m15_swing_high'] = swings.get('m15_swing_high', {}).get('level_price')
                summary['levels']['m15_swing_low'] = swings.get('m15_swing_low', {}).get('level_price')

        logger.info(f"Level Engine update complete for {instrument}: {summary['levels']}")
        return summary


# CLI for testing and cron jobs
if __name__ == '__main__':
    import argparse

    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    parser = argparse.ArgumentParser(description='HERMES Level Engine')
    parser.add_argument('--instrument', default='XAU_USD', help='Instrument to process')
    parser.add_argument('--update-all', action='store_true', help='Update all levels')
    parser.add_argument('--pdh-pdl', action='store_true', help='Compute PDH/PDL only')
    parser.add_argument('--asia', action='store_true', help='Compute Asia range only')
    parser.add_argument('--swings', action='store_true', help='Compute M15 swings only')
    parser.add_argument('--show', action='store_true', help='Show active levels')

    args = parser.parse_args()

    engine = LevelEngine()

    if args.update_all:
        result = engine.update_all_levels(args.instrument)
        print(f"Updated levels: {result}")

    elif args.pdh_pdl:
        result = engine.compute_pdh_pdl(args.instrument)
        if result:
            engine.save_levels_to_db(args.instrument, result)
        print(f"PDH/PDL: {result}")

    elif args.asia:
        result = engine.compute_asia_range(args.instrument)
        if result:
            engine.save_levels_to_db(args.instrument, result)
        print(f"Asia Range: {result}")

    elif args.swings:
        result = engine.compute_m15_swings(args.instrument)
        if result:
            engine.save_levels_to_db(args.instrument, result)
        print(f"M15 Swings: {result}")

    elif args.show:
        levels = engine.get_active_levels(args.instrument)
        print(f"Active levels for {args.instrument}:")
        for level in levels:
            print(f"  {level['level_type']}: {level['level_price']} (zone: {level['zone_lower']}-{level['zone_upper']})")
    else:
        parser.print_help()
