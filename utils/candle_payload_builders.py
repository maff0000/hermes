"""HERMES inert Redis-ready payload builders.
WO-HELM-HERMES-CANDLE-H4-M30-FORWARD-DERIVATION-AND-FEATURES-0001.

Pure builders for the future HERMES market-truth Redis contracts. INERT: they return dicts
only — NO Redis writes, NO publisher activation, NO host/port/db, no I/O. Every payload uses the
HERMES universal envelope. HERMES owns hermes:* only — NO falcon:*/solo:*/neo:*/matt:* keys and
no Falcon-shaped fields (regime/compression/break/support-resistance). ALL timestamps UTC.

Doctrine: complete != fresh. complete=0 candles are never served as complete (Approach A).
H4 candle_context separates active (FORMING) from last-closed (COMPLETE only if source complete).
Indicators require COMPLETE_ONLY source; deterministic EMA comparison state only — no trend/regime.
"""
from __future__ import annotations

SCHEMA_VERSION = "v1"
SERVICE = "HERMES"
H4_ANCHOR = {"anchor_type": "UTC", "anchor_status": "RATIFIED_FOR_HERMES_V1",
             "anchor_schedule_utc": ["00:00", "04:00", "08:00", "12:00", "16:00", "20:00"],
             "not_session_interpretive": True}
_FORBIDDEN_KEY_PREFIXES = ("falcon:", "solo:", "neo:", "matt:")
_FORBIDDEN_FIELD_TOKENS = ("regime", "compression", "break_quality", "support", "resistance",
                           "confidence", "signal", "setup", "permission")


def envelope(domain, key, *, generated_at_utc, valid_until_utc, ttl_seconds,
             freshness_state, status, reason_codes, provenance, data):
    """HERMES universal envelope. Asserts HERMES-owned key + no Falcon-shaped tokens."""
    if any(key.startswith(p) for p in _FORBIDDEN_KEY_PREFIXES):
        raise ValueError(f"GOV-PAY-001: forbidden non-HERMES key {key} (fail-loud)")
    if not key.startswith("hermes:"):
        raise ValueError(f"GOV-PAY-002: HERMES payload key must be hermes:* (got {key}) (fail-loud)")
    return {"schema_version": SCHEMA_VERSION, "service": SERVICE, "domain": domain, "key": key,
            "generated_at_utc": generated_at_utc, "valid_until_utc": valid_until_utc,
            "ttl_seconds": ttl_seconds, "freshness_state": freshness_state, "status": status,
            "reason_codes": list(reason_codes), "provenance": dict(provenance), "data": data}


def assert_no_forbidden_fields(payload):
    """Recursively assert no Falcon/strategy-shaped field tokens leak into a HERMES payload."""
    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                kl = str(k).lower()
                for tok in _FORBIDDEN_FIELD_TOKENS:
                    if tok in kl:
                        raise ValueError(f"GOV-PAY-003: forbidden field token '{tok}' in '{k}' (fail-loud)")
                walk(v)
        elif isinstance(o, list):
            for x in o:
                walk(x)
    walk(payload)
    return True


def build_candle_features_latest(*, instrument, timeframe, latest_complete_feature,
                                 latest_closed_feature, generated_at_utc, valid_until_utc,
                                 ttl_seconds):
    """hermes:candle_features:latest:v1 — Approach A: serve the latest COMPLETE candle. If the most
    recent CLOSED bucket is incomplete, flag latest_closed_incomplete=true + status=DEGRADED.
    Never publish an incomplete candle as complete."""
    key = f"hermes:candle_features:latest:v1:{instrument}:{timeframe}"
    status, reason = "OK", []
    if latest_complete_feature is None:
        status, reason = "UNAVAILABLE", ["NO_COMPLETE_CANDLE_AVAILABLE"]
        data = {"instrument": instrument, "timeframe": timeframe, "feature": None,
                "latest_closed_incomplete": None}
        fresh = "UNAVAILABLE"
    else:
        if latest_complete_feature.get("complete") is not True:
            raise ValueError("GOV-PAY-004: latest_complete_feature must be complete=true (fail-loud)")
        latest_closed_incomplete = bool(latest_closed_feature is not None
                                        and latest_closed_feature.get("complete") is not True)
        if latest_closed_incomplete:
            status, reason = "DEGRADED", ["LATEST_CLOSED_INCOMPLETE", "SOURCE_CANDLE_INCOMPLETE"]
        data = {"instrument": instrument, "timeframe": timeframe,
                "feature": latest_complete_feature, "latest_closed_incomplete": latest_closed_incomplete,
                "latest_closed_close_utc": (latest_closed_feature or {}).get("source_close_utc")}
        fresh = latest_complete_feature.get("freshness_state", "UNAVAILABLE")
    payload = envelope("candle_features", key, generated_at_utc=generated_at_utc,
                       valid_until_utc=valid_until_utc, ttl_seconds=ttl_seconds, freshness_state=fresh,
                       status=status, reason_codes=reason,
                       provenance={"source": "HERMES_SQL", "instrument": instrument, "timeframe": timeframe},
                       data=data)
    assert_no_forbidden_fields(payload)
    return payload


