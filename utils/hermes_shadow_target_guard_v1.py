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

# WO-...-WP3-PR126-ALL-REDIS-WRITE-TARGETS-ISOLATION-CORRECTION-0001 — C-WP3-SECONDARY-REDIS-TARGETS-
# UNGUARDED. Every Redis WRITE path in the repo, classified. In SHADOW each must be manifest-validated or
# disabled BEFORE its client is constructed (all are gated + built after this guard in main.lifespan).
#   role: what it writes · enable_flag: the master gate (default false) · target: which Redis env it reads
#   default_enabled: False for every writer · shadow_policy: how the guard treats it in SHADOW.
# COUNT RECONCILIATION (R2D2 13 vs HELM 14): 14 registry MODULE ENTRIES = 11 that construct their OWN Redis
# client (redis_publisher, candle_runtime_seam[shadow+canonical], candle_history_forward, candle_d1_history,
# backfill_status, gap_status, live_tick_emitter, shadow_tick_emitter, publisher_supervisor,
# healthcheck_process, manual_script) + 3 writer-logic modules that consume an INJECTED client and construct
# none (candle_d1_publish_wire, candle_h4_publish_wire, candle_publisher_lib). R2D2's 13 counts distinct
# WRITER ROLES, folding candle_publisher_lib (the shared serialiser the candle-forward seam injects a client
# into — no own client, no independent enablement). Both are truthful at their granularity; the registry is
# kept at 14 module entries so the static-inventory test covers every redis-client-bearing module.
REDIS_WRITER_REGISTRY = {
    # WO-HELM-HERMES-DEV-SHADOW-REDIS-WRITER-AUTH-COMPLETION-0001: startup guard
    # probing enabled write targets (read-only PING probe; writes nothing).
    "utils/hermes_write_target_auth_guard_v1.py": {
        "role": "write_target_auth_probe", "enable_flag": None,
        "target": "every ENABLED write target (main REDIS_* + shadow HERMES_*_REDIS_*)",
        "default_enabled": True, "shadow_policy": "probe_only_no_publication"},
    "utils/redis_publisher.py": {
        "role": "primary_publisher", "enable_flag": None, "target": "config.redis (REDIS_HOST/PORT)",
        "default_enabled": True, "shadow_policy": "primary_manifest_validated"},
    "utils/candle_runtime_seam_v1.py": {
        "role": "candle_forward_seam", "enable_flag": "HERMES_CANDLE_FORWARD_ENABLED",
        "target": "canonical: HERMES_CANDLE_CANONICAL_REDIS_* | shadow: HERMES_CANDLE_FORWARD_SHADOW_REDIS_*",
        "default_enabled": False, "shadow_policy": "canonical_sink_forbidden_or_shadow_target_validated"},
    "utils/candle_history_forward_writer_v1.py": {
        "role": "candle_history_forward", "enable_flag": "HERMES_CANDLE_HISTORY_FORWARD_ENABLED",
        "target": "HERMES_CANDLE_CANONICAL_REDIS_*", "default_enabled": False,
        "shadow_policy": "canonical_writer_forbidden"},
    "utils/candle_d1_history_v1.py": {
        "role": "candle_d1_history", "enable_flag": "HERMES_CANDLE_D1_HISTORY_ENABLED",
        "target": "HERMES_CANDLE_CANONICAL_REDIS_*", "default_enabled": False,
        "shadow_policy": "canonical_writer_forbidden"},
    "utils/hermes_backfill_status_v1.py": {
        "role": "backfill_status", "enable_flag": "HERMES_BACKFILL_STATUS_PUBLISH_ENABLED",
        "target": "HERMES_CANDLE_CANONICAL_REDIS_*", "default_enabled": False,
        "shadow_policy": "canonical_writer_forbidden"},
    "utils/hermes_gaps_v1.py": {
        "role": "gap_status", "enable_flag": "HERMES_D1_HISTORY_BACKFILL_ENABLED",
        "target": "HERMES_CANDLE_CANONICAL_REDIS_*", "default_enabled": False,
        "shadow_policy": "canonical_writer_forbidden"},
    "utils/tick_live_emitter_v1.py": {
        "role": "live_tick_emitter", "enable_flag": "HERMES_TICK_PUBLISH_ENABLED",
        "target": "HERMES_CANDLE_CANONICAL_REDIS_*", "default_enabled": False,
        "shadow_policy": "canonical_writer_forbidden"},
    "utils/tick_shadow_publisher_v1.py": {
        "role": "shadow_tick_emitter", "enable_flag": "HERMES_SHADOW_TICK_PUBLISH_ENABLED",
        "target": "HERMES_SHADOW_TICK_REDIS_*", "default_enabled": False,
        "shadow_policy": "shadow_target_validated"},
    "utils/hermes_publisher_runtime_v1.py": {
        "role": "publisher_supervisor", "enable_flag": "HERMES_PUBLISHER_RUNTIME_ENABLED",
        "target": "config.redis (via primary publisher)", "default_enabled": False,
        "shadow_policy": "primary_manifest_validated"},
    "utils/candle_d1_publish_wire_v1.py": {
        "role": "candle_d1_publish_wire", "enable_flag": "HERMES_CANDLE_PUBLISH_ENABLED",
        "target": "canonical seam", "default_enabled": False, "shadow_policy": "canonical_writer_forbidden"},
    "utils/candle_h4_publish_wire_v1.py": {
        "role": "candle_h4_publish_wire", "enable_flag": "HERMES_CANDLE_H4_PUBLISH_ENABLED",
        "target": "canonical seam", "default_enabled": False, "shadow_policy": "canonical_writer_forbidden"},
    "utils/candle_publisher_v1.py": {
        "role": "candle_publisher_lib", "enable_flag": None, "target": "injected client (no own client)",
        "default_enabled": False, "shadow_policy": "library_no_own_client"},
    "healthcheck/signal_health.py": {
        "role": "healthcheck_process", "enable_flag": None, "target": "separate healthcheck process",
        "default_enabled": False, "shadow_policy": "out_of_startup_path"},
    "scripts/shadow_activate_publish.py": {
        "role": "manual_script", "enable_flag": None, "target": "manual ops script",
        "default_enabled": False, "shadow_policy": "out_of_startup_path"},
    # WO-HELM-HERMES-DEV-REDIS-CAPACITY-RETENTION-AND-PROD-INCIDENT-RECOVERY-DESIGN-0001: one-shot/periodic
    # maintenance tool, not part of the startup/runtime path. Dry-run by default; writes only when invoked
    # with --apply, and even then the only write is ZREMRANGEBYSCORE on hermes:candles:*:history:v1:index
    # (never a candle value key, never a shadow target).
    "tools/hermes_history_index_prune_v1.py": {
        "role": "manual_script", "enable_flag": "--apply (CLI flag, not env var; dry-run without it)",
        "target": "HERMES_CANDLE_CANONICAL_REDIS_* (history index ZSETs only)",
        "default_enabled": False, "shadow_policy": "out_of_startup_path"},
    # WO-HELM-HERMES-DEV-PRE-PROD-RECOVERY-GATE-CLOSURE-0001: host-side detection-only tripwire,
    # not part of the HERMES application container's startup path. Read-only Redis INFO; the only
    # writes anywhere in this module are to the existing hermes_incidents table via HealthPersistence.
    "utils/hermes_operational_tripwires_v1.py": {
        "role": "manual_script", "enable_flag": "governed host-side systemd timer (see ops/systemd/)",
        "target": "HERMES_CANDLE_CANONICAL_REDIS_* (INFO only, read-only)",
        "default_enabled": False, "shadow_policy": "out_of_startup_path"},
}

