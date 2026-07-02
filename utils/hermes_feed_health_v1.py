"""HERMES governed DETERMINISTIC FEED / INGESTION HEALTH contract + publisher foundation v1.
WO-HELM-HERMES-FEED-HEALTH-CONTRACT-V1-0001.

CODE/DESIGN ONLY. Governed v1 contract for DETERMINISTIC market-data INGESTION HEALTH FACTS — source connectivity,
last tick/quote UTC, last candle close UTC by timeframe, ingest lag/age, fresh/stale/missing status, per-timeframe
candle health, publisher fault counters, source/provenance metadata, deterministic missing-data/gap facts. NO Redis
I/O / network / SQL / file writes at import OR anywhere in the builder. Disabled by default; ENABLED-without-
AUTHORISED -> SystemExit(101). UTC-only. NO auth. NO regime/risk/decision/ARES-owned interpretation.

Ownership: HERMES owns deterministic ingestion-health FACTS. Interpretive fields (regime, risk state, go/no-go,
setup quality, trade permission, ARES event/liquidity/smart-money/order-block meaning, decision/gating conclusions)
are ARES-owned and REJECTED as field keys. Key: hermes:feed_health:XAU_USD:v1 (versioned; XAU_USD only, no XAUUSD).

Nothing is published in this WO. The builder is PURE (payloads only); the snapshot collector is client-INJECTED
(reads HERMES-owned surfaces, NEVER writes). D1-derived health stays GATED (never RED) until D1 latest is GREEN.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone

from utils import candle_contract_v1 as cc

SCHEMA_VERSION = "v1"
CONTRACT_NAME = "feed_health"
PUBLISHER = "HERMES"
CANONICAL_INSTRUMENT = "XAU_USD"
_ALIAS_DENY = ("XAUUSD",)

# Deterministic, fail-loud status vocabulary (aggregate + per-timeframe).
STATUS_GREEN = "GREEN"
STATUS_AMBER_STALE = "AMBER_STALE"
STATUS_RED_MISSING = "RED_MISSING"
STATUS_GATED = "GATED"                       # D1-derived health while D1 latest not GREEN — never RED
STATUS_UNKNOWN = "UNKNOWN_NEEDS_PROBE"       # source state indeterminate -> fail loud, not a false GREEN
FEED_HEALTH_STATUSES = (STATUS_GREEN, STATUS_AMBER_STALE, STATUS_RED_MISSING, STATUS_GATED, STATUS_UNKNOWN)

# Source connectivity (deterministic; UNKNOWN -> UNKNOWN_NEEDS_PROBE aggregate).
CONN_CONNECTED = "CONNECTED"
CONN_DEGRADED = "DEGRADED"
CONN_OFFLINE = "OFFLINE"
CONN_UNKNOWN = "UNKNOWN"
SOURCE_CONNECTIVITY_STATES = (CONN_CONNECTED, CONN_DEGRADED, CONN_OFFLINE, CONN_UNKNOWN)

FRESHNESS_FRESH = "FRESH"
FRESHNESS_STALE = "STALE"
FRESHNESS_UNAVAILABLE = "UNAVAILABLE"

# Methodology constant (documented; surfaced in provenance.method_config): a timeframe's last CLOSED candle is
# STALE once its age exceeds STALE_TF_MULTIPLIER x the timeframe span (one missed-candle grace). Deterministic.
STALE_TF_MULTIPLIER = 2.0
TTL_SECONDS = 180                            # liveness surface -> self-expiring TTL (aligns with heartbeat TTL)
D1_TIMEFRAME = "D1"

_FORBIDDEN_FIELD_KEY_TOKENS = ("regime", "risk", "liquidity", "order_block", "smart_money", "decision", "trade",
                               "signal", "bias", "setup", "go_no_go", "confidence", "intent", "permission",
                               "gating", "conclusion", "password", "secret", "credential", "acl", "noauth")

ENABLED_ENV = "HERMES_FEED_HEALTH_PUBLISH_ENABLED"
AUTHORISED_ENV = "HERMES_FEED_HEALTH_PUBLISH_AUTHORISED"
INSTRUMENTS_ENV = "HERMES_FEED_HEALTH_PUBLISH_INSTRUMENTS"
SOURCE_NAME_ENV = "HERMES_FEED_HEALTH_SOURCE_NAME"
HALT_CODE = 101


def _utc(dt):
    if not isinstance(dt, datetime):
        raise ValueError(f"GOV-HERMES-FH-001: timestamp must be a datetime (got {type(dt).__name__})")
    return cc._fmt(cc.normalise_utc(dt))


def _assert_utc(value, field):
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"GOV-HERMES-FH-002: {field} must be a UTC ISO string ending 'Z' (got {value!r})")
    return True


def _scan_no_forbidden_field_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = str(k).lower()
            for tok in _FORBIDDEN_FIELD_KEY_TOKENS:
                if tok in kl:
                    raise ValueError(f"GOV-HERMES-FH-003: forbidden feed-health field key {k!r} (token {tok!r}) "
                                     "— HERMES publishes deterministic ingestion-health facts only; no regime/risk/"
                                     "decision/ARES interpretation")
            _scan_no_forbidden_field_keys(v)
    elif isinstance(obj, (list, tuple)):
        for x in obj:
            _scan_no_forbidden_field_keys(x)
    return True


def _assert_instrument(instrument):
    if instrument in _ALIAS_DENY or instrument != CANONICAL_INSTRUMENT:
        raise ValueError(f"GOV-HERMES-FH-004: instrument {instrument!r} not allowed (canonical XAU_USD only; no XAUUSD alias)")


def feed_health_key(instrument):
    _assert_instrument(instrument)
    return f"hermes:{CONTRACT_NAME}:{CANONICAL_INSTRUMENT}:{SCHEMA_VERSION}"


def stale_threshold_seconds(tf):
    if tf not in cc.TF_SECONDS:
        raise ValueError(f"GOV-HERMES-FH-005: unknown timeframe {tf!r}")
    return STALE_TF_MULTIPLIER * cc.TF_SECONDS[tf]


def is_d1_derived_tf(tf):
    return tf == D1_TIMEFRAME


def timeframe_health(*, tf, generated_at_utc, last_candle_close_utc, d1_latest_green=False):
    """Deterministic per-timeframe candle health. D1 stays GATED (never RED) until D1 latest GREEN. A missing
    last-close -> RED_MISSING; age beyond the stale threshold -> AMBER_STALE; otherwise GREEN. Pure; no I/O."""
    if tf not in cc.TF_SECONDS:
        raise ValueError(f"GOV-HERMES-FH-005: unknown timeframe {tf!r}")
    now = cc.normalise_utc(generated_at_utc)
    if is_d1_derived_tf(tf) and not d1_latest_green:
        return {"timeframe": tf, "last_candle_utc": (_utc(last_candle_close_utc) if last_candle_close_utc else None),
                "age_seconds": None, "status": STATUS_GATED}
    if last_candle_close_utc is None:
        return {"timeframe": tf, "last_candle_utc": None, "age_seconds": None, "status": STATUS_RED_MISSING}
    close = cc.normalise_utc(last_candle_close_utc)
    age = int((now - close).total_seconds())
    status = STATUS_AMBER_STALE if age > stale_threshold_seconds(tf) else STATUS_GREEN
    return {"timeframe": tf, "last_candle_utc": _utc(close), "age_seconds": age, "status": status}


def _aggregate_status(*, connectivity, tf_statuses):
    """Deterministic aggregate. Order: UNKNOWN source -> UNKNOWN_NEEDS_PROBE; OFFLINE or any non-gated RED ->
    RED_MISSING; DEGRADED or any AMBER -> AMBER_STALE; else GREEN. GATED (D1) alone never forces RED/AMBER."""
    if connectivity == CONN_UNKNOWN:
        return STATUS_UNKNOWN
    non_gated = [s for s in tf_statuses if s != STATUS_GATED]
    if connectivity == CONN_OFFLINE or any(s == STATUS_RED_MISSING for s in non_gated):
        return STATUS_RED_MISSING
    if connectivity == CONN_DEGRADED or any(s == STATUS_AMBER_STALE for s in non_gated):
        return STATUS_AMBER_STALE
    return STATUS_GREEN


def _freshness_for(status):
    if status == STATUS_GREEN:
        return FRESHNESS_FRESH
    if status == STATUS_AMBER_STALE:
        return FRESHNESS_STALE
    return FRESHNESS_UNAVAILABLE


def build_feed_health_contract(*, instrument, generated_at_utc, source_name, connectivity, timeframes,
                               last_candle_close_utc_by_tf, last_tick_utc=None, last_quote_utc=None,
                               d1_latest_green=False, source_dependencies=None, fault_counters=None, notes=None):
    """hermes:feed_health:XAU_USD:v1 payload — DETERMINISTIC ingestion-health FACTS. Pure; no I/O.
    Missing/stale are EXPLICIT (never hidden). deterministic_only=true; no regime/risk/decision/ARES fields."""
    _assert_instrument(instrument)
    if connectivity not in SOURCE_CONNECTIVITY_STATES:
        raise ValueError(f"GOV-HERMES-FH-006: connectivity {connectivity!r} not in {SOURCE_CONNECTIVITY_STATES}")
    tfs = tuple(timeframes)
    if not tfs:
        raise ValueError("GOV-HERMES-FH-007: timeframes must be a non-empty ordered list")
    per_tf, last_by_tf, age_by_tf = {}, {}, {}
    missing, stale, healthy, gated = [], [], [], []
    for tf in tfs:
        h = timeframe_health(tf=tf, generated_at_utc=generated_at_utc,
                             last_candle_close_utc=last_candle_close_utc_by_tf.get(tf), d1_latest_green=d1_latest_green)
        per_tf[tf] = h
        last_by_tf[tf] = h["last_candle_utc"]
        age_by_tf[tf] = h["age_seconds"]
        {STATUS_RED_MISSING: missing, STATUS_AMBER_STALE: stale,
         STATUS_GREEN: healthy, STATUS_GATED: gated}[h["status"]].append(tf)
    status = _aggregate_status(connectivity=connectivity, tf_statuses=[h["status"] for h in per_tf.values()])
    faults = dict(fault_counters or {})
    fault = {"state": "FAULTS_PRESENT" if any(int(v) > 0 for v in faults.values()) else "NONE", "counters": faults}
    payload = {
        "publisher": PUBLISHER, "schema_version": SCHEMA_VERSION, "contract": f"{CONTRACT_NAME}:{SCHEMA_VERSION}",
        "instrument": CANONICAL_INSTRUMENT, "canonical_instrument": CANONICAL_INSTRUMENT,
        "generated_at_utc": _utc(generated_at_utc),
        "source": {"name": str(source_name) if source_name else "UNKNOWN", "connectivity": connectivity},
        "status": status,
        "fault": fault,
        "freshness": _freshness_for(status),
        "last_tick_utc": (_utc(last_tick_utc) if last_tick_utc else None),
        "last_quote_utc": (_utc(last_quote_utc) if last_quote_utc else None),
        "last_candle_utc_by_tf": last_by_tf,
        "age_seconds_by_tf": age_by_tf,
        "timeframes": list(tfs),
        "missing_timeframes": missing,
        "stale_timeframes": stale,
        "healthy_timeframes": healthy,
        "gated_timeframes": gated,
        "per_timeframe_health": per_tf,
        "source_dependencies": list(source_dependencies) if source_dependencies else [],
        "publisher_identity": "hermes-signal",
        "ttl_seconds": TTL_SECONDS,
        "provenance": {"deterministic_only": True, "source_timeframes": list(tfs),
                       "method_config": {"stale_tf_multiplier": STALE_TF_MULTIPLIER,
                                         "stale_threshold_seconds_by_tf": {tf: stale_threshold_seconds(tf) for tf in tfs},
                                         "d1_latest_green": bool(d1_latest_green)}},
        "notes": list(notes) if notes else [],
        "deterministic_only": True,
    }
    validate_feed_health_contract(payload)
    return payload


def validate_feed_health_contract(p):
    if p.get("publisher") != PUBLISHER or p.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("GOV-HERMES-FH-010: feed-health publisher/schema_version must be HERMES/v1")
    if p.get("contract") != f"{CONTRACT_NAME}:{SCHEMA_VERSION}":
        raise ValueError("GOV-HERMES-FH-011: feed-health contract must be feed_health:v1")
    if p.get("instrument") != CANONICAL_INSTRUMENT or p.get("canonical_instrument") != CANONICAL_INSTRUMENT:
        raise ValueError("GOV-HERMES-FH-012: feed-health instrument must be XAU_USD (no alias/non-XAU)")
    if p.get("status") not in FEED_HEALTH_STATUSES:
        raise ValueError("GOV-HERMES-FH-013: feed-health status not in governed vocab")
    if (p.get("source") or {}).get("connectivity") not in SOURCE_CONNECTIVITY_STATES:
        raise ValueError("GOV-HERMES-FH-014: source.connectivity not in governed vocab")
    if p.get("deterministic_only") is not True:
        raise ValueError("GOV-HERMES-FH-015: feed-health payload must be deterministic_only=true")
    if not isinstance(p.get("ttl_seconds"), int) or p["ttl_seconds"] <= 0:
        raise ValueError("GOV-HERMES-FH-016: ttl_seconds must be a positive int (liveness/freshness metadata)")
    _assert_utc(p.get("generated_at_utc"), "generated_at_utc")
    if not isinstance(p.get("timeframes"), list) or not p["timeframes"]:
        raise ValueError("GOV-HERMES-FH-017: timeframes must be a non-empty list")
    _scan_no_forbidden_field_keys(p)
    return True


# --------------------------------------------------------------------------- snapshot collector (READ-ONLY)
def collect_feed_health_snapshot(redis_client, *, timeframes, generated_at_utc, source_name=None,
                                 d1_latest_green=False, source_dependencies=None):
    """Build the builder kwargs from HERMES-owned Redis surfaces via an INJECTED client. READ-ONLY (GET only) —
    NEVER writes, NO I/O at import. Derives per-tf last CLOSED-candle close from the governed candle latest keys
    (`hermes:candles:XAU_USD:{tf}:latest:v1`) and connectivity from the publisher heartbeat. Missing/stale are
    left EXPLICIT (a missing latest key -> last_candle_close None -> RED_MISSING / GATED for D1)."""
    import json
    last_close = {}
    for tf in timeframes:
        raw = redis_client.get(f"hermes:candles:{CANONICAL_INSTRUMENT}:{tf}:latest:v1")
        close = None
        if raw:
            e = json.loads(raw)
            d = e.get("data", {})
            if e.get("status") == "OK" and d.get("timestamp_utc") and d.get("is_closed") is not False:
                open_dt = datetime.strptime(d["timestamp_utc"][:-1], cc._UTC_MS).replace(tzinfo=timezone.utc)
                close = open_dt + timedelta(seconds=cc.TF_SECONDS[tf])   # last CLOSED candle close = open + span
        last_close[tf] = close
    hb_raw = redis_client.get("hermes:publisher:heartbeat:v1")
    connectivity = CONN_UNKNOWN if not hb_raw else (
        CONN_CONNECTED if json.loads(hb_raw).get("status") == "OK" else CONN_DEGRADED)
    return {"instrument": CANONICAL_INSTRUMENT, "generated_at_utc": generated_at_utc, "source_name": source_name,
            "connectivity": connectivity, "timeframes": list(timeframes), "last_candle_close_utc_by_tf": last_close,
            "last_tick_utc": None, "last_quote_utc": None, "d1_latest_green": d1_latest_green,
            "source_dependencies": source_dependencies}


# --------------------------------------------------------------------------- publisher foundation (DISABLED)
def parse_feed_health_instruments(raw):
    if raw is None or not str(raw).strip():
        raise ValueError(f"GOV-HERMES-FH-020: {INSTRUMENTS_ENV} required and non-empty (fail-closed)")
    items = [x.strip() for x in str(raw).split(",") if x.strip()]
    if not items:
        raise ValueError(f"GOV-HERMES-FH-020: {INSTRUMENTS_ENV} required and non-empty (fail-closed)")
    for inst in items:
        if inst == "XAUUSD" or inst != CANONICAL_INSTRUMENT:
            raise ValueError(f"GOV-HERMES-FH-021: instrument {inst!r} not allowed (canonical XAU_USD only; no XAUUSD/non-XAU)")
    return frozenset({CANONICAL_INSTRUMENT})


class DisabledFeedHealthPublisher:
    enabled = False

    def status(self):
        return {"enabled": False}


class FeedHealthPublisher:
    enabled = True

    def __init__(self, *, allowed_instruments, source_name):
        if frozenset(allowed_instruments) != frozenset({CANONICAL_INSTRUMENT}):
            raise ValueError("GOV-HERMES-FH-021: feed-health allowlist must be exactly {XAU_USD}")
        self.allowed_instruments = frozenset(allowed_instruments)
        self.source_name = source_name

    def status(self):
        return {"enabled": True, "instruments": sorted(self.allowed_instruments), "source_name": self.source_name}

    def build(self, **kw):
        return build_feed_health_contract(**kw)

    def key(self, instrument):
        return feed_health_key(instrument)


def build_feed_health_publisher_from_env():
    """DEFAULT DISABLED -> DisabledFeedHealthPublisher (no-op, no Redis client, no I/O). ENABLED without
    AUTHORISED -> SystemExit(101). ENABLED + AUTHORISED -> FeedHealthPublisher (payloads only, NO Redis I/O).
    No hidden defaults; NO Redis/SQL/network at import or here."""
    from env_config import get_env, get_env_bool   # lazy; HERMES-owned config only
    if not get_env_bool(ENABLED_ENV, False):
        return DisabledFeedHealthPublisher()
    if not get_env_bool(AUTHORISED_ENV, False):
        raise SystemExit(HALT_CODE)
    instruments = parse_feed_health_instruments(get_env(INSTRUMENTS_ENV, default=None))
    source_name = get_env(SOURCE_NAME_ENV, default="UNKNOWN") or "UNKNOWN"
    return FeedHealthPublisher(allowed_instruments=instruments, source_name=source_name)
