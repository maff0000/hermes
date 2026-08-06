"""HERMES XAU-parity checker v1.
WO-HELM-HERMES-COMPLETE-TRACKED-RUNTIME-CONFIG-AND-XAU-PARITY-0001.

Deterministic comparison of the CURRENT live effective container configuration against the effective configuration the
tracked deployment bundle would produce, for every parity-critical field in the schema. Load-bearing: any parity-critical
field that is omitted, or whose value differs, is a FAILURE (not a warning). No secrets are compared — secret/authority
fields are excluded (their presence is validated by the preflight, not their value).
"""
from __future__ import annotations

from deploy.advanced_v1 import runtime_config_schema_v1 as schema
from deploy.advanced_v1.render_effective_config import container_var


def compare(live_effective, tracked_effective, environment="DEV"):
    """live_effective / tracked_effective are dicts of CONTAINER variable name -> string value.
    Returns (ok, results) where results is a list of per-field dicts. Only non-secret parity-critical fields checked."""
    results = []
    ok = True
    for name in schema.PARITY_CRITICAL:
        rec = schema.BY_NAME[name]
        if rec["secret"] or rec["authority_bearing"]:
            continue  # value not compared; presence is a preflight concern
        cvar = container_var(name, environment)
        live = live_effective.get(cvar)
        tracked = tracked_effective.get(cvar)
        if tracked is None:
            status, reason = "FAIL", "parity-critical field absent from tracked bundle"
        elif live is None:
            status, reason = "FAIL", "field absent from live effective config (fixture incomplete)"
        elif str(live) == str(tracked):
            status, reason = "OK", "match"
        else:
            status, reason = "FAIL", f"value differs live={live!r} tracked={tracked!r}"
        if status != "OK":
            ok = False
        results.append({"field": name, "container_var": cvar, "live": live, "tracked": tracked,
                        "status": status, "reason": reason})
    return ok, results


def missing_from_tracked(tracked_effective, environment="DEV"):
    """List of parity-critical non-secret container vars that the tracked bundle failed to set (fail-closed helper)."""
    out = []
    for name in schema.PARITY_CRITICAL:
        rec = schema.BY_NAME[name]
        if rec["secret"] or rec["authority_bearing"]:
            continue
        cvar = container_var(name, environment)
        if tracked_effective.get(cvar) is None:
            out.append(cvar)
    return out