# Canonical-Redis secondary writers that must be DISABLED in SHADOW (each defaults false; any truthy -> fail).
_CANONICAL_WRITER_FLAGS = (
    "HERMES_TICK_PUBLISH_ENABLED", "HERMES_CANDLE_PUBLISH_ENABLED",
    "HERMES_CANDLE_HISTORY_FORWARD_ENABLED", "HERMES_CANDLE_D1_HISTORY_ENABLED",
    "HERMES_D1_HISTORY_BACKFILL_ENABLED", "HERMES_BACKFILL_STATUS_PUBLISH_ENABLED",
    "HERMES_CANDLE_H4_PUBLISH_ENABLED",
)
_FORBIDDEN_CF_SINKS = frozenset({"canonical", "live", "prod", "production"})
_INERT_CF_SINKS = frozenset({"none", "inert", ""})

# WP3-PR126-SHADOW-REDIS-KEYSPACE-RUN-SCOPING — a generic `hermes:shadow:candles:` prefix is NOT enough:
# two runs would collide. Each enabled shadow writer's key prefix must carry the EXACT run-id as a discrete
# ':'-delimited component (boundary-safe: run 'wp3-123' must not match a prefix component 'wp3-1234'), must
# not resolve into a canonical keyspace, and is recorded in the manifest as the value the writer must use.
_CANONICAL_CANDLE_PREFIX = "hermes:candles:"
_CANONICAL_TICK_PREFIX = "hermes:ticks:"


