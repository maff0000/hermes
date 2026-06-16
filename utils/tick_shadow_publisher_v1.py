"""HERMES Redis tick publisher v1 — SHADOW build.
WO-HELM-HERMES-REDIS-TICK-PUBLISHER-SHADOW-BUILD-0001.

Governed SHADOW publish path: builds valid tick-contract payloads (utils.tick_contract_v1), reuses
the inert plan builders (utils.tick_publisher_v1), and writes ONLY to an explicitly-shadowed Redis
namespace through an injected Redis client. This WO does NOT activate production/live publishing,
does NOT write the canonical live keys, and does NOT perform any consumer cutover.

Live (FORBIDDEN here)              ->  Shadow (this WO only)
  hermes:ticks:{instrument}:latest:v1  ->  hermes:shadow:ticks:{instrument}:latest:v1
  hermes:ticks:latest:v1               ->  hermes:shadow:ticks:latest:v1

The shadow keys are NON-CONSUMER, NON-CUTOVER, NON-PRODUCTION contract-validation keys. Falcon /
Falcon-structure must NOT consume them unless a later Architect-authorised consumer-shadow WO
explicitly permits it. The envelope's own `key` field stays the canonical contract identity (so it
validates and so a later WO can compare); only the Redis storage key is shadowed.

HERMES owns RAW market-tick truth ONLY: no interpretive consumer fields, no consumer-shaped live
keys, no legacy/consumer imports, no database path. `seq` stays null; stored-row seq is rejected.
ALL timestamps UTC.
"""
from __future__ import annotations
from datetime import datetime

from utils import tick_contract_v1 as tc
from utils import tick_publisher_v1 as tp

WRITE_MODE_SHADOW = "SHADOW_NO_LIVE"
WRITE_MODE_LIVE = "LIVE"            # named only to be rejected; live is a separate authorised WO
SHADOW_PREFIX = "hermes:shadow:"
LIVE_PREFIX = "hermes:ticks:"
_NON_PROD_HOSTS = ("localhost", "127.0.0.1", "::1", "")


# --------------------------------------------------------------------------- shadow key transform
def to_shadow_key(live_key: str) -> str:
    """hermes:ticks:... -> hermes:shadow:ticks:...  (fail loud on anything else)."""
    if not isinstance(live_key, str) or not live_key.startswith(LIVE_PREFIX):
        raise ValueError(f"GOV-PUB-SHADOW-KEY-001: cannot shadow non-live key {live_key!r}")
    return SHADOW_PREFIX + live_key[len("hermes:"):]


def assert_shadow_key(key: str):
    """Reject any attempt to write a canonical LIVE key, or a non-shadow key, in SHADOW mode."""
    if key in (tc.aggregate_key(),) or (key.startswith(LIVE_PREFIX) and not key.startswith(SHADOW_PREFIX)):
        raise ValueError(f"GOV-PUB-SHADOW-KEY-002: refusing to write LIVE key {key!r} in SHADOW mode")
    if not key.startswith(SHADOW_PREFIX):
        raise ValueError(f"GOV-PUB-SHADOW-KEY-003: SHADOW mode writes only {SHADOW_PREFIX}* keys (got {key!r})")
    return True


