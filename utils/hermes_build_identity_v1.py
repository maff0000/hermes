"""
HERMES build-identity — WO-HELM-HERMES-CONTAINER-MVP-WP2-CANONICAL-BUILD-AND-EXTERNALISED-CONFIGURATION-0001

Externally-visible, secret-free build identity for the signal-service container. Values are set by the
Dockerfile (build args promoted to ENV) and read here at runtime. Fail-SOFT to explicit sentinels
(NEVER "latest") so a mis-built or bare-host image is loudly identifiable rather than silently plausible.

CONTRACT (no secrets ever appear in this dict):
  application           -> always "HERMES"
  source_sha            -> ENV SOURCE_SHA              else "UNKNOWN_SOURCE_SHA"
  build_utc             -> ENV BUILD_UTC               else "UNKNOWN_BUILD_UTC"
  image_ref             -> ENV HERMES_IMAGE_REF        else "UNKNOWN_IMAGE"
  config_version        -> ENV HERMES_CONFIG_VERSION   else live market-hours config_version else "UNKNOWN"
  build_classification  -> ENV HERMES_BUILD_CLASSIFICATION else "UNVERIFIED"
  repository            -> ENV HERMES_REPO             else "hermes"
  build_identity_valid  -> False iff CANDIDATE mode and identity fails validate_build_identity()

CANDIDATE mode == build_classification == "NON_PROMOTED_ENGINEERING_CANDIDATE": source_sha MUST be a
40-hex string; otherwise build_identity_valid=False (runtime never crashes — this is diagnostic).
"""

import os
import re

APPLICATION = "HERMES"
CANDIDATE_CLASSIFICATION = "NON_PROMOTED_ENGINEERING_CANDIDATE"

_SENTINEL_SOURCE_SHA = "UNKNOWN_SOURCE_SHA"
_SENTINEL_BUILD_UTC = "UNKNOWN_BUILD_UTC"
_SENTINEL_IMAGE = "UNKNOWN_IMAGE"
_SENTINEL_CONFIG = "UNKNOWN"
_SENTINEL_CLASSIFICATION = "UNVERIFIED"

_40HEX = re.compile(r"^[0-9a-f]{40}$")   # 40 LOWERCASE hex (governed contract; matches `git rev-parse HEAD` output)
_INVALID_SHA_SENTINELS = {"", "latest", "UNKNOWN_SOURCE_SHA", "unknown_source_sha", "none", "None"}


def _resolve_live_config_version():
    """Best-effort read of the live market-hours config_version. NEVER raises, NEVER opens a network/DB
    connection, NEVER reads a secret. Returns a string or None."""
    try:
        import json
        from pathlib import Path
        cfg_path = Path(__file__).parent.parent / "config" / "market_hours_schedule.v1.json"
        if cfg_path.exists():
            with open(cfg_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            cv = data.get("config_version")
            if cv is not None:
                return str(cv)
    except Exception:
        pass
    return None


def validate_build_identity(d) -> list:
    """Pure validator. Returns a list of reason codes (empty == valid). No secrets, no I/O.

    Reason codes:
      BI-SHA-MISSING      source_sha absent / sentinel
      BI-SHA-NOT-40-HEX   source_sha present but not a 40-hex string
      BI-SHA-IS-LATEST    source_sha == "latest"
      BI-BUILD-UTC-MISSING build_utc absent / sentinel
    """
    reasons = []
    sha = (d or {}).get("source_sha")
    if sha is None or str(sha).strip() in ("", _SENTINEL_SOURCE_SHA):
        reasons.append("BI-SHA-MISSING")
    elif str(sha).strip().lower() == "latest":
        reasons.append("BI-SHA-IS-LATEST")
    elif not _40HEX.match(str(sha).strip()):
        reasons.append("BI-SHA-NOT-40-HEX")

    bu = (d or {}).get("build_utc")
    if bu is None or str(bu).strip() in ("", _SENTINEL_BUILD_UTC):
        reasons.append("BI-BUILD-UTC-MISSING")

    return reasons


def build_identity() -> dict:
    """Assemble the secret-free build identity dict from ENV, fail-soft to explicit sentinels.

    In CANDIDATE mode an invalid source_sha yields build_identity_valid=False (never crashes)."""
    source_sha = os.getenv("SOURCE_SHA") or _SENTINEL_SOURCE_SHA
    build_utc = os.getenv("BUILD_UTC") or _SENTINEL_BUILD_UTC
    image_ref = os.getenv("HERMES_IMAGE_REF") or _SENTINEL_IMAGE
    classification = os.getenv("HERMES_BUILD_CLASSIFICATION") or _SENTINEL_CLASSIFICATION
    repository = os.getenv("HERMES_REPO") or "hermes"

    config_version = os.getenv("HERMES_CONFIG_VERSION")
    if not config_version:
        config_version = _resolve_live_config_version() or _SENTINEL_CONFIG

    d = {
        "application": APPLICATION,
        "source_sha": source_sha,
        "build_utc": build_utc,
        "image_ref": image_ref,
        "config_version": config_version,
        "build_classification": classification,
        "repository": repository,
    }

    # Diagnostic validity flag: only meaningful/enforced in CANDIDATE mode. Never crashes the app.
    if classification == CANDIDATE_CLASSIFICATION:
        reasons = validate_build_identity(d)
        d["build_identity_valid"] = (len(reasons) == 0)
        d["build_identity_reasons"] = reasons
    else:
        d["build_identity_valid"] = True
        d["build_identity_reasons"] = []

    return d
