"""HERMES governed DETERMINISTIC INDICATOR Redis contract + publisher foundation v1.
WO-HELM-HERMES-INDICATOR-PUBLISHER-AND-CONTROL-PLANE-FIX-0001.

CODE/DESIGN ONLY. Builds the governed v1 deterministic-indicator PAYLOAD + a DISABLED publisher foundation so
the indicator surface can be activated by a later deploy/activate WO. NO Redis I/O, NO network, NO SQL, NO file
writes — at import OR anywhere. Disabled by default; ENABLED-without-AUTHORISED -> terminal halt SystemExit(101).
UTC-only. NO auth/ACL. NO regime / regime_confidence / risk / trade-decision fields (those are ARES-owned).

Ownership: HERMES owns DETERMINISTIC market-data indicators derived from HERMES candles/price — moving averages
(EMA/SMA), ATR, RSI, MACD, deterministic bands (Bollinger/Keltner), deterministic VWAP, deterministic pivots,
deterministic high/low sweeps, deterministic Fibonacci anchors, candle/body/wick features. ARES owns regime,
risk, order-block interpretation, liquidity/risk context, event-risk interpretation, decision/gating.

Future versioned key (per-timeframe separation — cleaner than one blob): hermes:indicators:XAU_USD:{TF}:v1
Not published in this WO.
"""
from __future__ import annotations
from datetime import datetime, timezone

from utils import candle_contract_v1 as cc   # UTC helpers + canonical instrument check; no I/O

SCHEMA_VERSION = "v1"
PUBLISHER = "HERMES"
INDICATOR_SET_VERSION = "v1"
SOURCE_CANDLE_CONTRACT = "v1"
CANONICAL_INSTRUMENT = "XAU_USD"
_ALIAS_DENY = ("XAUUSD",)
INDICATOR_TIMEFRAMES = ("M1", "M5", "M15", "H1", "H4", "D1")   # D1 gated until D1 latest GREEN
_D1 = "D1"

# The HERMES-owned deterministic indicator set this contract carries (extensible).
# WO-HELM-HERMES-INDICATOR-PUBLICATION-WIRING-0001: "adx" added (trivial extension so ADX/+DI/-DI are a first-class
# deterministic family with a declared method — reuses the SAME contract/key, no new surface).
HERMES_DETERMINISTIC_INDICATORS = ("ema", "sma", "rsi", "macd", "atr", "bollinger", "vwap", "adx")
# Field-key tokens forbidden in an indicator payload (ARES-owned / auth). Deterministic indicators only.
_FORBIDDEN_FIELD_KEY_TOKENS = ("regime", "regime_confidence", "risk", "order_block", "liquidity",
                               "decision", "trade", "gate", "password", "secret", "credential", "acl", "noauth")

# --------------------------------------------------------------------------- gating env
ENABLED_ENV = "HERMES_INDICATOR_PUBLISH_ENABLED"
AUTHORISED_ENV = "HERMES_INDICATOR_PUBLISH_AUTHORISED"
INSTRUMENTS_ENV = "HERMES_INDICATOR_PUBLISH_INSTRUMENTS"
TIMEFRAMES_ENV = "HERMES_INDICATOR_PUBLISH_TIMEFRAMES"
D1_AUTHORISED_ENV = "HERMES_INDICATOR_D1_AUTHORISED"   # D1 indicators denied until D1 latest GREEN
HALT_CODE = 101

REASON_DISABLED = "INDICATOR_PUBLISH_DISABLED"

# Declared deterministic METHODS/conventions so consumers (ARES/Falcon) cannot misinterpret values.
INDICATOR_METHODS = {
    "ema": "STANDARD_2_OVER_N_PLUS_1_SMA_SEED",   # EMA: multiplier 2/(n+1), seeded with SMA(n)
    "rsi": "CUTLER_SMA_14",                        # Cutler's RSI: simple average of gains/losses over n (NOT Wilder smoothing)
    "atr": "SMA_14",                               # ATR = SMA of True Range over n
    "macd": "EMA12_EMA26_SIGNAL9",                 # if MACD present
    "bollinger": "SMA_N_STD_2",                    # if bands present
    "vwap": "CUMULATIVE_PRICE_VOLUME",             # if VWAP present
    "adx": "WILDER_ADX_14",                        # ADX/+DI/-DI: Wilder-smoothed directional movement (settled at 2*period)
}
_METHOD_FIELD = {"ema": "ema_method", "rsi": "rsi_method", "atr": "atr_method",
                 "macd": "macd_method", "bollinger": "bands_method", "vwap": "vwap_method", "adx": "adx_method"}


def _family_of(name):
    for fam in ("ema", "rsi", "atr", "macd", "bollinger", "vwap", "adx"):
        if str(name).startswith(fam):
            return fam
    return None


def methods_for(indicators):
    """Declare the deterministic computation method for every indicator family present (no value change)."""
    methods = {}
    for n in indicators:
        fam = _family_of(n)
        if fam:
            methods[_METHOD_FIELD[fam]] = INDICATOR_METHODS[fam]
    return methods


