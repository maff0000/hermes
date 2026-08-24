"""HERMES full-runtime-contract fail-closed PREFLIGHT v1.
WO-HELM-HERMES-COMPLETE-TRACKED-RUNTIME-CONFIG-AND-XAU-PARITY-0001.

Extends the earlier dark_config_preflight to validate the COMPLETE deployment contract driven by
runtime_config_schema_v1: identity/image, DB (canonical, port 3307, no legacy conflict), Redis routing (no unsafe
fallback), canonical-Redis + activation authority, all XAU operational controls (via effective render + parity),
expansion-dark boundary, hard safety invariants, and the exact deployment artefact set. Fail-closed: any parity-critical
omission, type/enum/range/must_equal violation, canonical/legacy conflict, unsafe fallback, wrong compose set, mutable
image, or missing rollback input FAILS. Never prints secret values.
"""
from __future__ import annotations
import os

from deploy.advanced_v1 import runtime_config_schema_v1 as schema
from deploy.advanced_v1 import render_effective_config as renderer
from deploy.advanced_v1 import xau_parity_v1 as parity

_SECRET_TOKENS = ("PASSWORD", "SECRET", "TOKEN", "API_KEY", "CREDENTIAL", "WEBHOOK", "ACCOUNT_ID", "ACTIVATION_APPROVAL")
_UNSAFE_REDIS_HOSTS = ("127.0.0.1", "localhost", "::1", "")

EXPECTED_COMPOSE = ["docker-compose.yml",
                    "deploy/advanced_v1/docker-compose.operational.yml",
                    "deploy/advanced_v1/docker-compose.dark.yml"]
_FORBIDDEN_COMPOSE_SUBSTR = ("dev-override", "override.yml")

# The hard-required governed env inputs (the overlay references these with ${VAR:?}; no safe default exists).
# Secrets that already live in the base `.env` env_file (DB_PASSWORD, OANDA keys, Redis password) are NOT listed here.
REQUIRED_ENV_INPUTS = ["SOURCE_SHA", "BUILD_UTC", "HERMES_IMAGE_REF", "DB_HOST", "DB_PORT", "DB_NAME",
                       "REDIS_HOST", "HERMES_CANDLE_CANONICAL_REDIS_HOST",
                       "HERMES_CANDLE_CANONICAL_ACTIVATION_APPROVAL", "OANDA_ENVIRONMENT"]


def _is_secret_name(n):
    return any(t in n.upper() for t in _SECRET_TOKENS)


def _typecheck(rec, value):
    """Return a fault string or None. Validates type/enum/range/must_equal for a rendered value."""
    name, t = rec["name"], rec["type"]
    if rec["must_equal"] is not None and str(value) != str(rec["must_equal"]):
        return f"CFG-MUSTEQUAL-{name}: expected {rec['must_equal']!r} got {value!r}"
    if t == "bool" and str(value).lower() not in ("true", "false", "0", "1"):
        return f"CFG-TYPE-{name}: not a bool ({value!r})"
    if t == "int":
        try:
            iv = int(str(value))
        except ValueError:
            return f"CFG-TYPE-{name}: not an int ({value!r})"
        if rec["min"] is not None and iv < rec["min"]:
            return f"CFG-RANGE-{name}: {iv} < {rec['min']}"
        if rec["max"] is not None and iv > rec["max"]:
            return f"CFG-RANGE-{name}: {iv} > {rec['max']}"
    if t == "enum" and rec["enum"] and str(value) not in rec["enum"]:
        return f"CFG-ENUM-{name}: {value!r} not in {rec['enum']}"
    if t == "sha40" and not (len(str(value)) == 40 and all(c in "0123456789abcdef" for c in str(value))):
        return f"CFG-SHA-{name}: not 40-lowercase-hex"
    return None


