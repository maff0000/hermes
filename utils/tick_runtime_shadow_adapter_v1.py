"""HERMES runtime tick-path SHADOW emit boundary (INERT / disabled-by-default).
WO-HELM-HERMES-REDIS-TICK-PUBLISHER-RUNTIME-INTEGRATE-INERT-0001.

Wires the proven serialising shadow publisher into the HERMES runtime tick path as a gated,
non-consumer emit seam. By default it is DISABLED (a no-op emitter). It can ONLY publish when
explicitly enabled AND fully configured AND authorised — and then ONLY to hermes:shadow:* keys via
the JSON-serialised SerializingShadowWriter (a real Redis client is never handed a Python dict).

Canonical live keys (hermes:ticks:*) can never be written here. There is no silent fallback: enabled
but missing/invalid config FAILS LOUD; enabled without a client FAILS LOUD (never a silent no-op).

HERMES owns raw market truth only — this seam consumes HERMES raw tick facts, never pushes into a
consumer, never reads SQL, never imports legacy/consumer code. ALL timestamps UTC.
"""
from __future__ import annotations
from datetime import datetime, timezone

from utils import tick_contract_v1 as tc
from utils import tick_shadow_publisher_v1 as sh
from utils import tick_shadow_activation_v1 as act
from utils import tick_shadow_observability_v1 as obs

WRITE_MODE_SHADOW_RUNTIME_INERT = "SHADOW_RUNTIME_INERT"
WRITE_MODE_LIVE = "LIVE"            # named only to be rejected


# --------------------------------------------------------------------------- raw-tick adaptation
def tick_to_raw_tick(tick):
    """Adapt a HERMES runtime tick fact (duck-typed) or a raw dict into the publisher's input shape.
    Reads market fields only; a stored-row `seq`, if present on a dict input, is intentionally left
    in place so the publisher rejects it fail-loud (it is not a completeness proof)."""
    if isinstance(tick, dict):
        raw = dict(tick)
        if "source_received_at_utc" not in raw:
            for alt in ("source_timestamp", "received_at", "timestamp"):
                if raw.get(alt) is not None:
                    raw["source_received_at_utc"] = raw[alt]; break
        return raw
    instrument = getattr(tick, "instrument", None)
    bid = getattr(tick, "bid", None)
    ask = getattr(tick, "ask", None)
    src = getattr(tick, "source", "oanda")
    src = src.value if hasattr(src, "value") else str(src)
    received = (getattr(tick, "source_timestamp", None) or getattr(tick, "received_at", None)
                or getattr(tick, "timestamp", None))
    raw = {"instrument": instrument, "source": src}
    if bid is not None:
        raw["bid"] = bid
    if ask is not None:
        raw["ask"] = ask
    if received is not None:
        raw["source_received_at_utc"] = received
    return raw