def _utc(dt):
    if not isinstance(dt, datetime):
        raise ValueError(f"GOV-HERMES-IND-001: timestamp must be a datetime (got {type(dt).__name__})")
    return cc._fmt(cc.normalise_utc(dt))


def _assert_utc(value, field):
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"GOV-HERMES-IND-002: {field} must be a UTC ISO string ending 'Z' (got {value!r})")
    return True


def _scan_no_forbidden_field_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = str(k).lower()
            for tok in _FORBIDDEN_FIELD_KEY_TOKENS:
                if tok in kl:
                    raise ValueError(f"GOV-HERMES-IND-003: forbidden indicator field key {k!r} (token {tok!r}) "
                                     "— HERMES indicators are deterministic only; no regime/risk/decision/auth fields")
            _scan_no_forbidden_field_keys(v)
    elif isinstance(obj, (list, tuple)):
        for x in obj:
            _scan_no_forbidden_field_keys(x)
    return True


def canonical_instrument(instrument):
    if instrument in _ALIAS_DENY:
        return CANONICAL_INSTRUMENT          # XAUUSD -> XAU_USD (never an alias output)
    return instrument


def indicator_key(instrument, timeframe):
    """Per-timeframe versioned indicator key: hermes:indicators:XAU_USD:{TF}:v1 (no unversioned aliases)."""
    if instrument in _ALIAS_DENY or instrument != CANONICAL_INSTRUMENT:
        raise ValueError(f"GOV-HERMES-IND-004: instrument {instrument!r} not allowed (canonical XAU_USD only; no XAUUSD alias)")
    if timeframe not in INDICATOR_TIMEFRAMES:
        raise ValueError(f"GOV-HERMES-IND-005: timeframe {timeframe!r} not in {INDICATOR_TIMEFRAMES}")
    return f"hermes:indicators:{CANONICAL_INSTRUMENT}:{timeframe}:{SCHEMA_VERSION}"


# --------------------------------------------------------------------------- contract payload
def build_indicator_contract(*, instrument, timeframe, generated_at_utc, value_open_time_utc, indicators,
                             freshness_state="FRESH"):
    """hermes:indicators:XAU_USD:{TF}:v1 payload — DETERMINISTIC indicators derived from the v1 candle contract.
    `indicators` is a dict of deterministic indicator name -> value (e.g. {'ema_12':..., 'rsi_14':..., 'atr_14':...}).
    Pure; no I/O. deterministic_only=true; no regime/risk/decision fields."""
    if instrument in _ALIAS_DENY or instrument != CANONICAL_INSTRUMENT:
        raise ValueError(f"GOV-HERMES-IND-004: instrument {instrument!r} not allowed (canonical XAU_USD only; no XAUUSD alias)")
    if timeframe not in INDICATOR_TIMEFRAMES:
        raise ValueError(f"GOV-HERMES-IND-005: timeframe {timeframe!r} not in {INDICATOR_TIMEFRAMES}")
    payload = {
        "publisher": PUBLISHER, "schema_version": SCHEMA_VERSION, "contract_version": "v1",
        "instrument": CANONICAL_INSTRUMENT, "timeframe": timeframe,
        "generated_at_utc": _utc(generated_at_utc),
        "value_open_time_utc": _utc(value_open_time_utc),
        "source_candle_contract": SOURCE_CANDLE_CONTRACT,
        "source_timeframes": [timeframe],
        "indicator_set_version": INDICATOR_SET_VERSION,
        "indicators": dict(indicators),
        "methods": methods_for(indicators),       # declared conventions (RSI=Cutler SMA, ATR=SMA, EMA=standard)
        "freshness_state": freshness_state,
        "deterministic_only": True,
    }
    validate_indicator_contract(payload)
    return payload


def validate_indicator_contract(p):
    if p.get("publisher") != PUBLISHER:
        raise ValueError("GOV-HERMES-IND-010: indicator publisher must be HERMES")
    if p.get("contract_version") != "v1":
        raise ValueError("GOV-HERMES-IND-011: indicator contract_version must be v1")
    if p.get("instrument") != CANONICAL_INSTRUMENT:
        raise ValueError("GOV-HERMES-IND-012: indicator instrument must be XAU_USD (no alias/non-XAU)")
    if p.get("timeframe") not in INDICATOR_TIMEFRAMES:
        raise ValueError("GOV-HERMES-IND-013: indicator timeframe not in grid")
    if p.get("deterministic_only") is not True:
        raise ValueError("GOV-HERMES-IND-014: indicator payload must be deterministic_only=true")
    _assert_utc(p.get("generated_at_utc"), "generated_at_utc")
    _assert_utc(p.get("value_open_time_utc"), "value_open_time_utc")
    if not isinstance(p.get("indicators"), dict) or not p["indicators"]:
        raise ValueError("GOV-HERMES-IND-015: indicators must be a non-empty dict")
    methods = p.get("methods")
    if not isinstance(methods, dict):
        raise ValueError("GOV-HERMES-IND-016: indicator payload must declare a methods block")
    for n in p["indicators"]:                  # every indicator family present must declare its method
        fam = _family_of(n)
        if fam and _METHOD_FIELD[fam] not in methods:
            raise ValueError(f"GOV-HERMES-IND-017: missing method declaration {_METHOD_FIELD[fam]!r} for {n!r}")
    _scan_no_forbidden_field_keys(p)          # no regime/regime_confidence/risk/decision/trade fields
    return True


