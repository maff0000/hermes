"""HERMES effective-configuration RENDERER v1 (pure; no Docker required).
WO-HELM-HERMES-COMPLETE-TRACKED-RUNTIME-CONFIG-AND-XAU-PARITY-0001.

Resolves the exact effective container environment that a given compose-file sequence + governed env file would produce
for the `hermes-signal` service, using compose interpolation + environment-block precedence (later -f overrides earlier).
Pure Python (parses YAML, interpolates), so tests exercise real rendering infra-free; the isolated rehearsal additionally
cross-checks against `docker compose config`.

NEVER prints secret values — `safe_report()` redacts anything the schema marks secret/authority_bearing.
"""
from __future__ import annotations
import os
import re

import yaml

from deploy.advanced_v1 import runtime_config_schema_v1 as schema

_INTERP = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(:?[-+?])?([^}]*)\}")


class RenderError(Exception):
    pass


def interpolate(value, env):
    """Resolve ${VAR}, ${VAR:-default}, ${VAR:+alt}, ${VAR:?err}, ${VAR-default} against env (a dict)."""
    if not isinstance(value, str):
        value = str(value)

    def repl(m):
        name, op, arg = m.group(1), m.group(2), m.group(3)
        present = name in env and env[name] != ""
        raw_present = name in env
        val = env.get(name, "")
        if op in (":-", "-"):
            if (op == ":-" and not present) or (op == "-" and not raw_present):
                return arg
            return val
        if op in (":+", "+"):
            if (op == ":+" and present) or (op == "+" and raw_present):
                return arg
            return ""
        if op in (":?", "?"):
            if (op == ":?" and not present) or (op == "?" and not raw_present):
                raise RenderError(f"required variable {name} is unset: {arg or 'missing'}")
            return val
        # bare ${VAR}
        return val

    prev = None
    out = value
    for _ in range(6):  # bounded nested interpolation
        out2 = _INTERP.sub(repl, out)
        if out2 == out:
            break
        out = out2
    return out


def _service_env_block(doc, service):
    svc = (doc.get("services") or {}).get(service, {}) if isinstance(doc, dict) else {}
    env = svc.get("environment", {})
    if isinstance(env, list):  # "KEY=VALUE" form
        out = {}
        for item in env:
            if "=" in str(item):
                k, v = str(item).split("=", 1)
                out[k] = v
        return out
    return dict(env or {})


def render_effective(compose_paths, env, service="hermes-signal"):
    """Merge the `environment:` blocks of `service` across compose_paths (in order; later overrides earlier), then
    interpolate every value against `env`. Returns the resolved container-env dict. env_file contents (secrets) are
    intentionally out of scope here — this renderer proves the NON-secret contract the overlays inject."""
    merged = {}
    for p in compose_paths:
        try:
            with open(p, "r", encoding="utf-8") as fh:
                doc = yaml.safe_load(fh)
        except OSError as e:
            raise RenderError(f"compose file unreadable: {p} ({e})")
        merged.update(_service_env_block(doc, service))
    resolved = {}
    for k, raw in merged.items():
        resolved[k] = interpolate(raw, env)
    return resolved


def container_var(field_name, environment="DEV"):
    """The variable name the app actually consumes for a canonical field. Under ENVIRONMENT=DEV the app reads the
    DEV_* alias for host/routing fields; otherwise the canonical name."""
    rec = schema.BY_NAME.get(field_name, {})
    for alias in rec.get("deprecated_aliases", []):
        if alias.startswith(environment + "_"):
            return alias
    return field_name


def safe_report(compose_paths, env, service="hermes-signal", environment="DEV"):
    """A secret-redacted effective-config report driven by the schema."""
    rendered = render_effective(compose_paths, env, service)
    rows = []
    secret_or_auth = set(schema.SECRET_FIELDS) | set(schema.AUTHORITY_FIELDS)
    # also redact by consumed alias
    redact_names = set(secret_or_auth) | {container_var(n, environment) for n in secret_or_auth}
    for name in sorted(rendered):
        v = rendered[name]
        display = "<REDACTED>" if (name in redact_names or re.search(r"(PASSWORD|SECRET|TOKEN|API_KEY|CREDENTIAL|WEBHOOK|ACCOUNT_ID|ACTIVATION_APPROVAL)", name, re.I)) else v
        rows.append({"name": name, "value": display,
                     "secret_present": bool(v) if display == "<REDACTED>" else None})
    return {"service": service, "environment": environment, "fields": rows}
