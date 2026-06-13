"""HERMES deterministic candle-feature calculation.
WO-HELM-HERMES-CANDLE-H4-M30-FORWARD-DERIVATION-AND-FEATURES-0001.

Pure, deterministic market FACTS derived from a single candle (+ optional previous candle and
local neighbour context). HERMES owns market truth ONLY. These are facts, NOT strategy:
no buy/sell/setup/permission/confidence/signal; no trend/regime; no session interpretation.

Zero-range candles are handled safely (ratios -> None, classified DOJI). Incomplete/forming
candles still get geometric facts, but completeness/freshness metadata marks them honestly so
no consumer treats an incomplete candle as complete. ALL timestamps UTC.
"""
from __future__ import annotations

SCHEMA_VERSION = "v1"
# documented classification thresholds (tunable; NOT hidden config — geometric constants)
DOJI_BODY_TO_RANGE_MAX = 0.10
FULL_BODY_MIN = 0.80
LONG_WICK_RATIO_MIN = 0.50
PIN_BAR_WICK_TO_BODY_MIN = 2.0
PIN_BAR_BODY_TO_RANGE_MAX = 0.35

COMPLETE, INCOMPLETE, FORMING, UNAVAILABLE = "COMPLETE", "INCOMPLETE", "FORMING", "UNAVAILABLE"


def _f(x):
    return float(x)


def _ratio(num, den):
    return None if den == 0 else round(num / den, 6)


def candle_geometry(open_, high, low, close):
    """Pure geometric facts from O/H/L/C. Fail loud on malformed (None / high<low)."""
    if None in (open_, high, low, close):
        raise ValueError("GOV-FEAT-001: malformed candle (None OHLC) (fail-loud)")
    o, h, l, c = _f(open_), _f(high), _f(low), _f(close)
    if h < l:
        raise ValueError(f"GOV-FEAT-002: high<low ({h}<{l}) malformed candle (fail-loud)")
    body_high, body_low = max(o, c), min(o, c)
    total_range = h - l
    body_size = abs(c - o)
    upper_wick = h - body_high
    lower_wick = body_low - l
    direction = "DOJI"
    if _ratio(body_size, total_range) is not None and (body_size / total_range) >= DOJI_BODY_TO_RANGE_MAX:
        direction = "BULLISH" if c > o else ("BEARISH" if c < o else "DOJI")
    body_to_range = _ratio(body_size, total_range)
    upper_ratio = _ratio(upper_wick, total_range)
    lower_ratio = _ratio(lower_wick, total_range)
    return {
        "open": o, "high": h, "low": l, "close": c,
        "candle_high": h, "candle_low": l, "wick_high": h, "wick_low": l,
        "body_high": body_high, "body_low": body_low,
        "total_range": round(total_range, 6), "body_size": round(body_size, 6),
        "upper_wick_size": round(upper_wick, 6), "lower_wick_size": round(lower_wick, 6),
        "upper_wick_ratio": upper_ratio, "lower_wick_ratio": lower_ratio,
        "body_to_range_ratio": body_to_range,
        "close_position_in_range": _ratio(c - l, total_range),
        "open_position_in_range": _ratio(o - l, total_range),
        "candle_direction": direction,
        "is_bullish": direction == "BULLISH", "is_bearish": direction == "BEARISH",
        "is_doji": direction == "DOJI",
        "is_full_body": (body_to_range is not None and body_to_range >= FULL_BODY_MIN),
        "is_long_upper_wick": (upper_ratio is not None and upper_ratio >= LONG_WICK_RATIO_MIN),
        "is_long_lower_wick": (lower_ratio is not None and lower_ratio >= LONG_WICK_RATIO_MIN),
        "is_pin_bar_candidate": _is_pin_bar(body_size, total_range, upper_wick, lower_wick),
    }


def _is_pin_bar(body_size, total_range, upper_wick, lower_wick):
    if total_range == 0 or body_size == 0:
        return False
    if (body_size / total_range) > PIN_BAR_BODY_TO_RANGE_MAX:
        return False
    dom = max(upper_wick, lower_wick)
    return (dom / body_size) >= PIN_BAR_WICK_TO_BODY_MIN


def previous_candle_facts(geo, prev):
    """Deterministic facts relative to the previous candle. prev = geometry dict or None."""
    if prev is None:
        return {"previous_candle_id": None, "previous_high": None, "previous_low": None,
                "previous_open": None, "previous_close": None, "previous_body_high": None,
                "previous_body_low": None, "breaks_previous_high": None, "breaks_previous_low": None,
                "closes_above_previous_high": None, "closes_below_previous_low": None,
                "closes_inside_previous_range": None, "is_inside_bar_candidate": None,
                "is_outside_bar_candidate": None}
    ph, pl = prev["high"], prev["low"]
    return {
        "previous_candle_id": prev.get("candle_id"),
        "previous_high": ph, "previous_low": pl,
        "previous_open": prev["open"], "previous_close": prev["close"],
        "previous_body_high": prev["body_high"], "previous_body_low": prev["body_low"],
        "breaks_previous_high": geo["high"] > ph,
        "breaks_previous_low": geo["low"] < pl,
        "closes_above_previous_high": geo["close"] > ph,
        "closes_below_previous_low": geo["close"] < pl,
        "closes_inside_previous_range": pl <= geo["close"] <= ph,
        "is_inside_bar_candidate": geo["high"] <= ph and geo["low"] >= pl,
        "is_outside_bar_candidate": geo["high"] > ph and geo["low"] < pl,
    }


