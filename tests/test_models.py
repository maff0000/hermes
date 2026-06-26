"""
Unit tests for tradingSignals models
EPIC-D002 / STORY-D002-05

Tests:
- SignalTick creation and serialization
- Candle creation and updates
- CandleAggregator tick processing
"""
import pytest
import sys
from datetime import datetime, timedelta

# Path setup moved to conftest.py

from models.tick import SignalTick, TickSource
from models.candle import Candle, CandleAggregator, Timeframe


class TestSignalTick:
    """Test SignalTick model"""

    def test_tick_creation(self):
        """Test basic tick creation"""
        tick = SignalTick(
            instrument="XAU_USD",
            bid=2650.50,
            ask=2651.00,
            timestamp=datetime.utcnow(),
            source=TickSource.OANDA
        )
        assert tick.instrument == "XAU_USD"
        assert tick.bid == 2650.50
        assert tick.ask == 2651.00
        assert tick.source == TickSource.OANDA

    def test_tick_derived_fields(self):
        """Test mid and spread calculation"""
        tick = SignalTick(
            instrument="XAU_USD",
            bid=2650.00,
            ask=2651.00,
            timestamp=datetime.utcnow(),
            source=TickSource.OANDA
        )
        assert tick.mid == 2650.50  # (2650 + 2651) / 2
        assert tick.spread == 1.00   # 2651 - 2650

    def test_tick_to_dict(self):
        """Test serialization to dict"""
        ts = datetime.utcnow()
        tick = SignalTick(
            instrument="XAU_USD",
            bid=2650.00,
            ask=2651.00,
            timestamp=ts,
            source=TickSource.MOCK
        )
        d = tick.to_dict()
        assert d["instrument"] == "XAU_USD"
        assert d["bid"] == 2650.00
        assert d["ask"] == 2651.00
        assert d["source"] == "mock"
        assert "timestamp" in d

    def test_tick_from_dict(self):
        """Test deserialization from dict"""
        data = {
            "instrument": "XAG_USD",
            "bid": 30.50,
            "ask": 30.55,
            "timestamp": "2026-01-02T12:00:00",
            "source": "oanda"
        }
        tick = SignalTick.from_dict(data)
        assert tick.instrument == "XAG_USD"
        assert tick.bid == 30.50
        assert tick.source == TickSource.OANDA

    def test_tick_to_json(self):
        """Test JSON serialization"""
        tick = SignalTick(
            instrument="XAU_USD",
            bid=2650.00,
            ask=2651.00,
            timestamp=datetime.utcnow(),
            source=TickSource.OANDA
        )
        json_str = tick.to_json()
        assert "XAU_USD" in json_str
        assert "2650" in json_str

    def test_tick_redis_hash(self):
        """Test Redis hash format"""
        tick = SignalTick(
            instrument="XAU_USD",
            bid=2650.00,
            ask=2651.00,
            timestamp=datetime.utcnow(),
            source=TickSource.OANDA
        )
        h = tick.to_redis_hash()
        assert h["instrument"] == "XAU_USD"
        assert h["bid"] == "2650.0"  # String for Redis
        assert h["source"] == "oanda"

    def test_tick_latency(self):
        """Test latency calculation"""
        source_time = datetime.utcnow() - timedelta(milliseconds=50)
        received_time = datetime.utcnow()
        tick = SignalTick(
            instrument="XAU_USD",
            bid=2650.00,
            ask=2651.00,
            timestamp=datetime.utcnow(),
            source=TickSource.OANDA,
            source_timestamp=source_time,
            received_at=received_time
        )
        assert tick.latency_ms is not None
        assert tick.latency_ms >= 40  # At least 40ms


class TestCandle:
    """Test Candle model"""

    def test_candle_creation(self):
        """Test basic candle creation"""
        candle = Candle(
            instrument="XAU_USD",
            timeframe=Timeframe.M5,
            timestamp=datetime.utcnow(),
            open=2650.00,
            high=2655.00,
            low=2648.00,
            close=2652.00
        )
        assert candle.instrument == "XAU_USD"
        assert candle.timeframe == Timeframe.M5
        assert candle.high == 2655.00

    def test_candle_update(self):
        """Test candle price updates"""
        candle = Candle(
            instrument="XAU_USD",
            timeframe=Timeframe.M5,
            timestamp=datetime.utcnow(),
            open=0, high=0, low=0, close=0
        )

        # First update sets all prices
        candle.update(2650.00)
        assert candle.open == 2650.00
        assert candle.high == 2650.00
        assert candle.low == 2650.00
        assert candle.close == 2650.00
        assert candle.tick_count == 1

        # Update with higher price
        candle.update(2655.00)
        assert candle.open == 2650.00  # Unchanged
        assert candle.high == 2655.00   # Updated
        assert candle.low == 2650.00    # Unchanged
        assert candle.close == 2655.00  # Updated
        assert candle.tick_count == 2

        # Update with lower price
        candle.update(2645.00)
        assert candle.high == 2655.00   # Unchanged
        assert candle.low == 2645.00    # Updated
        assert candle.close == 2645.00  # Updated
        assert candle.tick_count == 3

    def test_candle_to_dict(self):
        """Test candle serialization"""
        candle = Candle(
            instrument="XAU_USD",
            timeframe=Timeframe.H1,
            timestamp=datetime.utcnow(),
            open=2650.00,
            high=2660.00,
            low=2645.00,
            close=2655.00,
            volume=1000,
            complete=True
        )
        d = candle.to_dict()
        assert d["instrument"] == "XAU_USD"
        assert d["timeframe"] == "H1"
        assert d["volume"] == 1000
        assert d["complete"] is True


