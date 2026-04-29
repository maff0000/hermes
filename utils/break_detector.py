#!/usr/bin/env python3
"""
HERMES Break Quality Detector
EPIC-D027 / STORY-D027-03: Phase 3 Trading Strategies

Detects and scores breakout quality to filter false breakouts:
- Wick-only breaks (price touched but didn't close through)
- Single-candle spikes (no follow-through)
- Body percentage through level (how much body crossed)
- Close position relative to level
- Volume confirmation

Break quality score 0-1: higher = higher quality breakout.

GOV-ENV-001: Config from .env, no hardcoded DEV/PROD values.
"""

import logging
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

# Ensure parent directory is in path for imports
_this_dir = Path(__file__).parent
_parent_dir = _this_dir.parent
if str(_parent_dir) not in sys.path:
    sys.path.insert(0, str(_parent_dir))

logger = logging.getLogger("signal-service.break_detector")


@dataclass
class BreakResult:
    """Result of break quality detection."""
    break_detected: bool       # Whether a break was detected
    direction: str             # 'long', 'short', or 'none'
    quality_score: float       # 0-1, higher = higher quality
    level_id: int              # ID of broken level (0 if none)
    level_type: str            # Type of broken level
    level_price: float         # Price of broken level
    is_wick_only: bool         # True if only wick touched level
    body_through_pct: float    # Percentage of candle body through level
    close_beyond: bool         # True if close is beyond level
    volume_confirmed: bool     # True if volume above average
    components: Dict           # Individual component scores


