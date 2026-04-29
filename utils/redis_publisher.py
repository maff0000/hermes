"""
Redis Pub/Sub Publisher for Signal Service
EPIC-D002: Standalone Signal Service
STORY-D002-04: Redis Pub/Sub Publisher
GOV-ENV-001: Environment-aware configuration

Publishes ticks and candles to Redis channels for real-time consumers.

Channels:
- {prefix}signals:tick:{instrument}     - Real-time ticks
- {prefix}signals:candle:{tf}:{instrument} - Completed candles
- {prefix}signals:health               - Service health updates
"""
import sys
import json
import logging
from datetime import datetime, timezone
from typing import Optional
from pathlib import Path

import redis

# Setup - use relative path from module location
UTILS_DIR = Path(__file__).parent.absolute()
BASE_DIR = UTILS_DIR.parent
sys.path.insert(0, str(BASE_DIR))

from env_config import get_redis_config

logger = logging.getLogger(__name__)


class RedisPublisher:
    """
    Publishes market data to Redis pub/sub channels.
    Thread-safe singleton pattern.
    """
    _instance: Optional['RedisPublisher'] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        # Use environment-aware config (GOV-ENV-001)
        redis_cfg = get_redis_config()
        self.host = redis_cfg['host']
        self.port = redis_cfg['port']
        self.password = redis_cfg['password']
        self.db = redis_cfg['db']
        self.prefix = redis_cfg['key_prefix']

        self._client: Optional[redis.Redis] = None
        self._connected = False
        self._initialized = True

        logger.info(f"RedisPublisher initialized: {self.host}:{self.port} db={self.db} prefix={self.prefix}")

    def connect(self) -> bool:
        """Establish Redis connection"""
        try:
            self._client = redis.Redis(
                host=self.host,
                port=self.port,
                password=self.password,
                db=self.db,
                decode_responses=True,
                socket_timeout=5,
                socket_connect_timeout=5
            )
            # Test connection
            self._client.ping()
            self._connected = True
            logger.info("Redis connection established")
            return True
        except redis.ConnectionError as e:
            logger.error(f"Redis connection failed: {e}")
            self._connected = False
            return False

    @property
    def is_connected(self) -> bool:
        """Check if Redis is connected"""
        if not self._client:
            return False
        try:
            self._client.ping()
            return True
        except:
            self._connected = False
            return False

    def _channel(self, *parts) -> str:
        """Build channel name with prefix"""
        return f"{self.prefix}{''.join(parts)}"

    def publish_tick(self, tick_data: dict) -> int:
        """
        Publish tick to Redis channel.

        Args:
            tick_data: Dict with instrument, bid, ask, mid, timestamp, etc.

        Returns:
            Number of subscribers that received the message
        """
        if not self.is_connected:
            if not self.connect():
                return 0

        try:
            instrument = tick_data.get('instrument', 'UNKNOWN')
            channel = self._channel(f"signals:tick:{instrument}")

            # Add publish timestamp
            tick_data['published_at'] = datetime.now(timezone.utc).isoformat()

            message = json.dumps(tick_data)
            subscribers = self._client.publish(channel, message)

            # Also update latest price hash
            price_key = self._channel(f"signals:price:{instrument}")
            self._client.hset(price_key, mapping={
                'bid': str(tick_data.get('bid', 0)),
                'ask': str(tick_data.get('ask', 0)),
                'mid': str(tick_data.get('mid', 0)),
                'spread': str(tick_data.get('spread', 0)),
                'timestamp': tick_data.get('timestamp', ''),
                'source': tick_data.get('source', 'unknown'),  # mock/oanda/ibkr
                'updated_at': datetime.now(timezone.utc).isoformat()
            })
            self._client.expire(price_key, 300)  # 5 min TTL

            return subscribers

        except redis.RedisError as e:
            logger.error(f"Failed to publish tick: {e}")
            self._connected = False
            return 0

    def publish_candle(self, timeframe: str, candle_data: dict) -> int:
        """
        Publish completed candle to Redis channel.

        Args:
            timeframe: M1, M5, H1, D1
            candle_data: Dict with instrument, timestamp, open, high, low, close, volume

        Returns:
            Number of subscribers that received the message
        """
        if not self.is_connected:
            if not self.connect():
                return 0

        try:
            instrument = candle_data.get('instrument', 'UNKNOWN')
            channel = self._channel(f"signals:candle:{timeframe}:{instrument}")

            # Add publish timestamp
            candle_data['published_at'] = datetime.now(timezone.utc).isoformat()
            candle_data['timeframe'] = timeframe

            message = json.dumps(candle_data, default=str)
            subscribers = self._client.publish(channel, message)

            # Also update latest candle hash
            candle_key = self._channel(f"signals:candle:{timeframe}:{instrument}:latest")
            self._client.hset(candle_key, mapping={
                'open': str(candle_data.get('open', 0)),
                'high': str(candle_data.get('high', 0)),
                'low': str(candle_data.get('low', 0)),
                'close': str(candle_data.get('close', 0)),
                'volume': str(candle_data.get('volume', 0)),
                'timestamp': str(candle_data.get('timestamp', '')),
                'updated_at': datetime.now(timezone.utc).isoformat()
            })
            self._client.expire(candle_key, 3600)  # 1 hour TTL

            return subscribers

        except redis.RedisError as e:
            logger.error(f"Failed to publish candle: {e}")
            self._connected = False
            return 0

    def publish_health(self, health_data: dict) -> int:
        """
        Publish health status update.

        Args:
            health_data: Dict with status, adapters, last_tick, etc.

        Returns:
            Number of subscribers
        """
        if not self.is_connected:
            if not self.connect():
                return 0

        try:
            channel = self._channel("signals:health")
            health_data['timestamp'] = datetime.now(timezone.utc).isoformat()

            message = json.dumps(health_data, default=str)
            subscribers = self._client.publish(channel, message)

            # Also update health key
            health_key = self._channel("signals:health:status")
            self._client.hset(health_key, mapping={
                'status': health_data.get('status', 'unknown'),
                'updated_at': datetime.now(timezone.utc).isoformat()
            })
            self._client.expire(health_key, 60)  # 1 min TTL

            return subscribers

        except redis.RedisError as e:
            logger.error(f"Failed to publish health: {e}")
            return 0

    def get_latest_price(self, instrument: str) -> Optional[dict]:
        """Get latest price from Redis hash"""
        if not self.is_connected:
            if not self.connect():
                return None

        try:
            price_key = self._channel(f"signals:price:{instrument}")
            data = self._client.hgetall(price_key)
            if data:
                return {
                    'instrument': instrument,
                    'bid': float(data.get('bid', 0)),
                    'ask': float(data.get('ask', 0)),
                    'mid': float(data.get('mid', 0)),
                    'spread': float(data.get('spread', 0)),
                    'timestamp': data.get('timestamp', ''),
                    'updated_at': data.get('updated_at', '')
                }
            return None
        except redis.RedisError as e:
            logger.error(f"Failed to get price: {e}")
            return None

    def publish_instrument_config(self, instrument: str, config: dict) -> bool:
        """
        Publish instrument configuration to Redis.

        Stores instrument config as a hash for fast lookup by any service.
        Key pattern: {prefix}instrument:{symbol}

        Args:
            instrument: Instrument symbol (e.g., 'XAU_USD')
            config: Dict with pip_value_per_lot, contract_size, etc.

        Returns:
            True if successful
        """
        if not self.is_connected:
            if not self.connect():
                return False

        try:
            key = self._channel(f"instrument:{instrument}")

            # Convert all values to strings for Redis hash
            mapping = {k: str(v) for k, v in config.items()}
            mapping['updated_at'] = datetime.now(timezone.utc).isoformat()

            self._client.hset(key, mapping=mapping)
            logger.debug(f"Published instrument config: {instrument}")
            return True

        except redis.RedisError as e:
            logger.error(f"Failed to publish instrument config {instrument}: {e}")
            return False

    def publish_instruments_list(self, instruments: list) -> bool:
        """
        Publish list of enabled instruments to Redis.

        Key: {prefix}instruments:enabled (SET)

        Args:
            instruments: List of enabled instrument symbols

        Returns:
            True if successful
        """
        if not self.is_connected:
            if not self.connect():
                return False

        try:
            key = self._channel("instruments:enabled")

            # Clear and repopulate
            self._client.delete(key)
            if instruments:
                self._client.sadd(key, *instruments)

            logger.info(f"Published {len(instruments)} enabled instruments to Redis")
            return True

        except redis.RedisError as e:
            logger.error(f"Failed to publish instruments list: {e}")
            return False

    def get_instrument_config(self, instrument: str) -> Optional[dict]:
        """
        Get instrument configuration from Redis.

        Args:
            instrument: Instrument symbol (e.g., 'XAU_USD')

        Returns:
            Dict with config or None if not found
        """
        if not self.is_connected:
            if not self.connect():
                return None

        try:
            key = self._channel(f"instrument:{instrument}")
            data = self._client.hgetall(key)

            if data:
                # Convert numeric fields back from strings
                return {
                    'symbol': instrument,
                    'pip_value_per_lot': float(data.get('pip_value_per_lot', 10)),
                    'contract_size': int(data.get('contract_size', 100)),
                    'default_sl_distance': float(data.get('default_sl_distance', 15)),
                    'default_tp_multiplier': float(data.get('default_tp_multiplier', 1.5)),
                    'min_lot_size': float(data.get('min_lot_size', 0.01)),
                    'max_lot_size': float(data.get('max_lot_size', 100)),
                    'updated_at': data.get('updated_at', '')
                }
            return None

        except redis.RedisError as e:
            logger.error(f"Failed to get instrument config {instrument}: {e}")
            return None

    def close(self):
        """Close Redis connection"""
        if self._client:
            self._client.close()
            self._connected = False
            logger.info("Redis connection closed")


# Singleton instance
publisher = RedisPublisher()
