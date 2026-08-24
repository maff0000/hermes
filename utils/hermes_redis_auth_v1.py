"""
HERMES Redis writer-authority helper — single auth seam for EVERY runtime Redis client.
WO-HELM-HERMES-DEV-REDIS-CONSUMER-INTEGRITY-READONLY-BOUNDARY-0001.

DOCTRINE (docs/architecture/redis-consumer-integrity-boundary.md):
HERMES Redis is a PUBLISHED READ interface. Consumers may read HERMES market
truth (read-only ACL on `hermes:*`) but may not mutate HERMES operational
state. HERMES itself publishes through a dedicated ACL writer authority
(`REDIS_USERNAME`, secret supplied externally) — never through the open
default user once the boundary is active.

Config contract (external, one shared schema, values per environment):
  REDIS_USERNAME   optional. Empty/unset => legacy open model ({} — client
                   connects as Redis `default`, pre-boundary behaviour).
                   Set (e.g. `hermes-writer`) => writer authority REQUIRED.
  REDIS_PASSWORD   the writer secret when REDIS_USERNAME is set. Lives ONLY in
                   the environment-owned 0600 env file (same model as
                   OANDA_API_KEY). Never in code/Git/image/logs/Fabric.

FAIL-SAFE (§9 — no hidden fallback): if REDIS_USERNAME is set but the secret
is missing/empty, `redis_auth_kwargs()` RAISES RedisAuthConfigError. HERMES
must fail loudly/degrade — it must never silently fall back to default
authority, and it must never disable Redis security to recover. Callers do
NOT catch this into a default-auth retry.

No secret is ever logged or returned in reprs; kwargs go straight into the
redis client constructor.
"""
from __future__ import annotations


class RedisAuthConfigError(RuntimeError):
    """Writer authority misconfigured — fail loudly, never fall back to default."""


def redis_auth_kwargs(env_get=None) -> dict:
    """kwargs to splat into EVERY HERMES runtime redis client constructor.

    Returns {} when no writer authority is configured (legacy/open model), or
    {'username': ..., 'password': ...} when REDIS_USERNAME is set. Raises
    RedisAuthConfigError when the authority is configured but incomplete.
    """
    if env_get is None:
        from env_config import get_env

        def env_get(key):
            return get_env(key, default="")
    username = (env_get("REDIS_USERNAME") or "").strip()
    if not username:
        return {}
    password = (env_get("REDIS_PASSWORD") or "").strip()
    if not password:
        raise RedisAuthConfigError(
            "REDIS_USERNAME=%r is set but REDIS_PASSWORD is missing/empty — "
            "writer authority is REQUIRED once configured; refusing silent "
            "fallback to default Redis authority" % username)
    return {"username": username, "password": password}
