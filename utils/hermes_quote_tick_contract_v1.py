"""HERMES governed DETERMINISTIC QUOTE contract + TICK reconciliation v1.
WO-HELM-HERMES-QUOTE-TICK-CONTRACT-RECONCILIATION-V1-0001.

CODE/DESIGN ONLY. Governed v1 QUOTE contract for DETERMINISTIC market-data quote FACTS — latest bid/ask/mid/spread
(+ spread_bps, spread_points if a governed point size is supplied), source/received/last-quote UTC timestamps,
ingest age, fresh/stale/missing status, provenance. NO Redis I/O / network / SQL / file writes at import OR in the
builder. Disabled by default; ENABLED-without-AUTHORISED -> SystemExit(101). UTC-only. NO auth. NO regime/risk/
decision/ARES/signal/entry-exit interpretation.

TICK: a governed tick surface ALREADY EXISTS — `utils.tick_contract_v1` publishes `hermes:ticks:XAU_USD:latest:v1`.
This WO does NOT rebuild it and does NOT invent a duplicate `hermes:tick:*` key. Instead it REFERENCES the existing
tick surface (`governed_tick_surface_reference`) and RECONCILES the existing tick envelope + legacy quote-bearing
surfaces (`hermes:signals:*`, legacy market_map) into the governed quote schema — dropping ALL interpretation.

Ownership: HERMES owns deterministic quote/tick FACTS. Interpretive fields (regime, risk, go/no-go, setup, trade
permission, ARES event/liquidity/smart-money/order-block meaning, decision/gating conclusions, signal/entry-exit
advice) are ARES-owned and REJECTED as field keys. Key: hermes:quote:XAU_USD:v1 (versioned; XAU_USD output only;
XAUUSD is an INBOUND ALIAS only, never an output publish key). Nothing is published in this WO.
"""
from __future__ import annotations
from datetime import datetime, timezone

from utils import candle_contract_v1 as cc
from utils import tick_contract_v1 as tickc      # the EXISTING governed tick surface (referenced, not rebuilt)

SCHEMA_VERSION = "v1"
QUOTE_CONTRACT_NAME = "quote"
PUBLISHER = "HERMES"
CANONICAL_INSTRUMENT = "XAU_USD"
INBOUND_ALIASES = ("XAUUSD",)
_ALIAS_DENY_OUTPUT = ("XAUUSD",)

# Deterministic, fail-loud status vocabulary.
STATUS_GREEN = "GREEN"
STATUS_AMBER_STALE = "AMBER_STALE"
STATUS_RED_MISSING = "RED_MISSING"
STATUS_GATED = "GATED"
STATUS_NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
STATUS_UNKNOWN = "UNKNOWN_NEEDS_PROBE"
QUOTE_STATUSES = (STATUS_GREEN, STATUS_AMBER_STALE, STATUS_RED_MISSING, STATUS_GATED,
                  STATUS_NOT_IMPLEMENTED, STATUS_UNKNOWN)

CONN_CONNECTED = "CONNECTED"
CONN_DEGRADED = "DEGRADED"
CONN_OFFLINE = "OFFLINE"
CONN_UNKNOWN = "UNKNOWN"
SOURCE_CONNECTIVITY_STATES = (CONN_CONNECTED, CONN_DEGRADED, CONN_OFFLINE, CONN_UNKNOWN)

FRESHNESS_FRESH = "FRESH"
FRESHNESS_STALE = "STALE"
FRESHNESS_UNAVAILABLE = "UNAVAILABLE"

# Methodology constants (documented; surfaced in provenance.method_config). Overridable per-call.
QUOTE_STALE_THRESHOLD_SECONDS = 10.0        # a quote older than this (vs generated_at) is STALE
QUOTE_TTL_SECONDS = 15                       # hot surface -> short self-expiring TTL

# The existing governed tick surface (referenced, NOT rebuilt).
TICK_CONTRACT_KEY_TEMPLATE = "hermes:ticks:XAU_USD:latest:v1"
TICK_SURFACE_STATUS_CODE_PRESENT = "CODE_PRESENT"