def validate_runtime_config(env, *, compose_files=None, environment="DEV", require_digest_pin=True,
                            require_rollback=True):
    """env: parsed governed env-file dict. Returns (ok, faults, safe_summary). Renders the effective config from the
    tracked compose set, then validates the full contract. Fail-closed."""
    faults = []
    compose_files = list(compose_files or EXPECTED_COMPOSE)

    # ---- deployment artefact set ----
    if compose_files != EXPECTED_COMPOSE:
        faults.append(f"CFG-COMPOSE-SET: expected {EXPECTED_COMPOSE} got {compose_files}")
    for cf in compose_files:
        if any(s in cf for s in _FORBIDDEN_COMPOSE_SUBSTR):
            faults.append(f"CFG-FORBIDDEN-OVERRIDE: untracked/extra override in compose set ({cf})")

    # ---- canonical vs legacy conflict (fail-loud, no silent alias fallback) ----
    for alias, canonical in schema.ALIAS_TO_CANONICAL.items():
        if alias in env and canonical in env and str(env[alias]) != str(env[canonical]):
            faults.append(f"CFG-ALIAS-CONFLICT: {alias}={env[alias]!r} != {canonical}={env[canonical]!r}")
        if alias in env and canonical not in env:
            faults.append(f"CFG-LEGACY-ONLY: {alias} supplied without canonical {canonical} (use canonical name)")

    # ---- identity / image ----
    for name in ("SOURCE_SHA", "BUILD_UTC", "HERMES_IMAGE_REF"):
        if not env.get(name):
            faults.append(f"CFG-IDENTITY-MISSING-{name}")
    sha = env.get("SOURCE_SHA", "")
    if sha and (len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha)):
        faults.append("CFG-SOURCE-SHA-MALFORMED (need 40 lowercase hex)")
    img = env.get("HERMES_IMAGE_REF", "")
    if require_digest_pin and img and "@sha256:" not in img and not img.startswith("hermes-signal:prod-"):
        faults.append("CFG-IMAGE-NOT-PINNED (need @sha256 digest or governed prod- tag)")

    # ---- required governed env inputs present (the ${VAR:?} contract; secrets from .env are excluded) ----
    for name in REQUIRED_ENV_INPUTS:
        if not env.get(name):
            rec = schema.BY_NAME.get(name, {})
            faults.append(f"CFG-ENV-MISSING-{name}: {rec.get('consequence', 'required governed input')}")

    # ---- render the effective config (a missing ${VAR:?} raises -> fault) ----
    rendered = None
    try:
        rendered = renderer.render_effective(compose_files, env)
    except renderer.RenderError as e:
        faults.append(f"CFG-RENDER-FAILED: {e}")

    if rendered is not None:
        # DB port must be the governed 3307 (never the silent 3306 fallback)
        dbp = rendered.get(renderer.container_var("DB_PORT", environment))
        if dbp != "3307":
            faults.append(f"CFG-DB-PORT: effective DB port {dbp!r} != 3307 (silent 3306 fallback?)")
        # Redis host must not be an unsafe local fallback
        rh = rendered.get(renderer.container_var("REDIS_HOST", environment))
        if rh in _UNSAFE_REDIS_HOSTS:
            faults.append(f"CFG-REDIS-UNSAFE-FALLBACK: effective Redis host {rh!r}")
        crh = rendered.get("HERMES_CANDLE_CANONICAL_REDIS_HOST")
        if crh in _UNSAFE_REDIS_HOSTS:
            faults.append(f"CFG-CANONICAL-REDIS-UNSAFE: {crh!r}")
        # type/enum/range/must_equal for every rendered contract field
        for name, rec in schema.BY_NAME.items():
            if rec["secret"] or rec["authority_bearing"]:
                continue
            cvar = renderer.container_var(name, environment)
            if cvar in rendered:
                f = _typecheck(rec, rendered[cvar])
                if f:
                    faults.append(f)
        # expansion-dark + safety invariants (must_equal already covers, but assert explicitly)
        for cvar, want, code in (("HERMES_ADVANCED_V1_MASTER_ENABLED", "false", "CFG-MASTER-NOT-FALSE"),
                                 ("HERMES_ADVANCED_V1_PUBLISHER_MODE", "DISABLED", "CFG-MODE-NOT-DISABLED"),
                                 ("CONSUMER_LIVE", "false", "CFG-CONSUMER-ON"),
                                 ("HERMES_BACKFILL_EXECUTION_ENABLED", "false", "CFG-BACKFILL-EXEC-ON")):
            if str(rendered.get(cvar, "")).lower() != want.lower():
                faults.append(f"{code}: {cvar}={rendered.get(cvar)!r} (need {want})")
        # one-stream scope: OANDA subscription must remain the governed 14-instrument set
        instr = rendered.get("INSTRUMENTS", "")
        if instr and instr != schema.BY_NAME["INSTRUMENTS"]["default"]:
            faults.append("CFG-INSTRUMENTS-CHANGED: OANDA subscription scope differs from the authorised 14-set")
        # publisher process model
        if rendered.get("HERMES_PUBLISHER_RUNTIME_OWNER") not in (None, "in_process"):
            faults.append("CFG-PROCESS-OWNER: publisher owner not in_process (topology change)")
        # parity: no parity-critical non-secret field left unset by the tracked bundle
        missing = parity.missing_from_tracked(rendered, environment)
        if missing:
            faults.append(f"CFG-PARITY-INCOMPLETE: parity-critical fields unset by tracked bundle: {sorted(missing)}")

    # WO-HELM-HERMES-PROD-IDENTITY-PROMOTION-PRECHECK-AND-RUNENV-SCHEMA-RECONCILIATION-0001:
    # governed identity pair (single authority: utils/hermes_runtime_identity_v1).
    # DEV->STAGING, PROD->PRODUCTION. A contradictory pair is a wrong-environment
    # config and must never pass deployment validation. Enum membership is already
    # checked per-field above; this enforces the PAIRING fail-closed.
    from utils.hermes_runtime_identity_v1 import RUN_ENV_BY_ENVIRONMENT as _pair
    _envv, _runv = str(env.get("ENVIRONMENT") or ""), str(env.get("RUN_ENV") or "")
    if _envv in _pair and _runv and _runv != _pair[_envv]:
        faults.append(f"CFG-RUNENV-PAIR: ENVIRONMENT={_envv} requires RUN_ENV={_pair[_envv]} got {_runv!r}")

    # ---- rollback inputs ----
    if require_rollback and not env.get("HERMES_ROLLBACK_IMAGE_REF"):
        faults.append("CFG-ROLLBACK-MISSING: HERMES_ROLLBACK_IMAGE_REF (previous immutable digest) required")

    safe = _safe_summary(env, rendered, environment)
    return (len(faults) == 0), faults, safe