def _run_scoped_prefix(prefix: object, run_id: str, *, canonical_marker: str,
                       req_code: str, canon_code: str, scope_code: str, required_start: str = None) -> str:
    """Validate a shadow writer key prefix is present, non-canonical, and RUN-SCOPED (run-id as a discrete
    ':'-delimited component — no substring coincidence). When `required_start` is given, the prefix must
    also begin with it (guard/emitter contract alignment — e.g. the tick emitter requires 'hermes:shadow:').
    Returns the validated prefix. Fail-closed."""
    if not isinstance(prefix, str) or not prefix.strip():
        raise ShadowTargetGuardError(req_code, "an explicit run-scoped shadow key prefix is required")
    p = prefix.strip()
    norm = _normalise_ns(p)
    if norm == canonical_marker.rstrip(":") or norm == canonical_marker or norm.startswith(canonical_marker):
        raise ShadowTargetGuardError(canon_code, "shadow key prefix resolves into a canonical keyspace")
    if required_start is not None and not p.startswith(required_start):
        raise ShadowTargetGuardError(
            scope_code, f"shadow key prefix must begin with '{required_start}' (emitter contract)")
    # boundary-safe component check: split on ':' and require the exact run-id as one whole component.
    components = [c for c in p.split(":") if c != ""]
    if run_id not in components:
        raise ShadowTargetGuardError(
            scope_code, "shadow key prefix must contain the exact run-id as a discrete ':'-delimited component")
    return p


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
    redis_writers: tuple  # ((role, "disabled"|"shadow-validated"|"primary-validated"), ...) — §12 observability
    candle_forward_shadow_prefix: Optional[str]  # run-scoped keyspace the writer MUST use (None if disabled)
    shadow_tick_prefix: Optional[str]            # run-scoped keyspace the writer MUST use (None if disabled)
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
            consumer_state=str(env("CONSUMER_LIVE", default="false")), redis_writers=(),
            candle_forward_shadow_prefix=None, shadow_tick_prefix=None, validated=True,
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

    # ---- §3-§8 SECONDARY Redis write-target guard (C-WP3-SECONDARY-REDIS-TARGETS-UNGUARDED) + §3.2 run-
    #      scoped keyspace (C-WP3-SHADOW-REDIS-KEYSPACE-NOT-RUN-SCOPED) -----------------------------------
    redis_writers, cf_prefix, st_prefix = _validate_secondary_redis_targets(env, run_id)

    return ShadowTargetManifest(
        run_environment=run_env, is_shadow=True, run_id=run_id, feed_mode=feed_mode, oanda_class=oanda_class,
        redis_class=redis_class, redis_host=redis_host, redis_port=redis_port, redis_namespace=redis_ns,
        sql_class=db_class, sql_host=db_host, sql_port=db_port, masked_db=_mask_db(db_name),
        consumer_state="false", redis_writers=redis_writers, candle_forward_shadow_prefix=cf_prefix,
        shadow_tick_prefix=st_prefix, validated=True, validation_utc=ts,
        guard_contract_version=GUARD_CONTRACT_VERSION)


