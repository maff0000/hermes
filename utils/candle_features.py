"""HERMES deterministic candle-feature calculation.
WO-HELM-HERMES-CANDLE-H4-M30-FORWARD-DERIVATION-AND-FEATURES-0001.

Pure, deterministic market FACTS from a single candle (+ optional previous candle and local
neighbour context). HERMES owns market truth ONLY. Facts, NOT strategy: no buy/sell/setup/
permission/confidence/signal; no trend/regime; no session interpretation.

GOVERNED CLASSIFICATION (R2D2 B-CONST): classification thresholds (doji/full-body/long-wick/
pin-bar/swing_lookback) are NOT module constants — they are governed config supplied as an
explicit `CandleFeatureConfig` (loaded from hermes_candle_feature_config via load_config(),
fail-loud). There is NO module-constant fallback on the live calculation path; geometry is
pure, classification REQUIRES config. Config provenance is stamped into every feature.

Zero-range candles handled safely (ratios -> None, classified DOJI). Incomplete/forming candles
still get geometry; completeness/freshness metadata marks them honestly. ALL timestamps UTC.
"""
from __future__ import annotations
import json
from dataclasses import dataclass, field

SCHEMA_VERSION = "v1"
CLASSIFICATION_CONFIG_KEY = "candle_feature_classification:v1"
COMPLETE, INCOMPLETE, FORMING, UNAVAILABLE = "COMPLETE", "INCOMPLETE", "FORMING", "UNAVAILABLE"
_RATIO_FIELDS = ("doji_body_to_range_max", "full_body_min_body_to_range",
                 "long_wick_ratio_min", "pin_bar_body_to_range_max")


@dataclass(frozen=True)
class CandleFeatureConfig:
    """Governed classification thresholds. Explicit; no hidden defaults. Validate() fail-loud."""
    doji_body_to_range_max: float
    full_body_min_body_to_range: float
    long_wick_ratio_min: float
    pin_bar_wick_to_body_min: float
    pin_bar_body_to_range_max: float
    swing_lookback: int
    config_key: str = CLASSIFICATION_CONFIG_KEY
    config_id: int = None
    config_version: str = "v1"

    def validate(self):
        for name in _RATIO_FIELDS:
            v = getattr(self, name)
            if v is None or not (0.0 <= float(v) <= 1.0):
                raise ValueError(f"GOV-FEAT-CFG-003: {name}={v} out of range [0,1] (fail-loud)")
        if self.pin_bar_wick_to_body_min is None or not (0.0 < float(self.pin_bar_wick_to_body_min) <= 100.0):
            raise ValueError(f"GOV-FEAT-CFG-003: pin_bar_wick_to_body_min={self.pin_bar_wick_to_body_min} "
                             "out of range (0,100] (fail-loud)")
        if not isinstance(self.swing_lookback, int) or not (1 <= self.swing_lookback <= 500):
            raise ValueError(f"GOV-FEAT-CFG-004: swing_lookback={self.swing_lookback} invalid "
                             "(int in [1,500]) (fail-loud)")
        return self

    def provenance(self):
        return {"config_key": self.config_key, "config_id": self.config_id,
                "config_version": self.config_version}


_REQUIRED_KEYS = ("doji_body_to_range_max", "full_body_min_body_to_range", "long_wick_ratio_min",
                  "pin_bar_wick_to_body_min", "pin_bar_body_to_range_max", "swing_lookback")


def config_from_json(raw, config_key=CLASSIFICATION_CONFIG_KEY, config_id=None):
    """Build + validate a CandleFeatureConfig from a JSON string/dict. Fail-loud on malformed."""
    try:
        d = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except Exception as e:
        raise ValueError(f"GOV-FEAT-CFG-002: malformed classification config json ({e}) (fail-loud)")
    missing = [k for k in _REQUIRED_KEYS if k not in d]
    if missing:
        raise ValueError(f"GOV-FEAT-CFG-002: classification config missing keys {missing} (fail-loud)")
    cfg = CandleFeatureConfig(
        doji_body_to_range_max=float(d["doji_body_to_range_max"]),
        full_body_min_body_to_range=float(d["full_body_min_body_to_range"]),
        long_wick_ratio_min=float(d["long_wick_ratio_min"]),
        pin_bar_wick_to_body_min=float(d["pin_bar_wick_to_body_min"]),
        pin_bar_body_to_range_max=float(d["pin_bar_body_to_range_max"]),
        swing_lookback=int(d["swing_lookback"]),
        config_key=config_key, config_id=config_id)
    return cfg.validate()


