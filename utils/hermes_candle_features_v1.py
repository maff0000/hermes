"""HERMES governed DETERMINISTIC CANDLE-FEATURES Redis contract + publisher foundation v1.
WO-HELM-HERMES-CANDLE-FEATURES-PUBLISHER-AND-INDICATOR-CONVENTIONS-0001.

CODE/DESIGN ONLY. Builds the governed v1 deterministic candle-feature PAYLOAD + a DISABLED publisher foundation.
NO Redis I/O, NO network, NO SQL, NO file writes — at import OR anywhere. Disabled by default; ENABLED-without-
AUTHORISED -> terminal halt SystemExit(101). UTC-only. NO auth/ACL. NO regime / risk / decision / trade fields.

Ownership: HERMES owns DETERMINISTIC candle-derived features — body_size, range_size, wick_high/low, body_high/low,
candle_direction, body-to-range ratio, upper/lower wick ratios, close position in range, and THRESHOLD-DEFINED
deterministic classification flags (doji / full_body / long_wick / pin_bar / inside / outside / engulfing) where the
thresholds are governed config. ARES owns interpretive features (regime, risk, liquidity intent, smart-money, order-
block meaning, trade signal/decision, go/no-go) — REJECTED here.

Future versioned key (per-timeframe): hermes:candle_features:XAU_USD:{TF}:v1. Not published in this WO.
"""
from __future__ import annotations
from datetime import datetime, timezone

from utils import candle_contract_v1 as cc   # UTC helpers; no I/O

SCHEMA_VERSION = "v1"
PUBLISHER = "HERMES"
FEATURE_SET_VERSION = "v1"
SOURCE_CANDLE_CONTRACT = "v1"
CANONICAL_INSTRUMENT = "XAU_USD"
_ALIAS_DENY = ("XAUUSD",)
FEATURE_TIMEFRAMES = ("M1", "M5", "M15", "H1", "H4", "D1")   # D1 gated until D1 latest GREEN
_D1 = "D1"

# HERMES-owned deterministic feature names this contract carries (extensible). Geometry mirrors the candle
# contract; ratios + threshold-defined flags come from the governed candle_features classifier.
HERMES_DETERMINISTIC_FEATURES = (
    "body_size", "range_size", "wick_high", "wick_low", "body_high", "body_low", "candle_direction",
    "body_to_range_ratio", "upper_wick_ratio", "lower_wick_ratio", "close_position_in_range",
    "doji", "full_body", "long_wick", "pin_bar", "inside_bar", "outside_bar", "engulfing",
)
# Interpretive / ARES-owned feature names — REJECTED as field keys (regime/risk/decision tokens also caught below).
_ARES_INTERPRETIVE_FEATURE_TOKENS = ("regime", "risk", "liquidity", "smart_money", "order_block", "decision",
                                     "trade", "signal", "go_no_go", "intent", "confidence", "permission", "setup")
_FORBIDDEN_FIELD_KEY_TOKENS = _ARES_INTERPRETIVE_FEATURE_TOKENS + ("password", "secret", "credential", "acl", "noauth")

# --------------------------------------------------------------------------- gating env
ENABLED_ENV = "HERMES_CANDLE_FEATURE_PUBLISH_ENABLED"
AUTHORISED_ENV = "HERMES_CANDLE_FEATURE_PUBLISH_AUTHORISED"
INSTRUMENTS_ENV = "HERMES_CANDLE_FEATURE_PUBLISH_INSTRUMENTS"
TIMEFRAMES_ENV = "HERMES_CANDLE_FEATURE_PUBLISH_TIMEFRAMES"
D1_AUTHORISED_ENV = "HERMES_CANDLE_FEATURE_D1_AUTHORISED"
HALT_CODE = 101


def _utc(dt):
    if not isinstance(dt, datetime):
        raise ValueError(f"GOV-HERMES-FEAT-001: timestamp must be a datetime (got {type(dt).__name__})")
    return cc._fmt(cc.normalise_utc(dt))


def _assert_utc(value, field):
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"GOV-HERMES-FEAT-002: {field} must be a UTC ISO string ending 'Z' (got {value!r})")
    return True


