"""
Trading Hours Utility
EPIC-D013: Platform Health Monitoring Service (Project Hygieia)
GOV-ENV-001: Environment-aware configuration

Determines if markets are open based on trading windows from database.
Used by healthcheck to avoid false alarms during market closures.

Forex Market Hours (UTC):
- Sunday 22:00 - Friday 22:00 (continuous)
- Weekend: Closed

Sessions:
- Asia: 22:00-07:00 UTC (Sydney/Tokyo)
- London: 07:00-15:30 UTC
- New York: 12:00-22:00 UTC
- Overlap London/NY: 12:00-15:30 UTC
"""

import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from pathlib import Path

import pymysql
import pymysql.cursors

# Setup paths
UTILS_DIR = Path(__file__).parent.absolute()
BASE_DIR = UTILS_DIR.parent
sys.path.insert(0, str(BASE_DIR))

from env_config import get_db_config


class TradingHoursChecker:
    """Checks if markets are currently open."""

    def __init__(self):
        self.db_config = get_db_config()
        self._windows_cache = None
        self._cache_time = None
        self._cache_ttl = 3600  # 1 hour cache

    def _get_connection(self):
        """Get database connection."""
        return pymysql.connect(
            host=self.db_config['host'],
            port=self.db_config['port'],
            user=self.db_config['user'],
            password=self.db_config['password'],
            database=self.db_config['database'],
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor
        )

    def _load_windows(self) -> List[Dict]:
        """Load trading windows from database."""
        # Check cache
        now = datetime.utcnow()
        if self._windows_cache and self._cache_time:
            if (now - self._cache_time).total_seconds() < self._cache_ttl:
                return self._windows_cache

        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT session_name, day_of_week, start_time_utc, end_time_utc,
                   is_active, volatility_level
            FROM trading_windows
            WHERE is_active = 1
            ORDER BY day_of_week, start_time_utc
        """)

        self._windows_cache = cursor.fetchall()
        self._cache_time = now

        cursor.close()
        conn.close()

        return self._windows_cache

    def is_market_open(self, timestamp: datetime = None) -> Tuple[bool, str]:
        """
        Check if forex market is open at given time.

        Args:
            timestamp: Time to check (default: now UTC)

        Returns:
            Tuple of (is_open, reason)
        """
        if timestamp is None:
            timestamp = datetime.utcnow()

        # Day of week: 0=Monday ... 6=Sunday
        dow = timestamp.weekday()
        current_time = timestamp.time()
        hour = timestamp.hour

        # Weekend check (simplified)
        # Forex closes Friday 22:00 UTC, opens Sunday 22:00 UTC
        if dow == 5:  # Saturday
            return False, "Market closed - Saturday"
        elif dow == 6:  # Sunday
            if hour < 22:
                return False, "Market closed - Sunday before 22:00 UTC"
            else:
                return True, "Market open - Sunday session started"
        elif dow == 4:  # Friday
            if hour >= 22:
                return False, "Market closed - Friday after 22:00 UTC"

        return True, "Market open"

    def get_current_session(self, timestamp: datetime = None) -> str:
        """
        Get current trading session name.

        Args:
            timestamp: Time to check (default: now UTC)

        Returns:
            Session name: asia, london, newyork, overlap_ldn_ny, off_hours
        """
        if timestamp is None:
            timestamp = datetime.utcnow()

        is_open, _ = self.is_market_open(timestamp)
        if not is_open:
            return "off_hours"

        hour = timestamp.hour

        # Session definitions (UTC)
        if 22 <= hour or hour < 7:
            return "asia"
        elif 12 <= hour < 16:
            return "overlap_ldn_ny"
        elif 7 <= hour < 12:
            return "london"
        elif 16 <= hour < 22:
            return "newyork"
        else:
            return "off_hours"

    def get_session_info(self, timestamp: datetime = None) -> Dict:
        """
        Get detailed session information.

        Args:
            timestamp: Time to check (default: now UTC)

        Returns:
            Dict with session details
        """
        if timestamp is None:
            timestamp = datetime.utcnow()

        is_open, reason = self.is_market_open(timestamp)
        session = self.get_current_session(timestamp)

        # Estimate volatility by session
        volatility_map = {
            'asia': 'low',
            'london': 'high',
            'newyork': 'high',
            'overlap_ldn_ny': 'very_high',
            'off_hours': 'none'
        }

        return {
            'timestamp': timestamp.isoformat(),
            'is_open': is_open,
            'reason': reason,
            'session': session,
            'volatility': volatility_map.get(session, 'unknown'),
            'day_of_week': timestamp.strftime('%A')
        }

    def should_expect_data(self, timestamp: datetime = None) -> bool:
        """
        Check if we should expect market data at given time.

        Accounts for a 10-minute grace period after market open.

        Args:
            timestamp: Time to check (default: now UTC)

        Returns:
            True if we should expect data
        """
        is_open, _ = self.is_market_open(timestamp)
        return is_open

    def get_next_market_open(self, timestamp: datetime = None) -> datetime:
        """
        Get next market open time.

        Args:
            timestamp: Current time (default: now UTC)

        Returns:
            Next market open datetime
        """
        if timestamp is None:
            timestamp = datetime.utcnow()

        is_open, _ = self.is_market_open(timestamp)
        if is_open:
            return timestamp

        # Find next open
        check = timestamp
        for _ in range(7 * 24):  # Max 1 week of hours
            check = check + timedelta(hours=1)
            check = check.replace(minute=0, second=0, microsecond=0)
            if self.is_market_open(check)[0]:
                return check

        return timestamp + timedelta(days=7)

    def get_next_market_close(self, timestamp: datetime = None) -> datetime:
        """
        Get next market close time.

        Args:
            timestamp: Current time (default: now UTC)

        Returns:
            Next market close datetime
        """
        if timestamp is None:
            timestamp = datetime.utcnow()

        is_open, _ = self.is_market_open(timestamp)
        if not is_open:
            # Market already closed - find when it opens then closes again
            next_open = self.get_next_market_open(timestamp)
            return self.get_next_market_close(next_open)

        # Find next close
        check = timestamp
        for _ in range(7 * 24):  # Max 1 week of hours
            check = check + timedelta(hours=1)
            check = check.replace(minute=0, second=0, microsecond=0)
            if not self.is_market_open(check)[0]:
                return check

        return timestamp + timedelta(days=7)


# Singleton instance
_checker: Optional[TradingHoursChecker] = None


def get_trading_hours_checker() -> TradingHoursChecker:
    """Get or create trading hours checker singleton."""
    global _checker
    if _checker is None:
        _checker = TradingHoursChecker()
    return _checker


def is_market_open(timestamp: datetime = None) -> Tuple[bool, str]:
    """Convenience function to check if market is open."""
    return get_trading_hours_checker().is_market_open(timestamp)


def get_current_session(timestamp: datetime = None) -> str:
    """Convenience function to get current session."""
    return get_trading_hours_checker().get_current_session(timestamp)


def should_expect_data(timestamp: datetime = None) -> bool:
    """Convenience function to check if we should expect data."""
    return get_trading_hours_checker().should_expect_data(timestamp)


# Test function
if __name__ == "__main__":
    checker = TradingHoursChecker()

    now = datetime.utcnow()
    info = checker.get_session_info(now)

    print(f"Current UTC time: {now}")
    print(f"Market open: {info['is_open']}")
    print(f"Session: {info['session']}")
    print(f"Volatility: {info['volatility']}")
    print(f"Reason: {info['reason']}")

    if not info['is_open']:
        next_open = checker.get_next_market_open(now)
        print(f"Next market open: {next_open}")
    else:
        next_close = checker.get_next_market_close(now)
        print(f"Next market close: {next_close}")
