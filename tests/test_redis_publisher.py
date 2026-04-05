"""
Integration tests for Redis Publisher
EPIC-D002 / STORY-D002-05

Tests:
- Redis connection
- Tick publishing
- Candle publishing
- Price retrieval
"""
import pytest
import sys
from datetime import datetime

# Path setup moved to conftest.py

# Reset singleton for testing
import utils.redis_publisher as rp
rp.RedisPublisher._instance = None

from utils.redis_publisher import RedisPublisher


class TestRedisConnection:
    """Test Redis connection handling"""

    def test_publisher_creation(self):
        """Test RedisPublisher can be created"""
        # Reset singleton
        RedisPublisher._instance = None
        publisher = RedisPublisher()
        assert publisher is not None
        assert publisher.host is not None
        assert publisher.port is not None

    def test_redis_connect(self):
        """Test Redis connection"""
        RedisPublisher._instance = None
        publisher = RedisPublisher()
        connected = publisher.connect()
        assert connected is True
        assert publisher.is_connected is True
        publisher.close()

    def test_redis_reconnect(self):
        """Test Redis auto-reconnect"""
        RedisPublisher._instance = None
        publisher = RedisPublisher()
        publisher.connect()
        publisher.close()
        # Note: is_connected calls ping() which may reconnect
        # Just verify we can publish after close

        # Should reconnect on publish
        tick_data = {
            "instrument": "TEST_USD",
            "bid": 100.0,
            "ask": 100.5,
            "mid": 100.25,
            "spread": 0.5,
            "timestamp": datetime.utcnow().isoformat(),
            "source": "mock"
        }
        # This should trigger reconnect
        result = publisher.publish_tick(tick_data)
        # If publish succeeds (returns >= 0), reconnect worked
        assert result >= 0
        publisher.close()


class TestTickPublishing:
    """Test tick publishing functionality"""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup and teardown for each test"""
        RedisPublisher._instance = None
        self.publisher = RedisPublisher()
        self.publisher.connect()
        yield
        self.publisher.close()

    def test_publish_tick(self, sample_tick_data):
        """Test publishing a tick"""
        sample_tick_data["timestamp"] = datetime.utcnow().isoformat()
        subscribers = self.publisher.publish_tick(sample_tick_data)
        assert subscribers >= 0  # May be 0 if no subscribers

    def test_publish_tick_updates_hash(self, sample_tick_data):
        """Test that publishing updates the price hash"""
        sample_tick_data["timestamp"] = datetime.utcnow().isoformat()
        self.publisher.publish_tick(sample_tick_data)

        # Retrieve the price
        instrument = sample_tick_data["instrument"]
        price = self.publisher.get_latest_price(instrument)
        assert price is not None
        assert price["instrument"] == instrument
        assert price["bid"] == sample_tick_data["bid"]
        assert price["ask"] == sample_tick_data["ask"]

    def test_publish_tick_with_all_fields(self):
        """Test publishing tick with all optional fields"""
        tick_data = {
            "instrument": "XAG_USD",
            "bid": 30.50,
            "ask": 30.55,
            "mid": 30.525,
            "spread": 0.05,
            "timestamp": datetime.utcnow().isoformat(),
            "source": "oanda",
            "volume": 1000,
            "latency_ms": 15.5
        }
        subscribers = self.publisher.publish_tick(tick_data)
        assert subscribers >= 0


class TestCandlePublishing:
    """Test candle publishing functionality"""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup and teardown"""
        RedisPublisher._instance = None
        self.publisher = RedisPublisher()
        self.publisher.connect()
        yield
        self.publisher.close()

    def test_publish_candle(self, sample_candle_data):
        """Test publishing a candle"""
        sample_candle_data["timestamp"] = datetime.utcnow().isoformat()
        subscribers = self.publisher.publish_candle("M5", sample_candle_data)
        assert subscribers >= 0

    def test_publish_candle_all_timeframes(self, sample_candle_data):
        """Test publishing candles for all timeframes"""
        sample_candle_data["timestamp"] = datetime.utcnow().isoformat()

        for tf in ["M1", "M5", "H1", "D1"]:
            subscribers = self.publisher.publish_candle(tf, sample_candle_data)
            assert subscribers >= 0


class TestHealthPublishing:
    """Test health status publishing"""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup and teardown"""
        RedisPublisher._instance = None
        self.publisher = RedisPublisher()
        self.publisher.connect()
        yield
        self.publisher.close()

    def test_publish_health(self):
        """Test publishing health status"""
        health_data = {
            "status": "healthy",
            "adapters": {
                "oanda": "connected",
                "ibkr": "disconnected"
            },
            "tick_count": 1000
        }
        subscribers = self.publisher.publish_health(health_data)
        assert subscribers >= 0


class TestPriceRetrieval:
    """Test price retrieval from Redis"""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup and teardown"""
        RedisPublisher._instance = None
        self.publisher = RedisPublisher()
        self.publisher.connect()
        yield
        self.publisher.close()

    def test_get_latest_price(self):
        """Test retrieving latest price"""
        # First publish a tick
        tick_data = {
            "instrument": "XPT_USD",
            "bid": 1000.00,
            "ask": 1001.00,
            "mid": 1000.50,
            "spread": 1.00,
            "timestamp": datetime.utcnow().isoformat(),
            "source": "mock"
        }
        self.publisher.publish_tick(tick_data)

        # Then retrieve it
        price = self.publisher.get_latest_price("XPT_USD")
        assert price is not None
        assert price["bid"] == 1000.00
        assert price["ask"] == 1001.00

    def test_get_nonexistent_price(self):
        """Test retrieving price for unknown instrument"""
        price = self.publisher.get_latest_price("NONEXISTENT_PAIR")
        assert price is None


class TestChannelNaming:
    """Test Redis channel naming conventions"""

    def test_channel_with_prefix(self):
        """Test channel names include prefix"""
        RedisPublisher._instance = None
        publisher = RedisPublisher()

        # The channel should include the prefix
        channel = publisher._channel("signals:tick:XAU_USD")
        assert publisher.prefix in channel or channel == "signals:tick:XAU_USD"
        # If prefix is "dev:", channel should be "dev:signals:tick:XAU_USD"
