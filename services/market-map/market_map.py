#!/usr/bin/env python3
"""
Market Map Builder Service
EPIC-D002: tradingSignals - Single Source of Truth

Builds market map snapshots using candle data from tradingSignals.
Publishes to Redis for consumers (decision-daemon, etc.).

The Market Map provides:
- Current trading session context
- Key levels (PDH, PDL, session ranges)
- ADR metrics and usage percentage
- Current bid/ask prices

GOV-ENV-001: All configuration from environment variables.
GOV-CAB-CODE-001: No hardcoded ports/DB/usernames/passwords.

Usage:
    python market_map.py [--once] [--interval=60]
"""

import os
import sys
import time
import signal
import logging
import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional, Dict, Any, Tuple

import pymysql
import pymysql.cursors

# Add parent path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from env_config import get_db_config, get_redis_config, get_env_list
from utils.redis_publisher import RedisPublisher

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('market-map')


class MarketMapBuilder:
    """Builds market map snapshots for trading instruments."""

    def __init__(self, redis_publisher: RedisPublisher = None):
        self.running = True

        # Get DB config from environment (GOV-ENV-001)
        db_cfg = get_db_config()
        self.db_config = {
            'host': db_cfg['host'],
            'port': db_cfg['port'],
            'user': db_cfg['user'],
            'password': db_cfg['password'],
            'database': db_cfg['database'],
            'charset': 'utf8mb4',
            'autocommit': False,
        }

        # Connect to tradingSignals database
        self.db = pymysql.connect(**self.db_config)

        # Redis publisher for real-time updates
        self.redis = redis_publisher

        # Load instruments from environment or database
        self.instruments = self._load_instruments()

        # Load trading windows
        self.trading_windows = self._load_trading_windows()

        # Setup signal handlers
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

        logger.info(f"MarketMapBuilder initialized for {len(self.instruments)} instruments")

    def _handle_signal(self, signum, frame):
        logger.info(f"Received signal {signum}, shutting down...")
        self.running = False

    def _reconnect_db(self):
        """Reconnect to database if connection lost."""
        try:
            self.db.ping(reconnect=True)
        except Exception:
            logger.warning("Reconnecting to database...")
            self.db = pymysql.connect(**self.db_config)

    def _load_instruments(self) -> list:
        """Load instruments from environment or database."""
        # Try environment first
        env_instruments = get_env_list('INSTRUMENTS', [])
        if env_instruments:
            return env_instruments

        # Fall back to database
        cursor = self.db.cursor()
        cursor.execute("SELECT symbol FROM instruments WHERE is_active = TRUE")
        instruments = [row[0] for row in cursor.fetchall()]
        cursor.close()
        return instruments

    def _load_trading_windows(self) -> Dict[str, dict]:
        """Load trading windows from database."""
        cursor = self.db.cursor(pymysql.cursors.DictCursor)
        cursor.execute("""
            SELECT session_name, start_time_utc, end_time_utc,
                   dst_region, typical_adr_pct
            FROM trading_windows
            WHERE is_active = TRUE
        """)
        windows = {row['session_name']: row for row in cursor.fetchall()}
        cursor.close()
        return windows

    def _timedelta_to_seconds(self, td: timedelta) -> int:
        """Convert timedelta (from MariaDB TIME) to seconds since midnight."""
        return int(td.total_seconds())

    def get_current_session(self, utc_now: datetime = None) -> str:
        """Determine current trading session based on UTC time."""
        if utc_now is None:
            utc_now = datetime.now(timezone.utc)

        current_seconds = utc_now.hour * 3600 + utc_now.minute * 60 + utc_now.second

        # Priority order for session detection
        for session_name in ['overlap_ldn_ny', 'london', 'newyork', 'asia', 'off_hours']:
            window = self.trading_windows.get(session_name)
            if not window:
                continue

            start_td = window['start_time_utc']
            end_td = window['end_time_utc']
            start_secs = self._timedelta_to_seconds(start_td)
            end_secs = self._timedelta_to_seconds(end_td)

            # Handle sessions crossing midnight
            if start_secs > end_secs:
                if current_seconds >= start_secs or current_seconds < end_secs:
                    return session_name
            else:
                if start_secs <= current_seconds < end_secs:
                    return session_name

        return 'off_hours'

    def get_previous_day_range(self, instrument: str, reference_date: datetime = None) -> Tuple[Optional[Decimal], Optional[Decimal]]:
        """Get previous day's high and low from D1 candles."""
        if reference_date is None:
            reference_date = datetime.now(timezone.utc)

        yesterday = (reference_date - timedelta(days=1)).date()

        cursor = self.db.cursor()
        cursor.execute("""
            SELECT high as pdh, low as pdl
            FROM candles_D1
            WHERE instrument = %s
            AND DATE(timestamp) = %s
        """, (instrument, yesterday))

        row = cursor.fetchone()
        cursor.close()

        if row and row[0] is not None:
            return Decimal(str(row[0])), Decimal(str(row[1]))
        return None, None

    def get_session_range(self, instrument: str, session_name: str,
                          reference_date: datetime = None) -> Tuple[Optional[Decimal], Optional[Decimal]]:
        """Get high/low for a specific session using H1 candles."""
        if reference_date is None:
            reference_date = datetime.now(timezone.utc)

        window = self.trading_windows.get(session_name)
        if not window:
            return None, None

        today = reference_date.date()
        start_td = window['start_time_utc']
        end_td = window['end_time_utc']
        start_secs = self._timedelta_to_seconds(start_td)
        end_secs = self._timedelta_to_seconds(end_td)

        cursor = self.db.cursor()

        if start_secs > end_secs:
            # Session crosses midnight
            cursor.execute("""
                SELECT MAX(high) as high, MIN(low) as low
                FROM candles_H1
                WHERE instrument = %s
                AND ((DATE(timestamp) = %s AND HOUR(timestamp) * 3600 >= %s)
                     OR (DATE(timestamp) = %s AND HOUR(timestamp) * 3600 < %s))
            """, (instrument, today, start_secs,
                  today + timedelta(days=1), end_secs))
        else:
            cursor.execute("""
                SELECT MAX(high) as high, MIN(low) as low
                FROM candles_H1
                WHERE instrument = %s
                AND DATE(timestamp) = %s
                AND HOUR(timestamp) * 3600 >= %s
                AND HOUR(timestamp) * 3600 < %s
            """, (instrument, today, start_secs, end_secs))

        row = cursor.fetchone()
        cursor.close()

        if row and row[0] is not None:
            return Decimal(str(row[0])), Decimal(str(row[1]))
        return None, None

    def calculate_adr(self, instrument: str, period: int = 20) -> Optional[Decimal]:
        """Calculate Average Daily Range over specified period."""
        cursor = self.db.cursor()
        cursor.execute("""
            SELECT AVG(high - low) as adr
            FROM (
                SELECT high, low
                FROM candles_D1
                WHERE instrument = %s
                AND high IS NOT NULL AND low IS NOT NULL
                ORDER BY timestamp DESC
                LIMIT %s
            ) recent_ranges
        """, (instrument, period))

        row = cursor.fetchone()
        cursor.close()

        if row and row[0] is not None:
            return Decimal(str(row[0]))
        return None

    def get_current_price(self, instrument: str) -> Tuple[Optional[Decimal], Optional[Decimal]]:
        """Get current price from latest M5 candle."""
        cursor = self.db.cursor()
        cursor.execute("""
            SELECT close
            FROM candles_M5
            WHERE instrument = %s
            ORDER BY timestamp DESC
            LIMIT 1
        """, (instrument,))

        row = cursor.fetchone()
        cursor.close()

        if row:
            price = Decimal(str(row[0]))
            return price, price
        return None, None

    def build_market_map(self, instrument: str) -> Dict[str, Any]:
        """Build complete market map for an instrument."""
        now = datetime.now(timezone.utc)

        current_session = self.get_current_session(now)
        pdh, pdl = self.get_previous_day_range(instrument, now)
        asia_high, asia_low = self.get_session_range(instrument, 'asia', now)
        london_high, london_low = self.get_session_range(instrument, 'london', now)
        adr_20 = self.calculate_adr(instrument, 20)
        current_bid, current_ask = self.get_current_price(instrument)

        # Calculate ADR percentage used
        adr_pct_used = None
        if adr_20 and pdh and pdl and current_bid:
            today_range = max(
                float(current_bid) - float(pdl) if pdl else 0,
                float(pdh) - float(current_bid) if pdh else 0,
                float(pdh) - float(pdl) if pdh and pdl else 0
            )
            if float(adr_20) > 0:
                adr_pct_used = round(today_range / float(adr_20) * 100, 2)

        # Session start time
        window = self.trading_windows.get(current_session, {})
        session_start = None
        if window.get('start_time_utc'):
            start_td = window['start_time_utc']
            start_secs = self._timedelta_to_seconds(start_td)
            hours, remainder = divmod(start_secs, 3600)
            minutes, seconds = divmod(remainder, 60)
            session_start = now.replace(hour=hours, minute=minutes, second=seconds, microsecond=0)

        return {
            'instrument': instrument,
            'current_session': current_session,
            'session_start_utc': session_start.isoformat() if session_start else None,
            'pdh': float(pdh) if pdh else None,
            'pdl': float(pdl) if pdl else None,
            'asia_high': float(asia_high) if asia_high else None,
            'asia_low': float(asia_low) if asia_low else None,
            'london_high': float(london_high) if london_high else None,
            'london_low': float(london_low) if london_low else None,
            'adr_20': float(adr_20) if adr_20 else None,
            'adr_pct_used': adr_pct_used,
            'current_bid': float(current_bid) if current_bid else None,
            'current_ask': float(current_ask) if current_ask else None,
            'timestamp': now.isoformat(),
        }

    def publish_market_map(self, market_map: dict) -> bool:
        """Publish market map to Redis."""
        if not self.redis or not self.redis.is_connected:
            return False

        try:
            redis_cfg = get_redis_config()
            key_prefix = redis_cfg['key_prefix']
            instrument = market_map['instrument']
            key = f"{key_prefix}market_map:{instrument}"

            # Convert to string values for Redis hash
            mapping = {k: str(v) if v is not None else '' for k, v in market_map.items()}
            mapping['updated_at'] = datetime.now(timezone.utc).isoformat()

            self.redis._client.hset(key, mapping=mapping)
            self.redis._client.expire(key, 600)  # 10 min TTL

            return True
        except Exception as e:
            logger.error(f"Failed to publish market map: {e}")
            return False

    def update_all_instruments(self) -> int:
        """Update market maps for all instruments. Returns count updated."""
        self._reconnect_db()
        updated = 0

        for instrument in self.instruments:
            try:
                market_map = self.build_market_map(instrument)
                if self.publish_market_map(market_map):
                    logger.debug(f"Published {instrument} market map")
                    updated += 1
            except Exception as e:
                logger.error(f"Error updating {instrument}: {e}")

        return updated

    def run(self, interval: float = 60.0, once: bool = False):
        """Main run loop."""
        logger.info(f"Starting market map builder (interval={interval}s, once={once})")

        while self.running:
            try:
                start = datetime.now(timezone.utc).timestamp()
                updated = self.update_all_instruments()
                elapsed = datetime.now(timezone.utc).timestamp() - start

                logger.info(f"Updated {updated}/{len(self.instruments)} instruments in {elapsed:.2f}s")

                if once:
                    break

                sleep_time = max(0, interval - elapsed)
                if sleep_time > 0:
                    time.sleep(sleep_time)

            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.exception("Error in main loop")
                time.sleep(5)

        logger.info("Market map builder stopped")

    def close(self):
        """Clean shutdown."""
        self.running = False
        if self.db.open:
            self.db.close()


def main():
    parser = argparse.ArgumentParser(description='Market Map Builder')
    parser.add_argument('--once', action='store_true', help='Run once and exit')
    parser.add_argument('--interval', type=float, default=60.0,
                        help='Seconds between updates (default: 60)')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Enable debug logging')
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Initialize Redis publisher
    redis_pub = RedisPublisher()
    if not redis_pub.connect():
        logger.warning("Redis connection failed - will retry")

    builder = MarketMapBuilder(redis_publisher=redis_pub)
    try:
        builder.run(interval=args.interval, once=args.once)
    finally:
        builder.close()
        if redis_pub:
            redis_pub.close()


if __name__ == '__main__':
    main()