_FORBIDDEN_FIELD_KEY_TOKENS = ("regime", "risk", "liquidity", "order_block", "smart_money", "decision", "trade",
                               "signal", "bias", "setup", "go_no_go", "confidence", "intent", "permission",
                               "conclusion", "recommendation", "entry", "exit", "advice",
                               "password", "secret", "credential", "acl", "noauth")

QUOTE_ENABLED_ENV = "HERMES_QUOTE_PUBLISH_ENABLED"
QUOTE_AUTHORISED_ENV = "HERMES_QUOTE_PUBLISH_AUTHORISED"
QUOTE_INSTRUMENTS_ENV = "HERMES_QUOTE_PUBLISH_INSTRUMENTS"
QUOTE_SOURCE_NAME_ENV = "HERMES_QUOTE_SOURCE_NAME"
TICK_ENABLED_ENV = "HERMES_TICK_PUBLISH_ENABLED"
TICK_AUTHORISED_ENV = "HERMES_TICK_PUBLISH_AUTHORISED"
HALT_CODE = 101


def _utc(dt):
    if not isinstance(dt, datetime):
        raise ValueError(f"GOV-HERMES-QT-001: timestamp must be a datetime (got {type(dt).__name__})")
    return cc._fmt(cc.normalise_utc(dt))


def _assert_utc(value, field):
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"GOV-HERMES-QT-002: {field} must be a UTC ISO string ending 'Z' (got {value!r})")
    return True


def _scan_no_forbidden_field_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = str(k).lower()
            for tok in _FORBIDDEN_FIELD_KEY_TOKENS:
                if tok in kl:
                    raise ValueError(f"GOV-HERMES-QT-003: forbidden quote field key {k!r} (token {tok!r}) "
                                     "— HERMES publishes deterministic quote/tick facts only; no regime/risk/"
                                     "decision/signal/entry-exit/ARES interpretation")
            _scan_no_forbidden_field_keys(v)
    elif isinstance(obj, (list, tuple)):
        for x in obj:
            _scan_no_forbidden_field_keys(x)
    return True


def _assert_instrument(instrument):
    if instrument in _ALIAS_DENY_OUTPUT or instrument != CANONICAL_INSTRUMENT:
        raise ValueError(f"GOV-HERMES-QT-004: instrument {instrument!r} not allowed as output (canonical XAU_USD "
                         "only; XAUUSD is an inbound alias, never an output publish key)")


def quote_key(instrument):
    _assert_instrument(instrument)
    return f"hermes:{QUOTE_CONTRACT_NAME}:{CANONICAL_INSTRUMENT}:{SCHEMA_VERSION}"


def governed_tick_surface_reference():
    """The EXISTING governed tick surface (from `tick_contract_v1`), referenced — never rebuilt here. This WO
    invents NO new tick key; the tick contract is already `hermes:ticks:XAU_USD:latest:v1`."""
    try:
        key = tickc.canonical_key(CANONICAL_INSTRUMENT)
    except Exception:  # noqa: BLE001
        key = TICK_CONTRACT_KEY_TEMPLATE
    return {"key": key, "contract": f"{tickc.CONTRACT}:{tickc.CONTRACT_VERSION}",
            "status": TICK_SURFACE_STATUS_CODE_PRESENT, "builder": "utils.tick_contract_v1.build_tick_contract",
            "note": "governed tick surface already implemented; this WO references it, does not rebuild or duplicate"}


def _quote_prices(bid, ask, *, point_size=None):
    """Deterministic quote arithmetic with fail-loud inverted-quote guard. Returns
    (bid, ask, mid, spread, spread_bps, spread_points). Raises on non-numeric or inverted (ask < bid)."""
    try:
        bid_f, ask_f = float(bid), float(ask)
    except (TypeError, ValueError):
        raise ValueError(f"GOV-HERMES-QT-005: bid/ask must be numeric (got {bid!r}/{ask!r})")
    if ask_f < bid_f:
        raise ValueError(f"GOV-HERMES-QT-006: inverted quote ask<bid ({ask_f}<{bid_f}) — fail loud, never accept")
    mid = round((bid_f + ask_f) / 2.0, 6)
    spread = round(ask_f - bid_f, 6)
    spread_bps = round((spread / mid) * 10000, 4) if mid else None
    spread_points = round(spread / float(point_size), 4) if point_size else None
    return bid_f, ask_f, mid, spread, spread_bps, spread_points