def _scan_no_forbidden_field_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = str(k).lower()
            for tok in _FORBIDDEN_FIELD_KEY_TOKENS:
                if tok in kl:
                    raise ValueError(f"GOV-HERMES-FEAT-003: forbidden candle-feature field key {k!r} (token {tok!r}) "
                                     "— HERMES candle-features are deterministic only; no regime/risk/decision/interpretive fields")
            _scan_no_forbidden_field_keys(v)
    elif isinstance(obj, (list, tuple)):
        for x in obj:
            _scan_no_forbidden_field_keys(x)
    return True


def candle_features_key(instrument, timeframe):
    """Per-timeframe versioned key: hermes:candle_features:XAU_USD:{TF}:v1 (no unversioned aliases)."""
    if instrument in _ALIAS_DENY or instrument != CANONICAL_INSTRUMENT:
        raise ValueError(f"GOV-HERMES-FEAT-004: instrument {instrument!r} not allowed (canonical XAU_USD only; no XAUUSD alias)")
    if timeframe not in FEATURE_TIMEFRAMES:
        raise ValueError(f"GOV-HERMES-FEAT-005: timeframe {timeframe!r} not in {FEATURE_TIMEFRAMES}")
    return f"hermes:candle_features:{CANONICAL_INSTRUMENT}:{timeframe}:{SCHEMA_VERSION}"


# --------------------------------------------------------------------------- contract payload
def build_candle_feature_contract(*, instrument, timeframe, generated_at_utc, source_candle_key,
                                  source_candle_open_time_utc, features, method_config=None,
                                  freshness_state="FRESH"):
    """hermes:candle_features:XAU_USD:{TF}:v1 payload — DETERMINISTIC candle features. `features` is a dict of
    deterministic feature name -> value. `method_config` carries the governed thresholds when classification
    flags are present. Pure; no I/O. deterministic_only=true; no regime/risk/decision fields."""
    if instrument in _ALIAS_DENY or instrument != CANONICAL_INSTRUMENT:
        raise ValueError(f"GOV-HERMES-FEAT-004: instrument {instrument!r} not allowed (canonical XAU_USD only; no XAUUSD alias)")
    if timeframe not in FEATURE_TIMEFRAMES:
        raise ValueError(f"GOV-HERMES-FEAT-005: timeframe {timeframe!r} not in {FEATURE_TIMEFRAMES}")
    payload = {
        "publisher": PUBLISHER, "schema_version": SCHEMA_VERSION, "contract_version": "v1",
        "instrument": CANONICAL_INSTRUMENT, "timeframe": timeframe,
        "generated_at_utc": _utc(generated_at_utc),
        "source_candle_key": str(source_candle_key),
        "source_candle_open_time_utc": _utc(source_candle_open_time_utc),
        "source_candle_contract": SOURCE_CANDLE_CONTRACT,
        "feature_set_version": FEATURE_SET_VERSION,
        "features": dict(features),
        "method_config": dict(method_config) if method_config else {},
        "freshness_state": freshness_state,
        "deterministic_only": True,
    }
    validate_candle_feature_contract(payload)
    return payload


def validate_candle_feature_contract(p):
    if p.get("publisher") != PUBLISHER:
        raise ValueError("GOV-HERMES-FEAT-010: candle-feature publisher must be HERMES")
    if p.get("contract_version") != "v1":
        raise ValueError("GOV-HERMES-FEAT-011: candle-feature contract_version must be v1")
    if p.get("instrument") != CANONICAL_INSTRUMENT:
        raise ValueError("GOV-HERMES-FEAT-012: candle-feature instrument must be XAU_USD (no alias/non-XAU)")
    if p.get("timeframe") not in FEATURE_TIMEFRAMES:
        raise ValueError("GOV-HERMES-FEAT-013: candle-feature timeframe not in grid")
    if p.get("deterministic_only") is not True:
        raise ValueError("GOV-HERMES-FEAT-014: candle-feature payload must be deterministic_only=true")
    _assert_utc(p.get("generated_at_utc"), "generated_at_utc")
    _assert_utc(p.get("source_candle_open_time_utc"), "source_candle_open_time_utc")
    if not isinstance(p.get("source_candle_key"), str) or ":candles:" not in p["source_candle_key"]:
        raise ValueError("GOV-HERMES-FEAT-015: source_candle_key must be a governed hermes:candles key")
    if not isinstance(p.get("features"), dict) or not p["features"]:
        raise ValueError("GOV-HERMES-FEAT-016: features must be a non-empty dict")
    _scan_no_forbidden_field_keys(p)          # rejects ARES-owned interpretive feature keys + regime/risk/decision/auth
    return True


