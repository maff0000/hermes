"""
Environment Configuration Loader
GOV-ENV-001: Single .env with ENVIRONMENT tag (DEV/PROD)

Usage:
    from env_config import get_env, ENV, BASE_DIR

    db_host = get_env('DB_HOST')  # Reads DEV_DB_HOST or PROD_DB_HOST based on ENVIRONMENT
    shared = get_env('OANDA_API_KEY')  # Reads OANDA_API_KEY (no prefix needed for shared vars)
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Base directory is where this file lives (no hardcoded paths)
BASE_DIR = Path(__file__).parent.absolute()

# Load .env from the base directory
_env_path = BASE_DIR / '.env'
if _env_path.exists():
    load_dotenv(_env_path)

# Active environment (DEV or PROD)
ENV = os.getenv('ENVIRONMENT', 'DEV').upper()

# Validate environment
if ENV not in ('DEV', 'PROD'):
    raise ValueError(f"Invalid ENVIRONMENT: {ENV}. Must be DEV or PROD.")


def get_env(key: str, default: str = None, required: bool = False) -> str:
    """
    Get environment variable with environment prefix fallback.

    Order of lookup:
    1. {ENV}_{key} (e.g., DEV_DB_HOST or PROD_DB_HOST)
    2. {key} (e.g., DB_HOST for shared variables)
    3. default value (if not required)

    Args:
        key: Variable name without environment prefix
        default: Default value if not found (ignored if required=True)
        required: If True, raise error when not found

    Returns:
        Environment variable value

    Raises:
        ValueError: If required=True and variable not found
    """
    # Try environment-specific first
    env_key = f"{ENV}_{key}"
    value = os.getenv(env_key)

    if value is not None:
        return value

    # Fall back to non-prefixed (shared variables)
    value = os.getenv(key)

    if value is not None:
        return value

    if required:
        raise ValueError(f"Required environment variable not set: {key} (tried {env_key} and {key})")

    return default


def get_env_int(key: str, default: int = None, required: bool = False) -> int:
    """Get environment variable as integer."""
    value = get_env(key, required=required)
    if value is None:
        if required:
            raise ValueError(f"Required environment variable not set: {key}")
        return default if default is not None else 0
    try:
        return int(value)
    except ValueError:
        raise ValueError(f"Invalid integer value for {key}: {value}")


def get_env_bool(key: str, default: bool = False) -> bool:
    """Get environment variable as boolean."""
    value = get_env(key)
    if value is None:
        return default
    return value.lower() in ('true', '1', 'yes', 'on')


def get_env_list(key: str, default: list = None) -> list:
    """Get environment variable as list (comma-separated)."""
    value = get_env(key)
    if value is None:
        return default or []
    return [item.strip() for item in value.split(',') if item.strip()]


# Convenience exports for common configs
def get_db_config() -> dict:
    """Get database configuration for current environment. All values required from .env."""
    return {
        'host': get_env('DB_HOST', required=True),
        'port': get_env_int('DB_PORT', required=True),
        'user': get_env('DB_USER', required=True),
        'password': get_env('DB_PASSWORD', required=True),
        'database': get_env('DB_NAME', required=True),
    }


def get_redis_config() -> dict:
    """Get Redis configuration for current environment. All values required from .env."""
    password = get_env('REDIS_PASSWORD', default='')  # Empty password allowed
    return {
        'host': get_env('REDIS_HOST', required=True),
        'port': get_env_int('REDIS_PORT', required=True),
        'db': get_env_int('REDIS_DB', required=True),
        'password': password if password else None,
        'key_prefix': get_env('REDIS_KEY_PREFIX', required=True),
    }


# Log environment on import (only in debug mode)
if get_env_bool('DEBUG'):
    print(f"[ENV_CONFIG] Environment: {ENV}")
    print(f"[ENV_CONFIG] Base directory: {BASE_DIR}")