def swing_facts(geo, left_highs, left_lows, right_highs, right_lows, swing_lookback):
    """Deterministic local swing-high/low CANDIDATE facts. NOT a strategy signal.
    CONFIRMED when both sides have `swing_lookback` neighbours; CANDIDATE when only the left is
    available (right not yet formed); UNAVAILABLE when insufficient left context."""
    left_ok = len(left_highs) >= swing_lookback and len(left_lows) >= swing_lookback
    right_ok = len(right_highs) >= swing_lookback and len(right_lows) >= swing_lookback
    if not left_ok:
        status = UNAVAILABLE
    elif right_ok:
        status = "CONFIRMED"
    else:
        status = "CANDIDATE"
    is_high = is_low = None
    if left_ok:
        hi = geo["high"] > max(left_highs[:swing_lookback] + (right_highs[:swing_lookback] if right_ok else []))
        lo = geo["low"] < min(left_lows[:swing_lookback] + (right_lows[:swing_lookback] if right_ok else []))
        is_high, is_low = hi, lo
    return {
        "is_local_swing_high_candidate": is_high,
        "is_local_swing_low_candidate": is_low,
        "swing_lookback": swing_lookback,
        "swing_left_highs_lows_available": left_ok,
        "swing_right_highs_lows_available": right_ok,
        "swing_status": status,
    }


def completeness_block(complete_state, expected, actual):
    """complete / complete_state / source counts / freshness placeholder / status / reason_codes.
    Fail-closed: only COMPLETE yields complete=true. status derives from completeness."""
    if complete_state not in (COMPLETE, INCOMPLETE, FORMING, UNAVAILABLE):
        raise ValueError(f"GOV-FEAT-003: unknown complete_state {complete_state} (fail-loud)")
    complete = complete_state == COMPLETE
    reason, status = [], "OK"
    if complete_state == INCOMPLETE:
        reason, status = ["SOURCE_CANDLE_INCOMPLETE"], "DEGRADED" if False else "WARN"
    elif complete_state == FORMING:
        reason, status = ["SOURCE_CANDLE_FORMING"], "WARN"
    elif complete_state == UNAVAILABLE:
        reason, status = ["SOURCE_CANDLE_UNAVAILABLE"], "UNAVAILABLE"
    return {
        "complete": complete, "complete_state": complete_state,
        "source_complete": complete, "source_complete_policy": "COMPLETE_ONLY",
        "expected_source_candle_count": expected, "actual_source_candle_count": actual,
        "missing_source_candle_count": max(0, (expected or 0) - (actual or 0)),
        "status": status, "reason_codes": reason,
    }


def anchor_block(timeframe):
    if timeframe == "H4":
        return {"anchor_type": "UTC", "anchor_status": "RATIFIED_FOR_HERMES_V1",
                "anchor_schedule_utc": ["00:00", "04:00", "08:00", "12:00", "16:00", "20:00"],
                "not_session_interpretive": True}
    return {"anchor_type": "UTC", "anchor_status": "RATIFIED_FOR_HERMES_V1",
            "not_session_interpretive": True}


def build_candle_feature(*, instrument, timeframe, candle_id, source_table, source_candle_id,
                         source_open_utc, source_close_utc, generated_at_utc,
                         open_, high, low, close, complete_state, expected, actual,
                         freshness_state, prev_geo=None, swing=None):
    """Full deterministic candle-feature record (market facts only)."""
    geo = candle_geometry(open_, high, low, close)
    feat = {
        "schema_version": SCHEMA_VERSION, "service": "HERMES", "domain": "candle_features",
        "instrument": instrument, "timeframe": timeframe, "candle_id": candle_id,
        "source_table": source_table, "source_timeframe": timeframe, "source_candle_id": source_candle_id,
        "source_open_utc": source_open_utc, "source_close_utc": source_close_utc,
        "generated_at_utc": generated_at_utc, "freshness_state": freshness_state,
    }
    feat.update(geo)
    feat.update(previous_candle_facts(geo, prev_geo))
    feat.update(swing or {"is_local_swing_high_candidate": None, "is_local_swing_low_candidate": None,
                          "swing_lookback": None, "swing_left_highs_lows_available": False,
                          "swing_right_highs_lows_available": False, "swing_status": UNAVAILABLE})
    feat.update(completeness_block(complete_state, expected, actual))
    feat.update(anchor_block(timeframe))
    return feat