def build_quote_contract(*, instrument, generated_at_utc, source_name, bid, ask, connectivity=CONN_CONNECTED,
                         last_quote_utc=None, source_timestamp_utc=None, received_at_utc=None, point_size=None,
                         stale_threshold_seconds=QUOTE_STALE_THRESHOLD_SECONDS, source_dependencies=None,
                         fault_counters=None, notes=None):
    """hermes:quote:XAU_USD:v1 payload — DETERMINISTIC quote FACTS. Pure; no I/O. Missing/stale EXPLICIT (never a
    false GREEN). deterministic_only=true; no regime/risk/decision/signal/entry-exit/ARES fields."""
    _assert_instrument(instrument)
    if connectivity not in SOURCE_CONNECTIVITY_STATES:
        raise ValueError(f"GOV-HERMES-QT-007: connectivity {connectivity!r} not in {SOURCE_CONNECTIVITY_STATES}")
    now = cc.normalise_utc(generated_at_utc)
    prices = {"bid": None, "ask": None, "mid": None, "spread": None, "spread_bps": None, "spread_points": None}
    src_ts = source_timestamp_utc or last_quote_utc
    if connectivity == CONN_UNKNOWN:
        status, age = STATUS_UNKNOWN, None
    elif bid is None or ask is None:
        status, age = STATUS_RED_MISSING, None                          # missing quote -> never GREEN
    else:
        b, a, mid, spread, sbps, spts = _quote_prices(bid, ask, point_size=point_size)
        prices = {"bid": b, "ask": a, "mid": mid, "spread": spread, "spread_bps": sbps, "spread_points": spts}
        if src_ts is None:
            status, age = STATUS_UNKNOWN, None                          # no source ts -> cannot prove freshness -> loud
        else:
            age = round((now - cc.normalise_utc(src_ts)).total_seconds(), 3)
            if connectivity == CONN_OFFLINE:
                status = STATUS_RED_MISSING
            elif age > float(stale_threshold_seconds) or connectivity == CONN_DEGRADED:
                status = STATUS_AMBER_STALE
            else:
                status = STATUS_GREEN
    freshness = (FRESHNESS_FRESH if status == STATUS_GREEN else
                 FRESHNESS_STALE if status == STATUS_AMBER_STALE else FRESHNESS_UNAVAILABLE)
    faults = dict(fault_counters or {})
    payload = {
        "publisher": PUBLISHER, "schema_version": SCHEMA_VERSION,
        "contract": f"{QUOTE_CONTRACT_NAME}:{SCHEMA_VERSION}",
        "instrument": CANONICAL_INSTRUMENT, "canonical_instrument": CANONICAL_INSTRUMENT,
        "generated_at_utc": _utc(generated_at_utc),
        "source": {"name": str(source_name) if source_name else "UNKNOWN", "connectivity": connectivity},
        "status": status,
        "fault": {"state": "FAULTS_PRESENT" if any(int(v) > 0 for v in faults.values()) else "NONE", "counters": faults},
        "freshness": freshness,
        "bid": prices["bid"], "ask": prices["ask"], "mid": prices["mid"], "spread": prices["spread"],
        "spread_points": prices["spread_points"], "spread_bps": prices["spread_bps"],
        "last_quote_utc": (_utc(last_quote_utc) if last_quote_utc else None),
        "received_at_utc": (_utc(received_at_utc) if received_at_utc else None),
        "source_timestamp_utc": (_utc(source_timestamp_utc) if source_timestamp_utc else None),
        "age_seconds": age,
        "ttl_seconds": QUOTE_TTL_SECONDS,
        "source_dependencies": list(source_dependencies) if source_dependencies else [],
        "publisher_identity": "hermes-signal",
        "provenance": {"deterministic_only": True,
                       "method_config": {"stale_threshold_seconds": float(stale_threshold_seconds),
                                         "point_size": point_size}},
        "notes": list(notes) if notes else [],
        "deterministic_only": True,
    }
    validate_quote_contract(payload)
    return payload


