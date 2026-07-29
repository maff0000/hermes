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


# ---------------------------------------------------------------------------------------------------
# WO-HELM-HERMES-CONTAINER-MVP-WP2-PR125-SECRET-FILE-PATH-BOUNDARY-CORRECTION-0001
# C-WP2-SECRET-FILE-UNBOUNDED-PATH: the *_FILE reference is env/operator-controlled, so the loader MUST
# NOT be an arbitrary-file read primitive. Every secret file must live BENEATH an explicit, externally
# configurable, NON-SECRET allowed root (HERMES_SECRET_ROOT, default the conventional Docker-secret mount
# /run/secrets), must be a REGULAR file (no dir/FIFO/socket/device/procfs), must not escape the root via
# symlink or traversal, and must be within a bounded size. Fail-closed with distinct, non-secret codes.
# ---------------------------------------------------------------------------------------------------

# Externally-configurable NON-SECRET allowed root for mounted secret files. Safe container default is the
# conventional read-only Docker-secret mount. Never hard-coded as a hidden host dependency — overridable.
DEFAULT_SECRET_ROOT = "/run/secrets"
# Bounded maximum for a credential / webhook value. 8 KiB is ample for API keys, DB passwords and webhook
# URLs while rejecting log/dump/procfs-style reads. Documented limit.
MAX_SECRET_FILE_BYTES = 8192
# Special pseudo-filesystems that must NEVER be readable through the loader, even if the root were mis-set.
_FORBIDDEN_FS_PREFIXES = ("/proc", "/sys", "/dev", "/srv-dev")


def _resolve_secret_root() -> str:
    """Resolve + validate the allowed secret root. Fail-closed:
      - unset -> DEFAULT_SECRET_ROOT
      - not absolute            -> SECRET-ROOT-RELATIVE
      - missing                 -> SECRET-ROOT-MISSING
      - not a directory         -> SECRET-ROOT-NOT-DIRECTORY
      - realpath differs (root is itself a symlink to elsewhere) is allowed ONLY if it still resolves to a
        real directory; the CANONICAL root is used for all containment checks.
    Returns the canonical (realpath) root."""
    root = os.getenv(f"{ENV}_HERMES_SECRET_ROOT") or os.getenv("HERMES_SECRET_ROOT") or DEFAULT_SECRET_ROOT
    if not os.path.isabs(root):
        raise ValueError(f"SECRET-ROOT-RELATIVE: HERMES_SECRET_ROOT must be an absolute path ({root})")
    real_root = os.path.realpath(root)
    if not os.path.exists(real_root):
        raise ValueError(f"SECRET-ROOT-MISSING: HERMES_SECRET_ROOT does not exist ({root})")
    if not os.path.isdir(real_root):
        raise ValueError(f"SECRET-ROOT-NOT-DIRECTORY: HERMES_SECRET_ROOT is not a directory ({root})")
    return real_root


def _canonical_secret_path(path: str, real_root: str) -> str:
    """Resolve the requested secret path under the allowed root and enforce containment. The `_FILE`
    value may be absolute (must already resolve beneath the root) or relative (resolved beneath the root
    under one rule). Symlink escape and traversal fail because we compare the fully-resolved realpath.

    Fail-closed:
      - empty/malformed         -> SECRET-PATH-RELATIVE-MALFORMED
      - realpath under a forbidden pseudo-fs -> SECRET-SPECIAL-FS
      - realpath references a known cross-application tree -> SECRET-CROSS-APP-PATH
      - realpath not beneath the canonical root -> SECRET-PATH-OUTSIDE-ROOT
    """
    if not isinstance(path, str) or not path.strip():
        raise ValueError("SECRET-PATH-RELATIVE-MALFORMED: empty secret file reference")
    raw = path.strip()
    candidate = raw if os.path.isabs(raw) else os.path.join(real_root, raw)
    # realpath resolves ALL symlinks + `..` in the whole chain — a symlink escape or traversal collapses
    # to its true target, which the containment check below then rejects.
    real_path = os.path.realpath(candidate)
    low = real_path.lower()
    for pref in _FORBIDDEN_FS_PREFIXES:
        if real_path == pref or real_path.startswith(pref + os.sep):
            raise ValueError(f"SECRET-SPECIAL-FS: secret path resolves into a pseudo-filesystem ({pref})")
    if "tradingproteus" in low or "/ares/" in low or low.endswith("/ares/.env"):
        raise ValueError("SECRET-CROSS-APP-PATH: secret path references a non-HERMES application tree")
    # containment: the canonical target must be the root itself's child (root + sep + ...).
    if real_path != real_root and not real_path.startswith(real_root + os.sep):
        raise ValueError("SECRET-PATH-OUTSIDE-ROOT: secret file escapes the allowed secret root")
    return real_path