# --------------------------------------------------------------------------- config / fail-loud
class RuntimeShadowConfig:
    """Runtime-seam config. Runtime-level invariants validate always; the live target (host/port/db,
    authorisation) is validated only when enabled, by the underlying ShadowPublisherConfig."""

    def __init__(self, *, publisher_enabled, write_mode, namespace, shadow_prefix,
                 redis_host, redis_port, redis_db, redis_ex_seconds, payload_ttl_seconds,
                 shadow_authorised, treat_as_production, dev_shadow):
        if publisher_enabled is None or not isinstance(publisher_enabled, bool):
            raise ValueError("GOV-PUB-RT-CFG-001: publisher_enabled must be an explicit bool")
        if write_mode != WRITE_MODE_SHADOW_RUNTIME_INERT:
            raise ValueError("GOV-PUB-RT-CFG-002: write_mode must be SHADOW_RUNTIME_INERT "
                             "(LIVE/production rejected)")
        if namespace != "hermes":
            raise ValueError("GOV-PUB-RT-CFG-003: namespace must be 'hermes'")
        if not isinstance(shadow_prefix, str) or not shadow_prefix.startswith(sh.SHADOW_PREFIX):
            raise ValueError(f"GOV-PUB-RT-CFG-004: shadow_prefix must start with '{sh.SHADOW_PREFIX}'")
        if redis_ex_seconds != tc.REDIS_EX_SECONDS:
            raise ValueError(f"GOV-PUB-RT-CFG-005: redis_ex_seconds must be {tc.REDIS_EX_SECONDS}")
        if payload_ttl_seconds != tc.TTL_SECONDS:
            raise ValueError(f"GOV-PUB-RT-CFG-006: payload ttl_seconds must be {tc.TTL_SECONDS}")
        self.publisher_enabled = publisher_enabled
        self.write_mode = write_mode
        self.namespace = namespace
        self.shadow_prefix = shadow_prefix
        self.redis_host = redis_host
        self.redis_port = redis_port
        self.redis_db = redis_db
        self.redis_ex_seconds = redis_ex_seconds
        self.payload_ttl_seconds = payload_ttl_seconds
        self.shadow_authorised = shadow_authorised
        self.treat_as_production = treat_as_production
        self.dev_shadow = dev_shadow

    def to_shadow_publisher_config(self):
        """Build the proven shadow-writer config (fail-loud on missing host/port/db/authorisation)."""
        return sh.ShadowPublisherConfig(
            publisher_enabled=self.publisher_enabled, write_mode=sh.WRITE_MODE_SHADOW,
            namespace=self.namespace, shadow_prefix=self.shadow_prefix, contract_version="v1",
            redis_ex_seconds=self.redis_ex_seconds, payload_ttl_seconds=self.payload_ttl_seconds,
            redis_host=self.redis_host, redis_port=self.redis_port, redis_db=self.redis_db,
            shadow_authorised=self.shadow_authorised, treat_as_production=self.treat_as_production,
            test_only=self.dev_shadow)


# --------------------------------------------------------------------------- emitters
class DisabledShadowEmitter:
    """Explicit no-op emitter — used ONLY when the seam is explicitly disabled. Holds no client and
    performs no I/O. emit_tick never writes."""

    def __init__(self, config):
        self.config = config
        self.enabled = False
        self.metrics = obs.ShadowEmitMetrics()

    def emit_tick(self, tick, *, generated_at_utc=None, instrument_registry=None):
        return {"emitted": False, "reason": "SHADOW_RUNTIME_DISABLED"}

    def emit_tick_observed(self, tick, *, logger=None, generated_at_utc=None, instrument_registry=None):
        return {"emitted": False, "reason": "SHADOW_RUNTIME_DISABLED"}

    def emit_aggregate(self, instruments, *, generated_at_utc=None):
        return {"emitted": False, "reason": "SHADOW_RUNTIME_DISABLED"}

    def recovery_probe(self):
        return False, "SHADOW_RUNTIME_DISABLED", "emitter disabled; no route to probe"

    def status(self):
        return {"enabled": False, **self.metrics.status()}


