"""
Pytest fixtures for tradingSignals tests
EPIC-D002 / STORY-D002-05
GOV-ENV-001: Environment-aware configuration
"""
import os
import sys
import pytest
from datetime import datetime
from pathlib import Path

# Add parent path for imports (relative to this file)
# CRITICAL: Insert at position 0 to override any /srv-dev/config shadows
TESTS_DIR = Path(__file__).parent.absolute()
BASE_DIR = TESTS_DIR.parent

# Remove any paths that might shadow our imports
sys.path = [p for p in sys.path if 'config' not in p or 'tradingSignals' in p]
sys.path.insert(0, str(BASE_DIR))

# Load test environment
from dotenv import load_dotenv
load_dotenv(BASE_DIR / '.env')


@pytest.fixture
def sample_tick_data():
    """Sample tick data for testing"""
    return {
        "instrument": "XAU_USD",
        "bid": 2650.50,
        "ask": 2651.00,
        "timestamp": datetime.utcnow().isoformat(),
        "source": "mock"
    }


@pytest.fixture
def sample_candle_data():
    """Sample candle data for testing"""
    return {
        "instrument": "XAU_USD",
        "timeframe": "M5",
        "timestamp": datetime.utcnow().isoformat(),
        "open": 2650.00,
        "high": 2655.00,
        "low": 2649.00,
        "close": 2652.00,
        "volume": 150,
        "complete": True
    }


@pytest.fixture
def redis_config():
    """Redis configuration from environment"""
    return {
        "host": os.getenv("REDIS_HOST", "127.0.0.1"),
        "port": int(os.getenv("REDIS_PORT", "6379")),
        "password": os.getenv("REDIS_PASSWORD", ""),
        "db": int(os.getenv("REDIS_DB", "1"))
    }


@pytest.fixture
def db_config():
    """Database configuration from environment"""
    return {
        "host": os.getenv("DB_HOST", "127.0.0.1"),
        "port": int(os.getenv("DB_PORT", "3306")),
        "user": os.getenv("DB_USER", "root"),
        "password": os.getenv("DB_PASSWORD", ""),
        "database": os.getenv("DB_NAME", "tradingSignals")
    }
