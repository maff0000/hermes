#!/usr/bin/env python3
"""
Volume Analyzer - Signal Layer Enhancement
EPIC-115: TCS2 Two-Phase Architecture

Detects Distribution/Accumulation patterns from volume and price action.
A world-class trader watches for:
- High volume on down moves = Distribution (smart money selling)
- High volume on up moves = Accumulation (smart money buying)
- Consecutive distribution candles = Bearish regime override

This module enriches signals with volume intelligence BEFORE they reach the kernel.

Usage:
    from volume_analyzer import VolumeAnalyzer

    analyzer = VolumeAnalyzer()
    vol_signal = analyzer.analyze_candle(price_change, volume_ratio)
    vol_regime = analyzer.get_volume_regime(history)
"""

import os
import logging
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)

# Debug mode
DEBUG_VOLUME = os.environ.get('DEBUG_VOLUME', 'false').lower() == 'true'


def debug_print(msg: str):
    """Print debug message if debug mode enabled."""
    if DEBUG_VOLUME:
        print(f"[VOLUME DEBUG] {msg}")


class VolumeSignal(Enum):
    """Per-candle volume signal."""
    DISTRIBUTION = "DISTRIBUTION"   # High volume + price drop = selling
    ACCUMULATION = "ACCUMULATION"   # High volume + price rise = buying
    NEUTRAL = "NEUTRAL"             # Normal volume or no clear signal


class VolumeRegime(Enum):
    """Rolling volume regime based on recent patterns."""
    HEAVY_DISTRIBUTION = "HEAVY_DISTRIBUTION"   # 3+ recent distribution candles
    HEAVY_ACCUMULATION = "HEAVY_ACCUMULATION"   # 3+ recent accumulation candles
    MIXED = "MIXED"                             # No clear pattern


@dataclass
class VolumeAnalysis:
    """Complete volume analysis for a signal."""
    signal: VolumeSignal           # This candle's signal
    regime: VolumeRegime           # Rolling regime
    dist_count: int                # Distribution candles in lookback
    accum_count: int               # Accumulation candles in lookback
    consecutive_dist: int          # Consecutive distribution streak
    consecutive_accum: int         # Consecutive accumulation streak
    confidence: float              # Confidence in regime (0-100)