# --------------------------------------------------------------------------- publisher foundation (DISABLED)
def parse_indicator_instruments(raw):
    if raw is None or not str(raw).strip():
        raise ValueError(f"GOV-HERMES-IND-020: {INSTRUMENTS_ENV} required and non-empty (fail-closed)")
    items = [x.strip() for x in str(raw).split(",") if x.strip()]
    if not items:
        raise ValueError(f"GOV-HERMES-IND-020: {INSTRUMENTS_ENV} required and non-empty (fail-closed)")
    canon = set()
    for inst in items:
        if inst == "XAUUSD" or canonical_instrument(inst) != CANONICAL_INSTRUMENT or inst != CANONICAL_INSTRUMENT:
            raise ValueError(f"GOV-HERMES-IND-021: instrument {inst!r} not allowed (canonical XAU_USD only; no XAUUSD/non-XAU)")
        canon.add(CANONICAL_INSTRUMENT)
    return frozenset(canon)


def parse_indicator_timeframes(raw, *, allow_d1=False):
    """Explicit, fail-closed. D1 indicators are GATED until D1 latest GREEN -> denied unless allow_d1."""
    if raw is None or not str(raw).strip():
        raise ValueError(f"GOV-HERMES-IND-022: {TIMEFRAMES_ENV} required and non-empty (fail-closed)")
    items = [x.strip() for x in str(raw).split(",") if x.strip()]
    if not items:
        raise ValueError(f"GOV-HERMES-IND-022: {TIMEFRAMES_ENV} required and non-empty (fail-closed)")
    out = []
    for tf in items:
        if tf == _D1 and not allow_d1:
            raise ValueError(f"GOV-HERMES-IND-D1-001: D1 indicators are GATED until D1 latest is GREEN — set "
                             f"{D1_AUTHORISED_ENV}=true only after D1 latest GREEN")
        if tf not in INDICATOR_TIMEFRAMES:
            raise ValueError(f"GOV-HERMES-IND-023: timeframe {tf!r} not in {INDICATOR_TIMEFRAMES}")
        if tf not in out:
            out.append(tf)
    return tuple(out)


class DisabledIndicatorPublisher:
    """Safe no-op (default). No Redis client, no connection, no I/O."""
    enabled = False

    def status(self):
        return {"enabled": False}


class IndicatorPublisher:
    """ENABLED + AUTHORISED. Builds the governed deterministic-indicator PAYLOADS only — still NO Redis client
    and NO I/O (publishing is a separate, later, authorised WO)."""
    enabled = True

    def __init__(self, *, allowed_instruments, timeframes):
        allowed = frozenset(allowed_instruments)
        if allowed != frozenset({CANONICAL_INSTRUMENT}):
            raise ValueError("GOV-HERMES-IND-021: indicator allowlist must be exactly {XAU_USD}")
        if not timeframes:
            raise ValueError("GOV-HERMES-IND-024: indicator timeframes must be non-empty")
        self.allowed_instruments = allowed
        self.timeframes = tuple(timeframes)

    def status(self):
        return {"enabled": True, "instruments": sorted(self.allowed_instruments),
                "timeframes": list(self.timeframes)}

    def build(self, **kw):
        return build_indicator_contract(**kw)

    def key(self, instrument, timeframe):
        return indicator_key(instrument, timeframe)


def build_indicator_publisher_from_env():
    """Boot factory. DEFAULT DISABLED -> DisabledIndicatorPublisher (no-op, no Redis client, no I/O). ENABLED
    without AUTHORISED -> terminal halt SystemExit(101). ENABLED + AUTHORISED -> IndicatorPublisher (payloads
    only, no Redis I/O). D1 indicators gated until HERMES_INDICATOR_D1_AUTHORISED=true (D1 latest GREEN). No
    hidden defaults; lazy env read; NO Redis/network/SQL/file I/O at import or here."""
    from env_config import get_env, get_env_bool   # lazy; HERMES-owned config only
    if not get_env_bool(ENABLED_ENV, False):
        return DisabledIndicatorPublisher()
    if not get_env_bool(AUTHORISED_ENV, False):
        raise SystemExit(HALT_CODE)        # fail-loud terminal halt (exit 101)
    instruments = parse_indicator_instruments(get_env(INSTRUMENTS_ENV, default=None))
    allow_d1 = get_env_bool(D1_AUTHORISED_ENV, False)
    timeframes = parse_indicator_timeframes(get_env(TIMEFRAMES_ENV, default=None), allow_d1=allow_d1)
    return IndicatorPublisher(allowed_instruments=instruments, timeframes=timeframes)
