"""
Configuration for Signal Service
EPIC-D002: Standalone Signal Service
GOV-ENV-001: Environment-aware configuration
"""
from dataclasses import dataclass
from typing import List, Optional

# Use environment-aware config loader (no hardcoded paths)
from env_config import get_env, get_env_int, get_env_bool, get_env_list, ENV, BASE_DIR


@dataclass
class OANDAConfig:
    """
    OANDA API configuration

    MOCK FUNCTIONALITY DEPRECATED:
    use_mock and mock_url are retained for backwards compatibility only.
    Project Dolos (EPIC-TBD) will provide a fully separate mock trading
    simulator with its own infrastructure, data generation, and market
    simulation capabilities. Do not use mock mode in this service.
    """
    api_key: str
    account_id: str
    environment: str = "practice"  # practice or live
    use_mock: bool = False  # DEPRECATED - see Project Dolos
    mock_url: str = "http://localhost:8299"  # DEPRECATED - see Project Dolos

    @property
    def streaming_url(self) -> str:
        if self.use_mock:
            return self.mock_url
        if self.environment == "live":
            return "https://stream-fxtrade.oanda.com"
        return "https://stream-fxpractice.oanda.com"

    @property
    def api_url(self) -> str:
        if self.use_mock:
            return self.mock_url
        if self.environment == "live":
            return "https://api-fxtrade.oanda.com"
        return "https://api-fxpractice.oanda.com"


@dataclass
class IBKRConfig:
    """Interactive Brokers configuration - all values from .env"""
    host: str
    port: int
    client_id: int


@dataclass
class DatabaseConfig:
    """Database configuration - all values from .env"""
    host: str
    port: int
    user: str
    password: str
    database: str
    write_ticks: bool = False  # Optional feature flag
    archive_dir: str = ""  # From .env
    archive_months: int = 12  # Default retention


@dataclass
class RedisConfig:
    """Redis configuration - all values from .env"""
    host: str
    port: int
    db: int
    key_prefix: str
    password: Optional[str] = None

    @property
    def tick_channel(self) -> str:
        return f"{self.key_prefix}signal:tick"

    def state_key(self, instrument: str) -> str:
        return f"{self.key_prefix}signal:state:{instrument}"


@dataclass
class ServiceConfig:
    """Main service configuration - all values from .env"""
    host: str
    port: int
    environment: str
    debug: bool = False

    oanda: OANDAConfig = None
    ibkr: IBKRConfig = None
    redis: RedisConfig = None
    database: DatabaseConfig = None

    # Instruments to stream (loaded from DB)
    instruments: List[str] = None

    # Failover settings
    failover_timeout_sec: int = 10  # Switch to backup after N seconds of no ticks
    primary_source: str = "oanda"
    backup_source: str = "ibkr"


def load_config() -> ServiceConfig:
    """Load configuration from environment using GOV-ENV-001 pattern.

    All critical values are required from .env - fails loudly if not configured.
    """

    # OANDA config (shared across environments)
    oanda = OANDAConfig(
        api_key=get_env("OANDA_API_KEY", required=True),
        account_id=get_env("OANDA_ACCOUNT_ID", required=True),
        environment=get_env("OANDA_ENVIRONMENT", required=True),
        use_mock=get_env_bool("USE_MOCK", False),
        mock_url=get_env("MOCK_OANDA_URL", default="")
    )

    # IBKR config (shared across environments)
    ibkr = IBKRConfig(
        host=get_env("IBKR_HOST", required=True),
        port=get_env_int("IBKR_PORT", required=True),
        client_id=get_env_int("IBKR_CLIENT_ID", required=True)
    )

    # Database config (environment-specific via get_env)
    database = DatabaseConfig(
        host=get_env("DB_HOST", required=True),
        port=get_env_int("DB_PORT", required=True),
        user=get_env("DB_USER", required=True),
        password=get_env("DB_PASSWORD", required=True),
        database=get_env("DB_NAME", required=True),
        write_ticks=get_env_bool("WRITE_TICKS", False),
        archive_dir=get_env("ARCHIVE_DIR", required=True),
        archive_months=get_env_int("ARCHIVE_MONTHS_TO_KEEP", default=12)
    )

    # Redis config (environment-specific via get_env)
    redis_password = get_env("REDIS_PASSWORD", default="")
    redis = RedisConfig(
        host=get_env("REDIS_HOST", required=True),
        port=get_env_int("REDIS_PORT", required=True),
        password=redis_password if redis_password else None,
        db=get_env_int("REDIS_DB", required=True),
        key_prefix=get_env("REDIS_KEY_PREFIX", required=True)
    )

    # Load instruments from environment (required)
    instruments = get_env_list("INSTRUMENTS")
    if not instruments:
        raise ValueError("Required environment variable not set: INSTRUMENTS")

    return ServiceConfig(
        host=get_env("SIGNAL_HOST", required=True),
        port=get_env_int("SIGNAL_PORT", required=True),
        debug=get_env_bool("DEBUG", False),
        environment=ENV,
        oanda=oanda,
        ibkr=ibkr,
        redis=redis,
        database=database,
        instruments=instruments
    )


def load_instruments_from_db() -> List[str]:
    """Load active instruments from database"""
    try:
        import sys
        # Add config path dynamically based on BASE_DIR
        config_path = BASE_DIR.parent / 'config'
        if config_path.exists():
            sys.path.insert(0, str(config_path))

        from instruments.loader import InstrumentConfig
        config = InstrumentConfig()
        # Get all instruments that are OANDA compatible
        instruments = []
        for symbol in ['XAU_USD', 'XAG_USD', 'XPT_USD', 'XCU_USD']:
            inst = config.get(symbol)
            if inst:
                instruments.append(symbol)
        return instruments if instruments else ['XAU_USD']  # Fallback
    except Exception as e:
        print(f"Warning: Could not load instruments from DB: {e}")
        return ['XAU_USD', 'XAG_USD']  # Default fallback