def validate_quote_contract(p):
    if p.get("publisher") != PUBLISHER or p.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("GOV-HERMES-QT-010: quote publisher/schema_version must be HERMES/v1")
    if p.get("contract") != f"{QUOTE_CONTRACT_NAME}:{SCHEMA_VERSION}":
        raise ValueError("GOV-HERMES-QT-011: quote contract must be quote:v1")
    if p.get("instrument") != CANONICAL_INSTRUMENT or p.get("canonical_instrument") != CANONICAL_INSTRUMENT:
        raise ValueError("GOV-HERMES-QT-012: quote instrument must be XAU_USD (no alias/non-XAU output)")
    if p.get("status") not in QUOTE_STATUSES:
        raise ValueError("GOV-HERMES-QT-013: quote status not in governed vocab")
    if (p.get("source") or {}).get("connectivity") not in SOURCE_CONNECTIVITY_STATES:
        raise ValueError("GOV-HERMES-QT-014: source.connectivity not in governed vocab")
    if p.get("deterministic_only") is not True:
        raise ValueError("GOV-HERMES-QT-015: quote payload must be deterministic_only=true")
    if not isinstance(p.get("ttl_seconds"), int) or p["ttl_seconds"] <= 0:
        raise ValueError("GOV-HERMES-QT-016: ttl_seconds must be a positive int")
    _assert_utc(p.get("generated_at_utc"), "generated_at_utc")
    bid, ask = p.get("bid"), p.get("ask")
    if bid is not None or ask is not None:
        if not isinstance(bid, (int, float)) or not isinstance(ask, (int, float)):
            raise ValueError("GOV-HERMES-QT-017: bid/ask must be numeric when present")
        if ask < bid:
            raise ValueError("GOV-HERMES-QT-018: inverted quote ask<bid must fail loud")
        if round(ask - bid, 6) != p.get("spread") or round((bid + ask) / 2.0, 6) != p.get("mid"):
            raise ValueError("GOV-HERMES-QT-019: spread must equal ask-bid and mid must equal (bid+ask)/2")
    _scan_no_forbidden_field_keys(p)
    return True


# --------------------------------------------------------------------------- reconciliation (deterministic only)
_DETERMINISTIC_QUOTE_FIELDS = ("bid", "ask")   # mid/spread are RECOMPUTED, never trusted from legacy


def reconcile_quote_from_tick_envelope(tick_env, *, generated_at_utc, source_name=None, point_size=None,
                                       stale_threshold_seconds=QUOTE_STALE_THRESHOLD_SECONDS):
    """Map the EXISTING governed tick envelope (`tick_contract_v1`) -> governed quote payload. Uses only the
    deterministic bid/ask + received/source timestamps; drops the tick's own status vocab (recomputed here)."""
    d = tick_env.get("data", {}) if isinstance(tick_env, dict) else {}
    src_ts = _parse_ts(d.get("received_at_utc") or (tick_env.get("provenance", {}) or {}).get("source_received_at_utc"))
    return build_quote_contract(instrument=CANONICAL_INSTRUMENT, generated_at_utc=generated_at_utc,
                                source_name=source_name or d.get("source") or "UNKNOWN",
                                bid=d.get("bid"), ask=d.get("ask"), connectivity=CONN_CONNECTED,
                                source_timestamp_utc=src_ts, received_at_utc=src_ts, last_quote_utc=src_ts,
                                point_size=point_size, stale_threshold_seconds=stale_threshold_seconds,
                                source_dependencies=[governed_tick_surface_reference()["key"]],
                                notes=["reconciled_from_governed_tick_surface"])