def _safe_summary(env, rendered, environment):
    """Secret-redacted effective summary (never a secret value)."""
    def red(name, val):
        if _is_secret_name(name) or name in schema.SECRET_FIELDS or name in schema.AUTHORITY_FIELDS:
            return "<PRESENT>" if val else "<ABSENT>"
        return val
    summ = {"schema_version": schema.SCHEMA_VERSION, "environment": environment,
            "db": {}, "redis": {}, "canonical_redis": {}, "expansion_dark": {}, "safety": {}, "identity": {}}
    if rendered:
        summ["db"] = {"host": rendered.get(renderer.container_var("DB_HOST", environment)),
                      "port": rendered.get(renderer.container_var("DB_PORT", environment)),
                      "name": rendered.get("DB_NAME"), "password": "<PRESENT>" if env.get("DB_PASSWORD") else "<EXTERNAL>"}
        summ["redis"] = {"host": rendered.get(renderer.container_var("REDIS_HOST", environment)),
                         "port": rendered.get(renderer.container_var("REDIS_PORT", environment)),
                         "db": rendered.get(renderer.container_var("REDIS_DB", environment))}
        summ["canonical_redis"] = {"host": rendered.get("HERMES_CANDLE_CANONICAL_REDIS_HOST"),
                                   "port": rendered.get("HERMES_CANDLE_CANONICAL_REDIS_PORT"),
                                   "activation": "<PRESENT>" if env.get("HERMES_CANDLE_CANONICAL_ACTIVATION_APPROVAL") else "<ABSENT>"}
        summ["expansion_dark"] = {"master": rendered.get("HERMES_ADVANCED_V1_MASTER_ENABLED"),
                                  "mode": rendered.get("HERMES_ADVANCED_V1_PUBLISHER_MODE"),
                                  "instruments": rendered.get("INSTRUMENTS", "")[:24] + "..."}
        summ["safety"] = {"consumer": rendered.get("CONSUMER_LIVE"),
                          "backfill_execution": rendered.get("HERMES_BACKFILL_EXECUTION_ENABLED"),
                          "publisher_owner": rendered.get("HERMES_PUBLISHER_RUNTIME_OWNER")}
    summ["identity"] = {"source_sha": env.get("SOURCE_SHA"), "image_ref": red("HERMES_IMAGE_REF", env.get("HERMES_IMAGE_REF"))}
    return summ
