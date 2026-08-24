"""
HERMES canonical runtime deployment identity + host/config binding.
WO-HELM-HERMES-DEV-DEPLOYMENT-IDENTITY-AND-HOST-CONFIG-BINDING-0001.

DEFECT (R2D2-recorded): the Redis public contract (manifest/heartbeat) reported
false identity — environment=dev / run_env=STAGING / deployed_sha=unknown on
PROD — because the publisher steps kept SEPARATE identity variables
(HERMES_ENVIRONMENT / HERMES_RUN_ENV / HERMES_DEPLOYED_SHA) with silent
defaults, independent of the real runtime identity that /buildinfo and
/readiness derive correctly.

DOCTRINE (permanent HERMES deployment rule; docs/architecture/
deployment-identity-and-config-binding.md):
  * ONE canonical environment identity: ENVIRONMENT (DEV|PROD), paired with its
    governed RUN_ENV (DEV->STAGING, PROD->PRODUCTION — the purge-gate identity).
  * ONE canonical build identity: the baked build identity (SOURCE_SHA / OCI
    revision — the same truth /buildinfo reports). Never a separately
    maintained deployed_sha that can rot to "unknown".
  * ONE host truth: the deployment mounts the HOST machine's /etc/hostname
    read-only into the container (HOST_HOSTNAME_PATH); the environment-owned
    runtime config declares EXPECTED_HOSTNAME. Wrong host/config combination —
    a DEV env file on the PROD host or vice versa — FAILS CLOSED at startup.
  * Every public identity surface (Redis manifest, Redis heartbeat, /buildinfo,
    /health, /readiness) derives from these SAME sources. No fallback identity
    variables exist any more.

No config in code: hostnames, environments and SHAs are external values. The
vocabulary below (accepted environments and their governed run-env pairing) is
schema, not environment-specific configuration — the same code runs DEV and
PROD.

Fail-closed: any missing/malformed identity fact raises DeploymentIdentityError
with a stable IDENT-* reason code and startup aborts visibly.
"""
from __future__ import annotations

from dataclasses import dataclass

# Governed identity vocabulary (schema, not per-environment values).
CANONICAL_ENVIRONMENTS = ("DEV", "PROD")
# ENVIRONMENT -> its single governed RUN_ENV (purge-gate identity: only
# RUN_ENV=PRODUCTION may ever purge; STAGING is the governed non-production
# gate identity). A pair mismatch is a wrong-environment config by definition.
RUN_ENV_BY_ENVIRONMENT = {"DEV": "STAGING", "PROD": "PRODUCTION"}

# Where the deployment mounts the host machine's /etc/hostname (read-only).
# Schema default; overridable via HOST_HOSTNAME_PATH external config.
DEFAULT_HOST_HOSTNAME_PATH = "/etc/host-hostname"

# Stable fail-closed reason codes.
ENV_MISSING = "IDENT-ENV-MISSING"
ENV_INVALID = "IDENT-ENV-INVALID"
RUNENV_MISSING = "IDENT-RUNENV-MISSING"
RUNENV_PAIR_MISMATCH = "IDENT-RUNENV-PAIR-MISMATCH"
BUILD_SHA_INVALID = "IDENT-BUILD-SHA-INVALID"
EXPECTED_HOST_MISSING = "IDENT-EXPECTED-HOST-MISSING"
HOST_SOURCE_MISSING = "IDENT-HOST-SOURCE-MISSING"
HOST_MISMATCH = "IDENT-HOST-MISMATCH"


class DeploymentIdentityError(RuntimeError):
    """Wrong/missing deployment identity — startup must FAIL CLOSED."""

    def __init__(self, code: str, detail: str):
        self.code = code
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class RuntimeIdentity:
    environment: str          # DEV | PROD           (canonical env truth)
    run_env: str              # STAGING | PRODUCTION (governed pair of environment)
    source_sha: str           # 40-hex canonical build truth (== /buildinfo)
    expected_hostname: str    # environment-owned declared host
    actual_hostname: str      # host machine truth (mounted /etc/hostname)
    service_identity: str = "hermes-signal"