def _h4_sub(candle_feature, forming):
    """active vs last-closed H4 sub-object. active is always FORMING; last-closed COMPLETE only if
    source complete, else INCOMPLETE."""
    if candle_feature is None:
        return {"candle_id": None, "complete": False, "complete_state": "UNAVAILABLE",
                "reason_codes": ["SOURCE_CANDLE_UNAVAILABLE"]}
    if forming:
        return {"candle_id": None, "open_utc": candle_feature.get("source_open_utc"),
                "close_utc": candle_feature.get("source_close_utc"), "complete": False,
                "complete_state": "FORMING", "reason_codes": ["SOURCE_CANDLE_FORMING"],
                "provisional_ohlc": {k: candle_feature.get(k) for k in ("open", "high", "low", "close")}}
    complete = candle_feature.get("complete") is True
    return {"candle_id": candle_feature.get("candle_id"),
            "open_utc": candle_feature.get("source_open_utc"),
            "close_utc": candle_feature.get("source_close_utc"),
            "complete": complete, "complete_state": "COMPLETE" if complete else "INCOMPLETE",
            "reason_codes": [] if complete else ["SOURCE_CANDLE_INCOMPLETE"],
            "ohlc": {k: candle_feature.get(k) for k in ("open", "high", "low", "close")},
            "wick_body": {k: candle_feature.get(k) for k in
                          ("upper_wick_size", "lower_wick_size", "body_size", "total_range",
                           "candle_direction", "close_position_in_range")}}


def build_candle_context_current(*, instrument, active_h4_feature, last_closed_h4_feature,
                                 generated_at_utc, valid_until_utc, ttl_seconds):
    """hermes:candle_context:current:v1 — active_h4 (FORMING) + last_closed_h4 (COMPLETE only if
    candles_H4.complete=1; else INCOMPLETE + status DEGRADED). Explicit H4 UTC anchor metadata."""
    key = f"hermes:candle_context:current:v1:{instrument}"
    last_closed = _h4_sub(last_closed_h4_feature, forming=False)
    status, reason, fresh = "OK", [], "FRESH"
    if last_closed["complete_state"] != "COMPLETE":
        status = "DEGRADED" if last_closed["complete_state"] == "INCOMPLETE" else "UNAVAILABLE"
        reason = ["H4_LAST_CLOSED_" + last_closed["complete_state"]]
    if last_closed_h4_feature is not None:
        fresh = last_closed_h4_feature.get("freshness_state", "UNAVAILABLE")
    data = {"instrument": instrument, "timeframe": "H4", "anchor": H4_ANCHOR,
            "active_h4_candle": _h4_sub(active_h4_feature, forming=True),
            "last_closed_h4_candle": last_closed}
    payload = envelope("candle_context", key, generated_at_utc=generated_at_utc,
                       valid_until_utc=valid_until_utc, ttl_seconds=ttl_seconds, freshness_state=fresh,
                       status=status, reason_codes=reason,
                       provenance={"source": "HERMES_SQL", "instrument": instrument, "timeframe": "H4"},
                       data=data)
    assert_no_forbidden_fields(payload)
    return payload


_EMA_STATES = ("ABOVE", "BELOW", "CROSS_UP", "CROSS_DOWN")


def build_indicators_latest(*, instrument, timeframe, source_complete, indicators,
                            generated_at_utc, valid_until_utc, ttl_seconds):
    """hermes:indicators:latest:v1 — INERT scaffold. source_complete_policy=COMPLETE_ONLY: if the
    source candle is not complete, the payload degrades and emits no computed indicators.
    Deterministic EMA comparison state only (ABOVE/BELOW/CROSS_UP/CROSS_DOWN) — NO trend/regime."""
    key = f"hermes:indicators:latest:v1:{instrument}:{timeframe}"
    for ind in (indicators or {}).values():
        st = ind.get("ema_state") if isinstance(ind, dict) else None
        if st is not None and st not in _EMA_STATES:
            raise ValueError(f"GOV-PAY-005: invalid ema_state {st} (allowed {_EMA_STATES}) (fail-loud)")
    if not source_complete:
        status, reason, data = "DEGRADED", ["SOURCE_CANDLE_INCOMPLETE", "INDICATORS_REQUIRE_COMPLETE_SOURCE"], \
            {"instrument": instrument, "timeframe": timeframe, "indicators": None,
             "source_complete_policy": "COMPLETE_ONLY"}
        fresh = "DEGRADED"
    else:
        status, reason = "OK", []
        data = {"instrument": instrument, "timeframe": timeframe, "indicators": indicators or {},
                "source_complete_policy": "COMPLETE_ONLY"}
        fresh = "FRESH"
    payload = envelope("indicators", key, generated_at_utc=generated_at_utc,
                       valid_until_utc=valid_until_utc, ttl_seconds=ttl_seconds, freshness_state=fresh,
                       status=status, reason_codes=reason,
                       provenance={"source": "HERMES_SQL", "instrument": instrument, "timeframe": timeframe,
                                   "source_timeframe": timeframe},
                       data=data)
    assert_no_forbidden_fields(payload)
    return payload
