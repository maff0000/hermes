"""
Signal Builder - Candle Aggregation and Indicator Computation
EPIC-D007: tradingSignals as Single Source of Truth
EPIC-D026: HERMES World-Class SSOT Upgrade (ADR-077)
GOV-ENV-001: Environment-aware configuration

Aggregates ticks into candles, computes ALL indicators Zeus needs.
tradingSignals computes. Zeus decides.

EPIC-D026 Additions:
- ADX (Average Directional Index) with +DI/-DI for trend strength
- Bollinger Bands with squeeze detection for volatility
- Regime confidence and indicator tracking

Components:
- CandleAggregator: Ticks -> M1/M5/M15/H1/D1 candles
- SignalComputer: Candles -> Complete signals with all indicators
- SignalPublisher: Signals -> DB + Redis
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from decimal import Decimal
import json

# Local imports (env_config loads automatically)
from env_config import get_db_config, get_redis_config
from utils.indicators import (
    calculate_rsi, calculate_ema, get_ema_state,
    calculate_adx, calculate_bollinger_bands, ADXResult, BollingerBandsResult
)
from utils.atr_calculator import calculate_atr
from utils.regime_detector import RegimeDetector, RegimeResult
from utils.level_engine import LevelEngine
from utils.compression_detector import CompressionDetector
from utils.break_detector import BreakDetector

logger = logging.getLogger("signal-service.builder")

# EMA periods to compute
EMA_PERIODS = [9, 12, 20, 21, 26, 50, 200]

# EMA pairs for state computation
EMA_PAIRS = [
    (9, 21),    # Fast scalping
    (12, 26),   # MACD-style
    (20, 50),   # Swing trading
    (50, 200),  # Long-term trend
]

# Timeframe configurations
TIMEFRAMES = {
    'M1': {'seconds': 60, 'table': 'candles_M1'},
    'M5': {'seconds': 300, 'table': 'candles_M5'},
    'M15': {'seconds': 900, 'table': 'candles_M15'},
    'H1': {'seconds': 3600, 'table': 'candles_H1'},
    'D1': {'seconds': 86400, 'table': 'candles_D1'},
}


@dataclass
class Candle:
    """OHLCV candle data."""
    instrument: str
    timestamp: datetime
    timeframe: str
    open: float
    high: float
    low: float
    close: float
    volume: int = 0
    complete: bool = False

    def to_dict(self) -> dict:
        return {
            'instrument': self.instrument,
            'timestamp': self.timestamp.isoformat(),
            'timeframe': self.timeframe,
            'open': self.open,
            'high': self.high,
            'low': self.low,
            'close': self.close,
            'volume': self.volume,
            'complete': self.complete,
        }


@dataclass
class Signal:
    """Complete signal with all indicators."""
    instrument: str
    timestamp: datetime
    timeframe: str
    price_open: float      # HERMES OHLC for Tyche SL/TP checking
    price_high: float      # HERMES OHLC for Tyche SL/TP checking
    price_low: float       # HERMES OHLC for Tyche SL/TP checking
    price_close: float
    volume: int
    volume_ratio: float
    rsi_14: float
    ema_9: float
    ema_21: float
    ema_12: float
    ema_26: float
    ema_20: float
    ema_50: float
    ema_200: float
    ema_9_21_state: str
    ema_12_26_state: str
    ema_20_50_state: str
    ema_50_200_state: str
    atr_14: float
    regime: str
    session: str
    version: int = 1
    # STORY-D002-09: Day State classification support
    atr_baseline: float = 0.0      # 20-bar ATR baseline
    atr_opening_shock: float = 0.0  # Opening 30min ATR shock ratio
    atr_day_ratio: float = 0.0      # Current day range vs baseline
    # EPIC-D026: HERMES SSOT indicators
    adx_14: float = 0.0            # Average Directional Index
    plus_di: float = 0.0           # +DI component
    minus_di: float = 0.0          # -DI component
    bb_middle: float = 0.0         # Bollinger middle band (20 SMA)
    bb_upper: float = 0.0          # Bollinger upper band
    bb_lower: float = 0.0          # Bollinger lower band
    bb_width_pct: float = 0.0      # Band width as percentage
    bb_squeeze: bool = False       # Squeeze detection (low volatility)
    regime_confidence: float = 0.0 # Confidence in regime classification
    regime_indicators: str = '{}'  # JSON of indicators used for regime
    # EPIC-D027: Phase 3 Level Engine fields
    nearest_resistance_price: float = 0.0    # Nearest resistance level price
    nearest_resistance_dist_atr: float = 0.0 # Distance to resistance in ATR
    nearest_resistance_type: str = ''        # Level type (PDH, ASIA_HIGH, etc.)
    nearest_support_price: float = 0.0       # Nearest support level price
    nearest_support_dist_atr: float = 0.0    # Distance to support in ATR
    nearest_support_type: str = ''           # Level type (PDL, ASIA_LOW, etc.)
    # EPIC-D027: Phase 3 Compression Detector fields
    compression_score: float = 0.0           # Overall compression score 0-1
    compression_atr_pctl: float = 50.0       # ATR percentile 0-100
    compression_range_score: float = 0.0     # Range tightness score 0-1
    compression_bb_squeeze: bool = False     # BB squeeze detected
    compression_ema_converging: bool = False # EMAs converging
    # EPIC-D027: Phase 3 Break Quality fields
    break_quality_long: float = 0.0          # Break quality score for long (0-1)
    break_quality_short: float = 0.0         # Break quality score for short (0-1)
    break_level_id: int = 0                  # ID of broken level (0 if none)

    def to_dict(self) -> dict:
        return {
            'instrument': self.instrument,
            'timestamp': self.timestamp.isoformat(),
            'timeframe': self.timeframe,
            'price_open': self.price_open,
            'price_high': self.price_high,
            'price_low': self.price_low,
            'price_close': self.price_close,
            'volume': self.volume,
            'volume_ratio': self.volume_ratio,
            'rsi_14': self.rsi_14,
            'ema_9': self.ema_9,
            'ema_21': self.ema_21,
            'ema_12': self.ema_12,
            'ema_26': self.ema_26,
            'ema_20': self.ema_20,
            'ema_50': self.ema_50,
            'ema_200': self.ema_200,
            'ema_9_21_state': self.ema_9_21_state,
            'ema_12_26_state': self.ema_12_26_state,
            'ema_20_50_state': self.ema_20_50_state,
            'ema_50_200_state': self.ema_50_200_state,
            'atr_14': self.atr_14,
            'regime': self.regime,
            'session': self.session,
            'version': self.version,
            # STORY-D002-09: Day State metrics
            'atr_baseline': self.atr_baseline,
            'atr_opening_shock': self.atr_opening_shock,
            'atr_day_ratio': self.atr_day_ratio,
            # EPIC-D026: HERMES SSOT indicators
            'adx_14': self.adx_14,
            'plus_di': self.plus_di,
            'minus_di': self.minus_di,
            'bb_middle': self.bb_middle,
            'bb_upper': self.bb_upper,
            'bb_lower': self.bb_lower,
            'bb_width_pct': self.bb_width_pct,
            'bb_squeeze': self.bb_squeeze,
            'regime_confidence': self.regime_confidence,
            'regime_indicators': self.regime_indicators,
            # EPIC-D027: Phase 3 Level Engine fields
            'nearest_resistance_price': self.nearest_resistance_price,
            'nearest_resistance_dist_atr': self.nearest_resistance_dist_atr,
            'nearest_resistance_type': self.nearest_resistance_type,
            'nearest_support_price': self.nearest_support_price,
            'nearest_support_dist_atr': self.nearest_support_dist_atr,
            'nearest_support_type': self.nearest_support_type,
            # EPIC-D027: Phase 3 Compression Detector fields
            'compression_score': self.compression_score,
            'compression_atr_pctl': self.compression_atr_pctl,
            'compression_range_score': self.compression_range_score,
            'compression_bb_squeeze': self.compression_bb_squeeze,
            'compression_ema_converging': self.compression_ema_converging,
            # EPIC-D027: Phase 3 Break Quality fields
            'break_quality_long': self.break_quality_long,
            'break_quality_short': self.break_quality_short,
            'break_level_id': self.break_level_id,
        }


class CandleAggregator:
    """
    Aggregates ticks into candles for multiple timeframes.

    Maintains current candle state per instrument/timeframe.
    Emits completed candles when timeframe boundary is crossed.
    """

    def __init__(self, instruments: List[str], timeframes: List[str] = None):
        """
        Initialize aggregator.

        Args:
            instruments: List of instruments to track
            timeframes: Timeframes to aggregate (default: M5 only for signals)
        """
        self.instruments = instruments
        self.timeframes = timeframes or ['M5']

        # Current candles: {instrument: {timeframe: Candle}}
        self._current: Dict[str, Dict[str, Candle]] = {}

        # Initialize empty candle slots
        for inst in instruments:
            self._current[inst] = {}
            for tf in self.timeframes:
                self._current[inst][tf] = None

        logger.info(f"CandleAggregator initialized: {len(instruments)} instruments, timeframes={self.timeframes}")

    def _get_candle_start(self, timestamp: datetime, timeframe: str) -> datetime:
        """Get the start timestamp for a candle period."""
        seconds = TIMEFRAMES[timeframe]['seconds']

        # Truncate to timeframe boundary
        ts = timestamp.replace(tzinfo=None)
        epoch = datetime(1970, 1, 1)
        total_seconds = (ts - epoch).total_seconds()
        period_start = int(total_seconds // seconds) * seconds

        return epoch + timedelta(seconds=period_start)

    def process_tick(self, instrument: str, bid: float, ask: float,
                     timestamp: datetime) -> List[Candle]:
        """
        Process a tick and return any completed candles.

        Args:
            instrument: Instrument code
            bid: Bid price
            ask: Ask price
            timestamp: Tick timestamp

        Returns:
            List of completed candles (empty if no candle completed)
        """
        if instrument not in self._current:
            return []

        mid = (bid + ask) / 2
        completed = []

        for tf in self.timeframes:
            candle_start = self._get_candle_start(timestamp, tf)
            current = self._current[instrument][tf]

            # Check if we need to start a new candle
            if current is None or current.timestamp != candle_start:
                # Complete the old candle if exists
                if current is not None:
                    current.complete = True
                    completed.append(current)

                # Start new candle
                self._current[instrument][tf] = Candle(
                    instrument=instrument,
                    timestamp=candle_start,
                    timeframe=tf,
                    open=mid,
                    high=mid,
                    low=mid,
                    close=mid,
                    volume=1,
                    complete=False
                )
            else:
                # Update current candle
                current.high = max(current.high, mid)
                current.low = min(current.low, mid)
                current.close = mid
                current.volume += 1

        return completed

    def get_current_candle(self, instrument: str, timeframe: str = 'M5') -> Optional[Candle]:
        """Get the current incomplete candle for an instrument."""
        if instrument in self._current and timeframe in self._current[instrument]:
            return self._current[instrument][timeframe]
        return None


class SignalComputer:
    """
    Computes all indicators from candle history.

    Produces complete Signal objects with everything Zeus needs.
    """

    def __init__(self, db_config: dict = None):
        """
        Initialize signal computer.

        Args:
            db_config: Database config for fetching historical candles
        """
        # Use environment-aware config (GOV-ENV-001)
        if db_config:
            self.db_config = db_config
        else:
            cfg = get_db_config()
            self.db_config = {
                'host': cfg['host'],
                'port': cfg['port'],
                'user': cfg['user'],
                'password': cfg['password'],
                'database': cfg['database'],
            }

        # Version tracking per instrument
        self._versions: Dict[str, int] = {}

        # Volume average cache per instrument (for ratio calculation)
        self._volume_avg: Dict[str, float] = {}

        # STORY-D002-09: Day State metrics tracking
        # ATR baseline: rolling 20-bar ATR average per instrument
        self._atr_history: Dict[str, List[float]] = {}
        # Session tracking: {instrument: {'session_date': date, 'open_price': float, 'opening_atr': float}}
        self._session_state: Dict[str, Dict] = {}
        # Day high/low tracking for day ratio: {instrument: {'date': date, 'high': float, 'low': float}}
        self._day_range: Dict[str, Dict] = {}

        # EPIC-D026: Data-driven regime detection
        self._regime_detector = RegimeDetector(db_connection_func=self._get_db_connection)
        # EPIC-D027: Level Engine for Phase 3 breakout detection
        self._level_engine = LevelEngine(db_connection_func=self._get_db_connection)
        # EPIC-D027: Compression Detector for Phase 3 breakout detection
        self._compression_detector = CompressionDetector(db_connection_func=self._get_db_connection)
        # EPIC-D027: Break Quality Detector for Phase 3 breakout detection
        self._break_detector = BreakDetector(db_connection_func=self._get_db_connection)
        # Signal history cache for regime detection
        self._signal_history: Dict[str, List[Dict]] = {}

        logger.info("SignalComputer initialized")

    def _get_db_connection(self):
        """Get database connection for regime detector."""
        import pymysql
        return pymysql.connect(
            host=self.db_config['host'],
            port=self.db_config['port'],
            user=self.db_config['user'],
            password=self.db_config['password'],
            database=self.db_config['database'],
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=True
        )

    def _get_session(self, timestamp: datetime) -> str:
        """Determine trading session from timestamp."""
        hour = timestamp.hour

        # Session times (UTC)
        if 22 <= hour or hour < 7:
            return 'asia'
        elif 7 <= hour < 12:
            return 'london'
        elif 12 <= hour < 17:
            return 'newyork'
        else:
            return 'late_ny'

    def _determine_regime(self, ema_states: dict) -> str:
        """
        Determine market regime from EMA states.

        Args:
            ema_states: Dict with EMA state values

        Returns:
            Regime string: BULL_TREND, BEAR_TREND, TRANSITION, LOW_VOLATILITY
        """
        # Count bullish/bearish alignments
        bullish = 0
        bearish = 0

        for state in ema_states.values():
            if state == 'above':
                bullish += 1
            elif state == 'below':
                bearish += 1

        total = len(ema_states)

        if bullish >= total * 0.75:
            return 'BULL_TREND'
        elif bearish >= total * 0.75:
            return 'BEAR_TREND'
        elif bullish > 0 and bearish > 0:
            return 'TRANSITION'
        else:
            return 'LOW_VOLATILITY'

    def _compute_day_state_metrics(self, instrument: str, candle: Candle,
                                    atr_14: float) -> tuple:
        """
        STORY-D002-09: Compute Day State classification metrics.

        Args:
            instrument: Instrument code
            candle: Current candle
            atr_14: Current ATR-14 value

        Returns:
            Tuple of (atr_baseline, atr_opening_shock, atr_day_ratio)
        """
        # Initialize tracking for new instrument
        if instrument not in self._atr_history:
            self._atr_history[instrument] = []
        if instrument not in self._session_state:
            self._session_state[instrument] = {}
        if instrument not in self._day_range:
            self._day_range[instrument] = {}

        candle_date = candle.timestamp.date()

        # Update ATR history (keep last 20 values for baseline)
        if atr_14 > 0:
            self._atr_history[instrument].append(atr_14)
            if len(self._atr_history[instrument]) > 20:
                self._atr_history[instrument] = self._atr_history[instrument][-20:]

        # Compute ATR baseline (average of last 20 ATR values)
        atr_baseline = 0.0
        if self._atr_history[instrument]:
            atr_baseline = round(
                sum(self._atr_history[instrument]) / len(self._atr_history[instrument]),
                5
            )

        # Track session opening for shock calculation
        # Session starts at 00:00 UTC for simplicity (first M5 candle of day)
        session_state = self._session_state[instrument]
        is_new_session = session_state.get('session_date') != candle_date

        if is_new_session:
            # New session - record opening state
            session_state['session_date'] = candle_date
            session_state['open_price'] = candle.open
            session_state['opening_atr'] = atr_14
            session_state['shock_computed'] = False
            # Reset day range
            self._day_range[instrument] = {
                'date': candle_date,
                'high': candle.high,
                'low': candle.low
            }
        else:
            # Update day range
            day_range = self._day_range[instrument]
            if day_range.get('date') == candle_date:
                day_range['high'] = max(day_range.get('high', candle.high), candle.high)
                day_range['low'] = min(day_range.get('low', candle.low), candle.low)

        # Compute opening shock (first 30 min = 6 M5 bars)
        atr_opening_shock = 0.0
        minutes_since_open = (candle.timestamp - datetime.combine(
            candle_date, datetime.min.time()
        ).replace(tzinfo=candle.timestamp.tzinfo if candle.timestamp.tzinfo else None)).seconds // 60

        if minutes_since_open <= 30 and atr_baseline > 0:
            # Within first 30 min - compute shock as current ATR / baseline
            atr_opening_shock = round(atr_14 / atr_baseline, 4) if atr_baseline > 0 else 0.0
            session_state['opening_shock'] = atr_opening_shock
        elif not session_state.get('shock_computed', False) and atr_baseline > 0:
            # After 30 min - freeze the shock value
            atr_opening_shock = session_state.get('opening_shock', 0.0)
            session_state['shock_computed'] = True
        else:
            # Use frozen value
            atr_opening_shock = session_state.get('opening_shock', 0.0)

        # Compute day ratio (current day range / ATR baseline)
        atr_day_ratio = 0.0
        day_range = self._day_range[instrument]
        if day_range.get('date') == candle_date and atr_baseline > 0:
            current_range = day_range.get('high', 0) - day_range.get('low', 0)
            atr_day_ratio = round(current_range / atr_baseline, 4) if atr_baseline > 0 else 0.0

        return atr_baseline, atr_opening_shock, atr_day_ratio

    def compute_signal(self, candle: Candle, history: List[dict]) -> Optional[Signal]:
        """
        Compute complete signal from a candle and its history.

        Args:
            candle: The just-completed candle
            history: List of recent candles (oldest first) with OHLCV data
                     Should include at least 200 candles for EMA-200

        Returns:
            Complete Signal object, or None if insufficient data
        """
        instrument = candle.instrument

        if len(history) < 50:
            logger.warning(f"{instrument}: Insufficient history ({len(history)} candles)")
            return None

        # Extract close prices for indicators
        closes = [float(c['close']) for c in history]
        closes.append(candle.close)  # Add current candle

        # Compute RSI
        rsi_14 = round(calculate_rsi(closes, 14), 2)

        # Compute all EMAs
        emas = {}
        for period in EMA_PERIODS:
            if len(closes) >= period:
                emas[period] = round(calculate_ema(closes, period), 5)
            else:
                emas[period] = round(candle.close, 5)

        # Compute EMA states
        ema_states = {}
        state_values = {}
        for fast, slow in EMA_PAIRS:
            state = get_ema_state(emas.get(fast, 0), emas.get(slow, 0))
            key = f'ema_{fast}_{slow}_state'
            ema_states[key] = state
            state_values[f'{fast}_{slow}'] = state

        # Compute ATR
        atr_14 = None
        if len(history) >= 15:
            # Need OHLC for ATR
            atr_history = history[-15:]
            atr_history.append({
                'open': candle.open,
                'high': candle.high,
                'low': candle.low,
                'close': candle.close
            })
            atr_14 = calculate_atr(atr_history, 14)
        atr_14 = round(atr_14, 5) if atr_14 else 0.0

        # EPIC-D026: Compute ADX with +DI/-DI
        adx_result = ADXResult(adx=20.0, plus_di=20.0, minus_di=20.0)  # Default neutral
        if len(history) >= 30:
            # Extract OHLC for ADX calculation
            highs = [float(c['high']) for c in history]
            lows = [float(c['low']) for c in history]
            history_closes = [float(c['close']) for c in history]
            # Add current candle
            highs.append(candle.high)
            lows.append(candle.low)
            history_closes.append(candle.close)
            adx_result = calculate_adx(highs, lows, history_closes, 14)

        # EPIC-D026: Compute Bollinger Bands
        bb_result = calculate_bollinger_bands(closes, period=20, std_dev=2.0, squeeze_threshold=2.0)

        # STORY-D002-09: Compute Day State metrics
        atr_baseline, atr_opening_shock, atr_day_ratio = self._compute_day_state_metrics(
            instrument, candle, atr_14
        )

        # Compute volume ratio
        if instrument not in self._volume_avg:
            # Calculate average from history
            volumes = [c.get('volume', 0) for c in history[-20:]]
            self._volume_avg[instrument] = sum(volumes) / len(volumes) if volumes else 1

        avg_vol = self._volume_avg[instrument]
        volume_ratio = round(candle.volume / avg_vol, 4) if avg_vol > 0 else 1.0

        # Update running average
        self._volume_avg[instrument] = (avg_vol * 19 + candle.volume) / 20

        # Get session
        session = self._get_session(candle.timestamp)

        # EPIC-D026: Data-driven regime detection
        # Build current signal dict for regime detector
        current_signal = {
            'adx_14': adx_result.adx,
            'plus_di': adx_result.plus_di,
            'minus_di': adx_result.minus_di,
            'rsi_14': rsi_14,
            'atr_14': atr_14,
            'atr_baseline': atr_baseline,
            'bb_width_pct': bb_result.width_pct,
            'bb_squeeze': bb_result.squeeze,
            'ema_9_21_state': ema_states.get('ema_9_21_state', 'cross'),
            'ema_12_26_state': ema_states.get('ema_12_26_state', 'cross'),
            'ema_20_50_state': ema_states.get('ema_20_50_state', 'cross'),
            'ema_50_200_state': ema_states.get('ema_50_200_state', 'cross'),
            'ema_20': emas.get(20, 0),
            'ema_50': emas.get(50, 0),
        }

        # Get signal history for this instrument
        if instrument not in self._signal_history:
            self._signal_history[instrument] = []

        # Build indicators and classify regime
        indicators = self._regime_detector.build_indicators(
            current_signal,
            self._signal_history[instrument]
        )
        regime_result = self._regime_detector.classify(indicators)

        # Update signal history (keep last 30)
        self._signal_history[instrument].insert(0, current_signal)
        if len(self._signal_history[instrument]) > 30:
            self._signal_history[instrument] = self._signal_history[instrument][:30]

        # EPIC-D027: Get nearest levels from Level Engine
        nearest_levels = self._level_engine.get_nearest_levels(instrument, candle.close, atr_14)
        nearest_resistance = nearest_levels.get('nearest_resistance', {})
        nearest_support = nearest_levels.get('nearest_support', {})

        # EPIC-D027: Detect compression
        compression_result = self._compression_detector.detect(instrument, {
            'atr_14': atr_14,
            'bb_width_pct': bb_result.width_pct,
            'ema_9': emas.get(9, 0),
            'ema_20': emas.get(20, 0),
            'ema_21': emas.get(21, 0),
            'ema_50': emas.get(50, 0),
        })
        # Update ATR cache for percentile calculation
        self._compression_detector.update_atr_cache(instrument, atr_14)

        # EPIC-D027: Detect break quality
        break_quality_long, break_quality_short, break_level_id = self._break_detector.get_break_for_signal(
            instrument,
            {'open': candle.open, 'high': candle.high, 'low': candle.low, 'close': candle.close},
            volume_ratio
        )

        # Increment version
        self._versions[instrument] = self._versions.get(instrument, 0) + 1

        return Signal(
            instrument=instrument,
            timestamp=candle.timestamp,
            timeframe=candle.timeframe,
            price_open=round(candle.open, 5),
            price_high=round(candle.high, 5),
            price_low=round(candle.low, 5),
            price_close=round(candle.close, 5),
            volume=candle.volume,
            volume_ratio=volume_ratio,
            rsi_14=rsi_14,
            ema_9=emas.get(9, 0),
            ema_21=emas.get(21, 0),
            ema_12=emas.get(12, 0),
            ema_26=emas.get(26, 0),
            ema_20=emas.get(20, 0),
            ema_50=emas.get(50, 0),
            ema_200=emas.get(200, 0),
            ema_9_21_state=ema_states.get('ema_9_21_state', 'cross'),
            ema_12_26_state=ema_states.get('ema_12_26_state', 'cross'),
            ema_20_50_state=ema_states.get('ema_20_50_state', 'cross'),
            ema_50_200_state=ema_states.get('ema_50_200_state', 'cross'),
            atr_14=atr_14,
            regime=regime_result.regime_id,
            session=session,
            version=self._versions[instrument],
            # STORY-D002-09: Day State metrics
            atr_baseline=atr_baseline,
            atr_opening_shock=atr_opening_shock,
            atr_day_ratio=atr_day_ratio,
            # EPIC-D026: HERMES SSOT indicators
            adx_14=adx_result.adx,
            plus_di=adx_result.plus_di,
            minus_di=adx_result.minus_di,
            bb_middle=bb_result.middle,
            bb_upper=bb_result.upper,
            bb_lower=bb_result.lower,
            bb_width_pct=bb_result.width_pct,
            bb_squeeze=bb_result.squeeze,
            regime_confidence=regime_result.confidence,
            regime_indicators=json.dumps({
                'matched_rules': regime_result.matched_rules,
                'adx': indicators.get('adx'),
                'rsi_cycling': indicators.get('rsi_cycling'),
                'ema_flat': indicators.get('ema_flat'),
                'bb_squeeze': indicators.get('bb_squeeze'),
            }),
            # EPIC-D027: Phase 3 Level Engine fields
            nearest_resistance_price=float(nearest_resistance.get('level_price', 0)),
            nearest_resistance_dist_atr=float(nearest_resistance.get('dist_atr', 0)),
            nearest_resistance_type=nearest_resistance.get('level_type', ''),
            nearest_support_price=float(nearest_support.get('level_price', 0)),
            nearest_support_dist_atr=float(nearest_support.get('dist_atr', 0)),
            nearest_support_type=nearest_support.get('level_type', ''),
            # EPIC-D027: Phase 3 Compression Detector fields
            compression_score=compression_result.compression_score,
            compression_atr_pctl=compression_result.atr_pctl,
            compression_range_score=compression_result.range_score,
            compression_bb_squeeze=compression_result.bb_squeeze,
            compression_ema_converging=compression_result.ema_converging,
            # EPIC-D027: Phase 3 Break Quality fields
            break_quality_long=break_quality_long,
            break_quality_short=break_quality_short,
            break_level_id=break_level_id,
        )


class SignalPublisher:
    """
    Publishes signals to database and Redis.

    Ensures signals are available for Zeus to consume.
    """

    def __init__(self, db_config: dict = None, redis_publisher=None):
        """
        Initialize publisher.

        Args:
            db_config: Database configuration
            redis_publisher: RedisPublisher instance
        """
        # Use environment-aware config (GOV-ENV-001)
        if db_config:
            self.db_config = db_config
        else:
            cfg = get_db_config()
            self.db_config = {
                'host': cfg['host'],
                'port': cfg['port'],
                'user': cfg['user'],
                'password': cfg['password'],
                'database': cfg['database'],
            }
        self.redis = redis_publisher
        self._db_conn = None

        logger.info("SignalPublisher initialized")

    def _get_db(self):
        """Get or create database connection."""
        import pymysql
        if self._db_conn is None or not self._db_conn.open:
            self._db_conn = pymysql.connect(
                host=self.db_config['host'],
                port=self.db_config['port'],
                user=self.db_config['user'],
                password=self.db_config['password'],
                database=self.db_config['database'],
                autocommit=True
            )
        return self._db_conn

    def publish_signal(self, signal: Signal) -> bool:
        """
        Publish signal to DB and Redis.

        Args:
            signal: Complete Signal object

        Returns:
            True if successful
        """
        success = True

        # Write to database
        try:
            conn = self._get_db()
            cursor = conn.cursor()

            sql = """
            INSERT INTO signals
            (instrument, timestamp, timeframe, price_close, volume, volume_ratio,
             rsi_14, ema_9, ema_21, ema_12, ema_26, ema_20, ema_50, ema_200,
             ema_9_21_state, ema_12_26_state, ema_20_50_state, ema_50_200_state,
             atr_14, regime, session, version,
             atr_baseline, atr_opening_shock, atr_day_ratio,
             adx_14, plus_di, minus_di, bb_middle, bb_upper, bb_lower,
             bb_width_pct, bb_squeeze, regime_confidence, regime_indicators,
             nearest_resistance_price, nearest_resistance_dist_atr, nearest_resistance_type,
             nearest_support_price, nearest_support_dist_atr, nearest_support_type,
             compression_score, compression_atr_pctl, compression_range_score,
             compression_bb_squeeze, compression_ema_converging,
             break_quality_long, break_quality_short, break_level_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                price_close = VALUES(price_close),
                volume = VALUES(volume),
                volume_ratio = VALUES(volume_ratio),
                rsi_14 = VALUES(rsi_14),
                ema_9 = VALUES(ema_9),
                ema_21 = VALUES(ema_21),
                ema_12 = VALUES(ema_12),
                ema_26 = VALUES(ema_26),
                ema_20 = VALUES(ema_20),
                ema_50 = VALUES(ema_50),
                ema_200 = VALUES(ema_200),
                ema_9_21_state = VALUES(ema_9_21_state),
                ema_12_26_state = VALUES(ema_12_26_state),
                ema_20_50_state = VALUES(ema_20_50_state),
                ema_50_200_state = VALUES(ema_50_200_state),
                atr_14 = VALUES(atr_14),
                regime = VALUES(regime),
                session = VALUES(session),
                version = VALUES(version),
                atr_baseline = VALUES(atr_baseline),
                atr_opening_shock = VALUES(atr_opening_shock),
                atr_day_ratio = VALUES(atr_day_ratio),
                adx_14 = VALUES(adx_14),
                plus_di = VALUES(plus_di),
                minus_di = VALUES(minus_di),
                bb_middle = VALUES(bb_middle),
                bb_upper = VALUES(bb_upper),
                bb_lower = VALUES(bb_lower),
                bb_width_pct = VALUES(bb_width_pct),
                bb_squeeze = VALUES(bb_squeeze),
                regime_confidence = VALUES(regime_confidence),
                regime_indicators = VALUES(regime_indicators),
                nearest_resistance_price = VALUES(nearest_resistance_price),
                nearest_resistance_dist_atr = VALUES(nearest_resistance_dist_atr),
                nearest_resistance_type = VALUES(nearest_resistance_type),
                nearest_support_price = VALUES(nearest_support_price),
                nearest_support_dist_atr = VALUES(nearest_support_dist_atr),
                nearest_support_type = VALUES(nearest_support_type),
                compression_score = VALUES(compression_score),
                compression_atr_pctl = VALUES(compression_atr_pctl),
                compression_range_score = VALUES(compression_range_score),
                compression_bb_squeeze = VALUES(compression_bb_squeeze),
                compression_ema_converging = VALUES(compression_ema_converging),
                break_quality_long = VALUES(break_quality_long),
                break_quality_short = VALUES(break_quality_short),
                break_level_id = VALUES(break_level_id)
            """

            cursor.execute(sql, (
                signal.instrument, signal.timestamp, signal.timeframe,
                signal.price_close, signal.volume, signal.volume_ratio,
                signal.rsi_14, signal.ema_9, signal.ema_21, signal.ema_12,
                signal.ema_26, signal.ema_20, signal.ema_50, signal.ema_200,
                signal.ema_9_21_state, signal.ema_12_26_state,
                signal.ema_20_50_state, signal.ema_50_200_state,
                signal.atr_14, signal.regime, signal.session, signal.version,
                signal.atr_baseline, signal.atr_opening_shock, signal.atr_day_ratio,
                signal.adx_14, signal.plus_di, signal.minus_di,
                signal.bb_middle, signal.bb_upper, signal.bb_lower,
                signal.bb_width_pct, signal.bb_squeeze,
                signal.regime_confidence, signal.regime_indicators,
                signal.nearest_resistance_price, signal.nearest_resistance_dist_atr,
                signal.nearest_resistance_type,
                signal.nearest_support_price, signal.nearest_support_dist_atr,
                signal.nearest_support_type,
                signal.compression_score, signal.compression_atr_pctl,
                signal.compression_range_score, signal.compression_bb_squeeze,
                signal.compression_ema_converging,
                signal.break_quality_long, signal.break_quality_short,
                signal.break_level_id
            ))
            cursor.close()

        except Exception as e:
            logger.error(f"Failed to write signal to DB: {e}")
            success = False

        # Publish to Redis
        if self.redis:
            try:
                redis_cfg = get_redis_config()
                key_prefix = redis_cfg['key_prefix']
                signal_key = f"{key_prefix}signals:latest:{signal.instrument}"

                # Store as hash for easy field access
                self.redis._client.hset(signal_key, mapping={
                    'instrument': signal.instrument,
                    'timestamp': signal.timestamp.isoformat(),
                    'timeframe': signal.timeframe,
                    'price_open': str(signal.price_open),
                    'price_high': str(signal.price_high),
                    'price_low': str(signal.price_low),
                    'price_close': str(signal.price_close),
                    'volume': str(signal.volume),
                    'volume_ratio': str(signal.volume_ratio),
                    'rsi_14': str(signal.rsi_14),
                    'ema_9': str(signal.ema_9),
                    'ema_21': str(signal.ema_21),
                    'ema_12': str(signal.ema_12),
                    'ema_26': str(signal.ema_26),
                    'ema_20': str(signal.ema_20),
                    'ema_50': str(signal.ema_50),
                    'ema_200': str(signal.ema_200),
                    'ema_9_21_state': signal.ema_9_21_state,
                    'ema_12_26_state': signal.ema_12_26_state,
                    'ema_20_50_state': signal.ema_20_50_state,
                    'ema_50_200_state': signal.ema_50_200_state,
                    'atr_14': str(signal.atr_14),
                    'regime': signal.regime,
                    'session': signal.session,
                    'version': str(signal.version),
                    # STORY-D002-09: Day State metrics
                    'atr_baseline': str(signal.atr_baseline),
                    'atr_opening_shock': str(signal.atr_opening_shock),
                    'atr_day_ratio': str(signal.atr_day_ratio),
                    # EPIC-D026: HERMES SSOT indicators
                    'adx_14': str(signal.adx_14),
                    'plus_di': str(signal.plus_di),
                    'minus_di': str(signal.minus_di),
                    'bb_middle': str(signal.bb_middle),
                    'bb_upper': str(signal.bb_upper),
                    'bb_lower': str(signal.bb_lower),
                    'bb_width_pct': str(signal.bb_width_pct),
                    'bb_squeeze': str(signal.bb_squeeze),
                    'regime_confidence': str(signal.regime_confidence),
                    'regime_indicators': signal.regime_indicators,
                    # EPIC-D027: Phase 3 Level Engine fields
                    'nearest_resistance_price': str(signal.nearest_resistance_price),
                    'nearest_resistance_dist_atr': str(signal.nearest_resistance_dist_atr),
                    'nearest_resistance_type': signal.nearest_resistance_type,
                    'nearest_support_price': str(signal.nearest_support_price),
                    'nearest_support_dist_atr': str(signal.nearest_support_dist_atr),
                    'nearest_support_type': signal.nearest_support_type,
                    # EPIC-D027: Phase 3 Compression Detector fields
                    'compression_score': str(signal.compression_score),
                    'compression_atr_pctl': str(signal.compression_atr_pctl),
                    'compression_range_score': str(signal.compression_range_score),
                    'compression_bb_squeeze': str(signal.compression_bb_squeeze),
                    'compression_ema_converging': str(signal.compression_ema_converging),
                    # EPIC-D027: Phase 3 Break Quality fields
                    'break_quality_long': str(signal.break_quality_long),
                    'break_quality_short': str(signal.break_quality_short),
                    'break_level_id': str(signal.break_level_id),
                    # UTC_AUDIT_METADATA_OK: redis-publish audit timestamp;
                    # canonical signal timestamp lives elsewhere on the
                    # signal payload. WO-HERMES-UTC-AUDIT-FIX-0001.
                    'updated_at': datetime.now(timezone.utc).isoformat()
                })
                self.redis._client.expire(signal_key, 600)  # 10 min TTL

                # Also publish to channel for real-time subscribers
                channel = f"{key_prefix}signals:stream:{signal.instrument}"
                self.redis._client.publish(channel, json.dumps(signal.to_dict()))

            except Exception as e:
                logger.error(f"Failed to publish signal to Redis: {e}")
                success = False

        if success:
            logger.info(f"[{signal.instrument}] Signal v{signal.version}: "
                       f"RSI={signal.rsi_14} EMA(9/21)={signal.ema_9_21_state} "
                       f"regime={signal.regime}")

        return success

    def close(self):
        """Close database connection."""
        if self._db_conn:
            self._db_conn.close()
            self._db_conn = None