# --------------------------------------------------------------------------- config / fail-loud
class ShadowPublisherConfig:
    """Explicit, no-hidden-defaults SHADOW config. Every value passed explicitly; no default
    host/port/db; no localhost-as-production; SHADOW requires explicit authorisation; LIVE rejected."""

    def __init__(self, *, publisher_enabled, write_mode, namespace, shadow_prefix,
                 contract_version, redis_ex_seconds, payload_ttl_seconds,
                 redis_host, redis_port, redis_db, shadow_authorised,
                 treat_as_production=False, test_only=False):
        if publisher_enabled is None or not isinstance(publisher_enabled, bool):
            raise ValueError("GOV-PUB-SHADOW-CFG-001: publisher_enabled must be an explicit bool")
        if write_mode != WRITE_MODE_SHADOW:
            raise ValueError("GOV-PUB-SHADOW-CFG-002: only SHADOW_NO_LIVE permitted in this WO "
                             "(production/live mode rejected)")
        if namespace != tp.NAMESPACE:
            raise ValueError(f"GOV-PUB-SHADOW-CFG-003: namespace must be '{tp.NAMESPACE}'")
        if contract_version != tc.CONTRACT_VERSION:
            raise ValueError(f"GOV-PUB-SHADOW-CFG-004: contract_version must be {tc.CONTRACT_VERSION}")
        if redis_ex_seconds != tc.REDIS_EX_SECONDS:
            raise ValueError(f"GOV-PUB-SHADOW-CFG-005: redis_ex_seconds must be {tc.REDIS_EX_SECONDS}")
        if payload_ttl_seconds != tc.TTL_SECONDS:
            raise ValueError(f"GOV-PUB-SHADOW-CFG-006: payload ttl_seconds must be {tc.TTL_SECONDS}")
        if not redis_host or not isinstance(redis_host, str):
            raise ValueError("GOV-PUB-SHADOW-CFG-007: redis_host must be explicit (no default host)")
        if redis_port is None or not isinstance(redis_port, int):
            raise ValueError("GOV-PUB-SHADOW-CFG-008: redis_port must be explicit int (no default port)")
        if redis_db is None or not isinstance(redis_db, int):
            raise ValueError("GOV-PUB-SHADOW-CFG-009: redis_db must be explicit int (no default db)")
        if shadow_authorised is not True:
            raise ValueError("GOV-PUB-SHADOW-CFG-010: SHADOW mode requires explicit authorisation (fail-loud)")
        if treat_as_production and str(redis_host).lower() in _NON_PROD_HOSTS:
            raise ValueError("GOV-PUB-SHADOW-CFG-011: localhost/loopback may not be a production target")
        if not isinstance(shadow_prefix, str) or not shadow_prefix.startswith(SHADOW_PREFIX):
            raise ValueError(f"GOV-PUB-SHADOW-CFG-012: shadow_prefix must start with '{SHADOW_PREFIX}'")
        if str(redis_host).lower() in _NON_PROD_HOSTS and not test_only:
            raise ValueError("GOV-PUB-SHADOW-CFG-013: localhost target must be explicitly marked test_only")
        self.publisher_enabled = publisher_enabled
        self.write_mode = write_mode
        self.namespace = namespace
        self.shadow_prefix = shadow_prefix
        self.contract_version = contract_version
        self.redis_ex_seconds = redis_ex_seconds
        self.payload_ttl_seconds = payload_ttl_seconds
        self.redis_host = redis_host
        self.redis_port = redis_port
        self.redis_db = redis_db
        self.shadow_authorised = shadow_authorised
        self.treat_as_production = treat_as_production
        self.test_only = test_only

    def _inert_config(self):
        """Reuse the merged inert plan-builders for validated envelope construction."""
        return tp.InertPublisherConfig(
            publisher_enabled=self.publisher_enabled, namespace=self.namespace,
            contract_version=self.contract_version, redis_ex_seconds=self.redis_ex_seconds,
            payload_ttl_seconds=self.payload_ttl_seconds, write_mode=tp.WRITE_MODE_INERT)


def connect_shadow_redis(config):
    """Real Redis client factory — explicit target only, behind the SHADOW gate. Lazily imports the
    client lib so the module has no hard dependency at import time. NOT called by this WO's tests
    (which inject an in-memory fake); provided so the next gated WO has an explicit, default-free
    construction point."""
    if not isinstance(config, ShadowPublisherConfig):
        raise ValueError("GOV-PUB-SHADOW-CONN-001: explicit ShadowPublisherConfig required (no inferred target)")
    import redis  # lazy; explicit dependency only on the shadow path
    return redis.Redis(host=config.redis_host, port=config.redis_port, db=config.redis_db)


# --------------------------------------------------------------------------- shadow plan + publish
def build_shadow_plan(raw_tick, *, generated_at_utc: datetime, instrument_registry, config):
    """Build a SHADOW write plan from a raw tick: reuse the inert validated plan, then re-key to the
    shadow namespace. seq stays null; stored-row seq rejected; malformed/unsupported fail loud;
    absent source -> UNAVAILABLE (contract semantics)."""
    if not isinstance(config, ShadowPublisherConfig):
        raise ValueError("GOV-PUB-SHADOW-PLAN-001: ShadowPublisherConfig required")
    inert_plan = tp.build_tick_write_plan(raw_tick, generated_at_utc=generated_at_utc,
                                          instrument_registry=instrument_registry,
                                          config=config._inert_config())
    shadow_key = to_shadow_key(inert_plan["key"])
    assert_shadow_key(shadow_key)
    return {"operation": tp.OPERATION_SET, "key": shadow_key, "value": inert_plan["value"],
            "ex_seconds": config.redis_ex_seconds, "write_mode": WRITE_MODE_SHADOW}