class BreakDetector:
    """
    Detects and scores breakout quality.

    A quality breakout has:
    - Full candle body through level (not just wick)
    - Close beyond the level
    - Above-average volume
    - Follow-through in subsequent bars
    """

    # Default configuration (overridden by .env)
    DEFAULT_CONFIG = {
        'break_body_weight': 0.35,           # Weight for body-through component
        'break_close_weight': 0.30,          # Weight for close-beyond component
        'break_volume_weight': 0.20,         # Weight for volume component
        'break_followthrough_weight': 0.15,  # Weight for follow-through
        'break_min_body_pct': 0.30,          # Minimum body percentage through level
        'break_volume_threshold': 1.2,       # Volume ratio for confirmation
        'break_zone_touch_bars': 3,          # Bars to check for zone touch
    }

    def __init__(self, db_connection_func=None, config: Dict = None):
        """
        Initialize Break Detector.

        Args:
            db_connection_func: Function returning database connection
            config: Configuration dict (uses defaults + .env if not provided)
        """
        self._get_db = db_connection_func
        self.config = {**self.DEFAULT_CONFIG, **(config or {})}
        self._config_loaded_at = None

    def _get_db_connection(self):
        """Get database connection to tradingSignals."""
        if self._get_db:
            return self._get_db()
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
        from env_config import get_env

        env_mappings = {
            'break_body_weight': ('BREAK_BODY_WEIGHT', float, 0.35),
            'break_close_weight': ('BREAK_CLOSE_WEIGHT', float, 0.30),
            'break_volume_weight': ('BREAK_VOLUME_WEIGHT', float, 0.20),
            'break_followthrough_weight': ('BREAK_FOLLOWTHROUGH_WEIGHT', float, 0.15),
            'break_min_body_pct': ('BREAK_MIN_BODY_PCT', float, 0.30),
            'break_volume_threshold': ('BREAK_VOLUME_THRESHOLD', float, 1.2),
            'break_zone_touch_bars': ('BREAK_ZONE_TOUCH_BARS', int, 3),
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
        logger.debug(f"Break Detector config loaded: {len(self.config)} keys")

    def detect_break(self, instrument: str, candle: Dict, levels: List[Dict],
                    volume_ratio: float = 1.0) -> BreakResult:
        """
        Detect if current candle breaks any level and score the quality.

        Args:
            instrument: Trading instrument
            candle: Current candle dict with open, high, low, close
            levels: List of active level dicts from Level Engine
            volume_ratio: Current volume ratio (volume / avg)

        Returns:
            BreakResult with detection and quality details
        """
        # Load config if stale
        if self._config_loaded_at is None or (datetime.utcnow() - self._config_loaded_at).seconds > 300:
            self._load_config_from_env()

        if not levels or not candle:
            return self._no_break_result()

        open_price = float(candle.get('open') or 0)
        high = float(candle.get('high') or 0)
        low = float(candle.get('low') or 0)
        close = float(candle.get('close') or 0)

        if not all([open_price, high, low, close]):
            return self._no_break_result()

        # Check each level for breaks
        best_break = None
        best_score = 0

        for level in levels:
            level_price = float(level.get('level_price') or 0)
            zone_upper = float(level.get('zone_upper') or level_price)
            zone_lower = float(level.get('zone_lower') or level_price)
            level_type = level.get('level_type', '')
            level_id = level.get('id', 0)

            # Determine if this is a resistance or support level
            is_resistance = 'HIGH' in level_type or level_type == 'PDH'
            is_support = 'LOW' in level_type or level_type == 'PDL'

            # Check for break
            break_result = self._check_level_break(
                open_price, high, low, close,
                level_price, zone_upper, zone_lower,
                is_resistance, is_support,
                level_id, level_type, volume_ratio
            )

            if break_result and break_result.quality_score > best_score:
                best_break = break_result
                best_score = break_result.quality_score

        return best_break if best_break else self._no_break_result()

    def _check_level_break(self, open_price: float, high: float, low: float, close: float,
                           level_price: float, zone_upper: float, zone_lower: float,
                           is_resistance: bool, is_support: bool,
                           level_id: int, level_type: str,
                           volume_ratio: float) -> Optional[BreakResult]:
        """Check if candle breaks through a specific level."""

        # Bullish break (resistance)
        if is_resistance:
            # Check if candle moved from below to above
            if open_price < zone_lower and high > level_price:
                direction = 'long'
                return self._score_break(
                    direction, open_price, high, low, close,
                    level_price, zone_upper, level_id, level_type,
                    volume_ratio, break_up=True
                )

        # Bearish break (support)
        if is_support:
            # Check if candle moved from above to below
            if open_price > zone_upper and low < level_price:
                direction = 'short'
                return self._score_break(
                    direction, open_price, high, low, close,
                    level_price, zone_lower, level_id, level_type,
                    volume_ratio, break_up=False
                )

        return None

    def _score_break(self, direction: str, open_price: float, high: float,
                     low: float, close: float, level_price: float,
                     zone_boundary: float, level_id: int, level_type: str,
                     volume_ratio: float, break_up: bool) -> BreakResult:
        """Score the quality of a detected break."""

        candle_range = high - low
        if candle_range <= 0:
            return self._no_break_result()

        # Determine body
        body_top = max(open_price, close)
        body_bottom = min(open_price, close)
        body_size = body_top - body_bottom

        # 1. Body through level percentage
        if break_up:
            # For bullish break, measure body above level
            body_through = max(0, body_top - level_price)
            is_wick_only = body_top <= level_price  # Body didn't cross
        else:
            # For bearish break, measure body below level
            body_through = max(0, level_price - body_bottom)
            is_wick_only = body_bottom >= level_price  # Body didn't cross

        body_through_pct = (body_through / body_size) if body_size > 0 else 0

        # Score: 0 if no body through, 1 if full body through
        body_score = min(1.0, body_through_pct / 0.5)  # Cap at 50% body through = 1.0

        # 2. Close beyond level
        if break_up:
            close_beyond = close > level_price
            close_distance = (close - level_price) / candle_range if candle_range > 0 else 0
        else:
            close_beyond = close < level_price
            close_distance = (level_price - close) / candle_range if candle_range > 0 else 0

        close_score = min(1.0, close_distance * 2) if close_beyond else 0

        # 3. Volume confirmation
        volume_threshold = self.config.get('break_volume_threshold', 1.2)
        volume_confirmed = volume_ratio >= volume_threshold
        volume_score = min(1.0, volume_ratio / volume_threshold) if volume_ratio > 0 else 0.5

        # 4. Follow-through (for now, based on candle characteristics)
        # Strong candle = close near extreme
        if break_up:
            close_position = (close - low) / candle_range if candle_range > 0 else 0.5
        else:
            close_position = (high - close) / candle_range if candle_range > 0 else 0.5

        followthrough_score = close_position

        # Weighted quality score
        weights = {
            'body': self.config.get('break_body_weight', 0.35),
            'close': self.config.get('break_close_weight', 0.30),
            'volume': self.config.get('break_volume_weight', 0.20),
            'followthrough': self.config.get('break_followthrough_weight', 0.15),
        }

        quality_score = (
            body_score * weights['body'] +
            close_score * weights['close'] +
            volume_score * weights['volume'] +
            followthrough_score * weights['followthrough']
        )

        # Penalize wick-only breaks heavily
        if is_wick_only:
            quality_score *= 0.3

        return BreakResult(
            break_detected=True,
            direction=direction,
            quality_score=round(quality_score, 3),
            level_id=level_id,
            level_type=level_type,
            level_price=level_price,
            is_wick_only=is_wick_only,
            body_through_pct=round(body_through_pct, 3),
            close_beyond=close_beyond,
            volume_confirmed=volume_confirmed,
            components={
                'body_score': round(body_score, 3),
                'close_score': round(close_score, 3),
                'volume_score': round(volume_score, 3),
                'followthrough_score': round(followthrough_score, 3),
                'weights': weights,
            }
        )

    def _no_break_result(self) -> BreakResult:
        """Return a result indicating no break detected."""
        return BreakResult(
            break_detected=False,
            direction='none',
            quality_score=0.0,
            level_id=0,
            level_type='',
            level_price=0.0,
            is_wick_only=False,
            body_through_pct=0.0,
            close_beyond=False,
            volume_confirmed=False,
            components={}
        )

    def get_break_for_signal(self, instrument: str, candle: Dict,
                             volume_ratio: float = 1.0) -> Tuple[float, float, int]:
        """
        Convenience method for signal builder.

        Returns break quality scores for both directions.

        Args:
            instrument: Trading instrument
            candle: Current candle dict
            volume_ratio: Current volume ratio

        Returns:
            Tuple of (break_quality_long, break_quality_short, break_level_id)
        """
        # Get active levels
        conn = self._get_db_connection()
        cursor = conn.cursor()

        # WO-TRADING-SIGNALS-UTC-SWEEP-0001 — hermes_levels.valid_until is UTC.
        # SQL NOW() returns server-local; in BST, levels expire 1h early.
        now_utc = datetime.now(timezone.utc)
        try:
            cursor.execute("""
                SELECT id, level_type, level_price, zone_upper, zone_lower
                FROM hermes_levels
                WHERE instrument = %s
                  AND is_active = TRUE
                  AND (valid_until IS NULL OR valid_until > %s)
            """, (instrument, now_utc))

            levels = cursor.fetchall()

        finally:
            cursor.close()
            conn.close()

        if not levels:
            return (0.0, 0.0, 0)

        result = self.detect_break(instrument, candle, list(levels), volume_ratio)

        if result.break_detected:
            if result.direction == 'long':
                return (result.quality_score, 0.0, result.level_id)
            elif result.direction == 'short':
                return (0.0, result.quality_score, result.level_id)

        return (0.0, 0.0, 0)


# CLI for testing
if __name__ == '__main__':
    import argparse

    logging.basicConfig(level=logging.INFO,
                       format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    parser = argparse.ArgumentParser(description='HERMES Break Quality Detector')
    parser.add_argument('--instrument', default='XAU_USD', help='Instrument to analyze')
    parser.add_argument('--test', action='store_true', help='Run test detection')

    args = parser.parse_args()

    detector = BreakDetector()

    if args.test:
        # Get latest candle and levels for testing
        conn = detector._get_db_connection()
        cursor = conn.cursor()

        # Get latest candle
        cursor.execute("""
            SELECT open, high, low, close
            FROM candles_M15
            WHERE instrument = %s
            ORDER BY timestamp DESC
            LIMIT 1
        """, (args.instrument,))
        candle = cursor.fetchone()

        # Get active levels
        cursor.execute("""
            SELECT id, level_type, level_price, zone_upper, zone_lower
            FROM hermes_levels
            WHERE instrument = %s AND is_active = TRUE
        """, (args.instrument,))
        levels = cursor.fetchall()

        cursor.close()
        conn.close()

        if candle and levels:
            print(f"\nBreak Detection for {args.instrument}:")
            print(f"  Candle: O={candle['open']} H={candle['high']} L={candle['low']} C={candle['close']}")
            print(f"  Levels: {len(levels)} active")
            for lvl in levels:
                print(f"    {lvl['level_type']}: {lvl['level_price']}")

            result = detector.detect_break(args.instrument, candle, list(levels), 1.0)
            print(f"\n  Break Detected: {result.break_detected}")
            if result.break_detected:
                print(f"  Direction: {result.direction}")
                print(f"  Quality Score: {result.quality_score}")
                print(f"  Level: {result.level_type} @ {result.level_price}")
                print(f"  Wick Only: {result.is_wick_only}")
                print(f"  Body Through: {result.body_through_pct:.1%}")
                print(f"  Close Beyond: {result.close_beyond}")
                print(f"  Volume Confirmed: {result.volume_confirmed}")
                print(f"  Components: {result.components}")
        else:
            print(f"No candle or levels found for {args.instrument}")