class VolumeAnalyzer:
    """
    Analyzes volume patterns to detect smart money activity.

    Configuration (from trading_config or defaults):
    - volume_ratio_threshold: 1.5 (150% of average = significant)
    - price_change_threshold: 2.0 (minimum $ move to count)
    - lookback_candles: 6 (30 min at M5)
    - consecutive_threshold: 3 (3 in a row = regime override)
    """

    DEFAULT_CONFIG = {
        'volume_ratio_threshold': 1.5,    # Volume must be 1.5x average
        'price_change_threshold': 2.0,    # Minimum $2 move
        'lookback_candles': 6,            # Look back 6 candles (30 min)
        'consecutive_threshold': 3,       # 3 consecutive = regime override
        'regime_count_threshold': 3,      # 3 out of 6 = regime signal
    }

    def __init__(self, config: Dict = None):
        """Initialize with optional config override."""
        self.config = {**self.DEFAULT_CONFIG, **(config or {})}
        debug_print(f"VolumeAnalyzer initialized: {self.config}")

    def analyze_candle(self, price_change: float, volume_ratio: float) -> VolumeSignal:
        """
        Analyze a single candle for distribution/accumulation.

        Args:
            price_change: Price change from previous candle ($)
            volume_ratio: Volume ratio vs average (1.0 = average)

        Returns:
            VolumeSignal (DISTRIBUTION, ACCUMULATION, or NEUTRAL)
        """
        vol_threshold = self.config['volume_ratio_threshold']
        price_threshold = self.config['price_change_threshold']

        # Need high volume to count
        if volume_ratio < vol_threshold:
            return VolumeSignal.NEUTRAL

        # Check price direction with threshold
        if price_change <= -price_threshold:
            debug_print(f"DISTRIBUTION: price={price_change:+.2f}, vol_ratio={volume_ratio:.2f}")
            return VolumeSignal.DISTRIBUTION
        elif price_change >= price_threshold:
            debug_print(f"ACCUMULATION: price={price_change:+.2f}, vol_ratio={volume_ratio:.2f}")
            return VolumeSignal.ACCUMULATION

        return VolumeSignal.NEUTRAL

    def analyze_history(self, history: List[Dict]) -> VolumeAnalysis:
        """
        Analyze recent candle history for volume regime.

        Args:
            history: List of candle dicts with 'price_change' and 'volume_ratio'
                     Most recent candle LAST.

        Returns:
            VolumeAnalysis with signal, regime, and counts
        """
        lookback = self.config['lookback_candles']
        consec_threshold = self.config['consecutive_threshold']
        regime_threshold = self.config['regime_count_threshold']

        # Get recent candles
        recent = history[-lookback:] if len(history) >= lookback else history

        if not recent:
            return VolumeAnalysis(
                signal=VolumeSignal.NEUTRAL,
                regime=VolumeRegime.MIXED,
                dist_count=0,
                accum_count=0,
                consecutive_dist=0,
                consecutive_accum=0,
                confidence=0.0
            )

        # Analyze each candle
        signals = []
        for candle in recent:
            price_change = candle.get('price_change', 0)
            volume_ratio = candle.get('volume_ratio', 1.0)
            sig = self.analyze_candle(price_change, volume_ratio)
            signals.append(sig)

        # Count signals
        dist_count = sum(1 for s in signals if s == VolumeSignal.DISTRIBUTION)
        accum_count = sum(1 for s in signals if s == VolumeSignal.ACCUMULATION)

        # Current candle signal
        current_signal = signals[-1] if signals else VolumeSignal.NEUTRAL

        # Count consecutive (from end)
        consecutive_dist = 0
        consecutive_accum = 0

        for sig in reversed(signals):
            if sig == VolumeSignal.DISTRIBUTION:
                if consecutive_accum == 0:  # Only count if no accumulation yet
                    consecutive_dist += 1
                else:
                    break
            elif sig == VolumeSignal.ACCUMULATION:
                if consecutive_dist == 0:  # Only count if no distribution yet
                    consecutive_accum += 1
                else:
                    break
            else:
                break  # NEUTRAL breaks the streak

        # Determine regime
        if consecutive_dist >= consec_threshold or dist_count >= regime_threshold:
            regime = VolumeRegime.HEAVY_DISTRIBUTION
            confidence = min(100, (dist_count / lookback) * 100 + consecutive_dist * 10)
        elif consecutive_accum >= consec_threshold or accum_count >= regime_threshold:
            regime = VolumeRegime.HEAVY_ACCUMULATION
            confidence = min(100, (accum_count / lookback) * 100 + consecutive_accum * 10)
        else:
            regime = VolumeRegime.MIXED
            confidence = 50.0

        debug_print(f"Volume Regime: {regime.value} (dist={dist_count}, accum={accum_count}, "
                   f"consec_dist={consecutive_dist}, consec_accum={consecutive_accum}, conf={confidence:.0f}%)")

        return VolumeAnalysis(
            signal=current_signal,
            regime=regime,
            dist_count=dist_count,
            accum_count=accum_count,
            consecutive_dist=consecutive_dist,
            consecutive_accum=consecutive_accum,
            confidence=confidence
        )

    def should_override_regime(self, ema_regime: str, volume_analysis: VolumeAnalysis) -> tuple:
        """
        Determine if volume pattern should override EMA-based regime.

        Args:
            ema_regime: Current regime from EMA analysis (BULL_TREND, BEAR_TREND, etc.)
            volume_analysis: VolumeAnalysis from analyze_history()

        Returns:
            (should_override: bool, new_regime: str, reason: str)
        """
        vol_regime = volume_analysis.regime
        consec_dist = volume_analysis.consecutive_dist
        consec_accum = volume_analysis.consecutive_accum

        # HEAVY_DISTRIBUTION overrides bullish EMA
        if vol_regime == VolumeRegime.HEAVY_DISTRIBUTION:
            if ema_regime in ('BULL_TREND', 'TRANSITION'):
                reason = f"VOLUME OVERRIDE: {consec_dist} consecutive distribution candles"
                debug_print(f"{reason} - flipping {ema_regime} to BEAR_TREND")
                return True, 'BEAR_TREND', reason

        # HEAVY_ACCUMULATION overrides bearish EMA
        if vol_regime == VolumeRegime.HEAVY_ACCUMULATION:
            if ema_regime in ('BEAR_TREND', 'TRANSITION'):
                reason = f"VOLUME OVERRIDE: {consec_accum} consecutive accumulation candles"
                debug_print(f"{reason} - flipping {ema_regime} to BULL_TREND")
                return True, 'BULL_TREND', reason

        # No override needed
        return False, ema_regime, "Volume confirms EMA regime"


# Singleton instance
_volume_analyzer: Optional[VolumeAnalyzer] = None


def get_volume_analyzer(config: Dict = None) -> VolumeAnalyzer:
    """Get or create the singleton VolumeAnalyzer."""
    global _volume_analyzer
    if _volume_analyzer is None or config:
        _volume_analyzer = VolumeAnalyzer(config)
    return _volume_analyzer


def analyze_volume_regime(history: List[Dict], config: Dict = None) -> VolumeAnalysis:
    """
    Convenience function to analyze volume regime.

    Args:
        history: List of candle dicts with 'price_change' and 'volume_ratio'
        config: Optional config override

    Returns:
        VolumeAnalysis with full analysis
    """
    return get_volume_analyzer(config).analyze_history(history)
