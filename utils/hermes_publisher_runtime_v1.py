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
    return [
        ("control_plane", steps.control_plane_step, DEFAULT_INTERVAL_SECONDS),
        ("indicators", steps.indicator_step, DEFAULT_INTERVAL_SECONDS),
        ("candle_features", steps.candle_feature_step, DEFAULT_INTERVAL_SECONDS),
        ("sessions_levels", steps.sessions_levels_step, DEFAULT_INTERVAL_SECONDS),
    ]


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