def load_config(get_conn, config_key=CLASSIFICATION_CONFIG_KEY):
    """Load the governed classification config from hermes_candle_feature_config (enabled row).
    Fail loud if missing/disabled. No module-constant fallback."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, config_value_json FROM hermes_candle_feature_config "
                        "WHERE config_key=%s AND enabled=1", (config_key,))
            row = cur.fetchone()
    if not row:
        raise ValueError(f"GOV-FEAT-CFG-001: no enabled classification config for {config_key} "
                         "(no hidden default) (fail-loud)")
    return config_from_json(row[1], config_key=config_key, config_id=row[0])


def _f(x):
    return float(x)


def _ratio(num, den):
    return None if den == 0 else round(num / den, 6)


def candle_geometry(open_, high, low, close):
    """PURE geometric facts (config-free). Fail loud on malformed (None / high<low)."""
    if None in (open_, high, low, close):
        raise ValueError("GOV-FEAT-001: malformed candle (None OHLC) (fail-loud)")
    o, h, l, c = _f(open_), _f(high), _f(low), _f(close)
    if h < l:
        raise ValueError(f"GOV-FEAT-002: high<low ({h}<{l}) malformed candle (fail-loud)")
    body_high, body_low = max(o, c), min(o, c)
    total_range, body_size = h - l, abs(c - o)
    upper_wick, lower_wick = h - body_high, body_low - l
    return {
        "open": o, "high": h, "low": l, "close": c,
        "candle_high": h, "candle_low": l, "wick_high": h, "wick_low": l,
        "body_high": body_high, "body_low": body_low,
        "total_range": round(total_range, 6), "body_size": round(body_size, 6),
        "upper_wick_size": round(upper_wick, 6), "lower_wick_size": round(lower_wick, 6),
        "upper_wick_ratio": _ratio(upper_wick, total_range),
        "lower_wick_ratio": _ratio(lower_wick, total_range),
        "body_to_range_ratio": _ratio(body_size, total_range),
        "close_position_in_range": _ratio(c - l, total_range),
        "open_position_in_range": _ratio(o - l, total_range),
    }


def classify(geo, config):
    """Config-GOVERNED classification. config REQUIRED (no module-constant fallback)."""
    if config is None:
        raise ValueError("GOV-FEAT-CFG-001: classification config required (no hidden default) (fail-loud)")
    config.validate()
    o, c = geo["open"], geo["close"]
    body_to_range = geo["body_to_range_ratio"]
    upper_r, lower_r = geo["upper_wick_ratio"], geo["lower_wick_ratio"]
    if body_to_range is not None and body_to_range >= config.doji_body_to_range_max:
        direction = "BULLISH" if c > o else ("BEARISH" if c < o else "DOJI")
    else:
        direction = "DOJI"
    return {
        "candle_direction": direction,
        "is_bullish": direction == "BULLISH", "is_bearish": direction == "BEARISH",
        "is_doji": direction == "DOJI",
        "is_full_body": (body_to_range is not None and body_to_range >= config.full_body_min_body_to_range),
        "is_long_upper_wick": (upper_r is not None and upper_r >= config.long_wick_ratio_min),
        "is_long_lower_wick": (lower_r is not None and lower_r >= config.long_wick_ratio_min),
        "is_pin_bar_candidate": _is_pin_bar(geo["body_size"], geo["total_range"],
                                            geo["upper_wick_size"], geo["lower_wick_size"], config),
        "classification_config": config.provenance(),
    }


def _is_pin_bar(body_size, total_range, upper_wick, lower_wick, config):
    if total_range == 0 or body_size == 0:
        return False
    if (body_size / total_range) > config.pin_bar_body_to_range_max:
        return False
    return (max(upper_wick, lower_wick) / body_size) >= config.pin_bar_wick_to_body_min


def previous_candle_facts(geo, prev):
    if prev is None:
        return {k: None for k in (
            "previous_candle_id", "previous_high", "previous_low", "previous_open", "previous_close",
            "previous_body_high", "previous_body_low", "breaks_previous_high", "breaks_previous_low",
            "closes_above_previous_high", "closes_below_previous_low", "closes_inside_previous_range",
            "is_inside_bar_candidate", "is_outside_bar_candidate")}
    ph, pl = prev["high"], prev["low"]
    return {
        "previous_candle_id": prev.get("candle_id"),
        "previous_high": ph, "previous_low": pl, "previous_open": prev["open"], "previous_close": prev["close"],
        "previous_body_high": prev["body_high"], "previous_body_low": prev["body_low"],
        "breaks_previous_high": geo["high"] > ph, "breaks_previous_low": geo["low"] < pl,
        "closes_above_previous_high": geo["close"] > ph, "closes_below_previous_low": geo["close"] < pl,
        "closes_inside_previous_range": pl <= geo["close"] <= ph,
        "is_inside_bar_candidate": geo["high"] <= ph and geo["low"] >= pl,
        "is_outside_bar_candidate": geo["high"] > ph and geo["low"] < pl,
    }


def swing_facts(geo, left_highs, left_lows, right_highs, right_lows, swing_lookback):
    """Deterministic local swing-high/low CANDIDATE facts (NOT a strategy signal).
    swing_lookback is governed (from CandleFeatureConfig)."""
    if not isinstance(swing_lookback, int) or swing_lookback < 1:
        raise ValueError(f"GOV-FEAT-CFG-004: swing_lookback={swing_lookback} invalid (fail-loud)")
    left_ok = len(left_highs) >= swing_lookback and len(left_lows) >= swing_lookback
    right_ok = len(right_highs) >= swing_lookback and len(right_lows) >= swing_lookback
    status = UNAVAILABLE if not left_ok else ("CONFIRMED" if right_ok else "CANDIDATE")
    is_high = is_low = None
    if left_ok:
        is_high = geo["high"] > max(left_highs[:swing_lookback] + (right_highs[:swing_lookback] if right_ok else []))
        is_low = geo["low"] < min(left_lows[:swing_lookback] + (right_lows[:swing_lookback] if right_ok else []))
    return {"is_local_swing_high_candidate": is_high, "is_local_swing_low_candidate": is_low,
            "swing_lookback": swing_lookback, "swing_left_highs_lows_available": left_ok,
            "swing_right_highs_lows_available": right_ok, "swing_status": status}


def completeness_block(complete_state, expected, actual):
    if complete_state not in (COMPLETE, INCOMPLETE, FORMING, UNAVAILABLE):
        raise ValueError(f"GOV-FEAT-003: unknown complete_state {complete_state} (fail-loud)")
    complete = complete_state == COMPLETE
    reason, status = [], "OK"
    if complete_state == INCOMPLETE:
        reason, status = ["SOURCE_CANDLE_INCOMPLETE"], "WARN"
    elif complete_state == FORMING:
        reason, status = ["SOURCE_CANDLE_FORMING"], "WARN"
    elif complete_state == UNAVAILABLE:
        reason, status = ["SOURCE_CANDLE_UNAVAILABLE"], "UNAVAILABLE"
    return {"complete": complete, "complete_state": complete_state,
            "source_complete": complete, "source_complete_policy": "COMPLETE_ONLY",
            "expected_source_candle_count": expected, "actual_source_candle_count": actual,
            "missing_source_candle_count": max(0, (expected or 0) - (actual or 0)),
            "status": status, "reason_codes": reason}


def anchor_block(timeframe):
    base = {"anchor_type": "UTC", "anchor_status": "RATIFIED_FOR_HERMES_V1", "not_session_interpretive": True}
    if timeframe == "H4":
        base["anchor_schedule_utc"] = ["00:00", "04:00", "08:00", "12:00", "16:00", "20:00"]
    return base


def build_candle_feature(*, instrument, timeframe, candle_id, source_table, source_candle_id,
                         source_open_utc, source_close_utc, generated_at_utc,
                         open_, high, low, close, complete_state, expected, actual,
                         freshness_state, config, prev_geo=None, swing_neighbours=None):
    """Full deterministic candle-feature record. `config` (CandleFeatureConfig) REQUIRED — the live
    calculation path uses NO module-constant fallback. swing_neighbours, if given, =
    (left_highs,left_lows,right_highs,right_lows); swing_lookback comes from config."""
    if config is None:
        raise ValueError("GOV-FEAT-CFG-001: classification config required (fail-loud)")
    config.validate()
    geo = candle_geometry(open_, high, low, close)
    feat = {"schema_version": SCHEMA_VERSION, "service": "HERMES", "domain": "candle_features",
            "instrument": instrument, "timeframe": timeframe, "candle_id": candle_id,
            "source_table": source_table, "source_timeframe": timeframe, "source_candle_id": source_candle_id,
            "source_open_utc": source_open_utc, "source_close_utc": source_close_utc,
            "generated_at_utc": generated_at_utc, "freshness_state": freshness_state,
            "classification_config": config.provenance()}
    feat.update(geo)
    feat.update(classify(geo, config))
    feat.update(previous_candle_facts(geo, prev_geo))
    if swing_neighbours is not None:
        lh, ll, rh, rl = swing_neighbours
        feat.update(swing_facts(geo, lh, ll, rh, rl, config.swing_lookback))
    else:
        feat.update({"is_local_swing_high_candidate": None, "is_local_swing_low_candidate": None,
                     "swing_lookback": config.swing_lookback, "swing_left_highs_lows_available": False,
                     "swing_right_highs_lows_available": False, "swing_status": UNAVAILABLE})
    feat.update(completeness_block(complete_state, expected, actual))
    feat.update(anchor_block(timeframe))
    return feat
