#!/usr/bin/env python3
"""HERMES WP3 SHADOW target-isolation startup guard v1 (fail-closed, pre-connection).

WO-HELM-HERMES-CONTAINER-MVP-WP3-SHADOW-TARGET-ISOLATION-GUARDS-0001.
Closes C-WP3-TARGET-GUARDS-ABSENT-IN-IMAGE. Owner: HERMES (Helm). Created (UTC): 2026-07-30.
Contract version: 1.

WHAT THIS IS. A fail-closed startup contract: when a HERMES process is configured with `RUN_ENV=SHADOW`,
it must NOT start when any configured target could affect a live or canonical resource. The guard validates
the full target set — runtime mode, consumer activation, OANDA environment, Redis target class + namespace,
SQL target class + schema, and a shadow run identity — and returns an immutable validated target MANIFEST,
BEFORE any external connector (Redis client, SQL connection, OANDA adapter/auth, publisher) is constructed.
No "connect, inspect, then reject" — the guard runs at the earliest configuration-validation boundary.

NON-SHADOW COMPATIBILITY. When `RUN_ENV` is anything other than `SHADOW` (the current deployed runtime uses
`RUN_ENV=STAGING`), the guard is DORMANT: it returns a non-shadow manifest and imposes NO new rejection, so
the existing live configuration, OANDA behaviour, canonical Redis keys and SQL schema are unchanged. Only the
explicit opt-in `RUN_ENV=SHADOW` activates the isolation contract.

NO SECRETS. The manifest carries only classifications, hosts, ports, a masked DB identity and a namespace —
never an API key, DB/Redis password, full DSN or unmasked account id. Faults never carry a secret value.
"""
from __future__ import annotations

import dataclasses
import datetime
import re
from dataclasses import dataclass
from typing import Callable, Optional

from env_config import get_env

GUARD_CONTRACT_VERSION = "1"

SHADOW_MODE = "SHADOW"
_VALID_FEED_MODES = frozenset({"replay", "practice"})

# Reserved words a shadow run-id / namespace must never be or contain as an identity.
_RESERVED_WORDS = frozenset({"live", "prod", "production", "canonical"})

# Canonical Redis facts a SHADOW target must never point at.
_CANONICAL_REDIS_PORT = 6379
# A namespace that (after normalisation) is empty, equals the canonical service root, or would produce
# canonical `hermes:*` keys is forbidden in SHADOW.
_CANONICAL_NS_ROOTS = ("hermes",)

# The deployed/canonical HERMES SQL schema, and other-application schema markers.
_CANONICAL_DB_NAMES = frozenset({"tradingsignals"})
_CROSS_APP_DB_MARKERS = ("argus", "ares", "proteus", "tradingproteus", "helios")

# Accepted target classes.
_REDIS_SHADOW_CLASSES = frozenset({"shadow"})
_REDIS_FORBIDDEN_CLASSES = frozenset({"canonical", "production", "live"})
_DB_SHADOW_CLASSES = frozenset({"shadow", "ephemeral"})
_DB_FORBIDDEN_CLASSES = frozenset({"canonical", "production", "live"})

_RUN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{2,63}$")
_TRUE_TOKENS = frozenset({"true", "1", "yes", "on"})
_FALSE_TOKENS = frozenset({"false", "0", "no", "off", ""})


class ShadowTargetGuardError(RuntimeError):
    """Raised when SHADOW target validation fails closed. `fault_code` is a stable, non-secret code."""

    def __init__(self, fault_code: str, message: str) -> None:
        super().__init__(f"{fault_code}: {message}")
        self.fault_code = fault_code


@dataclass(frozen=True)
class ShadowTargetManifest:
    """§11 immutable validated target manifest — the single source of truth downstream connectors should
    consume instead of re-reading raw env. NO credentials. `masked_db` is a bounded, non-secret identity."""

    run_environment: str
    is_shadow: bool
    run_id: Optional[str]
    feed_mode: Optional[str]
    oanda_class: Optional[str]
    redis_class: Optional[str]
    redis_host: Optional[str]
    redis_port: Optional[int]
    redis_namespace: Optional[str]
    sql_class: Optional[str]
    sql_host: Optional[str]
    sql_port: Optional[int]
    masked_db: Optional[str]
    consumer_state: str
    validated: bool
    validation_utc: str
    guard_contract_version: str