class RuntimeShadowEmitter:
    """Enabled emitter — wraps the JSON-serialising real-client writer. Writes only hermes:shadow:*.
    Carries observability: attempt/success/failure counters, last_success/last_failure, a warning
    rate-limit, and a governed recovery probe."""

    def __init__(self, config, writer):
        if not isinstance(writer, act.SerializingShadowWriter):
            raise ValueError("GOV-PUB-RT-ADP-002: enabled emitter requires a SerializingShadowWriter "
                             "(the only allowed real-client path)")
        self.config = config
        self.writer = writer
        self.enabled = True
        self.metrics = obs.ShadowEmitMetrics()

    def emit_tick(self, tick, *, generated_at_utc=None, instrument_registry=None):
        """Fail-loud emit (records attempt + success/failure counters). Raises on GOV/contract
        faults so callers see them; emit_tick_observed wraps this for the runtime tick path."""
        gen = generated_at_utc or datetime.now(timezone.utc)
        self.metrics.record_attempt()
        try:
            raw = tick_to_raw_tick(tick)
            reg = instrument_registry if instrument_registry is not None else {raw.get("instrument")}
            res = self.writer.publish_tick(raw, generated_at_utc=gen, instrument_registry=reg)
        except Exception as exc:
            self.metrics.record_failure(datetime.now(timezone.utc), repr(exc))
            raise
        self.metrics.record_success(datetime.now(timezone.utc))
        return {"emitted": True, "reason": obs.REASON_EMIT_OK, **res}

    def emit_tick_observed(self, tick, *, logger=None, generated_at_utc=None, instrument_registry=None):
        """Runtime tick-path entrypoint: never raises (a shadow fault must not disrupt the market-truth
        path), records counters, applies the warning rate-limit, surfaces structured reason tags."""
        try:
            res = self.emit_tick(tick, generated_at_utc=generated_at_utc,
                                 instrument_registry=instrument_registry)
            if logger is not None:
                logger.debug("[%s] key=%s", obs.REASON_EMIT_OK, res.get("key"))
            return res
        except Exception as exc:  # noqa: BLE001 - bounded: shadow emit never breaks the tick path
            now = datetime.now(timezone.utc)
            warn, suppressed = self.metrics.should_emit_warning(now)
            if logger is not None:
                if warn:
                    logger.warning("[%s] reason=%s failed=%d suppressed_since_last=%d error=%r",
                                   obs.REASON_EMIT_FAIL, self.metrics.last_failure_reason,
                                   self.metrics.failed, suppressed, exc)
                else:
                    logger.debug("[%s] withheld=%d (rate-limited; persistent failure)",
                                 obs.REASON_RATE_LIMITED, suppressed)
            return {"emitted": False, "reason": obs.REASON_EMIT_FAIL, "error": repr(exc)}

    def emit_aggregate(self, instruments, *, generated_at_utc=None):
        gen = generated_at_utc or datetime.now(timezone.utc)
        res = self.writer.publish_aggregate(list(instruments), generated_at_utc=gen)
        return {"emitted": True, **res}

    def recovery_probe(self):
        """Governed recovery probe against the configured shadow route (hermes:shadow:* only)."""
        return obs.run_recovery_probe(redis_client=self.writer.redis_client,
                                      ex_seconds=self.config.redis_ex_seconds,
                                      now=datetime.now(timezone.utc))

    def status(self):
        return {"enabled": True, **self.metrics.status()}


# --------------------------------------------------------------------------- factory
def build_runtime_shadow_emitter(*, config, redis_client=None, redis_client_factory=None):
    """Disabled -> DisabledShadowEmitter (no client). Enabled -> RuntimeShadowEmitter built on the
    proven serialising writer (fail-loud on missing target/client; never a silent no-op)."""
    if not isinstance(config, RuntimeShadowConfig):
        raise ValueError("GOV-PUB-RT-ADP-003: RuntimeShadowConfig required")
    if not config.publisher_enabled:
        return DisabledShadowEmitter(config)
    spc = config.to_shadow_publisher_config()       # fail-loud on missing host/port/db/authorisation
    client = redis_client
    if client is None:
        if redis_client_factory is None:
            raise ValueError("GOV-PUB-RT-ADP-001: enabled runtime shadow emitter requires an explicit "
                             "redis client or factory (no silent no-op when enabled)")
        client = redis_client_factory(spc)
    writer = act.SerializingShadowWriter(config=spc, redis_client=client)
    return RuntimeShadowEmitter(config, writer)


def _default_real_client_factory(spc):
    """Explicit-target lazy real client (only ever called when the seam is enabled)."""
    return sh.connect_shadow_redis(spc)


