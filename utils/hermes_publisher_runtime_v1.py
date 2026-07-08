"""HERMES durable in-process PUBLISHER RUNTIME supervisor v1.
WO-HELM-HERMES-DURABLE-PUBLISHER-WIRING-MAINPY-0001.

Moves the (currently detached dev-loop) governed publishers into a durable in-process supervisor so they survive
container restart and live/die with the HERMES service. Generic, governed, gated, EXCEPTION-ISOLATED (one
publisher fault never silently kills the app — it is counted + surfaced), BOUNDED loops, GRACEFUL start/stop.

DISABLED by default. ENABLED-without-AUTHORISED -> SystemExit(101). A DUPLICATE-PUBLISHER GUARD refuses to start
unless HERMES_PUBLISHER_RUNTIME_OWNER=in_process, so the in-process supervisor and a detached loop can never both
publish the same governed key family. NO Redis I/O, NO threads, NO publisher starts at import. NO auth. NO regime.

The per-family publish STEPS are governed callables (control-plane, indicators, candle_features, sessions/levels)
that build via the merged contract builders + their existing from_env gates (so all instrument/timeframe/scope/D1
gates are preserved). A step is a no-op when its family gate is disabled.
"""
from __future__ import annotations
import threading

ENABLED_ENV = "HERMES_PUBLISHER_RUNTIME_ENABLED"
AUTHORISED_ENV = "HERMES_PUBLISHER_RUNTIME_AUTHORISED"
OWNER_ENV = "HERMES_PUBLISHER_RUNTIME_OWNER"      # must be "in_process" to start (duplicate-publisher guard)
DETACHED_OWNER = "detached"
IN_PROCESS_OWNER = "in_process"
HALT_CODE = 101
DEFAULT_INTERVAL_SECONDS = 60
# Quote is a HOT surface: its key TTL is short (hermes_quote_tick_contract_v1.QUOTE_TTL_SECONDS=15). The quote
# runner MUST publish FASTER than that TTL so the key stays continuously present (publish_interval < TTL) — the
# 60s default would leave the key absent ~45s of every 60s. Governed, explicit, testable; enforced < TTL at
# spec-build time (fail loud otherwise). WO-HELM-HERMES-QUOTE-PUBLISHER-CADENCE-FIX-0001.
QUOTE_PUBLISH_INTERVAL_SECONDS = 5
_FAULT_LOG_EVERY = 20

try:
    from hermes_logging import get_logger
    _LOG = get_logger("hermes.publisher_runtime")
except Exception:   # logging is optional at import; never fail the import on it
    import logging
    _LOG = logging.getLogger("hermes.publisher_runtime")


class PublisherRunner:
    """Runs one governed publish STEP on a bounded interval in a daemon thread. Exception-isolated: a step
    fault is counted + rate-limited-logged, the loop continues, the app is never crashed. Graceful stop."""

    def __init__(self, name, step_fn, interval_seconds=DEFAULT_INTERVAL_SECONDS):
        self.name = name
        self.step_fn = step_fn
        self.interval = max(1, int(interval_seconds))
        self.metrics = {"runs": 0, "faults": 0, "published": 0, "last_fault": None}
        self._stop = threading.Event()
        self._thread = None
        self._fault_log_count = 0

    def _loop(self, redis_client):
        while not self._stop.is_set():
            try:
                res = self.step_fn(redis_client) or {}
                self.metrics["runs"] += 1
                self.metrics["published"] += int(res.get("published", 0))
            except SystemExit:
                raise                                  # gate fail-loud (enabled-without-authorised) must propagate
            except Exception as exc:  # noqa: BLE001 - exception isolation: count + log, never kill the app
                self.metrics["faults"] += 1
                self.metrics["last_fault"] = repr(exc)[:200]
                self._fault_log_count += 1
                if self._fault_log_count == 1 or self._fault_log_count % _FAULT_LOG_EVERY == 0:
                    _LOG.warning("[PUB_RUNTIME_FAULT] runner=%s count=%d error=%s",
                                 self.name, self.metrics["faults"], self.metrics["last_fault"])
            self._stop.wait(self.interval)             # bounded; graceful stop wakes immediately on stop()

    def start(self, redis_client):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, args=(redis_client,),
                                        name=f"hermes-pub-{self.name}", daemon=True)
        self._thread.start()

    def stop(self, timeout=5):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def status(self):
        return {"name": self.name, "interval_seconds": self.interval, **self.metrics}


