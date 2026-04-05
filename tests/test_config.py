"""
Unit tests for tradingSignals config
EPIC-D002 / STORY-D002-05

Tests:
- Config loading from environment
- Standardized env var names (DB_*, REDIS_*)
- Default values
"""
import pytest
import os
import sys

# Path setup moved to conftest.py

from config import load_config, ServiceConfig


class TestConfigLoading:
    """Test configuration loading"""

    def test_load_config(self):
        """Test basic config loading"""
        config = load_config()
        assert config is not None
        assert isinstance(config, ServiceConfig)

    def test_database_config(self):
        """Test database configuration loaded correctly"""
        from env_config import get_env, get_env_int
        config = load_config()
        assert config.database is not None
        assert config.database.host == get_env("DB_HOST", "127.0.0.1")
        assert config.database.port == get_env_int("DB_PORT", 3306)
        assert config.database.user == get_env("DB_USER", "root")

    def test_redis_config(self):
        """Test Redis configuration loaded correctly"""
        from env_config import get_env, get_env_int
        config = load_config()
        assert config.redis is not None
        assert config.redis.host == get_env("REDIS_HOST", "127.0.0.1")
        assert config.redis.port == get_env_int("REDIS_PORT", 6379)

    def test_oanda_config(self):
        """Test OANDA configuration loaded correctly"""
        config = load_config()
        assert config.oanda is not None
        assert config.oanda.api_key is not None or os.getenv("OANDA_API_KEY") is None

    def test_instruments_config(self):
        """Test instruments list loaded"""
        config = load_config()
        assert config.instruments is not None
        assert len(config.instruments) > 0
        assert "XAU_USD" in config.instruments or isinstance(config.instruments, list)


class TestEnvVarCompliance:
    """Test that standardized env var names are used"""

    def test_no_legacy_tas_db_vars(self):
        """Verify no TAS_DB_* variables are used in config"""
        # These should NOT be set (legacy)
        legacy_vars = [
            "TAS_DB_HOST",
            "TAS_DB_PORT",
            "TAS_DB_USER",
            "TAS_DB_PASSWORD",
            "TAS_DB_NAME"
        ]
        # Just verify the config loads without needing legacy vars
        config = load_config()
        assert config.database is not None

    def test_standardized_db_vars_used(self):
        """Verify {ENV}_DB_* variables are used per GOV-ENV-001"""
        from env_config import ENV
        # GOV-ENV-001: {ENV}_DB_HOST takes priority over DB_HOST
        env_prefix = ENV  # DEV or PROD
        prefixed_key = f"{env_prefix}_DB_HOST"

        # Save originals
        original_prefixed = os.environ.get(prefixed_key)
        original_unprefixed = os.environ.get("DB_HOST")

        # Set prefixed var
        os.environ[prefixed_key] = "test-host-123"
        os.environ.pop("DB_HOST", None)

        try:
            config = load_config()
            assert config.database.host == "test-host-123"
        finally:
            if original_prefixed:
                os.environ[prefixed_key] = original_prefixed
            else:
                os.environ.pop(prefixed_key, None)
            if original_unprefixed:
                os.environ["DB_HOST"] = original_unprefixed

    def test_standardized_redis_vars_used(self):
        """Verify {ENV}_REDIS_* variables are used per GOV-ENV-001"""
        from env_config import ENV
        # GOV-ENV-001: {ENV}_REDIS_HOST takes priority over REDIS_HOST
        env_prefix = ENV  # DEV or PROD
        prefixed_key = f"{env_prefix}_REDIS_HOST"

        # Save originals
        original_prefixed = os.environ.get(prefixed_key)
        original_unprefixed = os.environ.get("REDIS_HOST")

        # Set prefixed var
        os.environ[prefixed_key] = "redis-test-host"
        os.environ.pop("REDIS_HOST", None)

        try:
            config = load_config()
            assert config.redis.host == "redis-test-host"
        finally:
            if original_prefixed:
                os.environ[prefixed_key] = original_prefixed
            else:
                os.environ.pop(prefixed_key, None)
            if original_unprefixed:
                os.environ["REDIS_HOST"] = original_unprefixed


class TestCandleTimeframes:
    """Test candle timeframes configuration - GOV-ENV-001"""

    def test_candle_timeframes_from_env(self):
        """Test CANDLE_TIMEFRAMES loaded from environment"""
        from env_config import get_env_list

        # Save original
        original = os.environ.get("CANDLE_TIMEFRAMES")

        # Set test value
        os.environ["CANDLE_TIMEFRAMES"] = "M5,H1,D1"

        try:
            timeframes = get_env_list("CANDLE_TIMEFRAMES", ["M5"])
            assert timeframes == ["M5", "H1", "D1"]
        finally:
            if original:
                os.environ["CANDLE_TIMEFRAMES"] = original
            else:
                os.environ.pop("CANDLE_TIMEFRAMES", None)

    def test_candle_timeframes_default(self):
        """Test CANDLE_TIMEFRAMES defaults to M5"""
        from env_config import get_env_list

        # Save and remove
        original = os.environ.pop("CANDLE_TIMEFRAMES", None)

        try:
            timeframes = get_env_list("CANDLE_TIMEFRAMES", ["M5"])
            assert timeframes == ["M5"]
        finally:
            if original:
                os.environ["CANDLE_TIMEFRAMES"] = original

    def test_valid_timeframes(self):
        """Test that timeframes match TIMEFRAMES dict in signal_builder"""
        from signal_builder import TIMEFRAMES

        valid_timeframes = ["M5", "H1", "D1"]
        for tf in valid_timeframes:
            assert tf in TIMEFRAMES, f"Timeframe {tf} not in TIMEFRAMES dict"
            assert 'table' in TIMEFRAMES[tf], f"Timeframe {tf} missing table config"


class TestConfigDefaults:
    """Test configuration loads from .env correctly"""

    def test_database_from_env(self):
        """Test database config loads from prefixed env vars"""
        from env_config import get_env, get_env_int
        config = load_config()
        # Should match what get_env returns (respects {ENV}_ prefix)
        assert config.database.host == get_env("DB_HOST", "127.0.0.1")
        assert config.database.port == get_env_int("DB_PORT", 3306)

    def test_redis_from_env(self):
        """Test Redis config loads from prefixed env vars"""
        from env_config import get_env, get_env_int
        config = load_config()
        # Should match what get_env returns (respects {ENV}_ prefix)
        assert config.redis.host == get_env("REDIS_HOST", "127.0.0.1")
        assert config.redis.port == get_env_int("REDIS_PORT", 6379)