def build_aggregate_shadow_plan(instruments, *, generated_at_utc: datetime, config):
    """Aggregate discovery catalog -> shadow key. Discovery only; no price."""
    if not isinstance(config, ShadowPublisherConfig):
        raise ValueError("GOV-PUB-SHADOW-PLAN-001: ShadowPublisherConfig required")
    inert_plan = tp.build_aggregate_write_plan(instruments, generated_at_utc=generated_at_utc,
                                               config=config._inert_config())
    shadow_key = to_shadow_key(inert_plan["key"])
    assert_shadow_key(shadow_key)
    return {"operation": tp.OPERATION_SET, "key": shadow_key, "value": inert_plan["value"],
            "ex_seconds": config.redis_ex_seconds, "write_mode": WRITE_MODE_SHADOW}


def _looks_like_real_redis_client(client):
    """True for a real network Redis client (it would receive a dict on the legacy path). In-memory
    no-write/capturing fakes used in tests do not look like this."""
    if client is None:
        return False
    module_root = (type(client).__module__ or "").split(".")[0]
    if module_root in ("redis", "valkey", "rediscluster"):
        return True
    return any(hasattr(client, attr) for attr in ("connection_pool", "execute_command",
                                                  "get_connection", "connection_kwargs"))


class ShadowTickPublisher:
    """Legacy dict-path shadow writer — RETAINED for in-memory no-write/fake clients ONLY (it hands
    the client a Python dict). A REAL Redis client must NEVER be wired here: real-client writes route
    exclusively through tick_shadow_activation_v1.SerializingShadowWriter (JSON-serialised). This
    class fails loud if handed a real-client-like object (closes the PR #24 hygiene note)."""

    def __init__(self, *, config, redis_client):
        if not isinstance(config, ShadowPublisherConfig):
            raise ValueError("GOV-PUB-SHADOW-PUB-001: ShadowPublisherConfig required")
        if redis_client is None:
            raise ValueError("GOV-PUB-SHADOW-PUB-002: redis_client must be supplied explicitly "
                             "(no default/inferred client)")
        if _looks_like_real_redis_client(redis_client):
            raise ValueError("GOV-PUB-SHADOW-PUB-004: ShadowTickPublisher accepts only no-write/fake "
                             "clients (dict-to-client path). Route real Redis clients through "
                             "SerializingShadowWriter (JSON-serialised). (fail-loud)")
        self.config = config
        self.redis_client = redis_client

    def _publish_plan(self, plan):
        assert_shadow_key(plan["key"])
        if plan["write_mode"] != WRITE_MODE_SHADOW:
            raise ValueError("GOV-PUB-SHADOW-PUB-003: only SHADOW_NO_LIVE plans may be published here")
        tc.validate_tick_contract(plan["value"])
        # store as a JSON-serialisable string would be the caller's job; client receives the dict
        redis_result = self.redis_client.set(plan["key"], plan["value"], ex=plan["ex_seconds"])
        v = plan["value"]
        return {"key": plan["key"], "ex_seconds": plan["ex_seconds"], "write_mode": plan["write_mode"],
                "payload_validated": True, "redis_result": redis_result,
                "generated_at_utc": v["generated_at_utc"], "valid_until_utc": v["valid_until_utc"],
                "freshness_state": v["freshness_state"], "status": v["status"],
                "reason_codes": v["reason_codes"]}

    def publish_tick(self, raw_tick, *, generated_at_utc, instrument_registry):
        plan = build_shadow_plan(raw_tick, generated_at_utc=generated_at_utc,
                                 instrument_registry=instrument_registry, config=self.config)
        return self._publish_plan(plan)

    def publish_aggregate(self, instruments, *, generated_at_utc):
        plan = build_aggregate_shadow_plan(instruments, generated_at_utc=generated_at_utc,
                                           config=self.config)
        return self._publish_plan(plan)