class HermesPublisherSupervisor:
    """Owns the runners; start()/stop() spawn/join daemon threads. start() enforces the duplicate-publisher
    guard (owner must be in_process). status()/fault_summary() feed the control-plane heartbeat/health."""
    enabled = True

    def __init__(self, runners, owner, redis_client):
        self.runners = list(runners)
        self.owner = owner
        self.redis_client = redis_client
        self.started = False

    def start(self):
        if self.owner != IN_PROCESS_OWNER:
            raise ValueError(f"GOV-HERMES-PUBRT-002: refusing to start the in-process supervisor unless "
                             f"{OWNER_ENV}=in_process (duplicate-publisher guard — a detached loop must not also run)")
        if self.started:
            return
        for r in self.runners:
            r.start(self.redis_client)
        self.started = True

    def stop(self):
        for r in self.runners:
            r.stop()
        self.started = False

    def status(self):
        return {"enabled": True, "owner": self.owner, "started": self.started,
                "runners": [r.status() for r in self.runners]}

    def fault_summary(self):
        return {r.name: r.metrics["faults"] for r in self.runners}


class DisabledPublisherSupervisor:
    """Safe no-op (default). No threads, no Redis client, no I/O."""
    enabled = False

    def start(self):
        return

    def stop(self):
        return

    def status(self):
        return {"enabled": False}

    def fault_summary(self):
        return {}


def _default_redis_client():
    import redis  # lazy; only on the enabled path
    from env_config import get_env, get_env_int
    return redis.Redis(host=get_env("HERMES_CANDLE_CANONICAL_REDIS_HOST", required=True),
                       port=get_env_int("HERMES_CANDLE_CANONICAL_REDIS_PORT", required=True),
                       db=get_env_int("HERMES_CANDLE_CANONICAL_REDIS_DB", required=True), socket_timeout=5)