def load_secret_file(path: str) -> str:
    """Read a secret from a file BENEATH the allowed secret root (HERMES_SECRET_ROOT), fail-closed.

    Path boundary (C-WP2-SECRET-FILE-UNBOUNDED-PATH correction):
      - the target must resolve BENEATH the canonical allowed root (else SECRET-PATH-OUTSIDE-ROOT);
      - traversal / symlink escape collapse via realpath and are rejected (SECRET-PATH-OUTSIDE-ROOT);
      - a final-component symlink is refused at open time via O_NOFOLLOW (SECRET-PATH-SYMLINK);
      - pseudo-filesystems (/proc,/sys,/dev,/srv-dev) -> SECRET-SPECIAL-FS;
      - cross-application trees -> SECRET-CROSS-APP-PATH.
    File validation (on the OPENED fd — TOCTOU-hardened: we validate what we actually opened):
      - must be a REGULAR file — dir/FIFO/socket/block/char device -> SECRET-FILE-NOT-REGULAR;
      - bounded size <= MAX_SECRET_FILE_BYTES -> SECRET-FILE-TOO-LARGE;
      - missing / unreadable -> SECRET-FILE-UNREADABLE;
      - empty / whitespace-only after strip -> SECRET-FILE-EMPTY.
    Content is NEVER logged and NEVER placed in an exception message. Group/world-accessible mode -> warn.
    """
    import stat as _stat

    real_root = _resolve_secret_root()
    real_path = _canonical_secret_path(path, real_root)

    fd = None
    try:
        # O_NOFOLLOW: if the FINAL component is a symlink, open fails with ELOOP -> we reject it explicitly
        # (defence-in-depth on top of the realpath containment above). Open the RESOLVED real_path.
        try:
            fd = os.open(real_path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0))
        except OSError as e:
            if getattr(e, "errno", None) in (_errno_ELOOP(),):
                raise ValueError("SECRET-PATH-SYMLINK: secret file (final component) is a symlink")
            raise ValueError("SECRET-FILE-UNREADABLE: secret file could not be opened")

        # Validate the OPENED descriptor, not the path (TOCTOU: a swap after this fstat cannot change fd).
        st = os.fstat(fd)
        if not _stat.S_ISREG(st.st_mode):
            raise ValueError("SECRET-FILE-NOT-REGULAR: secret path is not a regular file "
                             "(directory/FIFO/socket/device/pseudo-file rejected)")
        if st.st_size > MAX_SECRET_FILE_BYTES:
            raise ValueError(f"SECRET-FILE-TOO-LARGE: secret file exceeds {MAX_SECRET_FILE_BYTES} bytes")
        if st.st_mode & 0o077:
            logger.warning(
                "SECRET-FILE-PERMISSIONS: secret file has group/world-accessible mode (%s) — restrict to "
                "0600/0400 (value not logged)", oct(st.st_mode & 0o777),
            )
        # Read at most MAX+1 bytes so a procfs-style st_size==0-but-streams file is still bounded.
        raw_bytes = os.read(fd, MAX_SECRET_FILE_BYTES + 1)
        if len(raw_bytes) > MAX_SECRET_FILE_BYTES:
            raise ValueError(f"SECRET-FILE-TOO-LARGE: secret file exceeds {MAX_SECRET_FILE_BYTES} bytes")
    except ValueError:
        raise
    except OSError:
        raise ValueError("SECRET-FILE-UNREADABLE: secret file could not be read")
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass

    try:
        raw = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("SECRET-FILE-UNREADABLE: secret file is not valid UTF-8")

    # Strip a single trailing newline then any trailing whitespace-only tail. Internal content intact.
    value = raw
    if value.endswith("\n"):
        value = value[:-1]
    value = value.rstrip()

    if value == "":
        raise ValueError("SECRET-FILE-EMPTY: secret file is empty or whitespace-only")

    return value


def _errno_ELOOP() -> int:
    import errno as _errno
    return _errno.ELOOP


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
