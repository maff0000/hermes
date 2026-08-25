"""
HERMES enabled-write-target authority guard.
WO-HELM-HERMES-DEV-SHADOW-REDIS-WRITER-AUTH-COMPLETION-0001 (R2D2 AMBER delta).

DEFECT: HERMES had TWO active Redis write targets (main + DEV shadow :6380) but
writer-authority provisioning covered only one — the shadow emitter then failed
authentication continuously (~499k suppressed SHADOW_TICK_EMIT_FAIL) with no
startup-visible surface naming the broken target.

INVARIANT (permanent): every Redis target that is ENABLED for HERMES write
publication must have USABLE writer authority before HERMES is considered
ready. At startup each enabled target is probed (connect + auth + PING) using
the SAME writer-authority seam every runtime client uses
(utils/hermes_redis_auth_v1.redis_auth_kwargs — no second credential model).

Semantics:
  enabled + probe OK          -> OK
  enabled + auth rejected     -> AUTH_FAILED   (CRITICAL log, surfaced on /health)
  enabled + unreachable       -> UNREACHABLE   (CRITICAL log, surfaced on /health)
  enabled + auth seam broken  -> AUTH_CONFIG_INVALID (fail-loud seam error surfaced)
  disabled                    -> DISABLED      (INERT: no connection, no credential
                                                requirement, no probe, no readiness
                                                impact — disabled means inert)

No fallback: a failed probe NEVER downgrades the emitter to default authority —
it only makes the failure visible at startup instead of an indefinite silent
error stream. Health colour stays owned by market truth; this guard adds a
named, visible `write_targets` surface plus CRITICAL logs.

No config in code: endpoints and flags come from the existing external schema
(main: REDIS_* via get_redis_config; shadow tick:
HERMES_SHADOW_TICK_PUBLISH_ENABLED + HERMES_SHADOW_TICK_AUTHORISED +
HERMES_SHADOW_TICK_REDIS_*; candle-forward shadow:
HERMES_CANDLE_FORWARD_SHADOW_AUTHORISED + HERMES_CANDLE_FORWARD_SHADOW_REDIS_*
— mirroring the emitters' own governed gates).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WriteTargetReport:
    name: str          # main | shadow_tick | candle_forward_shadow
    enabled: bool
    endpoint: str      # host:port/db ("" when disabled/unconfigured) — never a secret
    status: str        # OK | AUTH_FAILED | UNREACHABLE | AUTH_CONFIG_INVALID | DISABLED
    detail: str = ""


def _truthy(v) -> bool:
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


def enumerate_write_targets(env_get=None):
    """The governed write targets and whether each is enabled. Pure env reads."""
    if env_get is None:
        from env_config import get_env

        def env_get(key):
            return get_env(key, default="")
    targets = []
    # main publication target — always enabled (the canonical publish path).
    targets.append({
        "name": "main", "enabled": True,
        "host": env_get("REDIS_HOST"), "port": env_get("REDIS_PORT"),
        "db": env_get("REDIS_DB"),
    })
    st_enabled = _truthy(env_get("HERMES_SHADOW_TICK_PUBLISH_ENABLED")) \
        and _truthy(env_get("HERMES_SHADOW_TICK_AUTHORISED"))
    targets.append({
        "name": "shadow_tick", "enabled": st_enabled,
        "host": env_get("HERMES_SHADOW_TICK_REDIS_HOST"),
        "port": env_get("HERMES_SHADOW_TICK_REDIS_PORT"),
        "db": env_get("HERMES_SHADOW_TICK_REDIS_DB"),
    })
    cf_enabled = _truthy(env_get("HERMES_CANDLE_FORWARD_SHADOW_AUTHORISED"))
    targets.append({
        "name": "candle_forward_shadow", "enabled": cf_enabled,
        "host": env_get("HERMES_CANDLE_FORWARD_SHADOW_REDIS_HOST"),
        "port": env_get("HERMES_CANDLE_FORWARD_SHADOW_REDIS_PORT"),
        "db": env_get("HERMES_CANDLE_FORWARD_SHADOW_REDIS_DB"),
    })
    return targets


def _default_probe(host, port, db, auth_kwargs):
    """Bounded connect+auth+PING. Returns None on success, raises on failure."""
    import redis
    c = redis.Redis(host=host, port=int(port), db=int(db or 0),
                    socket_timeout=3, socket_connect_timeout=3, **auth_kwargs)
    try:
        c.ping()
    finally:
        try:
            c.close()
        except Exception:  # noqa: BLE001
            pass


def verify_enabled_write_targets(env_get=None, probe_fn=None, auth_kwargs_fn=None):
    """Probe every ENABLED write target with the governed writer authority.

    Returns [WriteTargetReport]. Disabled targets are INERT (no probe, no
    credential requirement). Never raises — failures are reported loudly by the
    caller (CRITICAL log + /health surface), not swallowed.
    """
    import redis as _redis
    if auth_kwargs_fn is None:
        from utils.hermes_redis_auth_v1 import redis_auth_kwargs as auth_kwargs_fn
    probe = probe_fn or _default_probe
    reports = []
    for t in enumerate_write_targets(env_get):
        endpoint = f"{t['host']}:{t['port']}/{t['db'] or 0}" if t["host"] else ""
        if not t["enabled"]:
            reports.append(WriteTargetReport(t["name"], False, endpoint, "DISABLED",
                                             "inert: no connection, no credential requirement"))
            continue
        try:
            auth = auth_kwargs_fn()
        except Exception as exc:  # RedisAuthConfigError — seam is fail-loud
            reports.append(WriteTargetReport(t["name"], True, endpoint,
                                             "AUTH_CONFIG_INVALID", str(exc)[:160]))
            continue
        try:
            probe(t["host"], t["port"], t["db"], auth)
            reports.append(WriteTargetReport(t["name"], True, endpoint, "OK"))
        except _redis.exceptions.AuthenticationError as exc:
            reports.append(WriteTargetReport(
                t["name"], True, endpoint, "AUTH_FAILED",
                "writer authority not usable on this target: %s" % str(exc)[:120]))
        except Exception as exc:  # noqa: BLE001
            reports.append(WriteTargetReport(t["name"], True, endpoint, "UNREACHABLE",
                                             str(exc)[:160]))
    return reports
