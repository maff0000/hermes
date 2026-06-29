"""HERMES governed CANDLE publisher v1 (inert + shadow + canonical-gated + observability).
WO-HELM-HERMES-GOVERNED-CANDLE-FORWARD-CONTRACT-AND-PUBLISHER-0001.

Turns a governed candle envelope (utils.candle_contract_v1) into intended Redis write plans and a
JSON-serialised shadow write path. Canonical (`hermes:candles:*`) publishing is GATED OFF and cannot
happen without explicit authorisation; shadow (`hermes:shadow:candles:*`) is also gated and dev-only.
A real Redis client is NEVER handed a Python dict. No live/canonical write is performed by this WO.

HERMES owns raw market-candle truth only. No legacy Proteus contract is emitted as governed output.
ALL timestamps UTC.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone

from utils import candle_contract_v1 as cc

WRITE_MODE_INERT = "INERT_NO_WRITE"
WRITE_MODE_SHADOW = "SHADOW_NO_LIVE"
WRITE_MODE_CANONICAL = "CANONICAL_LIVE"          # named only to be gated/rejected here
OPERATION_SET = "SET"
NAMESPACE = "hermes"
SHADOW_PREFIX = "hermes:shadow:candles:"          # legacy hardcoded default (fallback)
CANONICAL_PREFIX = "hermes:candles:"
_NON_PROD_HOSTS = ("localhost", "127.0.0.1", "::1", "")
SHADOW_KEY_PREFIX_ENV = "HERMES_CANDLE_FORWARD_SHADOW_KEY_PREFIX"

# Canonical publish grid (GOLD MTF). H4 is published ONLY via the governed derived-H4 path
# (candle_h4_publish_wire_v1); D1 ONLY via the governed derived-D1 path (candle_d1_publish_wire_v1) —
# neither is ever a stale direct candle, and the DIRECT seam still refuses both (UNSUPPORTED/DERIVED-only),
# so generic/direct D1 publication is impossible. The legacy "D" token remains NEVER published. Broker
# aliases must be canonicalised BEFORE the writer sees the key.
CANONICAL_PUBLISH_TIMEFRAMES = ("M1", "M5", "M15", "H1", "H4", "D1")
_CANONICAL_ALIAS_DENY = ("XAUUSD",)


def resolve_shadow_key_prefix(environ=None):
    """Resolve the shadow keyspace prefix, ingesting HERMES_CANDLE_FORWARD_SHADOW_KEY_PREFIX from the
    environment. Falls back GRACEFULLY to the legacy hardcoded default `hermes:shadow:candles:` when the
    variable is unset/blank — so existing runtimes are byte-for-byte unchanged.

    Trailing-colon safety: the returned prefix always ends with EXACTLY one ':' regardless of whether the
    configured value supplied zero, one, or many trailing colons (and surrounding whitespace is trimmed),
    so string interpolation `prefix + remainder` can never produce `::` or a missing separator. A value
    that is only colons/whitespace degrades to the legacy default rather than a degenerate ':' prefix.

    Canonical-namespace guard: a configured prefix that resolves into the canonical namespace
    `hermes:candles:` is REFUSED (fail-loud GOV-CANDLE-PUB-PREFIX-ERR-001) — a spoofed prefix must never
    let shadow writes leak into the canonical truth keyspace.
    """
    if environ is None:
        from env_config import get_env  # lazy: env_config is the codebase's environment configuration
        raw = (get_env(SHADOW_KEY_PREFIX_ENV, default="") or "").strip()
    else:
        raw = (environ.get(SHADOW_KEY_PREFIX_ENV) or "").strip()
    core = raw.rstrip(":")
    if not core:                      # unset, blank, or colons-only -> legacy default
        return SHADOW_PREFIX
    prefix = core + ":"
    if prefix.startswith(CANONICAL_PREFIX):
        raise ValueError(
            f"GOV-CANDLE-PUB-PREFIX-ERR-001: shadow key prefix {prefix!r} collides with the canonical "
            f"namespace {CANONICAL_PREFIX!r} — refusing (fail-loud). A shadow prefix must never resolve "
            "into canonical truth.")
    return prefix


# --------------------------------------------------------------------------- config / fail-loud
class CandlePublisherConfig:
    """Explicit, no-hidden-defaults config. Canonical publish is OFF unless BOTH enabled AND
    authorised (and even then this WO never performs the write). Shadow requires its own auth +
    explicit non-localhost-as-prod target."""

    def __init__(self, *, publish_enabled, publish_authorised, shadow_publish_enabled,
                 shadow_authorised, namespace, contract_version,
                 redis_host=None, redis_port=None, redis_db=None,
                 treat_as_production=False, dev_shadow=False):
        for name, val in (("publish_enabled", publish_enabled),
                          ("publish_authorised", publish_authorised),
                          ("shadow_publish_enabled", shadow_publish_enabled),
                          ("shadow_authorised", shadow_authorised)):
            if val is None or not isinstance(val, bool):
                raise ValueError(f"GOV-CANDLE-PUB-CFG-001: {name} must be an explicit bool")
        if namespace != NAMESPACE:
            raise ValueError("GOV-CANDLE-PUB-CFG-002: namespace must be 'hermes'")
        if contract_version != cc.CONTRACT_VERSION:
            raise ValueError(f"GOV-CANDLE-PUB-CFG-003: contract_version must be {cc.CONTRACT_VERSION}")
        self.publish_enabled = publish_enabled
        self.publish_authorised = publish_authorised
        self.shadow_publish_enabled = shadow_publish_enabled
        self.shadow_authorised = shadow_authorised
        self.namespace = namespace
        self.contract_version = contract_version
        self.redis_host = redis_host
        self.redis_port = redis_port
        self.redis_db = redis_db
        self.treat_as_production = treat_as_production
        self.dev_shadow = dev_shadow

    def assert_canonical_allowed(self):
        """Canonical live publish is DISABLED unless explicitly enabled AND authorised. This WO never
        sets both; the guard exists so a future caller cannot publish canonical keys silently."""
        if not (self.publish_enabled and self.publish_authorised):
            raise ValueError("GOV-CANDLE-PUB-CANON-001: canonical hermes:candles:* publish is DISABLED "
                             "(requires publish_enabled AND publish_authorised) [PUBLISH_DISABLED]")
        return True

    def assert_shadow_allowed(self):
        if not (self.shadow_publish_enabled and self.shadow_authorised):
            raise ValueError("GOV-CANDLE-PUB-SHADOW-001: shadow publish requires shadow_publish_enabled "
                             "AND shadow_authorised [SHADOW_DISABLED]")
        if not self.redis_host or not isinstance(self.redis_port, int) or not isinstance(self.redis_db, int):
            raise ValueError("GOV-CANDLE-PUB-SHADOW-002: explicit redis host/port/db required (no defaults)")
        if self.treat_as_production and str(self.redis_host).lower() in _NON_PROD_HOSTS:
            raise ValueError("GOV-CANDLE-PUB-SHADOW-003: localhost may not be a production target")
        if str(self.redis_host).lower() in _NON_PROD_HOSTS and not self.dev_shadow:
            raise ValueError("GOV-CANDLE-PUB-SHADOW-004: localhost target must be marked dev_shadow")
        return True


# --------------------------------------------------------------------------- shadow key transform
def to_shadow_key(canonical, prefix=None):
    """hermes:candles:... -> <shadow_prefix>... . `prefix` defaults to the env-resolved shadow prefix
    (legacy `hermes:shadow:candles:` when unset), so behaviour is unchanged unless configured."""
    if not isinstance(canonical, str) or not canonical.startswith(CANONICAL_PREFIX):
        raise ValueError(f"GOV-CANDLE-PUB-KEY-001: cannot shadow non-canonical key {canonical!r}")
    prefix = prefix or resolve_shadow_key_prefix()
    return prefix + canonical[len(CANONICAL_PREFIX):]


def assert_shadow_key(key, prefix=None):
    """Guard: a shadow-mode write may only target the (configurable) shadow prefix, never canonical.
    `prefix` defaults to the env-resolved shadow prefix (legacy default when unset)."""
    prefix = prefix or resolve_shadow_key_prefix()
    if key.startswith(CANONICAL_PREFIX) and not key.startswith(prefix):
        raise ValueError(f"GOV-CANDLE-PUB-KEY-002: refusing to write canonical key {key!r} in shadow mode")
    if not key.startswith(prefix):
        raise ValueError(f"GOV-CANDLE-PUB-KEY-003: shadow mode writes only {prefix}* (got {key!r})")
    return True


def assert_canonical_key(key):
    """Guard: a canonical write may target ONLY a versioned canonical key
    hermes:candles:{instrument}:{tf}:latest:v1, with a publish-allowed timeframe (M1/M5/M15/H1/H4/D1) and a
    non-alias instrument. H4 and D1 are reachable ONLY via their governed derived producers (the direct seam
    refuses them), so generic/direct H4/D1 publication is impossible. Fail-loud on unversioned keys, the
    legacy "D" token, or broker-alias instruments — the last line of defence even if a caller misbuilds the key."""
    if not isinstance(key, str) or not key.startswith(CANONICAL_PREFIX):
        raise ValueError(f"GOV-CANDLE-PUB-CANON-KEY-001: not a canonical key {key!r}")
    suffix = f":latest:{cc.CONTRACT_VERSION}"
    if not key.endswith(suffix):
        raise ValueError(f"GOV-CANDLE-PUB-CANON-KEY-002: canonical key must end {suffix!r} "
                         f"(no unversioned keys) — got {key!r}")
    parts = key.split(":")          # hermes:candles:{instrument}:{tf}:latest:v1 -> 6 parts
    if len(parts) != 6:
        raise ValueError(f"GOV-CANDLE-PUB-CANON-KEY-003: malformed canonical key {key!r}")
    instrument, tf = parts[2], parts[3]
    if instrument in _CANONICAL_ALIAS_DENY:
        raise ValueError(f"GOV-CANDLE-PUB-CANON-KEY-004: broker-alias instrument {instrument!r} must be "
                         "canonicalised before publish (no alias keys)")
    if tf not in CANONICAL_PUBLISH_TIMEFRAMES:
        raise ValueError(f"GOV-CANDLE-PUB-CANON-KEY-005: timeframe {tf!r} not publish-allowed "
                         f"(canonical grid {CANONICAL_PUBLISH_TIMEFRAMES}; legacy 'D' never published; "
                         "H4/D1 only via their governed derived producers)")
    return True


# --------------------------------------------------------------------------- write-plan builders
def _ex_for(envelope):
    return cc.redis_ex_seconds(envelope["data"]["timeframe"])


def build_inert_write_plan(envelope):
    """Validated INERT write plan for the canonical key — NEVER performs a write."""
    cc.validate_candle_contract(envelope)
    return {"operation": OPERATION_SET, "key": envelope["key"], "value": envelope,
            "ex_seconds": _ex_for(envelope), "write_mode": WRITE_MODE_INERT}


def build_shadow_write_plan(envelope):
    """Validated SHADOW write plan (re-keyed to the shadow namespace)."""
    cc.validate_candle_contract(envelope)
    prefix = resolve_shadow_key_prefix()          # resolve once, apply consistently to build + guard
    skey = to_shadow_key(envelope["key"], prefix=prefix)
    assert_shadow_key(skey, prefix=prefix)
    return {"operation": OPERATION_SET, "key": skey, "value": envelope,
            "ex_seconds": _ex_for(envelope), "write_mode": WRITE_MODE_SHADOW}


def build_canonical_write_plan(envelope):
    """Validated CANONICAL write plan (writes the governed canonical key as-is — NOT re-keyed).
    Re-validates the governed v1 contract (rejecting invalid OHLC/geometry/forbidden fields) and
    guards the key (versioned, publish-allowed timeframe, non-alias instrument) before any write."""
    cc.validate_candle_contract(envelope)
    key = envelope["key"]
    assert_canonical_key(key)
    return {"operation": OPERATION_SET, "key": key, "value": envelope,
            "ex_seconds": _ex_for(envelope), "write_mode": WRITE_MODE_CANONICAL}


# --------------------------------------------------------------------------- no-write sink (inert)
class NoWriteCandleSink:
    """In-memory capturing sink — connects to NOTHING. For inert tests/evidence only."""

    def __init__(self):
        self.captured = []

    def publish(self, plan):
        if not isinstance(plan, dict) or plan.get("write_mode") != WRITE_MODE_INERT:
            raise ValueError("GOV-CANDLE-PUB-SINK-001: no-write sink accepts INERT_NO_WRITE plans only")
        self.captured.append(plan)
        return plan


# --------------------------------------------------------------------------- serialisation + writer
def serialize_envelope(envelope):
    if not isinstance(envelope, dict):
        raise ValueError("GOV-CANDLE-PUB-SER-001: only a dict envelope may be serialised")
    return json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))


def deserialize_envelope(blob):
    if isinstance(blob, bytes):
        blob = blob.decode("utf-8")
    if not isinstance(blob, str):
        raise ValueError("GOV-CANDLE-PUB-SER-002: Redis value must be str/bytes JSON")
    env = json.loads(blob)
    cc.validate_candle_contract(env)
    return env


class SerializingCandleShadowWriter:
    """Real-client shadow write path. JSON-serialises before client.set; a real client is NEVER
    handed a Python dict; writes ONLY hermes:shadow:candles:* keys. Requires shadow auth."""

    def __init__(self, *, config, redis_client):
        if not isinstance(config, CandlePublisherConfig):
            raise ValueError("GOV-CANDLE-PUB-WR-001: CandlePublisherConfig required")
        config.assert_shadow_allowed()
        if redis_client is None:
            raise ValueError("GOV-CANDLE-PUB-WR-002: redis_client must be supplied explicitly")
        self.config = config
        self.redis_client = redis_client

    def publish(self, envelope):
        plan = build_shadow_write_plan(envelope)
        assert_shadow_key(plan["key"])
        json_value = serialize_envelope(plan["value"])
        if not isinstance(json_value, (str, bytes)):
            raise ValueError("GOV-CANDLE-PUB-WR-003: Redis value must be JSON str/bytes, never a dict")
        result = self.redis_client.set(plan["key"], json_value, ex=plan["ex_seconds"])
        v = plan["value"]
        return {"key": plan["key"], "ex_seconds": plan["ex_seconds"], "write_mode": plan["write_mode"],
                "serialised": True, "value_type": type(json_value).__name__, "redis_result": result,
                "status": v["status"], "freshness_state": v["freshness_state"],
                "gap_state": v["data"]["gap_state"], "source_coverage": v["data"]["source_coverage"]}


class SerializingCandleCanonicalWriter:
    """Real-client CANONICAL write path (GOLD MTF). Requires BOTH publish_enabled AND
    publish_authorised (assert_canonical_allowed) — a misconfigured caller can NEVER publish silently.
    JSON-serialises before client.set (a real client is NEVER handed a Python dict); writes ONLY
    versioned canonical hermes:candles:{instr}:{M1|M5|M15|H1}:latest:v1 keys; fail-loud on
    validation/guard failure; Redis write errors PROPAGATE to the caller (never swallowed here)."""

    def __init__(self, *, config, redis_client):
        if not isinstance(config, CandlePublisherConfig):
            raise ValueError("GOV-CANDLE-PUB-CANON-WR-001: CandlePublisherConfig required")
        config.assert_canonical_allowed()            # fail-loud unless enabled AND authorised
        if redis_client is None:
            raise ValueError("GOV-CANDLE-PUB-CANON-WR-002: redis_client must be supplied explicitly")
        self.config = config
        self.redis_client = redis_client

    def publish(self, envelope):
        plan = build_canonical_write_plan(envelope)   # validates + guards key (version/tf/alias)
        assert_canonical_key(plan["key"])             # belt-and-braces before the write
        json_value = serialize_envelope(plan["value"])
        if not isinstance(json_value, (str, bytes)):
            raise ValueError("GOV-CANDLE-PUB-CANON-WR-003: Redis value must be JSON str/bytes, never a dict")
        result = self.redis_client.set(plan["key"], json_value, ex=plan["ex_seconds"])
        v = plan["value"]
        return {"key": plan["key"], "ex_seconds": plan["ex_seconds"], "write_mode": plan["write_mode"],
                "serialised": True, "value_type": type(json_value).__name__, "redis_result": result,
                "status": v["status"], "freshness_state": v["freshness_state"],
                "gap_state": v["data"]["gap_state"], "source_coverage": v["data"]["source_coverage"]}


# --------------------------------------------------------------------------- observability
class CandlePublishMetrics:
    def __init__(self, warn_min_interval_seconds=30):
        self.attempted = 0
        self.success = 0
        self.failure = 0
        self.last_success_utc = None
        self.last_failure_utc = None
        self.last_failure_reason = None
        self.last_published_instrument = None
        self.last_published_timeframe = None
        self.source_gap_count = 0
        self.incomplete_source_count = 0
        self.suppressed_warning_count = 0
        self._warn_min = warn_min_interval_seconds
        self._last_warn = None

    def record_attempt(self):
        self.attempted += 1

    def record_success(self, now, instrument, timeframe, status, gap_state):
        self.success += 1
        self.last_success_utc = now
        self.last_published_instrument = instrument
        self.last_published_timeframe = timeframe
        if gap_state in ("GAP_DETECTED",):
            self.source_gap_count += 1
        if status == "SOURCE_INCOMPLETE":
            self.incomplete_source_count += 1

    def record_failure(self, now, reason):
        self.failure += 1
        self.last_failure_utc = now
        self.last_failure_reason = reason

    def should_warn(self, now):
        if self._last_warn is None or (now - self._last_warn).total_seconds() >= self._warn_min:
            withheld = self.suppressed_warning_count
            self._last_warn = now
            self.suppressed_warning_count = 0
            return True, withheld
        self.suppressed_warning_count += 1
        return False, self.suppressed_warning_count

    def status(self):
        def _iso(d):
            return d.astimezone(timezone.utc).isoformat() if isinstance(d, datetime) else None
        return {"attempted": self.attempted, "success": self.success, "failure": self.failure,
                "last_success_utc": _iso(self.last_success_utc),
                "last_failure_utc": _iso(self.last_failure_utc),
                "last_failure_reason": self.last_failure_reason,
                "last_published_instrument": self.last_published_instrument,
                "last_published_timeframe": self.last_published_timeframe,
                "source_gap_count": self.source_gap_count,
                "incomplete_source_count": self.incomplete_source_count,
                "suppressed_warning_count": self.suppressed_warning_count}


# --------------------------------------------------------------------------- disabled-by-default seam
class DisabledCandleEmitter:
    def __init__(self, config):
        self.config = config
        self.enabled = False
        self.metrics = CandlePublishMetrics()

    def emit(self, envelope=None, **_):
        # Clean no-op for the disabled seam: accepts emit(candle=...) / emit(envelope=...) / emit()
        # without raising. Writes nothing (no Redis client, no SQL, no shadow/canonical key).
        return {"emitted": False, "reason": "CANDLE_PUBLISH_DISABLED"}

    def status(self):
        return {"enabled": False, **self.metrics.status()}


def build_candle_emitter_from_env():
    """Boot seam for a FUTURE runtime wiring. Default DISABLED no-op. NOT wired into main.py in this
    WO; runtime activation is a separate authorised step. Env contract (documented, fail-loud):
      HERMES_CANDLE_PUBLISH_ENABLED / HERMES_CANDLE_PUBLISH_AUTHORISED          (canonical — default false)
      HERMES_CANDLE_SHADOW_PUBLISH_ENABLED / HERMES_CANDLE_SHADOW_AUTHORISED    (shadow — default false)
      HERMES_CANDLE_SHADOW_REDIS_HOST/PORT/DB, _TREAT_AS_PRODUCTION, _DEV_SHADOW (required when shadow enabled)
    """
    from env_config import get_env, get_env_bool, get_env_int  # lazy; HERMES-owned config
    pub_en = get_env_bool("HERMES_CANDLE_PUBLISH_ENABLED", False)
    sh_en = get_env_bool("HERMES_CANDLE_SHADOW_PUBLISH_ENABLED", False)
    cfg = CandlePublisherConfig(
        publish_enabled=pub_en, publish_authorised=get_env_bool("HERMES_CANDLE_PUBLISH_AUTHORISED", False),
        shadow_publish_enabled=sh_en, shadow_authorised=get_env_bool("HERMES_CANDLE_SHADOW_AUTHORISED", False),
        namespace="hermes", contract_version="v1",
        redis_host=(get_env("HERMES_CANDLE_SHADOW_REDIS_HOST", required=True) if sh_en else None),
        redis_port=(get_env_int("HERMES_CANDLE_SHADOW_REDIS_PORT", required=True) if sh_en else None),
        redis_db=(get_env_int("HERMES_CANDLE_SHADOW_REDIS_DB", required=True) if sh_en else None),
        treat_as_production=get_env_bool("HERMES_CANDLE_SHADOW_TREAT_AS_PRODUCTION", False),
        dev_shadow=get_env_bool("HERMES_CANDLE_SHADOW_DEV_SHADOW", False))
    # this WO: always return the disabled no-op emitter (no canonical, no shadow runtime write)
    return DisabledCandleEmitter(cfg)
