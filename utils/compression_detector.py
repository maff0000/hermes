#!/usr/bin/env python3
"""
HERMES Compression Detector
EPIC-D027 / STORY-D027-02: Phase 3 Trading Strategies

Detects market compression (coiling) before potential breakouts.
Compression score 0-1 based on multiple factors:
- ATR percentile (lower = more compressed)
- Range tightness (price range vs ATR)
- Bollinger Band squeeze (band width below threshold)
- EMA convergence (multiple EMAs close together)

GOV-ENV-001: Config from .env, no hardcoded DEV/PROD values.
"""

import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass

# Ensure parent directory is in path for imports
_this_dir = Path(__file__).parent
_parent_dir = _this_dir.parent
if str(_parent_dir) not in sys.path:
    sys.path.insert(0, str(_parent_dir))

logger = logging.getLogger("signal-service.compression_detector")


@dataclass
class CompressionResult:
    """Result of compression detection."""
    compression_score: float  # 0-1, higher = more compressed
    atr_pctl: float           # ATR percentile (0-100)
    range_score: float        # Range tightness score (0-1)
    bb_squeeze: bool          # Bollinger Band squeeze detected
    ema_converging: bool      # EMAs converging
    components: Dict          # Individual component values


class CompressionDetector:
    """
    Detects market compression using multiple indicators.

    Compression indicates potential energy building for a breakout.
    Higher compression score = more likely breakout incoming.
    """

    # Default configuration (overridden by .env)
    DEFAULT_CONFIG = {
        'compression_atr_lookback': 100,      # Bars to compute ATR percentile
        'compression_atr_weight': 0.30,       # Weight for ATR component
        'compression_range_weight': 0.25,     # Weight for range tightness
        'compression_bb_weight': 0.25,        # Weight for BB squeeze
        'compression_ema_weight': 0.20,       # Weight for EMA convergence
        'compression_bb_squeeze_pctl': 20,    # BB width percentile for squeeze
        'compression_ema_convergence_atr': 0.5,  # Max ATR distance for EMA convergence
        'compression_range_lookback': 10,     # Bars for range calculation
    }

    def __init__(self, db_connection_func=None, config: Dict = None):
        """
        Initialize Compression Detector.

        Args:
            db_connection_func: Function returning database connection
            config: Configuration dict (uses defaults + .env if not provided)
        """
        self._get_db = db_connection_func
        self.config = {**self.DEFAULT_CONFIG, **(config or {})}
        self._config_loaded_at = None
        self._atr_history: Dict[str, List[float]] = {}  # Cache for ATR percentile

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
            'compression_atr_lookback': ('COMPRESSION_ATR_LOOKBACK', int, 100),
            'compression_atr_weight': ('COMPRESSION_ATR_WEIGHT', float, 0.30),
            'compression_range_weight': ('COMPRESSION_RANGE_WEIGHT', float, 0.25),
            'compression_bb_weight': ('COMPRESSION_BB_WEIGHT', float, 0.25),
            'compression_ema_weight': ('COMPRESSION_EMA_WEIGHT', float, 0.20),
            'compression_bb_squeeze_pctl': ('COMPRESSION_BB_SQUEEZE_PCTL', int, 20),
            'compression_ema_convergence_atr': ('COMPRESSION_EMA_CONVERGENCE_ATR', float, 0.5),
            'compression_range_lookback': ('COMPRESSION_RANGE_LOOKBACK', int, 10),
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
        logger.debug(f"Compression Detector config loaded: {len(self.config)} keys")

    def compute_atr_percentile(self, instrument: str, current_atr: float) -> float:
        """
        Compute ATR percentile (where current ATR falls in historical distribution).

        Lower percentile = more compressed market.

        Args:
            instrument: Trading instrument
            current_atr: Current ATR value

        Returns:
            Percentile 0-100, lower = more compressed
        """
        # Use cached history if available
        if instrument not in self._atr_history or len(self._atr_history[instrument]) < 20:
            # Load historical ATR from database
            self._load_atr_history(instrument)

        history = self._atr_history.get(instrument, [])
        if not history or current_atr <= 0:
            return 50.0  # Default to median

        # Calculate percentile
        below_count = sum(1 for atr in history if atr <= current_atr)
        percentile = (below_count / len(history)) * 100

        return round(percentile, 1)

    def _load_atr_history(self, instrument: str):
        """Load historical ATR values from signals table."""
        lookback = self.config.get('compression_atr_lookback', 100)

        conn = self._get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT atr_14
                FROM signals
                WHERE instrument = %s AND atr_14 > 0
                ORDER BY timestamp DESC
                LIMIT %s
            """, (instrument, lookback))

            rows = cursor.fetchall()
            self._atr_history[instrument] = [float(r['atr_14']) for r in rows if r['atr_14']]

        finally:
            cursor.close()
            conn.close()

    def compute_range_tightness(self, instrument: str, current_atr: float) -> float:
        """
        Compute range tightness score.

        Compares recent price range to ATR - tighter range = higher score.

        Args:
            instrument: Trading instrument
            current_atr: Current ATR value

        Returns:
            Score 0-1, higher = tighter range (more compressed)
        """
        if current_atr <= 0:
            return 0.5

        lookback = self.config.get('compression_range_lookback', 10)

        conn = self._get_db_connection()
        cursor = conn.cursor()

        try:
            # Get recent candles and compute range
            cursor.execute("""
                SELECT MAX(high) as range_high, MIN(low) as range_low
                FROM (
                    SELECT high, low FROM candles_M15
                    WHERE instrument = %s
                    ORDER BY timestamp DESC
                    LIMIT %s
                ) recent
            """, (instrument, lookback))

            row = cursor.fetchone()
            if not row or row['range_high'] is None:
                return 0.5

            price_range = float(row['range_high']) - float(row['range_low'])

            # Range relative to ATR
            # Expected range over N bars would be approximately ATR * sqrt(N)
            expected_range = current_atr * (lookback ** 0.5)

            if expected_range <= 0:
                return 0.5

            # Ratio < 1 means tighter than expected
            ratio = price_range / expected_range

            # Convert to 0-1 score where 1 = very tight
            # ratio of 0.5 or less = score 1.0
            # ratio of 2.0 or more = score 0.0
            score = max(0, min(1, 1.0 - (ratio - 0.5) / 1.5))

            return round(score, 3)

        finally:
            cursor.close()
            conn.close()

    def compute_bb_squeeze(self, bb_width_pct: float, instrument: str = None) -> bool:
        """
        Detect Bollinger Band squeeze.

        Squeeze occurs when band width is in lowest percentile.

        Args:
            bb_width_pct: Current Bollinger Band width percentage
            instrument: Optional instrument for historical comparison

        Returns:
            True if squeeze detected
        """
        # Simple threshold check (from config)
        squeeze_threshold = self.config.get('compression_bb_squeeze_pctl', 20)

        # If we have instrument, compare to historical
        if instrument:
            conn = self._get_db_connection()
            cursor = conn.cursor()

            try:
                cursor.execute("""
                    SELECT bb_width_pct
                    FROM signals
                    WHERE instrument = %s AND bb_width_pct > 0
                    ORDER BY timestamp DESC
                    LIMIT 100
                """, (instrument,))

                rows = cursor.fetchall()
                if rows:
                    history = [float(r['bb_width_pct']) for r in rows if r['bb_width_pct']]
                    if history:
                        below_count = sum(1 for w in history if w <= bb_width_pct)
                        percentile = (below_count / len(history)) * 100
                        return percentile <= squeeze_threshold

            finally:
                cursor.close()
                conn.close()

        # Fallback: absolute threshold (typical squeeze below 2% width)
        return bb_width_pct < 2.0

    def compute_ema_convergence(self, ema_20: float, ema_50: float,
                                 ema_9: float, ema_21: float,
                                 atr: float) -> bool:
        """
        Detect EMA convergence (multiple EMAs close together).

        Args:
            ema_20: EMA 20 value
            ema_50: EMA 50 value
            ema_9: EMA 9 value
            ema_21: EMA 21 value
            atr: Current ATR

        Returns:
            True if EMAs are converging
        """
        if atr <= 0 or not all([ema_20, ema_50, ema_9, ema_21]):
            return False

        threshold_atr = self.config.get('compression_ema_convergence_atr', 0.5)
        threshold = atr * threshold_atr

        # Check if all EMAs are within threshold of each other
        emas = [ema_9, ema_20, ema_21, ema_50]
        ema_range = max(emas) - min(emas)

        return ema_range <= threshold

    def detect(self, instrument: str, signal_data: Dict) -> CompressionResult:
        """
        Detect compression from signal data.

        Args:
            instrument: Trading instrument
            signal_data: Dict with signal fields (atr_14, bb_width_pct, etc.)

        Returns:
            CompressionResult with score and components
        """
        # Load config if stale
        if self._config_loaded_at is None or (datetime.utcnow() - self._config_loaded_at).seconds > 300:
            self._load_config_from_env()

        atr = float(signal_data.get('atr_14') or 0)
        bb_width_pct = float(signal_data.get('bb_width_pct') or 0)

        # Compute components
        atr_pctl = self.compute_atr_percentile(instrument, atr)
        range_score = self.compute_range_tightness(instrument, atr)
        bb_squeeze = self.compute_bb_squeeze(bb_width_pct, instrument)
        ema_converging = self.compute_ema_convergence(
            float(signal_data.get('ema_20') or 0),
            float(signal_data.get('ema_50') or 0),
            float(signal_data.get('ema_9') or 0),
            float(signal_data.get('ema_21') or 0),
            atr
        )

        # Convert ATR percentile to score (invert: low percentile = high compression)
        atr_score = 1.0 - (atr_pctl / 100.0)

        # Compute weighted compression score
        weights = {
            'atr': self.config.get('compression_atr_weight', 0.30),
            'range': self.config.get('compression_range_weight', 0.25),
            'bb': self.config.get('compression_bb_weight', 0.25),
            'ema': self.config.get('compression_ema_weight', 0.20),
        }

        compression_score = (
            atr_score * weights['atr'] +
            range_score * weights['range'] +
            (1.0 if bb_squeeze else 0.0) * weights['bb'] +
            (1.0 if ema_converging else 0.0) * weights['ema']
        )

        return CompressionResult(
            compression_score=round(compression_score, 3),
            atr_pctl=atr_pctl,
            range_score=range_score,
            bb_squeeze=bb_squeeze,
            ema_converging=ema_converging,
            components={
                'atr_score': round(atr_score, 3),
                'range_score': round(range_score, 3),
                'bb_squeeze_score': 1.0 if bb_squeeze else 0.0,
                'ema_convergence_score': 1.0 if ema_converging else 0.0,
                'weights': weights,
            }
        )

    def update_atr_cache(self, instrument: str, atr: float):
        """Update ATR cache with new value (called on each signal)."""
        if instrument not in self._atr_history:
            self._atr_history[instrument] = []

        self._atr_history[instrument].insert(0, atr)

        # Keep limited history
        lookback = self.config.get('compression_atr_lookback', 100)
        if len(self._atr_history[instrument]) > lookback:
            self._atr_history[instrument] = self._atr_history[instrument][:lookback]


# CLI for testing
if __name__ == '__main__':
    import argparse

    logging.basicConfig(level=logging.INFO,
                       format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    parser = argparse.ArgumentParser(description='HERMES Compression Detector')
    parser.add_argument('--instrument', default='XAU_USD', help='Instrument to analyze')
    parser.add_argument('--test', action='store_true', help='Run test detection')

    args = parser.parse_args()

    detector = CompressionDetector()

    if args.test:
        # Get latest signal data for testing
        conn = detector._get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT atr_14, bb_width_pct, ema_9, ema_20, ema_21, ema_50
            FROM signals
            WHERE instrument = %s
            ORDER BY timestamp DESC
            LIMIT 1
        """, (args.instrument,))

        row = cursor.fetchone()
        cursor.close()
        conn.close()

        if row:
            result = detector.detect(args.instrument, row)
            print(f"\nCompression Detection for {args.instrument}:")
            print(f"  Compression Score: {result.compression_score}")
            print(f"  ATR Percentile: {result.atr_pctl}%")
            print(f"  Range Tightness: {result.range_score}")
            print(f"  BB Squeeze: {result.bb_squeeze}")
            print(f"  EMA Converging: {result.ema_converging}")
            print(f"  Components: {result.components}")
        else:
            print(f"No signal data found for {args.instrument}")
