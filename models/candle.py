"""
Candle models and aggregation logic
EPIC-D002: Standalone Signal Service

Aggregates ticks into M1, M5, H1, D1 candles for DB persistence.

D1 candles are aligned to NY 5PM (forex convention):
- Winter (EST): 5PM NY = 22:00 UTC
- Summer (EDT): 5PM NY = 21:00 UTC
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo
import logging

from .tick import SignalTick

# Timezone for forex D1 alignment
NY_TZ = ZoneInfo('America/New_York')
UTC_TZ = ZoneInfo('UTC')

logger = logging.getLogger("signal-service.candle")


class Timeframe(Enum):
    """Supported timeframes"""
    M1 = 1      # 1 minute
    M5 = 5      # 5 minutes
    H1 = 60     # 1 hour
    D1 = 1440   # 1 day (24 * 60)

    @property
    def table_name(self) -> str:
        return f"candles_{self.name}"

    @property
    def minutes(self) -> int:
        return self.value


@dataclass
class Candle:
    """OHLCV candle data"""
    instrument: str
    timeframe: Timeframe
    timestamp: datetime  # Candle open time
    open: float
    high: float
    low: float
    close: float
    volume: int = 0
    complete: bool = False
    tick_count: int = 0

    def update(self, price: float):
        """Update candle with new price"""
        if self.tick_count == 0:
            self.open = price
            self.high = price
            self.low = price
        else:
            self.high = max(self.high, price)
            self.low = min(self.low, price)
        self.close = price
        self.tick_count += 1
        self.volume += 1

    def to_dict(self) -> dict:
        return {
            "instrument": self.instrument,
            "timeframe": self.timeframe.name,
            "timestamp": self.timestamp.isoformat(),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "complete": self.complete
        }


class CandleAggregator:
    """
    Aggregates ticks into candles for all timeframes.

    Maintains current (incomplete) candle per instrument per timeframe.
    Emits completed candles for DB persistence.
    """

    def __init__(self, instruments: List[str]):
        self.instruments = instruments

        # Current candles: {instrument: {timeframe: Candle}}
        self._current: Dict[str, Dict[Timeframe, Candle]] = {}

        # Initialize for all instruments
        for inst in instruments:
            self._current[inst] = {}

        # Callbacks for completed candles
        self._on_candle_complete = None

    def set_candle_callback(self, callback):
        """Set callback for completed candles: callback(candle: Candle)"""
        self._on_candle_complete = callback

    def process_tick(self, tick: SignalTick) -> List[Candle]:
        """
        Process a tick and return any completed candles.

        Returns list of completed candles (may be empty).
        """
        completed = []
        instrument = tick.instrument

        if instrument not in self._current:
            self._current[instrument] = {}

        # Use mid price for candle building
        price = tick.mid

        # Process each timeframe
        for tf in Timeframe:
            candle_time = self._get_candle_time(tick.timestamp, tf)
            current = self._current[instrument].get(tf)

            if current is None:
                # Start new candle
                self._current[instrument][tf] = Candle(
                    instrument=instrument,
                    timeframe=tf,
                    timestamp=candle_time,
                    open=price,
                    high=price,
                    low=price,
                    close=price,
                    tick_count=1,
                    volume=1
                )
            elif current.timestamp < candle_time:
                # Current candle is complete, start new one
                current.complete = True
                completed.append(current)

                # Notify callback
                if self._on_candle_complete:
                    self._on_candle_complete(current)

                # Start new candle
                self._current[instrument][tf] = Candle(
                    instrument=instrument,
                    timeframe=tf,
                    timestamp=candle_time,
                    open=price,
                    high=price,
                    low=price,
                    close=price,
                    tick_count=1,
                    volume=1
                )
            else:
                # Update existing candle
                current.update(price)

        return completed

    def _get_candle_time(self, dt: datetime, tf: Timeframe) -> datetime:
        """
        Get the candle open time for a given timestamp and timeframe.

        Truncates timestamp to the start of the candle period.
        For D1, aligns to NY 5PM (forex convention) and returns the
        completion date (the calendar date when trading ends).
        """
        if tf == Timeframe.M1:
            return dt.replace(second=0, microsecond=0)
        elif tf == Timeframe.M5:
            minute = (dt.minute // 5) * 5
            return dt.replace(minute=minute, second=0, microsecond=0)
        elif tf == Timeframe.H1:
            return dt.replace(minute=0, second=0, microsecond=0)
        elif tf == Timeframe.D1:
            # D1 aligns to NY 5PM (forex convention)
            # Convert UTC to NY time to determine trading day
            dt_utc = dt.replace(tzinfo=UTC_TZ) if dt.tzinfo is None else dt
            dt_ny = dt_utc.astimezone(NY_TZ)

            # If after 5PM NY, this tick belongs to NEXT day's candle
            # If before/at 5PM NY, it belongs to current day's candle
            if dt_ny.hour >= 17:  # After 5PM NY
                trading_day = dt_ny.date() + timedelta(days=1)
            else:
                trading_day = dt_ny.date()

            # Return midnight UTC of trading day (completion date)
            return datetime(trading_day.year, trading_day.month, trading_day.day, 0, 0, 0)
        else:
            return dt.replace(second=0, microsecond=0)

    def get_current_candles(self, instrument: str) -> Dict[str, Candle]:
        """Get current (incomplete) candles for an instrument"""
        if instrument not in self._current:
            return {}
        return {
            tf.name: candle.to_dict()
            for tf, candle in self._current[instrument].items()
        }

    def flush_all(self) -> List[Candle]:
        """
        Flush all current candles as complete.
        Call on shutdown to persist partial candles.
        """
        completed = []
        for instrument in self._current:
            for tf, candle in self._current[instrument].items():
                if candle.tick_count > 0:
                    candle.complete = True
                    completed.append(candle)
                    if self._on_candle_complete:
                        self._on_candle_complete(candle)
        return completed
