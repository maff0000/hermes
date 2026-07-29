"""
Environment Configuration Loader
GOV-ENV-001: Single .env with ENVIRONMENT tag (DEV/PROD)

Usage:
    from env_config import get_env, ENV, BASE_DIR

    db_host = get_env('DB_HOST')  # Reads DEV_DB_HOST or PROD_DB_HOST based on ENVIRONMENT
    shared = get_env('OANDA_API_KEY')  # Reads OANDA_API_KEY (no prefix needed for shared vars)
"""

import os
import logging
from pathlib import Path
from dotenv import load_dotenv

# Base directory is where this file lives (no hardcoded paths)
BASE_DIR = Path(__file__).parent.absolute()

logger = logging.getLogger(__name__)

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


# ---------------------------------------------------------------------------------------------------
# Secret loading — generic `_FILE` reference support (WP2). BOUNDED, backward-compatible, fail-closed.
# WO-HELM-HERMES-CONTAINER-MVP-WP2-CANONICAL-BUILD-AND-EXTERNALISED-CONFIGURATION-0001.
#
# Resolution for get_secret(key):
#   1. {ENV}_{key}_FILE or {key}_FILE  -> read that file (file source)
#   2. else fall back to get_env(key)   -> direct env value (env source)
# CONFLICT: both a *_FILE ref AND a direct value present -> FAIL CLOSED (SECRET-SOURCE-CONFLICT).
# The secret VALUE is NEVER logged — only the key name and the source kind ("env" | "file:<path>").
# ---------------------------------------------------------------------------------------------------


def load_secret_file(path: str) -> str:
    """Read a secret from a file, fail-closed.

    Semantics:
      - strip a single trailing newline and any trailing whitespace-only tail (rstrip) — internal
        content is NEVER altered.
      - EMPTY after strip -> ValueError('SECRET-FILE-EMPTY: <path>')
      - unreadable / missing -> ValueError('SECRET-FILE-UNREADABLE: <path>')
      - world/group readable or writable (mode & 0o077) -> WARNING via module logger (policy = warn,
        does NOT fail). The value is NEVER logged.
    """
    p = Path(path)
    try:
        # Permission check first (warn-only). Never fails the read.
        try:
            mode = p.stat().st_mode
            if mode & 0o077:
                logger.warning(
                    "SECRET-FILE-PERMISSIONS: secret file has group/world-accessible mode "
                    "(%s) path=%s — restrict to 0600/0400 (value not logged)",
                    oct(mode & 0o777), path,
                )
        except OSError:
            # stat failure is handled by the read below (fail-closed as unreadable).
            pass

        with open(p, "r", encoding="utf-8") as fh:
            raw = fh.read()
    except (OSError, IOError):
        raise ValueError(f"SECRET-FILE-UNREADABLE: {path}")

    # Strip a single trailing newline then any trailing whitespace-only tail. Internal content intact.
    value = raw
    if value.endswith("\n"):
        value = value[:-1]
    value = value.rstrip()

    if value == "":
        raise ValueError(f"SECRET-FILE-EMPTY: {path}")

    return value


def get_secret(key: str, *, default: str = None, required: bool = False) -> str:
    """Resolve a secret with `_FILE` reference support, fail-closed.

    Order:
      - {ENV}_{key}_FILE  -> file
      - {key}_FILE        -> file
      - direct value via get_env(key) (i.e. {ENV}_{key} then {key})
    If BOTH a *_FILE ref AND a direct value are present -> ValueError('SECRET-SOURCE-CONFLICT').
    required=True and neither present -> ValueError('SECRET-REQUIRED-MISSING: <key>').

    Never logs the secret value — logs only the key name and source kind.
    """
    env_file_key = f"{ENV}_{key}_FILE"
    file_path = os.getenv(env_file_key)
    file_source_name = env_file_key
    if file_path is None:
        file_path = os.getenv(f"{key}_FILE")
        file_source_name = f"{key}_FILE"

    # Direct value (via existing prefix fallback). None if unset.
    direct_value = get_env(key)
    direct_source_name = f"{ENV}_{key}" if os.getenv(f"{ENV}_{key}") is not None else key

    # Explicit documented rule: file + direct conflict fails closed (name SOURCES, never values).
    if file_path is not None and direct_value is not None:
        raise ValueError(
            f"SECRET-SOURCE-CONFLICT: both a file reference ({file_source_name}) and a direct "
            f"value ({direct_source_name}) are set for '{key}' — provide exactly one source"
        )

    if file_path is not None:
        value = load_secret_file(file_path)
        logger.info("get_secret: key=%s source=file:%s", key, file_path)
        return value

    if direct_value is not None:
        logger.info("get_secret: key=%s source=env", key)
        return direct_value

    if required:
        raise ValueError(f"SECRET-REQUIRED-MISSING: {key}")

    return default


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
