"""HERMES governed Redis CANDLE contract v1 (builder + validator + freshness + derivation accounting).
WO-HELM-HERMES-GOVERNED-CANDLE-FORWARD-CONTRACT-AND-PUBLISHER-0001.

Forward live candle truth for the HERMES market-data spine. HERMES owns market truth ONLY — this is
raw OHLCV candle truth with explicit completeness/gap/derivation accounting, NOT structure/regime/
strategy interpretation. There is NO Redis I/O here. ALL timestamps UTC (ms precision).

Proteus is dead: the legacy `signals:candle:{tf}:{instr}:latest` pubsub/hash (utils.redis_publisher.
publish_candle) is NOT the governed contract and must never be emitted as governed output.

Canonical forward keys:  hermes:candles:{instrument}:{timeframe}:latest:v1
Shadow/dev keys:         hermes:shadow:candles:{instrument}:{timeframe}:latest:v1
Window (design only):    hermes:candles:{instrument}:{timeframe}:window:v1   (NOT implemented here)

Completeness != freshness (two independent axes — aligned with utils.forward_derivation_runner doctrine).
A derived H4/D is NEVER status OK while its lower-timeframe source coverage is incomplete.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone

SCHEMA_VERSION = "v1"
SERVICE = "HERMES"
DOMAIN = "candles"
CONTRACT = "hermes.candles.latest"
CONTRACT_VERSION = "v1"

# M1/M5/M15/H1 are the DIRECT-NATIVE shadow grid (WO-...-GOLD-MTF-CANDLE-CONTRACT-EXTEND-0001).
# H4/D remain recognised for the (deferred) derived path; the shadow seam still skips them.
TIMEFRAMES = ("M1", "M5", "M15", "H1", "H4", "D")
# candle validity = one timeframe period (a closed "latest" candle is valid until the next forms)
TF_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600, "H4": 14400, "D": 86400}
# documented Redis EX buffers (grace beyond validity; consumers freshness-gate on valid_until_utc)
TTL_BUFFER_SECONDS = {"M1": 30, "M5": 60, "M15": 180, "H1": 300, "H4": 900, "D": 3600}

# governed derivation policies (mirror utils.forward_derivation_runner — single doctrine)
DERIVATION_POLICY_FORWARD = "FORWARD_COMPLETE_M1_ONLY"
DERIVATION_POLICY_HISTORICAL = "HISTORICAL_ALL_M1_COUNTED"
DERIVATION_POLICY_DIRECT = "NONE_DIRECT"
DERIVATION_POLICY_H4_FROM_H1 = "DERIVED_H4_FROM_H1"   # governed H4 from 4xH1 (NY-5PM aligned buckets)
DERIVATION_POLICIES = (DERIVATION_POLICY_FORWARD, DERIVATION_POLICY_HISTORICAL,
                       DERIVATION_POLICY_DIRECT, DERIVATION_POLICY_H4_FROM_H1)

DERIVATION_DIRECT = "DIRECT_FROM_SOURCE"
DERIVATION_DERIVED = "DERIVED_FROM_LOWER_TIMEFRAME"
DERIVATIONS = (DERIVATION_DIRECT, DERIVATION_DERIVED)

FRESHNESS_STATES = ("FRESH", "STALE", "FORMING", "UNAVAILABLE")
STATUSES = ("OK", "STALE", "FORMING", "GAP_DETECTED", "SOURCE_INCOMPLETE",
            "NO_SOURCE_DATA", "MARKET_CLOSED", "PUBLISH_DISABLED")
GAP_STATES = ("NONE", "INCOMPLETE", "GAP_DETECTED")
REASON_VOCAB = set(STATUSES) | set(DERIVATIONS) | {
    "SHADOW_ONLY_NOT_PRODUCTION", "OK_COMPLETE", "DIRECT_FROM_SOURCE", "DERIVED_FROM_LOWER_TIMEFRAME"}

# fields a raw-candle market-truth contract must NEVER carry (consumer-owned interpretation)
_FORBIDDEN_TOKENS = ("structure", "choch", "bos", "order_block", "regime", "signal", "setup",
                     "confidence", "permission", "risk", "cockpit", "projection", "support",
                     "resistance")
_UTC_MS = "%Y-%m-%dT%H:%M:%S.%f"


def canonical_key(instrument, timeframe):
    _assert_inst_tf(instrument, timeframe)
    return f"hermes:candles:{instrument}:{timeframe}:latest:v1"


def shadow_key(instrument, timeframe):
    _assert_inst_tf(instrument, timeframe)
    return f"hermes:shadow:candles:{instrument}:{timeframe}:latest:v1"


def redis_ex_seconds(timeframe):
    _assert_tf(timeframe)
    return TF_SECONDS[timeframe] + TTL_BUFFER_SECONDS[timeframe]


def _assert_tf(tf):
    if tf not in TIMEFRAMES:
        raise ValueError(f"GOV-CANDLE-CONTRACT-001: timeframe must be one of {TIMEFRAMES} (got {tf!r})")


def _assert_inst_tf(instrument, tf):
    if not instrument or not isinstance(instrument, str):
        raise ValueError("GOV-CANDLE-CONTRACT-002: instrument required (fail-loud)")
    _assert_tf(tf)


def normalise_utc(dt):
    """Normalise a datetime to timezone-aware UTC. A NAIVE datetime is ASSUMED to be UTC (the runtime
    aggregator emits naive UTC candle timestamps) — never localised to the system/NY/London timezone.
    An aware datetime is converted to UTC. This makes all contract arithmetic aware-UTC only."""
    if dt.tzinfo is None or dt.utcoffset() is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _fmt(dt):
    return normalise_utc(dt).strftime("%Y-%m-%dT%H:%M:%S.") + f"{normalise_utc(dt).microsecond // 1000:03d}Z"


def aggregate_ohlc(source_candles):
    """Deterministic OHLCV aggregation of ordered lower-timeframe candles.
    open=first.open, high=max(high), low=min(low), close=last.close, volume=sum(volume)."""
    if not source_candles:
        raise ValueError("GOV-CANDLE-CONTRACT-003: cannot aggregate empty source set")
    o = source_candles[0]["open"]
    c = source_candles[-1]["close"]
    hi = max(x["high"] for x in source_candles)
    lo = min(x["low"] for x in source_candles)
    vol = sum(x.get("volume", 0) for x in source_candles)
    return {"open": o, "high": hi, "low": lo, "close": c, "volume": vol}


# Deterministic candle geometry — HERMES owns RAW market truth only (architect Option-A: candle-state/
# features, NOT regime). Fixed wick semantics (no high/low aliasing):
#   body_high = max(open, close)   body_low = min(open, close)   body_size = abs(close - open)
#   range_size = high - low        wick_high = high - body_high   wick_low = body_low - low
#   candle_direction = UP if close>open else DOWN if close<open else FLAT  (pure sign; no thresholds)
# wick_high is the UPPER WICK SIZE (== features.candle_geometry upper_wick_size), NEVER the high price.
_GEOM_FIELDS = ("body_high", "body_low", "body_size", "range_size",
                "wick_high", "wick_low", "candle_direction")

# Governed price precision. OHLC is quantised to this many decimals BEFORE bounds/geometry validation so
# raw float-hair (e.g. high=0.8626849999999999 vs close=0.862685) cannot violate high>=max(open,close)
# or make a rounded body_high exceed high. Rounding is monotonic, so quantising O/H/L/C preserves every
# invariant (round(open)<=round(high), etc.); geometry is then derived from the SAME quantised values.
_PRICE_DP = 6


def _q(v):
    """Quantise a price to the governed precision (None passes through)."""
    return round(float(v), _PRICE_DP) if v is not None else None


def _candle_geometry(price):
    """Return the deterministic geometry block for a price dict whose OHLC are ALREADY quantised to
    _PRICE_DP. All-None when unpriced (NO_SOURCE_DATA / MARKET_CLOSED). Because O/H/L/C are quantised,
    body_high=max(o,c)<=high and body_low=min(o,c)>=low hold exactly (no float-hair violations)."""
    o, h, lo, c = price["open"], price["high"], price["low"], price["close"]
    if None in (o, h, lo, c):
        return {k: None for k in _GEOM_FIELDS}
    body_high, body_low = max(o, c), min(o, c)
    return {
        "body_high": round(body_high, _PRICE_DP), "body_low": round(body_low, _PRICE_DP),
        "body_size": round(abs(c - o), _PRICE_DP), "range_size": round(h - lo, _PRICE_DP),
        "wick_high": round(h - body_high, _PRICE_DP), "wick_low": round(body_low - lo, _PRICE_DP),
        "candle_direction": "UP" if c > o else ("DOWN" if c < o else "FLAT"),
    }


def build_candle_contract(*, instrument, timeframe, timestamp_utc, ohlc, is_closed,
                          generated_at_utc, source_timeframe, source_count, expected_source_count,
                          derivation, derivation_policy, source_policy_epoch,
                          market_open=True, source_warning=False):
    """Build a governed candle envelope. Fail-loud on bad timeframe/derivation/OHLC. The incomplete-
    source gate (a derived candle with source_count < expected is NEVER status OK) is enforced here
    AND re-checked in validate_candle_contract."""
    key = canonical_key(instrument, timeframe)
    if derivation not in DERIVATIONS:
        raise ValueError(f"GOV-CANDLE-CONTRACT-004: derivation must be one of {DERIVATIONS}")
    if derivation_policy not in DERIVATION_POLICIES:
        raise ValueError(f"GOV-CANDLE-CONTRACT-005: derivation_policy must be one of {DERIVATION_POLICIES}")
    # Normalise BOTH timestamps to aware-UTC before any arithmetic. Runtime candles arrive tz-naive
    # (assumed UTC); mixing naive/aware previously raised TypeError -> silent CANDLE_VALIDATE_FAIL.
    timestamp_utc = normalise_utc(timestamp_utc)
    generated_at_utc = normalise_utc(generated_at_utc)
    ttl = TF_SECONDS[timeframe]
    valid_until = generated_at_utc + timedelta(seconds=ttl)
    close_time = timestamp_utc + timedelta(seconds=ttl)
    age = (generated_at_utc - close_time).total_seconds()

    coverage = None
    if expected_source_count:
        coverage = round(source_count / expected_source_count, 6)
    incomplete = bool(expected_source_count) and source_count < expected_source_count

    # gap_state
    if source_count == 0:
        gap_state = "GAP_DETECTED"
    elif incomplete:
        gap_state = "INCOMPLETE"
    else:
        gap_state = "NONE"

    # freshness + status (completeness independent of freshness; never launder incomplete to OK)
    reason_codes = [DERIVATION_DERIVED if derivation == DERIVATION_DERIVED else DERIVATION_DIRECT]
    if not market_open:
        freshness, status = "UNAVAILABLE", "MARKET_CLOSED"
        reason_codes.append("MARKET_CLOSED")
    elif source_count == 0:
        freshness, status = "UNAVAILABLE", "NO_SOURCE_DATA"
        reason_codes.append("NO_SOURCE_DATA")
    elif not is_closed:
        freshness, status = "FORMING", "FORMING"
        reason_codes.append("FORMING")
    elif incomplete:
        freshness = "STALE" if age > ttl else "FRESH"
        status = "SOURCE_INCOMPLETE"
        reason_codes.append("SOURCE_INCOMPLETE")
    elif age > ttl:
        freshness, status = "STALE", "STALE"
        reason_codes.append("STALE")
    else:
        freshness, status = "FRESH", "OK"
        reason_codes.append("OK_COMPLETE")
    if source_warning:
        reason_codes.append("GAP_DETECTED") if gap_state == "GAP_DETECTED" else None

    price = {"open": None, "high": None, "low": None, "close": None, "volume": None}
    if status not in ("NO_SOURCE_DATA", "MARKET_CLOSED"):
        # Quantise OHLC to the governed precision BEFORE the bounds check, so sub-precision float-hair
        # (high a few ulps under max(open,close)) cannot trip GOV-006/026. Rounding is monotonic, so
        # the bound high>=max(open,close) and low<=min(open,close) survive quantisation.
        price = {"open": _q(ohlc["open"]), "high": _q(ohlc["high"]), "low": _q(ohlc["low"]),
                 "close": _q(ohlc["close"]), "volume": ohlc.get("volume", 0)}
        if None not in (price["open"], price["high"], price["low"], price["close"]):
            if price["high"] < max(price["open"], price["close"]) or price["low"] > min(price["open"], price["close"]) or price["high"] < price["low"]:
                raise ValueError(f"GOV-CANDLE-CONTRACT-006: invalid OHLC {price} (fail-loud)")

    geom = _candle_geometry(price)

    return {
        "schema_version": SCHEMA_VERSION, "service": SERVICE, "domain": DOMAIN,
        "contract": CONTRACT, "contract_version": CONTRACT_VERSION, "key": key,
        "generated_at_utc": _fmt(generated_at_utc), "valid_until_utc": _fmt(valid_until),
        "ttl_seconds": ttl, "freshness_state": freshness, "status": status,
        "reason_codes": [r for r in reason_codes if r],
        "provenance": {"publisher": "HERMES", "source": "oanda",
                       "source_timeframe": source_timeframe, "derivation": derivation,
                       "generated_at_utc": _fmt(generated_at_utc),
                       "source_policy_epoch": source_policy_epoch},
        "data": {"instrument": instrument, "timeframe": timeframe,
                 "timestamp_utc": _fmt(timestamp_utc), **price, **geom, "is_closed": is_closed,
                 "source_timeframe": source_timeframe, "source_coverage": coverage,
                 "source_count": source_count, "expected_source_count": expected_source_count,
                 "gap_state": gap_state, "derivation_policy": derivation_policy,
                 "source_policy_epoch": source_policy_epoch, "publisher": "HERMES",
                 "contract_version": CONTRACT_VERSION},
    }


def build_derived_candle_contract(*, instrument, timeframe, timestamp_utc, source_candles,
                                  expected_source_count, generated_at_utc, source_timeframe,
                                  derivation_policy=DERIVATION_POLICY_FORWARD, source_policy_epoch,
                                  is_closed=True, market_open=True):
    """Derive a higher-timeframe candle from ordered lower-timeframe candles with completeness
    accounting. Incomplete coverage is surfaced (never hidden) and can never be status OK."""
    ohlc = aggregate_ohlc(source_candles) if source_candles else {"open": None, "high": None, "low": None, "close": None, "volume": 0}
    return build_candle_contract(
        instrument=instrument, timeframe=timeframe, timestamp_utc=timestamp_utc, ohlc=ohlc,
        is_closed=is_closed, generated_at_utc=generated_at_utc, source_timeframe=source_timeframe,
        source_count=len(source_candles), expected_source_count=expected_source_count,
        derivation=DERIVATION_DERIVED, derivation_policy=derivation_policy,
        source_policy_epoch=source_policy_epoch, market_open=market_open)


def _is_utc_ms(s):
    if not isinstance(s, str) or not s.endswith("Z"):
        return False
    try:
        datetime.strptime(s[:-1], _UTC_MS); return True
    except ValueError:
        return False


_ENVELOPE_KEYS = ("schema_version", "service", "domain", "contract", "contract_version", "key",
                  "generated_at_utc", "valid_until_utc", "ttl_seconds", "freshness_state",
                  "status", "reason_codes", "provenance", "data")
_DATA_KEYS = ("instrument", "timeframe", "timestamp_utc", "open", "high", "low", "close", "volume",
              "body_high", "body_low", "body_size", "range_size", "wick_high", "wick_low",
              "candle_direction", "is_closed", "source_timeframe", "source_coverage", "source_count",
              "expected_source_count", "gap_state", "derivation_policy", "source_policy_epoch",
              "publisher", "contract_version")


def validate_candle_contract(payload):
    """Fail-loud governance validation. Returns True or raises ValueError(GOV-CANDLE-CONTRACT-*)."""
    missing = [k for k in _ENVELOPE_KEYS if k not in payload]
    if missing:
        raise ValueError(f"GOV-CANDLE-CONTRACT-010: missing envelope fields {missing}")
    if payload["service"] != "HERMES" or payload["provenance"].get("publisher") != "HERMES":
        raise ValueError("GOV-CANDLE-CONTRACT-011: publisher/service must be HERMES")
    d = payload["data"]
    dmiss = [k for k in _DATA_KEYS if k not in d]
    if dmiss:
        raise ValueError(f"GOV-CANDLE-CONTRACT-012: missing data fields {dmiss}")
    tf = d["timeframe"]
    if tf not in TIMEFRAMES:
        raise ValueError(f"GOV-CANDLE-CONTRACT-013: bad timeframe {tf}")
    if payload["freshness_state"] not in FRESHNESS_STATES:
        raise ValueError(f"GOV-CANDLE-CONTRACT-014: bad freshness_state {payload['freshness_state']}")
    if payload["status"] not in STATUSES:
        raise ValueError(f"GOV-CANDLE-CONTRACT-015: bad status {payload['status']}")
    if payload["ttl_seconds"] != TF_SECONDS[tf]:
        raise ValueError(f"GOV-CANDLE-CONTRACT-016: ttl_seconds must be {TF_SECONDS[tf]} for {tf}")
    for ts in (payload["generated_at_utc"], payload["valid_until_utc"], d["timestamp_utc"]):
        if not _is_utc_ms(ts):
            raise ValueError(f"GOV-CANDLE-CONTRACT-017: non-UTC-ms timestamp {ts}")
    g = datetime.strptime(payload["generated_at_utc"][:-1], _UTC_MS)
    v = datetime.strptime(payload["valid_until_utc"][:-1], _UTC_MS)
    if abs((v - g).total_seconds() - TF_SECONDS[tf]) > 0.001:
        raise ValueError("GOV-CANDLE-CONTRACT-018: valid_until_utc must equal generated_at_utc + ttl_seconds")
    if payload["key"] != canonical_key(d["instrument"], tf):
        raise ValueError(f"GOV-CANDLE-CONTRACT-019: key inconsistent with instrument/timeframe")
    if payload["provenance"].get("derivation") not in DERIVATIONS:
        raise ValueError("GOV-CANDLE-CONTRACT-020: derivation must be DIRECT_FROM_SOURCE/DERIVED_FROM_LOWER_TIMEFRAME")
    if d["derivation_policy"] not in DERIVATION_POLICIES:
        raise ValueError("GOV-CANDLE-CONTRACT-021: bad derivation_policy")
    if d["gap_state"] not in GAP_STATES:
        raise ValueError(f"GOV-CANDLE-CONTRACT-022: bad gap_state {d['gap_state']}")
    # THE incomplete-source gate: incomplete coverage may NEVER be status OK
    exp, cnt = d["expected_source_count"], d["source_count"]
    if exp and cnt < exp:
        if payload["status"] == "OK":
            raise ValueError("GOV-CANDLE-CONTRACT-023: incomplete source (count<expected) may NOT be status OK")
        if d["gap_state"] == "NONE":
            raise ValueError("GOV-CANDLE-CONTRACT-024: incomplete source must set gap_state INCOMPLETE/GAP_DETECTED")
    # OHLC sanity when present
    if payload["status"] not in ("NO_SOURCE_DATA", "MARKET_CLOSED"):
        o, h, lo, c = d["open"], d["high"], d["low"], d["close"]
        if None in (o, h, lo, c):
            raise ValueError("GOV-CANDLE-CONTRACT-025: priced candle missing OHLC")
        if h < max(o, c) or lo > min(o, c) or h < lo:
            raise ValueError(f"GOV-CANDLE-CONTRACT-026: invalid OHLC o={o} h={h} l={lo} c={c}")
        # Geometry block: present + sign-consistent + recomputed (recomputation is the no-alias guard:
        # wick_high MUST equal high-body_high, NOT the high price; wick_low MUST equal body_low-low).
        bh, bl, bs = d["body_high"], d["body_low"], d["body_size"]
        rs, wh, wl, cd = d["range_size"], d["wick_high"], d["wick_low"], d["candle_direction"]
        if None in (bh, bl, bs, rs, wh, wl) or cd is None:
            raise ValueError("GOV-CANDLE-CONTRACT-030: priced candle missing geometry fields")
        if wh < 0 or wl < 0 or rs < 0:
            raise ValueError(f"GOV-CANDLE-CONTRACT-031: negative wick/range wick_high={wh} wick_low={wl} range_size={rs}")
        if bh > h or bl < lo:
            raise ValueError(f"GOV-CANDLE-CONTRACT-032: body outside high/low (bh={bh} bl={bl} h={h} l={lo})")
        if bs - rs > 1e-6:
            raise ValueError(f"GOV-CANDLE-CONTRACT-033: body_size {bs} > range_size {rs}")
        if abs(bh - max(o, c)) > 1e-6 or abs(bl - min(o, c)) > 1e-6:
            raise ValueError(f"GOV-CANDLE-CONTRACT-034: body_high/body_low inconsistent with open/close")
        if abs(wh - (h - bh)) > 1e-6 or abs(wl - (bl - lo)) > 1e-6:
            raise ValueError("GOV-CANDLE-CONTRACT-035: wick_high/wick_low not recomputable from OHLC "
                             "(high/low aliasing or miscompute)")
        if abs(rs - (h - lo)) > 1e-6:
            raise ValueError(f"GOV-CANDLE-CONTRACT-036: range_size {rs} != high-low {h - lo}")
        if cd not in ("UP", "DOWN", "FLAT"):
            raise ValueError(f"GOV-CANDLE-CONTRACT-037: bad candle_direction {cd}")
        if (cd == "UP") != (c > o) or (cd == "DOWN") != (c < o):
            raise ValueError(f"GOV-CANDLE-CONTRACT-038: candle_direction {cd} inconsistent with open/close")
    for rc in payload["reason_codes"]:
        if rc not in REASON_VOCAB:
            raise ValueError(f"GOV-CANDLE-CONTRACT-027: unknown reason_code {rc}")
    _scan_forbidden(payload)
    # no legacy/Proteus contract leakage
    blob = str(payload).lower()
    if "signals:candle" in blob or "tradingproteus" in blob:
        raise ValueError("GOV-CANDLE-CONTRACT-028: legacy signals:candle / tradingProteus reference forbidden")
    return True


def _scan_forbidden(o):
    if isinstance(o, dict):
        for k, val in o.items():
            kl = str(k).lower()
            for tok in _FORBIDDEN_TOKENS:
                if tok in kl:
                    raise ValueError(f"GOV-CANDLE-CONTRACT-029: forbidden interpretive field '{k}' "
                                     "(HERMES owns raw market truth only)")
            _scan_forbidden(val)
    elif isinstance(o, list):
        for x in o:
            _scan_forbidden(x)
    return True