def build_runtime_shadow_emitter_from_env(shadow_prefix=None):
    """Boot entrypoint for main.py. Reads an EXPLICIT, DOCUMENTED env contract via HERMES env_config.

    WP3 manifest binding: when the SHADOW composition root supplies `shadow_prefix` (the run-scoped
    manifest-approved value from validate_shadow_targets), the emitter uses THAT prefix exactly and never
    re-reads HERMES_SHADOW_TICK_KEY_PREFIX nor falls back to `hermes:shadow:`. Non-SHADOW callers pass None.

    Env contract (all HERMES_*; non-prefixed fall back per env_config):
      HERMES_SHADOW_TICK_PUBLISH_ENABLED   bool, default False  -> disabled no-op emitter
      HERMES_SHADOW_TICK_REDIS_HOST        str,  required when enabled (no default)
      HERMES_SHADOW_TICK_REDIS_PORT        int,  required when enabled (no default)
      HERMES_SHADOW_TICK_REDIS_DB          int,  required when enabled (no default)
      HERMES_SHADOW_TICK_AUTHORISED        bool, must be true when enabled
      HERMES_SHADOW_TICK_TREAT_AS_PRODUCTION bool, default False
      HERMES_SHADOW_TICK_DEV_SHADOW        bool, default False (dev/non-prod marker)

    Default (flag unset) -> DISABLED no-op. Enabled-but-misconfigured -> FAIL LOUD (no silent no-op).
    """
    from env_config import get_env, get_env_bool, get_env_int  # HERMES-owned config, lazy
    # WP3 Correction A: an EXPLICITLY supplied prefix (not None) that is empty/whitespace FAILS CLOSED — it
    # must NOT fall through to the environment or the generic 'hermes:shadow:' default. Only a genuine None
    # (non-SHADOW / legacy path) resolves env/default.
    if shadow_prefix is not None and (not isinstance(shadow_prefix, str) or not shadow_prefix.strip()):
        raise ValueError("GOV-PUB-RT-CFG-007: an explicitly supplied shadow_prefix must be a non-empty "
                         "string (empty/whitespace never falls back to env or the generic default)")
    _effective_prefix = shadow_prefix if shadow_prefix is not None else \
        (get_env("HERMES_SHADOW_TICK_KEY_PREFIX", default="") or "hermes:shadow:")
    enabled = get_env_bool("HERMES_SHADOW_TICK_PUBLISH_ENABLED", False)
    if not enabled:
        cfg = RuntimeShadowConfig(
            publisher_enabled=False, write_mode=WRITE_MODE_SHADOW_RUNTIME_INERT, namespace="hermes",
            shadow_prefix=_effective_prefix,
            redis_host=None, redis_port=None, redis_db=None,
            redis_ex_seconds=10, payload_ttl_seconds=5, shadow_authorised=False,
            treat_as_production=False, dev_shadow=False)
        return DisabledShadowEmitter(cfg)
    cfg = RuntimeShadowConfig(
        publisher_enabled=True, write_mode=WRITE_MODE_SHADOW_RUNTIME_INERT, namespace="hermes",
        shadow_prefix=_effective_prefix,
        redis_host=get_env("HERMES_SHADOW_TICK_REDIS_HOST", required=True),
        redis_port=get_env_int("HERMES_SHADOW_TICK_REDIS_PORT", required=True),
        redis_db=get_env_int("HERMES_SHADOW_TICK_REDIS_DB", required=True),
        redis_ex_seconds=10, payload_ttl_seconds=5,
        shadow_authorised=get_env_bool("HERMES_SHADOW_TICK_AUTHORISED", False),
        treat_as_production=get_env_bool("HERMES_SHADOW_TICK_TREAT_AS_PRODUCTION", False),
        dev_shadow=get_env_bool("HERMES_SHADOW_TICK_DEV_SHADOW", False))
    return build_runtime_shadow_emitter(config=cfg, redis_client_factory=_default_real_client_factory)