def _normalise_ns(value: object) -> str:
    """Collapse a namespace to a canonical comparison form: strip, casefold, drop whitespace / zero-width /
    common confusables, so a deceptive `hermes：` / ` hermes:` cannot smuggle canonical keys through."""
    if not isinstance(value, str):
        return ""
    s = value.strip().casefold()
    for zw in ("​", "‌", "‍", "﻿"):
        s = s.replace(zw, "")
    s = "".join(ch for ch in s if not ch.isspace())
    # fold a couple of full-width / confusable colons to ascii ':'
    s = s.replace("：", ":")
    return s


def _is_truthy(raw: object) -> Optional[bool]:
    """Parse a boolean env token. Returns True/False, or None if malformed (caller fails closed)."""
    if raw is None:
        return False
    t = str(raw).strip().lower()
    if t in _TRUE_TOKENS:
        return True
    if t in _FALSE_TOKENS:
        return False
    return None


def _mask_db(name: object) -> str:
    s = str(name or "")
    if len(s) <= 4:
        return s[:1] + "***"
    return s[:4] + "***"


def validate_shadow_targets(config: object, *, env: Callable[..., object] = get_env,
                            now_utc: Optional[str] = None) -> ShadowTargetManifest:
    """Validate the configured target set. Returns an immutable ShadowTargetManifest. Fail-closed: any
    SHADOW violation raises ShadowTargetGuardError(fault_code) BEFORE any connector is constructed.

    `config` is the loaded ServiceConfig (config.oanda / config.redis / config.database). `env` is the
    environment getter (injected for tests). Outside SHADOW the guard is dormant (no new rejection)."""
    run_env = str(env("RUN_ENV", default="") or "").strip().upper()
    ts = now_utc or datetime.datetime(2026, 7, 30, tzinfo=datetime.timezone.utc).isoformat()

    if run_env != SHADOW_MODE:
        # DORMANT — preserve current deployed behaviour. No new rejection outside SHADOW.
        return ShadowTargetManifest(
            run_environment=run_env, is_shadow=False, run_id=None, feed_mode=None, oanda_class=None,
            redis_class=None, redis_host=None, redis_port=None, redis_namespace=None, sql_class=None,
            sql_host=None, sql_port=None, masked_db=None,
            consumer_state=str(env("CONSUMER_LIVE", default="false")), validated=True,
            validation_utc=ts, guard_contract_version=GUARD_CONTRACT_VERSION)

    redis_cfg = getattr(config, "redis", None)
    oanda_cfg = getattr(config, "oanda", None)
    db_cfg = getattr(config, "database", None)

    # ---- §6 consumer guard ----------------------------------------------------------------------------
    consumer_raw = env("CONSUMER_LIVE", default="false")
    consumer = _is_truthy(consumer_raw)
    if consumer is None or consumer is True:
        raise ShadowTargetGuardError(
            "SHADOW-CONSUMER-LIVE-FORBIDDEN",
            "consumer_live must be an explicit false in SHADOW (a truthy/malformed value is rejected)")
    # secondary activation flags that could enable consumer publication/registration.
    for flag in ("HERMES_CONSUMER_ENABLED", "HERMES_CONSUMER_ACTIVE", "CONSUMER_ACTIVE",
                 "HERMES_DOWNSTREAM_CONSUMER_ENABLED"):
        v = _is_truthy(env(flag, default="false"))
        if v is None or v is True:
            raise ShadowTargetGuardError(
                "SHADOW-CONSUMER-LIVE-FORBIDDEN",
                f"secondary consumer-activation flag {flag} must be explicit false in SHADOW")

    # ---- §10 shadow run identity ----------------------------------------------------------------------
    run_id = str(env("HERMES_SHADOW_RUN_ID", default="") or "").strip()
    if not run_id:
        raise ShadowTargetGuardError("SHADOW-RUN-ID-REQUIRED", "HERMES_SHADOW_RUN_ID is required in SHADOW")
    if not _RUN_ID_RE.match(run_id):
        raise ShadowTargetGuardError(
            "SHADOW-RUN-ID-REQUIRED", "HERMES_SHADOW_RUN_ID must be [a-z0-9-], 3-64 chars")
    if run_id.lower() in _RESERVED_WORDS or any(w == run_id.lower() for w in _RESERVED_WORDS):
        raise ShadowTargetGuardError(
            "SHADOW-RUN-ID-REQUIRED", "HERMES_SHADOW_RUN_ID must not be a reserved word")

    # ---- §5/§7 feed mode + OANDA guard ----------------------------------------------------------------
    feed_mode = str(env("HERMES_FEED_MODE", default="") or "").strip().lower()
    if feed_mode not in _VALID_FEED_MODES:
        raise ShadowTargetGuardError(
            "SHADOW-FEED-MODE-REQUIRED", "HERMES_FEED_MODE must be 'replay' or 'practice' in SHADOW")

    oanda_env = str(getattr(oanda_cfg, "environment", "") or "").strip().lower()
    use_mock = bool(getattr(oanda_cfg, "use_mock", False))
    mock_url = str(getattr(oanda_cfg, "mock_url", "") or "")
    oanda_class: str
    if oanda_env == "live":
        raise ShadowTargetGuardError(
            "SHADOW-OANDA-LIVE-FORBIDDEN", "OANDA_ENVIRONMENT=live is forbidden in SHADOW")
    if feed_mode == "replay":
        if not use_mock:
            raise ShadowTargetGuardError(
                "SHADOW-OANDA-REPLAY-LIVE-CONFLICT",
                "replay feed requires USE_MOCK=true (a live OANDA path in replay mode is a conflict)")
        low = mock_url.lower()
        if ("oanda.com" in low) or low.startswith("https://api-fxtrade") or low.startswith("https://stream-fxtrade"):
            raise ShadowTargetGuardError(
                "SHADOW-OANDA-LIVE-FORBIDDEN", "replay mock_url must point at the isolated WP3 network, not OANDA")
        oanda_class = "replay-mock"
    else:  # practice
        if oanda_env != "practice":
            raise ShadowTargetGuardError(
                "SHADOW-OANDA-FEED-MODE-AMBIGUOUS", "practice feed requires OANDA_ENVIRONMENT=practice")
        # No practice connection is authorised under this WO; distinct governed practice creds must exist.
        if not str(env("OANDA_PRACTICE_TARGET_CLASS", default="") or "").strip():
            raise ShadowTargetGuardError(
                "SHADOW-PRACTICE-CREDENTIALS-ABSENT",
                "practice feed requires an explicit governed practice target class (absent)")
        oanda_class = "practice"

    # ---- §8 Redis target guard ------------------------------------------------------------------------
    redis_class = str(env("REDIS_TARGET_CLASS", default="") or "").strip().lower()
    redis_host = str(getattr(redis_cfg, "host", "") or "")
    redis_port = int(getattr(redis_cfg, "port", 0) or 0)
    redis_ns = str(getattr(redis_cfg, "key_prefix", "") or "")
    if not redis_class:
        raise ShadowTargetGuardError(
            "SHADOW-REDIS-CLASS-REQUIRED", "REDIS_TARGET_CLASS must be set to 'shadow' in SHADOW")
    if redis_class in _REDIS_FORBIDDEN_CLASSES or redis_class not in _REDIS_SHADOW_CLASSES:
        raise ShadowTargetGuardError(
            "SHADOW-REDIS-TARGET-FORBIDDEN", f"REDIS_TARGET_CLASS '{redis_class}' is not a shadow class")
    if redis_port == _CANONICAL_REDIS_PORT:
        raise ShadowTargetGuardError(
            "SHADOW-REDIS-TARGET-FORBIDDEN", "canonical Redis port 6379 is forbidden in SHADOW")
    # namespace must be present, non-canonical, and carry the run-id.
    ns_norm = _normalise_ns(redis_ns)
    if not ns_norm:
        raise ShadowTargetGuardError(
            "SHADOW-REDIS-NAMESPACE-REQUIRED", "a non-empty shadow Redis namespace/prefix is required")
    for root in _CANONICAL_NS_ROOTS:
        if ns_norm == root or ns_norm == root + ":" or ns_norm.startswith(root + ":"):
            raise ShadowTargetGuardError(
                "SHADOW-REDIS-CANONICAL-KEYSPACE-FORBIDDEN",
                "the Redis namespace would produce canonical hermes:* keys")
    if run_id.lower() not in ns_norm:
        raise ShadowTargetGuardError(
            "SHADOW-REDIS-NAMESPACE-REQUIRED", "the shadow Redis namespace must contain the shadow run-id")

    # ---- §9 SQL target guard --------------------------------------------------------------------------
    db_class = str(env("DB_TARGET_CLASS", default="") or "").strip().lower()
    db_host = str(getattr(db_cfg, "host", "") or "")
    db_port = int(getattr(db_cfg, "port", 0) or 0)
    db_name = str(getattr(db_cfg, "database", "") or "")
    if not db_class:
        raise ShadowTargetGuardError(
            "SHADOW-DB-CLASS-REQUIRED", "DB_TARGET_CLASS must be 'shadow' or 'ephemeral' in SHADOW")
    if db_class in _DB_FORBIDDEN_CLASSES or db_class not in _DB_SHADOW_CLASSES:
        raise ShadowTargetGuardError(
            "SHADOW-DB-TARGET-FORBIDDEN", f"DB_TARGET_CLASS '{db_class}' is not a shadow/ephemeral class")
    if not db_name.strip():
        raise ShadowTargetGuardError("SHADOW-DB-NAME-REQUIRED", "a shadow DB schema name is required")
    low_db = db_name.strip().lower()
    if low_db in _CANONICAL_DB_NAMES:
        raise ShadowTargetGuardError(
            "SHADOW-DB-CANONICAL-SCHEMA-FORBIDDEN", "the deployed/canonical HERMES schema is forbidden in SHADOW")
    if any(m in low_db for m in _CROSS_APP_DB_MARKERS):
        raise ShadowTargetGuardError(
            "SHADOW-DB-CROSS-APPLICATION-FORBIDDEN", "another application's schema is forbidden in SHADOW")

    return ShadowTargetManifest(
        run_environment=run_env, is_shadow=True, run_id=run_id, feed_mode=feed_mode, oanda_class=oanda_class,
        redis_class=redis_class, redis_host=redis_host, redis_port=redis_port, redis_namespace=redis_ns,
        sql_class=db_class, sql_host=db_host, sql_port=db_port, masked_db=_mask_db(db_name),
        consumer_state="false", validated=True, validation_utc=ts,
        guard_contract_version=GUARD_CONTRACT_VERSION)


def manifest_status_dict(manifest: ShadowTargetManifest) -> dict:
    """§13 non-secret guard metadata for /status + /buildinfo. NEVER contains credentials or a full DSN."""
    return {
        "guard_contract_version": manifest.guard_contract_version,
        "target_validation": "PASS" if manifest.validated else "FAIL",
        "run_environment": manifest.run_environment,
        "is_shadow": manifest.is_shadow,
        "shadow_run_id": manifest.run_id,
        "feed_mode": manifest.feed_mode,
        "oanda_class": manifest.oanda_class,
        "redis_target_class": manifest.redis_class,
        "redis_namespace": manifest.redis_namespace,
        "sql_target_class": manifest.sql_class,
        "sql_database": manifest.masked_db,
        "consumer_state": manifest.consumer_state,
    }