def _read_host_hostname(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read().strip()


def resolve_runtime_identity(env_get=None, build_identity_fn=None,
                             host_reader=None) -> RuntimeIdentity:
    """Resolve + validate the canonical runtime identity. Fail-closed.

    All collaborators are injectable for tests; production callers use the real
    env contract, the baked build identity, and the mounted host hostname.
    """
    import os
    getenv = env_get or os.environ.get

    # --- environment truth -------------------------------------------------
    environment = (getenv("ENVIRONMENT") or "").strip()
    if not environment:
        raise DeploymentIdentityError(ENV_MISSING, "ENVIRONMENT is not set/empty")
    if environment not in CANONICAL_ENVIRONMENTS:
        raise DeploymentIdentityError(
            ENV_INVALID,
            f"ENVIRONMENT={environment!r} not in {CANONICAL_ENVIRONMENTS} "
            "(no aliases: dev/staging/NON_PROD are not identities)")

    run_env = (getenv("RUN_ENV") or "").strip()
    if not run_env:
        raise DeploymentIdentityError(RUNENV_MISSING, "RUN_ENV is not set/empty")
    governed = RUN_ENV_BY_ENVIRONMENT[environment]
    if run_env != governed:
        raise DeploymentIdentityError(
            RUNENV_PAIR_MISMATCH,
            f"ENVIRONMENT={environment} requires RUN_ENV={governed}, got {run_env!r} "
            "(one unambiguous runtime identity; contradictions are wrong-env config)")

    # --- build truth (same source /buildinfo reports) ----------------------
    if build_identity_fn is None:
        from utils.hermes_build_identity_v1 import build_identity as build_identity_fn  # noqa: PLC0415
    bi = build_identity_fn()
    sha = str(bi.get("source_sha") or "")
    import re
    if not re.fullmatch(r"[0-9a-f]{40}", sha) or not bi.get("build_identity_valid", False):
        raise DeploymentIdentityError(
            BUILD_SHA_INVALID,
            f"build identity invalid per governed build contract "
            f"(source_sha={sha[:20]!r}, build_identity_valid={bi.get('build_identity_valid')!r}) "
            "— 'unknown' is not a deployable identity")

    # --- host truth ---------------------------------------------------------
    expected = (getenv("EXPECTED_HOSTNAME") or "").strip()
    if not expected:
        raise DeploymentIdentityError(
            EXPECTED_HOST_MISSING,
            "EXPECTED_HOSTNAME is not set — environment-owned host binding is mandatory")
    path = (getenv("HOST_HOSTNAME_PATH") or DEFAULT_HOST_HOSTNAME_PATH).strip()
    reader = host_reader or _read_host_hostname
    try:
        actual = reader(path)
    except Exception as exc:  # noqa: BLE001 — unreadable host truth is fail-closed
        raise DeploymentIdentityError(
            HOST_SOURCE_MISSING,
            f"host hostname source {path!r} unreadable ({exc!r}) — deployment must "
            "mount the host /etc/hostname read-only") from exc
    actual = (actual or "").strip()
    if not actual or actual != expected:
        raise DeploymentIdentityError(
            HOST_MISMATCH,
            f"actual host {actual!r} != EXPECTED_HOSTNAME {expected!r} — this config "
            "does not belong on this host (wrong-environment config fails closed)")

    return RuntimeIdentity(environment=environment, run_env=run_env, source_sha=sha,
                           expected_hostname=expected, actual_hostname=actual)


# --------------------------------------------------------------------------- process-wide cache
_CACHED = None


def cached_runtime_identity() -> RuntimeIdentity:
    """The identity resolved once per process (first caller resolves). Publishers
    derive Redis metadata from THIS — never from separate defaulted variables."""
    global _CACHED
    if _CACHED is None:
        _CACHED = resolve_runtime_identity()
    return _CACHED


def _reset_cached_identity_for_tests():
    global _CACHED
    _CACHED = None


# --------------------------------------------------------------------------- consistency invariant
def check_identity_consistency(identity: RuntimeIdentity, published: dict) -> list:
    """Pure §9 invariant: a previously-published Redis identity surface
    (manifest or heartbeat dict) vs the canonical runtime identity. Returns a
    list of stable mismatch codes (empty == consistent). Missing fields are
    mismatches — an identity surface without identity is not consistent."""
    faults = []
    if not isinstance(published, dict):
        return ["IDENT-PUBLISHED-SURFACE-MISSING"]
    env = published.get("environment")
    if env is not None and env != identity.environment:      # heartbeat has no environment field
        faults.append("IDENT-PUBLISHED-ENV-MISMATCH")
    if "run_env" in published and published.get("run_env") != identity.run_env:
        faults.append("IDENT-PUBLISHED-RUNENV-MISMATCH")
    if "deployed_sha" in published and published.get("deployed_sha") != identity.source_sha:
        faults.append("IDENT-PUBLISHED-SHA-MISMATCH")
    return faults