class TestTimeframe:
    """Test Timeframe enum"""

    def test_timeframe_values(self):
        """Test timeframe minute values"""
        assert Timeframe.M1.value == 1
        assert Timeframe.M5.value == 5
        assert Timeframe.H1.value == 60
        assert Timeframe.D1.value == 1440

    def test_timeframe_table_names(self):
        """Test table name generation"""
        assert Timeframe.M1.table_name == "candles_M1"
        assert Timeframe.M5.table_name == "candles_M5"
        assert Timeframe.H1.table_name == "candles_H1"
        assert Timeframe.D1.table_name == "candles_D1"


class TestCandleAggregator:
    """Test CandleAggregator"""

    def test_aggregator_creation(self):
        """Test aggregator initialization"""
        agg = CandleAggregator(["XAU_USD", "XAG_USD"])
        assert "XAU_USD" in agg._current
        assert "XAG_USD" in agg._current

    def test_process_first_tick(self):
        """Test processing first tick"""
        agg = CandleAggregator(["XAU_USD"])
        tick = SignalTick(
            instrument="XAU_USD",
            bid=2650.00,
            ask=2651.00,
            timestamp=datetime.utcnow(),
            source=TickSource.MOCK
        )
        completed = agg.process_tick(tick)
        assert completed == []  # No completed candles yet

        # Check current candles exist
        current = agg.get_current_candles("XAU_USD")
        assert "M1" in current
        assert "M5" in current
        assert "H1" in current
        assert "D1" in current

    def test_candle_completion(self):
        """Test candle completes when time advances"""
        agg = CandleAggregator(["XAU_USD"])

        # First tick at minute 0
        base_time = datetime(2026, 1, 2, 12, 0, 0)
        tick1 = SignalTick(
            instrument="XAU_USD",
            bid=2650.00, ask=2651.00,
            timestamp=base_time,
            source=TickSource.MOCK
        )
        agg.process_tick(tick1)

        # Second tick at minute 1 - completes M1 candle
        tick2 = SignalTick(
            instrument="XAU_USD",
            bid=2652.00, ask=2653.00,
            timestamp=base_time + timedelta(minutes=1),
            source=TickSource.MOCK
        )
        completed = agg.process_tick(tick2)

        # M1 should complete
        m1_candles = [c for c in completed if c.timeframe == Timeframe.M1]
        assert len(m1_candles) == 1
        assert m1_candles[0].complete is True

    def test_flush_all(self):
        """Test flushing all current candles"""
        agg = CandleAggregator(["XAU_USD"])
        tick = SignalTick(
            instrument="XAU_USD",
            bid=2650.00, ask=2651.00,
            timestamp=datetime.utcnow(),
            source=TickSource.MOCK
        )
        agg.process_tick(tick)

        # Flush should return all current candles
        flushed = agg.flush_all()
        assert len(flushed) == 5  # M1, M5, M15, H1, D1 (M15 added by GOLD MTF)
        assert {c.timeframe.name for c in flushed} == {"M1", "M5", "M15", "H1", "D1"}
        for candle in flushed:
            assert candle.complete is True

    def test_candle_callback(self):
        """Test candle completion callback"""
        agg = CandleAggregator(["XAU_USD"])
        completed_candles = []

        def on_complete(candle):
            completed_candles.append(candle)

        agg.set_candle_callback(on_complete)

        # Process ticks across minute boundary
        base_time = datetime(2026, 1, 2, 12, 0, 0)
        tick1 = SignalTick(
            instrument="XAU_USD",
            bid=2650.00, ask=2651.00,
            timestamp=base_time,
            source=TickSource.MOCK
        )
        tick2 = SignalTick(
            instrument="XAU_USD",
            bid=2652.00, ask=2653.00,
            timestamp=base_time + timedelta(minutes=1),
            source=TickSource.MOCK
        )

        agg.process_tick(tick1)
        agg.process_tick(tick2)

        # Callback should have been called
        assert len(completed_candles) >= 1
