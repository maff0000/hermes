"""Governed Advanced-v1 DARK deployment configuration preflight.
WO-HELM-HERMES-DEPLOYED-PROVENANCE-SCOPE-READINESS-AND-REPRODUCIBLE-DARK-CONFIG-0001.

Validates the EFFECTIVE deployment configuration (env dict) FAIL-CLOSED against the authorised dark-state contract and
prints the safe effective states WITHOUT printing secrets. Pure + testable: pass an env mapping; get (ok, faults,
safe_summary). A lower-priority unsafe value must not silently pass — the overlay's explicit master/mode are the
effective authority, and this preflight refuses to green-light a config that would not deploy dark.
"""
from __future__ import annotations

# Non-secret control keys the preflight prints/validates. Secrets (DB_PASSWORD, tokens) are NEVER printed.
_SAFE_PRINT_KEYS = (
    "HERMES_IMAGE_REF", "HERMES_IMAGE_TAG", "HERMES_REQUIRE_DIGEST_PIN", "SOURCE_SHA",
    "HERMES_ADVANCED_V1_MASTER_ENABLED", "HERMES_ADVANCED_V1_PUBLISHER_MODE",
    "HERMES_TICK_PUBLISH_ENABLED", "HERMES_TICK_PUBLISH_AUTHORISED",
    "HERMES_INDICATOR_PUBLISH_ENABLED", "HERMES_INDICATOR_PUBLISH_AUTHORISED",
    "HERMES_GAPS_PUBLISH_ENABLED", "HERMES_GAPS_PUBLISH_AUTHORISED",
    "HERMES_BACKFILL_STATUS_PUBLISH_ENABLED", "HERMES_BACKFILL_STATUS_PUBLISH_AUTHORISED",
    "HERMES_FEED_HEALTH_PUBLISH_ENABLED", "HERMES_FEED_HEALTH_PUBLISH_AUTHORISED",
    "CONSUMER_LIVE", "HERMES_BACKFILL_EXECUTION_ENABLED",
)
_SECRET_SUBSTRINGS = ("PASSWORD", "SECRET", "TOKEN", "CREDENTIAL", "KEY")


def _norm(v):
    return str(v).strip().lower() if v is not None else None


def validate_dark_config(env, *, pilot_scope_present=True, require_digest_pin=True):
    """Return (ok: bool, faults: list[str], safe_summary: dict). Fail-closed on any unsafe/missing critical control."""
    faults = []
    g = {k: env.get(k) for k in _SAFE_PRINT_KEYS}

    master = _norm(env.get("HERMES_ADVANCED_V1_MASTER_ENABLED"))
    if master is None:
        faults.append("DARK-MASTER-ABSENT")
    elif master != "false":
        faults.append(f"DARK-MASTER-NOT-FALSE:{master}")

    mode = _norm(env.get("HERMES_ADVANCED_V1_PUBLISHER_MODE"))
    if mode is None:
        faults.append("DARK-MODE-ABSENT")
    elif mode != "disabled":
        faults.append(f"DARK-MODE-NOT-DISABLED:{mode}")

    if not pilot_scope_present:
        faults.append("DARK-PILOT-SCOPE-ABSENT")

    src = env.get("SOURCE_SHA")
    if not src or str(src).strip() in ("", "UNKNOWN_SOURCE_SHA") or not (len(str(src)) == 40 and all(c in "0123456789abcdef" for c in str(src))):
        faults.append("DARK-SOURCE-SHA-MISSING-OR-MALFORMED")

    if require_digest_pin:
        ref = env.get("HERMES_IMAGE_REF") or ""
        tag = env.get("HERMES_IMAGE_TAG") or ""
        # a governed pinned reference must exist; a bare mutable 'local'/'latest' tag with no ref is rejected
        if not ref and _norm(tag) in (None, "", "local", "latest"):
            faults.append("DARK-IMAGE-NOT-PINNED")

    if _norm(env.get("CONSUMER_LIVE")) not in ("false", None) and _norm(env.get("CONSUMER_LIVE")) != "false":
        faults.append("DARK-CONSUMER-ENABLED")
    if _norm(env.get("CONSUMER_LIVE")) is None:
        faults.append("DARK-CONSUMER-STATE-ABSENT")
    if _norm(env.get("HERMES_BACKFILL_EXECUTION_ENABLED")) not in ("false", None) and _norm(env.get("HERMES_BACKFILL_EXECUTION_ENABLED")) != "false":
        faults.append("DARK-BACKFILL-EXECUTION-ENABLED")
    # order authority / second stream must not appear
    for k in env:
        ku = k.upper()
        if "ORDER" in ku and "ENABLE" in ku and _norm(env[k]) in ("true", "1", "yes", "on"):
            faults.append(f"DARK-ORDER-AUTHORITY:{k}")
        if "SECOND_STREAM" in ku or "OANDA_STREAM_COUNT" in ku:
            if _norm(env[k]) not in ("1", None, "false"):
                faults.append(f"DARK-SECOND-STREAM:{k}")

    # safe summary NEVER includes secrets
    safe = {k: v for k, v in g.items() if not any(s in k.upper() for s in _SECRET_SUBSTRINGS)}
    return (len(faults) == 0), faults, safe


if __name__ == "__main__":   # pragma: no cover
    import os, json, sys
    ok, faults, safe = validate_dark_config(dict(os.environ))
    print(json.dumps({"dark_config_ok": ok, "faults": faults, "effective_safe_controls": safe}, indent=2))
    sys.exit(0 if ok else 4)