def _validate_secondary_redis_targets(env: Callable[..., object], run_id: str) -> tuple:
    """§3-§8 in SHADOW every SECONDARY Redis writer must be manifest-validated or DISABLED before its client
    is constructed. Every writer here is env-gated (default false) and built AFTER this guard in
    main.lifespan, so a violation aborts startup before any secondary client exists. Also §3.2 enforces a
    RUN-SCOPED keyspace for every enabled shadow writer. Returns (writers_tuple, candle_forward_shadow_prefix,
    shadow_tick_prefix). Fail-closed with stable, non-secret faults."""
    writers = []
    cf_prefix = None
    st_prefix = None

    # (a) canonical-Redis secondary writers MUST be disabled in SHADOW (each defaults false).
    _cw_roles = {
        "HERMES_TICK_PUBLISH_ENABLED": "live_tick_emitter",
        "HERMES_CANDLE_PUBLISH_ENABLED": "candle_canonical_publish",
        "HERMES_CANDLE_HISTORY_FORWARD_ENABLED": "candle_history_forward",
        "HERMES_CANDLE_D1_HISTORY_ENABLED": "candle_d1_history",
        "HERMES_D1_HISTORY_BACKFILL_ENABLED": "gap_backfill",
        "HERMES_BACKFILL_STATUS_PUBLISH_ENABLED": "backfill_status",
        "HERMES_CANDLE_H4_PUBLISH_ENABLED": "candle_h4_publish",
    }
    for flag in _CANONICAL_WRITER_FLAGS:
        v = _is_truthy(env(flag, default="false"))
        if v is None or v is True:
            raise ShadowTargetGuardError(
                "SHADOW-AUX-CANONICAL-WRITER-FORBIDDEN",
                f"canonical Redis writer {flag} must be disabled in SHADOW ({_cw_roles.get(flag, flag)})")
        writers.append((_cw_roles.get(flag, flag), "disabled"))

    # (b) defence-in-depth: a canonical-Redis target on port 6379 must never be configured in SHADOW, even
    #     if the writer that would read it is (claimed) disabled.
    ccan_port = str(env("HERMES_CANDLE_CANONICAL_REDIS_PORT", default="") or "").strip()
    if ccan_port == str(_CANONICAL_REDIS_PORT):
        raise ShadowTargetGuardError(
            "SHADOW-CANDLE-FORWARD-REDIS-FORBIDDEN",
            "a canonical Redis target on port 6379 must not be configured in SHADOW")

    # (c) candle-forward seam.
    cf_enabled = _is_truthy(env("HERMES_CANDLE_FORWARD_ENABLED", default="false"))
    if cf_enabled is None:
        raise ShadowTargetGuardError(
            "SHADOW-CANDLE-FORWARD-CANONICAL-SINK-FORBIDDEN", "HERMES_CANDLE_FORWARD_ENABLED is malformed")
    if not cf_enabled:
        writers.append(("candle_forward_seam", "disabled"))
    else:
        sink = str(env("HERMES_CANDLE_FORWARD_SINK", default="") or "").strip().lower()
        if sink in _FORBIDDEN_CF_SINKS:
            raise ShadowTargetGuardError(
                "SHADOW-CANDLE-FORWARD-CANONICAL-SINK-FORBIDDEN",
                f"candle-forward sink '{sink}' selects canonical Redis — forbidden in SHADOW")
        if sink in _INERT_CF_SINKS:
            writers.append(("candle_forward_seam", "disabled"))
        elif sink == "shadow":
            sh_host = str(env("HERMES_CANDLE_FORWARD_SHADOW_REDIS_HOST", default="") or "").strip()
            sh_port = str(env("HERMES_CANDLE_FORWARD_SHADOW_REDIS_PORT", default="") or "").strip()
            if not sh_host or not sh_port:
                raise ShadowTargetGuardError(
                    "SHADOW-CANDLE-FORWARD-TARGET-REQUIRED",
                    "candle-forward shadow sink requires an explicit shadow Redis host+port")
            if sh_port == str(_CANONICAL_REDIS_PORT):
                raise ShadowTargetGuardError(
                    "SHADOW-CANDLE-FORWARD-REDIS-FORBIDDEN",
                    "candle-forward shadow Redis must not be canonical port 6379")
            # §3.2 the effective key prefix the writer reads (HERMES_CANDLE_FORWARD_SHADOW_KEY_PREFIX, which
            # utils.candle_publisher_v1.resolve_shadow_key_prefix consumes) MUST be run-scoped.
            cf_prefix = _run_scoped_prefix(
                env("HERMES_CANDLE_FORWARD_SHADOW_KEY_PREFIX", default=""), run_id,
                canonical_marker=_CANONICAL_CANDLE_PREFIX,
                req_code="SHADOW-CANDLE-FORWARD-KEYSPACE-REQUIRED",
                canon_code="SHADOW-CANDLE-FORWARD-KEYSPACE-CANONICAL",
                scope_code="SHADOW-CANDLE-FORWARD-KEYSPACE-NOT-RUN-SCOPED")
            writers.append(("candle_forward_seam", "shadow-validated"))
        else:
            raise ShadowTargetGuardError(
                "SHADOW-CANDLE-FORWARD-CANONICAL-SINK-FORBIDDEN",
                f"candle-forward sink '{sink}' is not a recognised safe SHADOW sink")

    # (d) shadow tick emitter — allowed ONLY with a validated non-canonical shadow target.
    st_enabled = _is_truthy(env("HERMES_SHADOW_TICK_PUBLISH_ENABLED", default="false"))
    if st_enabled is None:
        raise ShadowTargetGuardError(
            "SHADOW-SHADOW-TICK-TARGET-FORBIDDEN", "HERMES_SHADOW_TICK_PUBLISH_ENABLED is malformed")
    if not st_enabled:
        writers.append(("shadow_tick_emitter", "disabled"))
    else:
        st_port = str(env("HERMES_SHADOW_TICK_REDIS_PORT", default="") or "").strip()
        st_host = str(env("HERMES_SHADOW_TICK_REDIS_HOST", default="") or "").strip()
        if not st_host or not st_port:
            raise ShadowTargetGuardError(
                "SHADOW-SHADOW-TICK-TARGET-FORBIDDEN", "shadow tick emitter requires an explicit shadow target")
        if st_port == str(_CANONICAL_REDIS_PORT):
            raise ShadowTargetGuardError(
                "SHADOW-SHADOW-TICK-TARGET-FORBIDDEN", "shadow tick emitter must not target canonical port 6379")
        if _is_truthy(env("HERMES_SHADOW_TICK_TREAT_AS_PRODUCTION", default="false")):
            raise ShadowTargetGuardError(
                "SHADOW-SHADOW-TICK-TARGET-FORBIDDEN", "HERMES_SHADOW_TICK_TREAT_AS_PRODUCTION must be false in SHADOW")
        # §3.2 the shadow-tick key prefix (HERMES_SHADOW_TICK_KEY_PREFIX, consumed by the runtime shadow
        # emitter builder) MUST be run-scoped.
        st_prefix = _run_scoped_prefix(
            env("HERMES_SHADOW_TICK_KEY_PREFIX", default=""), run_id,
            canonical_marker=_CANONICAL_TICK_PREFIX,
            req_code="SHADOW-SHADOW-TICK-KEYSPACE-REQUIRED",
            canon_code="SHADOW-SHADOW-TICK-KEYSPACE-CANONICAL",
            scope_code="SHADOW-SHADOW-TICK-KEYSPACE-NOT-RUN-SCOPED",
            required_start="hermes:shadow:")   # emitter (RuntimeShadowConfig) requires this exact prefix
        writers.append(("shadow_tick_emitter", "shadow-validated"))

    writers.append(("primary_publisher", "primary-validated"))
    return tuple(writers), cf_prefix, st_prefix


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
        "redis_writers": {role: st for role, st in (manifest.redis_writers or ())},
        "candle_forward_shadow_prefix": manifest.candle_forward_shadow_prefix,
        "shadow_tick_prefix": manifest.shadow_tick_prefix,
    }