def default_runner_specs():
    """The governed publisher runner specs (name, step_fn, interval). Lazy import of the step module so this
    file does NO Redis/compute I/O at import. Each step is a no-op when its family gate is disabled."""
    from utils import hermes_runtime_publisher_steps_v1 as steps
    specs = [
        ("control_plane", steps.control_plane_step, DEFAULT_INTERVAL_SECONDS),
        ("indicators", steps.indicator_step, DEFAULT_INTERVAL_SECONDS),
        ("candle_features", steps.candle_feature_step, DEFAULT_INTERVAL_SECONDS),
        ("sessions_levels", steps.sessions_levels_step, DEFAULT_INTERVAL_SECONDS),
    ]
    # WO-HELM-HERMES-INSTRUMENT-CATALOG-RUNTIME-PUBLISHER-WIRING-0001 — instrument-catalog runner: DISABLED by
    # default -> NOT appended (default remains exactly the four active families). Enabled-without-authorised ->
    # SystemExit(101) (fail-closed via the contract gate). Enabled+authorised -> appended as a 5th governed runner.
    # No Redis I/O here (env read only); the catalog step is itself gate-first/no-op when disabled.
    from utils import hermes_instrument_catalog_v1 as ic
    if getattr(ic.build_instrument_catalog_publisher_from_env(), "enabled", False):
        specs.append(("instrument_catalog", steps.instrument_catalog_step, DEFAULT_INTERVAL_SECONDS))
    # WO-HELM-HERMES-FEED-HEALTH-RUNTIME-PUBLISHER-WIRING-0001 — feed-health runner: DISABLED by default -> NOT
    # appended (default stays exactly the current active families). Enabled-without-authorised -> SystemExit(101)
    # (fail-closed via the contract gate); enabled+authorised without valid canonical scope -> fail-closed
    # (GOV-HERMES-FH-020/021). Enabled+authorised -> appended AFTER instrument_catalog as the 6th governed runner.
    # No Redis I/O here (env read only); the feed-health step is itself gate-first/no-op when disabled. No quote/
    # tick/market_map runner is ever added here.
    from utils import hermes_feed_health_v1 as fh
    if getattr(fh.build_feed_health_publisher_from_env(), "enabled", False):
        specs.append(("feed_health", steps.feed_health_step, DEFAULT_INTERVAL_SECONDS))
    # WO-HELM-HERMES-QUOTE-RUNTIME-PUBLISHER-WIRING-0001 — quote runner: DISABLED by default -> NOT appended.
    # Enabled-without-authorised -> SystemExit(101); enabled+authorised without valid canonical scope -> fail-closed
    # (GOV-HERMES-QT-020/021). Enabled+authorised -> appended AFTER feed_health (so catalog+quote=6 with families
    # [..,instrument_catalog,quote]; catalog+feed_health+quote=7 with [..,instrument_catalog,feed_health,quote]).
    # No Redis I/O here (env read only); the quote step is itself gate-first/no-op when disabled. No tick/market_map
    # runner is ever added here.
    # WO-HELM-HERMES-QUOTE-PUBLISHER-CADENCE-FIX-0001 — quote is a HOT surface: it publishes on QUOTE_PUBLISH_INTERVAL_SECONDS
    # (< QUOTE_TTL_SECONDS) so the short-TTL key stays continuously present. Enforce the invariant fail-loud: a
    # publish interval >= TTL would leave the key flapping absent, which is a governance defect.
    from utils import hermes_quote_tick_contract_v1 as qt
    if getattr(qt.build_quote_publisher_from_env(), "enabled", False):
        if QUOTE_PUBLISH_INTERVAL_SECONDS >= qt.QUOTE_TTL_SECONDS:
            raise ValueError(f"GOV-HERMES-PUBRT-003: quote publish interval "
                             f"({QUOTE_PUBLISH_INTERVAL_SECONDS}s) must be < QUOTE_TTL_SECONDS "
                             f"({qt.QUOTE_TTL_SECONDS}s) so the hot quote key stays continuously present")
        specs.append(("quote", steps.quote_step, QUOTE_PUBLISH_INTERVAL_SECONDS))
    return specs


def build_publisher_supervisor_from_env(*, redis_client_factory=None, runner_specs=None):
    """Boot factory. DEFAULT DISABLED -> DisabledPublisherSupervisor (no-op, no threads, no Redis client).
    ENABLED without AUTHORISED -> terminal halt SystemExit(101). ENABLED + AUTHORISED -> HermesPublisherSupervisor
    (NOT started; main.py calls .start() in the lifespan). Duplicate-publisher guard: .start() refuses unless
    HERMES_PUBLISHER_RUNTIME_OWNER=in_process. No hidden defaults; NO Redis/thread/I-O at import or here (the
    Redis client is built only when enabled+authorised)."""
    from env_config import get_env, get_env_bool   # lazy; HERMES-owned config only
    if not get_env_bool(ENABLED_ENV, False):
        return DisabledPublisherSupervisor()
    if not get_env_bool(AUTHORISED_ENV, False):
        raise SystemExit(HALT_CODE)
    owner = (get_env(OWNER_ENV, default=DETACHED_OWNER) or DETACHED_OWNER).strip()
    client = (redis_client_factory or _default_redis_client)()
    specs = runner_specs if runner_specs is not None else default_runner_specs()
    runners = [PublisherRunner(n, step, interval) for (n, step, interval) in specs]
    return HermesPublisherSupervisor(runners, owner, client)
