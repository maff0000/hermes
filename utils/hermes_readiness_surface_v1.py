"""HERMES external SCOPE-AWARE readiness surface v1.
WO-HELM-HERMES-DEPLOYED-PROVENANCE-SCOPE-READINESS-AND-REPRODUCIBLE-DARK-CONFIG-0001.

Pure, read-only assembly of a deterministic, versioned readiness contract from AUTHORITATIVE runtime constituents
(build identity, live registry-loader summary, effective master/publisher-mode, pilot scope, calendar provenance,
stream/consumer/order/backfill authority state, core health). It performs NO mutation and derives publication authority
from configuration + runtime state (NOT from Redis key patterns). The overall decision may be GREEN for the authorised
DARK deployment while clearly reporting that expansion is NOT ready/active. Fail-closed on any authority-compliance
breach; distinguishes liveness / core-health / deployment-authority-compliance / expansion-readiness / trading-readiness.
"""
from __future__ import annotations
from datetime import datetime, timezone

UTC = timezone.utc
READINESS_CONTRACT_VERSION = "v1"

SEVEN_NEW = ("XAG_USD", "EUR_USD", "GBP_USD", "AUD_USD", "USD_JPY", "SPX500_USD", "WTICO_USD")
_CALENDAR_INSTRUMENTS = {"SPX500_USD": "index_cash", "WTICO_USD": "energy"}

# Overall + component readiness states.
OK = "GREEN"
AMBER = "AMBER"
RED = "RED"

# Deterministic authority-compliance fault codes (fail-closed).
F_SOURCE_MISSING = "RDY-SOURCE-IDENTITY-MISSING"
F_SOURCE_MALFORMED = "RDY-SOURCE-IDENTITY-MALFORMED"
# NOTE: an OCI-revision==source_sha MISMATCH is not runtime-derivable from build_identity (image_ref is a digest,
# not the git SHA); it is a BUILD-TIME guarantee enforced by ops/build/build_production_candidate.sh. No dead
# runtime fault code is declared for it (reconciled per WO §24).
F_REGISTRY_LOAD = "RDY-REGISTRY-LOAD-FAILED"
F_MALFORMED_CAP = "RDY-MALFORMED-CAPABILITY"
F_ACTIVE_INCOMPLETE = "RDY-ACTIVE-INCOMPLETE-ROW"
F_PILOT_INVALID = "RDY-PILOT-SCOPE-INVALID"
F_PILOT_INACTIVE = "RDY-XAU-PILOT-INACTIVE"
F_MASTER_TRUE = "RDY-EXPANSION-MASTER-TRUE-UNDER-DARK"
F_MODE_NOT_DISABLED = "RDY-PUBLISHER-MODE-NOT-DISABLED"
F_SEVEN_NEW_PUBLISHED = "RDY-SEVEN-NEW-PUBLISHED"
F_INACTIVE_PUBLISHED = "RDY-INACTIVE-ROW-PUBLISHED"
F_STREAM_COUNT = "RDY-STREAM-COUNT-NOT-ONE"
F_CALENDAR_DRIFT = "RDY-CALENDAR-APPROVAL-DRIFT"
F_CONSUMER_ON = "RDY-CONSUMER-ENABLED"
F_ORDER_PRESENT = "RDY-ORDER-PATH-PRESENT"
F_BACKFILL_ON = "RDY-BACKFILL-EXECUTION-ACTIVE"
# Observation-failure faults (fail-closed): an input that could NOT be authoritatively observed must fault, never
# silently report the safe value. Introduced by WO-...-PROVENANCE-SCOPE-READINESS-...-0001 continuation.
F_STREAM_UNKNOWN = "RDY-STREAM-STATE-UNKNOWN"
F_ORDER_UNKNOWN = "RDY-ORDER-PATH-UNKNOWN"
F_INACTIVE_UNOBSERVED = "RDY-INACTIVE-PUBLICATION-UNOBSERVED"

# Strict order-path states (the live derivation is strict; the helper still accepts a legacy boolean).
_ORDER_FAULTY_STATES = ("PRESENT", "PRESENT_ENABLED", "PRESENT_DISABLED")


def _iso(dt):
    if dt is None:
        return None
    return dt.astimezone(UTC).isoformat()