def reconcile_quote_from_legacy_snapshot(snapshot, *, generated_at_utc, source_name=None, point_size=None,
                                         connectivity=CONN_CONNECTED,
                                         stale_threshold_seconds=QUOTE_STALE_THRESHOLD_SECONDS, origin="legacy"):
    """Reconcile a legacy quote-bearing snapshot (e.g. `hermes:signals:*` or legacy market_map) into the governed
    quote payload. Extracts ONLY deterministic bid/ask (+ optional timestamp); mid/spread are RECOMPUTED; EVERY
    non-quote field (signal/decision/regime/market_map interpretation) is DROPPED. Missing bid/ask stays explicit
    (-> RED_MISSING), never silently omitted or fabricated."""
    s = snapshot if isinstance(snapshot, dict) else {}
    bid = _num_or_none(s.get("bid"))
    ask = _num_or_none(s.get("ask"))
    src_ts = _parse_ts(s.get("last_quote_utc") or s.get("source_timestamp_utc") or s.get("timestamp_utc")
                       or s.get("received_at_utc") or s.get("timestamp"))
    return build_quote_contract(instrument=CANONICAL_INSTRUMENT, generated_at_utc=generated_at_utc,
                                source_name=source_name or s.get("source") or "UNKNOWN", bid=bid, ask=ask,
                                connectivity=connectivity, source_timestamp_utc=src_ts, last_quote_utc=src_ts,
                                point_size=point_size, stale_threshold_seconds=stale_threshold_seconds,
                                notes=[f"reconciled_from_{origin}_deterministic_quote_fields_only"])


def _num_or_none(v):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f


def _parse_ts(v):
    if v is None or isinstance(v, datetime):
        return v
    try:
        return datetime.strptime(str(v)[:-1], cc._UTC_MS).replace(tzinfo=timezone.utc) if str(v).endswith("Z") \
            else None
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------- publisher foundation (DISABLED)
def parse_quote_instruments(raw):
    if raw is None or not str(raw).strip():
        raise ValueError(f"GOV-HERMES-QT-020: {QUOTE_INSTRUMENTS_ENV} required and non-empty (fail-closed)")
    items = [x.strip() for x in str(raw).split(",") if x.strip()]
    if not items:
        raise ValueError(f"GOV-HERMES-QT-020: {QUOTE_INSTRUMENTS_ENV} required and non-empty (fail-closed)")
    for inst in items:
        if inst == "XAUUSD" or inst != CANONICAL_INSTRUMENT:
            raise ValueError(f"GOV-HERMES-QT-021: instrument {inst!r} not allowed (canonical XAU_USD only; XAUUSD is inbound alias)")
    return frozenset({CANONICAL_INSTRUMENT})


class DisabledQuotePublisher:
    enabled = False

    def status(self):
        return {"enabled": False}


class QuotePublisher:
    enabled = True

    def __init__(self, *, allowed_instruments, source_name):
        if frozenset(allowed_instruments) != frozenset({CANONICAL_INSTRUMENT}):
            raise ValueError("GOV-HERMES-QT-021: quote allowlist must be exactly {XAU_USD}")
        self.allowed_instruments = frozenset(allowed_instruments)
        self.source_name = source_name

    def status(self):
        return {"enabled": True, "instruments": sorted(self.allowed_instruments), "source_name": self.source_name}

    def build(self, **kw):
        return build_quote_contract(**kw)

    def key(self, instrument):
        return quote_key(instrument)


def build_quote_publisher_from_env():
    """DEFAULT DISABLED -> DisabledQuotePublisher (no-op, no Redis client, no I/O). ENABLED without AUTHORISED ->
    SystemExit(101). ENABLED + AUTHORISED -> QuotePublisher (payloads only, NO Redis I/O). No hidden defaults;
    NO Redis/SQL/network at import or here."""
    from env_config import get_env, get_env_bool   # lazy; HERMES-owned config only
    if not get_env_bool(QUOTE_ENABLED_ENV, False):
        return DisabledQuotePublisher()
    if not get_env_bool(QUOTE_AUTHORISED_ENV, False):
        raise SystemExit(HALT_CODE)
    instruments = parse_quote_instruments(get_env(QUOTE_INSTRUMENTS_ENV, default=None))
    source_name = get_env(QUOTE_SOURCE_NAME_ENV, default="UNKNOWN") or "UNKNOWN"
    return QuotePublisher(allowed_instruments=instruments, source_name=source_name)