# --------------------------------------------------------------------------- publisher foundation (DISABLED)
def parse_feature_instruments(raw):
    if raw is None or not str(raw).strip():
        raise ValueError(f"GOV-HERMES-FEAT-020: {INSTRUMENTS_ENV} required and non-empty (fail-closed)")
    items = [x.strip() for x in str(raw).split(",") if x.strip()]
    if not items:
        raise ValueError(f"GOV-HERMES-FEAT-020: {INSTRUMENTS_ENV} required and non-empty (fail-closed)")
    for inst in items:
        if inst == "XAUUSD" or inst != CANONICAL_INSTRUMENT:
            raise ValueError(f"GOV-HERMES-FEAT-021: instrument {inst!r} not allowed (canonical XAU_USD only; no XAUUSD/non-XAU)")
    return frozenset({CANONICAL_INSTRUMENT})


def parse_feature_timeframes(raw, *, allow_d1=False):
    """Explicit, fail-closed. D1 candle-features GATED until D1 latest GREEN -> denied unless allow_d1."""
    if raw is None or not str(raw).strip():
        raise ValueError(f"GOV-HERMES-FEAT-022: {TIMEFRAMES_ENV} required and non-empty (fail-closed)")
    items = [x.strip() for x in str(raw).split(",") if x.strip()]
    if not items:
        raise ValueError(f"GOV-HERMES-FEAT-022: {TIMEFRAMES_ENV} required and non-empty (fail-closed)")
    out = []
    for tf in items:
        if tf == _D1 and not allow_d1:
            raise ValueError(f"GOV-HERMES-FEAT-D1-001: D1 candle-features are GATED until D1 latest is GREEN — set "
                             f"{D1_AUTHORISED_ENV}=true only after D1 latest GREEN")
        if tf not in FEATURE_TIMEFRAMES:
            raise ValueError(f"GOV-HERMES-FEAT-023: timeframe {tf!r} not in {FEATURE_TIMEFRAMES}")
        if tf not in out:
            out.append(tf)
    return tuple(out)


class DisabledCandleFeaturePublisher:
    enabled = False

    def status(self):
        return {"enabled": False}


class CandleFeaturePublisher:
    enabled = True

    def __init__(self, *, allowed_instruments, timeframes):
        if frozenset(allowed_instruments) != frozenset({CANONICAL_INSTRUMENT}):
            raise ValueError("GOV-HERMES-FEAT-021: candle-feature allowlist must be exactly {XAU_USD}")
        if not timeframes:
            raise ValueError("GOV-HERMES-FEAT-024: candle-feature timeframes must be non-empty")
        self.allowed_instruments = frozenset(allowed_instruments)
        self.timeframes = tuple(timeframes)

    def status(self):
        return {"enabled": True, "instruments": sorted(self.allowed_instruments), "timeframes": list(self.timeframes)}

    def build(self, **kw):
        return build_candle_feature_contract(**kw)

    def key(self, instrument, timeframe):
        return candle_features_key(instrument, timeframe)


def build_candle_feature_publisher_from_env():
    """Boot factory. DEFAULT DISABLED -> DisabledCandleFeaturePublisher. ENABLED without AUTHORISED -> SystemExit(101).
    ENABLED + AUTHORISED -> CandleFeaturePublisher (payloads only, no Redis I/O). D1 features gated until
    HERMES_CANDLE_FEATURE_D1_AUTHORISED=true (D1 latest GREEN). No hidden defaults; NO Redis/network/SQL/file I/O."""
    from env_config import get_env, get_env_bool   # lazy; HERMES-owned config only
    if not get_env_bool(ENABLED_ENV, False):
        return DisabledCandleFeaturePublisher()
    if not get_env_bool(AUTHORISED_ENV, False):
        raise SystemExit(HALT_CODE)
    instruments = parse_feature_instruments(get_env(INSTRUMENTS_ENV, default=None))
    allow_d1 = get_env_bool(D1_AUTHORISED_ENV, False)
    timeframes = parse_feature_timeframes(get_env(TIMEFRAMES_ENV, default=None), allow_d1=allow_d1)
    return CandleFeaturePublisher(allowed_instruments=instruments, timeframes=timeframes)