def build_readiness_report(*, now, build_identity, registry, master_enabled, publisher_mode, pilot_scope,
                           calendar_provenance, core_health, db_ok, redis_ok, stream_count,
                           consumer_live, order_path_present, backfill_execution,
                           seven_new_published_count=0, inactive_published_count=0, last_xau_tick_utc=None,
                           expected_master=False, expected_mode="DISABLED",
                           order_path_state=None, stream_unknown=False, inactive_observation_ok=True,
                           stream_health=None, configured_stream_count=None, active_stream_count=None,
                           last_stream_heartbeat_utc=None):
    """`order_path_state` (ABSENT|PRESENT_DISABLED|PRESENT_ENABLED|UNKNOWN) is the STRICT authoritative signal; when
    None it is derived from the legacy `order_path_present` bool. `stream_unknown` marks an indeterminate stream
    observation (fail-closed, never counted as the safe 1). `inactive_observation_ok=False` marks an inactive-
    publication observation that could not complete (fail readiness rather than report zero)."""
    """Assemble the readiness dict. `registry` is either an exception-free dict from registry_effective_summary + the
    typed records, or a fault marker; callers pass what they can resolve read-only. All authority state is injected so
    this stays pure and testable. `expected_*` express the authorised DARK deployment contract."""
    faults = []
    ts = _iso(now)

    # ---- identity ----
    src = (build_identity or {}).get("source_sha")
    id_valid = bool((build_identity or {}).get("build_identity_valid"))
    id_reasons = (build_identity or {}).get("build_identity_reasons", [])
    if src is None or str(src).strip() in ("", "UNKNOWN_SOURCE_SHA"):
        faults.append(F_SOURCE_MISSING)
    elif not (len(str(src)) == 40 and all(c in "0123456789abcdef" for c in str(src))):
        faults.append(F_SOURCE_MALFORMED)
    identity = {"service": "HERMES", "readiness_contract_version": READINESS_CONTRACT_VERSION,
                "source_sha": src, "image_revision": (build_identity or {}).get("image_ref"),
                "build_utc": (build_identity or {}).get("build_utc"), "build_identity_valid": id_valid,
                "build_identity_reasons": id_reasons, "evaluated_utc": ts}

    # ---- registry ----
    reg_ok = isinstance(registry, dict) and registry.get("loaded") is True
    if not reg_ok:
        faults.append(F_REGISTRY_LOAD)
    active = registry.get("active", []) if reg_ok else []
    reg_block = {
        "loaded": reg_ok, "row_count": registry.get("rows") if reg_ok else None,
        "advanced_v1_active_count": len(active), "not_enabled_count": registry.get("not_enabled") if reg_ok else None,
        "invalid_active_count": registry.get("invalid_active", 0) if reg_ok else None,
        "malformed_capability_count": registry.get("malformed_capability", 0) if reg_ok else None,
        "active_instruments": active,
    }
    if reg_ok and registry.get("invalid_active", 0):
        faults.append(F_ACTIVE_INCOMPLETE)
    if reg_ok and registry.get("malformed_capability", 0):
        faults.append(F_MALFORMED_CAP)

    # ---- pilot ----
    pilot = sorted(pilot_scope) if pilot_scope else []
    if not pilot:
        faults.append(F_PILOT_INVALID)
    xau_active = "XAU_USD" in active
    if pilot and not xau_active:
        faults.append(F_PILOT_INACTIVE)
    fam_states = registry.get("pilot_family_states", {}) if reg_ok else {}
    pilot_block = {"pilot_scope_version": (pilot_scope and "v1") or None, "pilot_instruments": pilot,
                   "xau_pilot_state": "ACTIVE_AUTHORISED" if xau_active else "INACTIVE",
                   "pilot_family_states": fam_states or {f: ("ACTIVE" if xau_active else "OFF")
                                                          for f in ("tick", "indicators", "gaps", "backfill_status", "feed_health")}}

    # ---- expansion ----
    m = bool(master_enabled)
    mode = str(publisher_mode).upper() if publisher_mode is not None else "DISABLED"
    if m is not bool(expected_master) and m is True:
        faults.append(F_MASTER_TRUE)
    if mode != str(expected_mode).upper():
        faults.append(F_MODE_NOT_DISABLED)
    seven_selected = [s for s in SEVEN_NEW if s in active]
    if seven_new_published_count:
        faults.append(F_SEVEN_NEW_PUBLISHED)
    if not inactive_observation_ok:
        faults.append(F_INACTIVE_UNOBSERVED)          # could not observe -> fail-closed, never report zero
    if inactive_published_count:
        faults.append(F_INACTIVE_PUBLISHED)
    expansion_block = {
        "expansion_master_enabled": m, "publisher_mode": mode,
        "expansion_aggregate_state": "DARK_MASTER_DISABLED" if (not m or mode == "DISABLED") else "MASTER_ENABLED",
        "seven_new_selected_count": len(seven_selected), "seven_new_published_count": seven_new_published_count,
        "inactive_row_published_count": inactive_published_count,
    }

    # ---- calendar ----
    cal = calendar_provenance or {}
    def _cstate(pkey):
        p = cal.get(pkey, {})
        return {"status": p.get("validation_status"), "production_approved": p.get("production_approved"),
                "fault_code": p.get("fault_code")}
    calendar_block = {
        "index_cash": _cstate("index_cash"), "energy": _cstate("energy"),
        "SPX500_USD": "BLOCKED_CALENDAR", "WTICO_USD": "BLOCKED_CALENDAR",
    }
    for pkey in ("index_cash", "energy"):
        if cal.get(pkey, {}).get("production_approved") is True:
            faults.append(F_CALENDAR_DRIFT)

    # ---- boundaries ----
    if _truthy(consumer_live):
        faults.append(F_CONSUMER_ON)
    # strict order-path authority: UNKNOWN (unobservable) is fail-closed; PRESENT_* is a breach; ABSENT is the only pass.
    o_state = order_path_state if order_path_state is not None else ("PRESENT" if order_path_present else "ABSENT")
    o_state = str(o_state).upper()
    if o_state == "UNKNOWN":
        faults.append(F_ORDER_UNKNOWN)
    elif o_state in _ORDER_FAULTY_STATES:
        faults.append(F_ORDER_PRESENT)
    if _truthy(backfill_execution):
        faults.append(F_BACKFILL_ON)
    boundaries = {"consumer_state": "ON" if _truthy(consumer_live) else "OFF",
                  "order_path_state": o_state,
                  "order_authority_state": "ABSENT" if o_state == "ABSENT" else o_state,
                  "backfill_execution_state": "ON" if _truthy(backfill_execution) else "OFF",
                  "autonomous_repair_state": "OFF"}

    # ---- stream / core ----
    # An indeterminate observation faults explicitly and can NEVER be read as the safe 1; a bad exact count faults too.
    if stream_unknown:
        faults.append(F_STREAM_UNKNOWN)
    elif stream_count != 1:
        faults.append(F_STREAM_COUNT)
    stream_ok = (not stream_unknown) and stream_count == 1
    core_block = {"service_health": core_health, "ingestion_health": core_health,
                  "database_connectivity": "OK" if db_ok else "FAILED",
                  "redis_connectivity": "OK" if redis_ok else "FAILED",
                  "shared_stream_state": "OK" if stream_ok else ("UNKNOWN" if stream_unknown else "DEGRADED"),
                  "stream_count": stream_count,
                  "configured_stream_count": configured_stream_count if configured_stream_count is not None else 1,
                  "active_stream_count": active_stream_count if active_stream_count is not None else stream_count,
                  "stream_health": stream_health or ("UNKNOWN" if stream_unknown else ("ACTIVE" if stream_ok else "DEGRADED")),
                  "last_stream_heartbeat_utc": last_stream_heartbeat_utc}

    # ---- overall decision ----
    authority_compliant = not faults
    core_green = (core_health == OK) and db_ok and redis_ok and stream_ok
    overall = OK if (authority_compliant and core_green) else (RED if not authority_compliant else AMBER)
    return {
        "identity": identity, "core": core_block, "registry": reg_block, "pilot": pilot_block,
        "expansion": expansion_block, "calendar": calendar_block, "boundaries": boundaries,
        "readiness": {
            "liveness": "OK",
            "core_health": OK if core_green else AMBER,
            "deployment_authority_compliance": OK if authority_compliant else RED,
            "expansion_readiness": "NOT_READY_DARK",           # never READY under this WO
            "trading_execution_readiness": "ABSENT",           # HERMES has no order authority
            "overall": overall,
            "fault_codes": faults,
        },
        "timestamps": {"evaluated_utc": ts, "last_xau_tick_utc": _iso(last_xau_tick_utc)},
    }


def _truthy(v):
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "on")
