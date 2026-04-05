#!/usr/bin/env python3
"""
HERMES Regime Detector - Data-Driven Market Regime Classification
EPIC-D026: HERMES World-Class SSOT Upgrade (ADR-077)

Classifies market regime based on ADX, ATR, Bollinger Bands, RSI, and EMA indicators.
Rules are loaded from regime_classifications table - no hardcoded logic.

Regime Types:
- BULL_TREND: Strong uptrend with bullish EMA alignment
- BEAR_TREND: Strong downtrend with bearish EMA alignment
- RANGING: Sideways market with cycling RSI and flat EMAs
- TRANSITION: Market between regimes, breakout imminent
- HIGH_VOLATILITY: Elevated ATR/BB width
- LOW_VOLATILITY: Compressed ATR/BB squeeze
- SAFE_FALLBACK: Default when confidence too low

This module is used by SignalComputer to determine regime for each signal.
"""

import json
import logging
from decimal import Decimal
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass

logger = logging.getLogger("signal-service.regime")


@dataclass
class RegimeResult:
    """Result of regime classification."""
    regime_id: str
    confidence: float
    indicators: Dict[str, Any]
    matched_rules: List[str]


class RegimeDetector:
    """
    Data-driven market regime detection.

    Loads detection rules from database and evaluates against indicators.
    All indicator calculation is done by SignalComputer - this only classifies.
    """

    # Default config (used if DB config not available)
    DEFAULT_CONFIG = {
        'rsi_cycle_lookback_bars': 20,
        'rsi_cycle_min_crosses': 2,
        'rsi_ranging_min': 30,
        'rsi_ranging_max': 70,
        'ema_flat_threshold_pct': 0.5,
        'atr_stable_variance_max': 20,
        'adx_rising_threshold': 2,
        'adx_falling_threshold': 2,
        'ema_compression_threshold_pct': 1.0,
        'confidence_fallback_pct': 50,
    }

    def __init__(self, db_connection_func=None):
        """
        Initialize detector.

        Args:
            db_connection_func: Optional function that returns a DB connection.
                               If not provided, rules must be passed to classify().
        """
        self._get_connection = db_connection_func
        self.config = self.DEFAULT_CONFIG.copy()
        self._regime_rules_cache = None
        self._config_cache = None

        # Load config if DB available
        if self._get_connection:
            self._load_config()

    def _load_config(self):
        """Load regime config from database."""
        if not self._get_connection:
            return

        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT config_key, config_value, value_type
                FROM regime_config
            """)
            for row in cursor.fetchall():
                key = row['config_key']
                val = row['config_value']
                val_type = row.get('value_type', 'decimal')

                if val_type == 'integer':
                    self.config[key] = int(val)
                elif val_type == 'boolean':
                    self.config[key] = val.lower() in ('true', '1', 'yes')
                else:
                    self.config[key] = float(val)
            cursor.close()
            self._config_cache = self.config.copy()
            logger.info(f"Loaded {len(self.config)} regime config values")
        except Exception as e:
            logger.warning(f"Could not load regime config, using defaults: {e}")

    def _load_regime_rules(self) -> List[Dict]:
        """Load regime detection rules from database."""
        if self._regime_rules_cache:
            return self._regime_rules_cache

        if not self._get_connection:
            return []

        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT regime_id, detection_rules, priority
                FROM regime_classifications
                WHERE status = 'active'
                ORDER BY priority ASC
            """)
            self._regime_rules_cache = cursor.fetchall()
            cursor.close()
            return self._regime_rules_cache
        except Exception as e:
            logger.warning(f"Could not load regime rules: {e}")
            return []

    # =========================================================================
    # Detection Helpers - Work on signal history
    # =========================================================================

    def detect_rsi_cycling(self, rsi_history: List[float]) -> Tuple[bool, int]:
        """
        Detect if RSI is cycling (crossing 50) indicating range-bound behavior.

        Args:
            rsi_history: List of recent RSI values (newest first)

        Returns: (is_cycling, cross_count)
        """
        lookback = int(self.config.get('rsi_cycle_lookback_bars', 20))
        min_crosses = int(self.config.get('rsi_cycle_min_crosses', 2))

        rsi_values = rsi_history[:lookback]
        if len(rsi_values) < 3:
            return False, 0

        # Count crosses of RSI 50
        cross_count = 0
        for i in range(len(rsi_values) - 1):
            if (rsi_values[i] > 50 and rsi_values[i+1] <= 50) or \
               (rsi_values[i] < 50 and rsi_values[i+1] >= 50):
                cross_count += 1

        return cross_count >= min_crosses, cross_count

    def detect_rsi_in_range(self, rsi_history: List[float]) -> Tuple[bool, float, float]:
        """
        Check if RSI is staying within ranging bounds (not overbought/oversold).

        Returns: (in_range, min_rsi, max_rsi)
        """
        lookback = int(self.config.get('rsi_cycle_lookback_bars', 20))
        rsi_min_threshold = int(self.config.get('rsi_ranging_min', 30))
        rsi_max_threshold = int(self.config.get('rsi_ranging_max', 70))

        rsi_values = rsi_history[:lookback]
        if not rsi_values:
            return True, 50.0, 50.0

        min_rsi = min(rsi_values)
        max_rsi = max(rsi_values)

        in_range = min_rsi >= rsi_min_threshold and max_rsi <= rsi_max_threshold
        return in_range, min_rsi, max_rsi

    def detect_ema_flat(self, ema_spreads: List[float]) -> Tuple[bool, float]:
        """
        Detect if EMA spread is flat (not expanding or compressing).

        Args:
            ema_spreads: List of EMA spread percentages (newest first)

        Returns: (is_flat, spread_change)
        """
        threshold = float(self.config.get('ema_flat_threshold_pct', 0.5))

        if len(ema_spreads) < 3:
            return True, 0.0

        # Compare recent spread to older spread
        recent_avg = sum(ema_spreads[:3]) / 3
        older_avg = sum(ema_spreads[3:6]) / 3 if len(ema_spreads) >= 6 else recent_avg

        spread_change = abs(recent_avg - older_avg)
        is_flat = spread_change < threshold

        return is_flat, spread_change

    def detect_ema_compression(self, ema_spreads: List[float]) -> Tuple[bool, float]:
        """
        Detect if EMAs are compressing (converging toward crossover).

        Returns: (is_compressing, compression_rate)
        """
        threshold = float(self.config.get('ema_compression_threshold_pct', 1.0))

        if len(ema_spreads) < 6:
            return False, 0.0

        recent_avg = sum(ema_spreads[:3]) / 3
        older_avg = sum(ema_spreads[3:6]) / 3

        compression_rate = older_avg - recent_avg  # Positive = compressing
        is_compressing = compression_rate > threshold

        return is_compressing, compression_rate

    def detect_atr_stable(self, atr_history: List[float]) -> Tuple[bool, float]:
        """
        Detect if ATR is stable (low variance) indicating calm market.

        Returns: (is_stable, variance_pct)
        """
        max_variance = int(self.config.get('atr_stable_variance_max', 20))

        if len(atr_history) < 5:
            return True, 0.0

        avg_atr = sum(atr_history) / len(atr_history)
        if avg_atr == 0:
            return True, 0.0

        variance = sum((v - avg_atr) ** 2 for v in atr_history) / len(atr_history)
        std_dev = variance ** 0.5
        variance_pct = (std_dev / avg_atr) * 100

        return variance_pct < max_variance, variance_pct

    def detect_adx_trajectory(self, adx_history: List[float]) -> Tuple[str, float]:
        """
        Detect ADX trajectory: rising, falling, or stable.

        Returns: (trajectory, change_amount)
        """
        rising_threshold = int(self.config.get('adx_rising_threshold', 2))
        falling_threshold = int(self.config.get('adx_falling_threshold', 2))

        if len(adx_history) < 4:
            return 'stable', 0.0

        recent_avg = sum(adx_history[:3]) / 3
        older_avg = sum(adx_history[3:6]) / 3 if len(adx_history) >= 6 else recent_avg

        change = recent_avg - older_avg

        if change > rising_threshold:
            return 'rising', change
        elif change < -falling_threshold:
            return 'falling', abs(change)
        else:
            return 'stable', abs(change)

    def detect_rsi_breakout(self, rsi_history: List[float]) -> Tuple[bool, str]:
        """
        Detect if RSI just broke out of ranging bounds.

        Returns: (has_breakout, direction)
        """
        rsi_min = int(self.config.get('rsi_ranging_min', 30))
        rsi_max = int(self.config.get('rsi_ranging_max', 70))

        if len(rsi_history) < 2:
            return False, 'none'

        current_rsi = rsi_history[0]
        prev_rsi = rsi_history[1]

        was_in_range = rsi_min <= prev_rsi <= rsi_max

        if was_in_range:
            if current_rsi > rsi_max:
                return True, 'bullish'
            elif current_rsi < rsi_min:
                return True, 'bearish'

        return False, 'none'

    # =========================================================================
    # Rule Evaluation
    # =========================================================================

    def _evaluate_rule(self, rule_key: str, rule_value, indicators: Dict) -> bool:
        """
        Evaluate a single detection rule against current indicators.

        Supports: min/max thresholds, boolean flags, state matches.
        """
        # Skip description fields
        if rule_key in ('description', 'desc'):
            return True

        # Threshold rules (adx_min, adx_max, atr_percentile_min, etc.)
        if rule_key.endswith('_min'):
            indicator_name = rule_key[:-4]
            indicator_val = indicators.get(indicator_name)
            if indicator_val is None:
                return False
            return float(indicator_val) >= float(rule_value)

        if rule_key.endswith('_max'):
            indicator_name = rule_key[:-4]
            indicator_val = indicators.get(indicator_name)
            if indicator_val is None:
                return False
            return float(indicator_val) <= float(rule_value)

        # State match rules
        if rule_key == 'ema_state':
            return indicators.get('ema_state') == rule_value

        if rule_key == 'adx_rising':
            trajectory = indicators.get('adx_trajectory', 'stable')
            return (trajectory == 'rising') if rule_value else (trajectory != 'rising')

        if rule_key == 'adx_falling':
            trajectory = indicators.get('adx_trajectory', 'stable')
            return (trajectory == 'falling') if rule_value else (trajectory != 'falling')

        # Boolean flags
        bool_keys = ['bb_squeeze', 'ema_flat', 'ema_compression', 'rsi_cycling',
                     'rsi_in_range', 'atr_stable', 'rsi_breakout']
        if rule_key in bool_keys:
            indicator_val = indicators.get(rule_key, False)
            if isinstance(rule_value, bool):
                return indicator_val == rule_value
            return indicator_val == (str(rule_value).lower() == 'true')

        # EMA momentum check
        if rule_key == 'ema_momentum':
            if indicators.get('ema_compressing'):
                return 'compressing' in rule_value
            elif indicators.get('ema_flat'):
                return 'stable' in rule_value
            else:
                return 'expanding' in rule_value

        # RSI cycles minimum
        if rule_key == 'rsi_cycles_min':
            return indicators.get('rsi_cross_count', 0) >= int(rule_value)

        # Confidence threshold
        if rule_key == 'confidence_below':
            return True  # Handled separately

        # Regime change detected
        if rule_key == 'regime_change_detected':
            return bool(indicators.get('transition_signals'))

        # ADX was above (for detecting trend fading)
        if rule_key == 'adx_was_above':
            adx_change = indicators.get('adx_change', 0)
            adx = indicators.get('adx', 20)
            return adx + adx_change > float(rule_value)

        # Trend consistency
        if rule_key == 'bullish_bars_min':
            return indicators.get('bullish_bars', 0) >= int(rule_value)

        if rule_key == 'bearish_bars_min':
            return indicators.get('bearish_bars', 0) >= int(rule_value)

        # Always match - for fallback regimes
        if rule_key == 'always_match':
            return bool(rule_value)

        # Unknown rule - pass
        logger.debug(f"Unknown rule key: {rule_key}")
        return True

    def _evaluate_rules(self, rules: Dict, indicators: Dict) -> Tuple[bool, int, List[str]]:
        """
        Evaluate all rules for a regime against indicators.

        Supports 'any_of' for OR conditions.
        Returns: (matches, match_count, matched_rules)
        """
        if not rules:
            return False, 0, []

        # Handle 'any_of' - OR logic
        if 'any_of' in rules:
            for condition_set in rules['any_of']:
                all_match = True
                matched = []
                for rule_key, rule_value in condition_set.items():
                    if self._evaluate_rule(rule_key, rule_value, indicators):
                        matched.append(rule_key)
                    else:
                        all_match = False
                        break
                if all_match:
                    return True, len(matched), matched
            return False, 0, []

        # Standard AND logic
        matched_rules = []
        for rule_key, rule_value in rules.items():
            if not self._evaluate_rule(rule_key, rule_value, indicators):
                return False, len(matched_rules), matched_rules
            matched_rules.append(rule_key)

        return True, len(matched_rules), matched_rules

    # =========================================================================
    # Main Classification
    # =========================================================================

    def build_indicators(self, current: Dict, history: List[Dict] = None) -> Dict:
        """
        Build comprehensive indicators dict from current signal and history.

        Args:
            current: Current signal with indicators
            history: Optional list of recent signals (newest first)

        Returns: Dict of all indicators for rule evaluation
        """
        history = history or []

        # Extract current values
        adx = float(current.get('adx_14', 20))
        plus_di = float(current.get('plus_di', 20))
        minus_di = float(current.get('minus_di', 20))
        rsi = float(current.get('rsi_14', 50))
        atr = float(current.get('atr_14', 0))
        atr_baseline = float(current.get('atr_baseline', atr))
        bb_width = float(current.get('bb_width_pct', 5))
        bb_squeeze = current.get('bb_squeeze', False)

        # Calculate ATR percentile
        atr_percentile = (atr / atr_baseline * 100) if atr_baseline > 0 else 100

        # Determine EMA state from current signal
        ema_9_21 = current.get('ema_9_21_state', 'cross')
        ema_50_200 = current.get('ema_50_200_state', 'cross')

        if ema_50_200 == 'above':
            ema_state = 'bullish'
        elif ema_50_200 == 'below':
            ema_state = 'bearish'
        else:
            ema_state = 'neutral'

        # Calculate EMA spread
        ema_20 = float(current.get('ema_20', 0))
        ema_50 = float(current.get('ema_50', 0))
        ema_spread_pct = abs(ema_20 - ema_50) / ema_50 * 100 if ema_50 > 0 else 0

        # Build history lists for detection helpers
        rsi_history = [rsi] + [float(s.get('rsi_14', 50)) for s in history if s.get('rsi_14')]
        adx_history = [adx] + [float(s.get('adx_14', 20)) for s in history if s.get('adx_14')]
        atr_history = [atr] + [float(s.get('atr_14', 2)) for s in history if s.get('atr_14')]

        # Build EMA spread history
        ema_spreads = [ema_spread_pct]
        for s in history:
            e20 = float(s.get('ema_20', 0))
            e50 = float(s.get('ema_50', 0))
            if e50 > 0:
                ema_spreads.append(abs(e20 - e50) / e50 * 100)

        # Run detection helpers
        rsi_cycling, rsi_cross_count = self.detect_rsi_cycling(rsi_history)
        rsi_in_range, rsi_min, rsi_max = self.detect_rsi_in_range(rsi_history)
        ema_flat, ema_spread_change = self.detect_ema_flat(ema_spreads)
        ema_compressing, compression_rate = self.detect_ema_compression(ema_spreads)
        atr_stable, atr_variance = self.detect_atr_stable(atr_history)
        adx_trajectory, adx_change = self.detect_adx_trajectory(adx_history)
        rsi_breakout, breakout_dir = self.detect_rsi_breakout(rsi_history)

        # Count trend consistency from history
        bullish_count = sum(1 for s in history[:12] if s.get('ema_50_200_state') == 'above')
        bearish_count = sum(1 for s in history[:12] if s.get('ema_50_200_state') == 'below')

        # Build transition signals
        transition_signals = []
        if ema_compressing:
            transition_signals.append('ema_compression')
        if rsi_breakout:
            transition_signals.append(f'rsi_breakout_{breakout_dir}')
        if adx_trajectory == 'rising' and adx > 15:
            transition_signals.append('adx_rising')

        return {
            # Core indicators
            'adx': adx,
            'plus_di': plus_di,
            'minus_di': minus_di,
            'rsi': rsi,
            'atr': atr,
            'atr_percentile': atr_percentile,
            'bb_width_pct': bb_width,
            'bb_squeeze': bb_squeeze,
            'ema_state': ema_state,
            'ema_spread_pct': ema_spread_pct,

            # Detection helper results
            'rsi_cycling': rsi_cycling,
            'rsi_cross_count': rsi_cross_count,
            'rsi_in_range': rsi_in_range,
            'rsi_range': [rsi_min, rsi_max],
            'ema_flat': ema_flat,
            'ema_spread_change': ema_spread_change,
            'ema_compressing': ema_compressing,
            'ema_compression': ema_compressing,  # Alias
            'compression_rate': compression_rate,
            'atr_stable': atr_stable,
            'atr_variance_pct': atr_variance,
            'adx_trajectory': adx_trajectory,
            'adx_change': adx_change,
            'rsi_breakout': rsi_breakout,
            'breakout_direction': breakout_dir,

            # Trend consistency
            'bullish_bars': bullish_count,
            'bearish_bars': bearish_count,
            'transition_signals': transition_signals,
        }

    def classify(self, indicators: Dict, regime_rules: List[Dict] = None) -> RegimeResult:
        """
        Classify market regime based on indicators.

        Args:
            indicators: Dict of all indicator values (from build_indicators)
            regime_rules: Optional list of regime rules. If not provided, loads from DB.

        Returns: RegimeResult with regime_id, confidence, indicators, matched_rules
        """
        # Get rules
        if regime_rules is None:
            regime_rules = self._load_regime_rules()

        if not regime_rules:
            # No rules - use simple fallback
            return self._simple_classify(indicators)

        # Evaluate each regime's rules in priority order
        for regime in regime_rules:
            regime_id = regime['regime_id']
            rules_json = regime['detection_rules']

            try:
                rules = json.loads(rules_json) if isinstance(rules_json, str) else rules_json
            except (json.JSONDecodeError, TypeError):
                logger.warning(f"Invalid detection_rules for {regime_id}")
                continue

            matches, match_count, matched_rules = self._evaluate_rules(rules, indicators)

            if matches:
                # Calculate confidence
                base_confidence = 60.0
                confidence_boost = min(match_count * 5, 35)
                confidence = min(95.0, base_confidence + confidence_boost)

                # Add ranging score for RANGING
                if regime_id == 'RANGING':
                    ranging_score = 0
                    if indicators.get('rsi_cycling'):
                        ranging_score += 25
                    if indicators.get('rsi_in_range'):
                        ranging_score += 25
                    if indicators.get('ema_flat'):
                        ranging_score += 25
                    if indicators.get('atr_stable'):
                        ranging_score += 25
                    indicators['ranging_score'] = ranging_score

                # Add transition reason
                if regime_id == 'TRANSITION' and indicators.get('transition_signals'):
                    indicators['transition_reason'] = ','.join(indicators['transition_signals'])

                indicators['matched_rules'] = matched_rules

                return RegimeResult(
                    regime_id=regime_id,
                    confidence=confidence,
                    indicators=indicators,
                    matched_rules=matched_rules
                )

        # No match - SAFE_FALLBACK
        return RegimeResult(
            regime_id='SAFE_FALLBACK',
            confidence=100.0,
            indicators=indicators,
            matched_rules=[]
        )

    def _simple_classify(self, indicators: Dict) -> RegimeResult:
        """
        Simple regime classification when no DB rules available.

        Uses hardcoded thresholds as fallback.
        """
        adx = indicators.get('adx', 20)
        ema_state = indicators.get('ema_state', 'neutral')
        atr_percentile = indicators.get('atr_percentile', 100)
        bb_squeeze = indicators.get('bb_squeeze', False)

        # Simple classification logic
        if adx > 25:
            if ema_state == 'bullish':
                regime = 'BULL_TREND'
            elif ema_state == 'bearish':
                regime = 'BEAR_TREND'
            else:
                regime = 'TRANSITION'
        elif adx < 20:
            if bb_squeeze or atr_percentile < 70:
                regime = 'LOW_VOLATILITY'
            else:
                regime = 'RANGING'
        elif atr_percentile > 150:
            regime = 'HIGH_VOLATILITY'
        else:
            regime = 'TRANSITION'

        return RegimeResult(
            regime_id=regime,
            confidence=70.0,
            indicators=indicators,
            matched_rules=['simple_fallback']
        )
