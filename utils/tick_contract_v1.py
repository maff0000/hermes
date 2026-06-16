"""HERMES Redis tick contract v1 (DESIGN-ONLY builder + validator).
WO-HELM-HERMES-REDIS-TICK-CONTRACT-DESIGN-0001.

Pure builder/validator/freshness logic for the HERMES-owned RAW-TICK Redis contract that
external standalone consumers (Falcon / Falcon-structure) read. HERMES owns market truth ONLY —
this contract carries raw ticks, NOT structure/regime/strategy/cockpit interpretation. There is
NO Redis I/O here; this WO does NOT activate any publisher. ALL timestamps UTC (ms precision).

Canonical keys:
  hermes:ticks:{instrument}:latest:v1   — per-instrument hot-path consumer key (canonical)
  hermes:ticks:latest:v1                — aggregate discovery/dashboard/cockpit support only

seq rule: HERMES does NOT have a reliable real-time monotonic tick sequence on the live publish
path. The stored-row tradingSignals.ticks.seq is a STORED-ROW index (gaps are unrecoverable; it is
NOT a market-completeness proof). Therefore the contract `seq` is NULL in v1 and consumers MUST NOT
infer tick completeness from seq.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone

SCHEMA_VERSION = "v1"
SERVICE = "HERMES"
DOMAIN = "ticks"
CONTRACT = "hermes.ticks.latest"
CONTRACT_VERSION = "v1"
TTL_SECONDS = 5
REDIS_EX_SECONDS = 10            # documented Redis EX for the (later) publisher; NOT applied here
FRESH_MAX_S = 5
DEGRADED_MAX_S = 15
DERIVATION = "NONE_RAW_TICK"
SOURCE_CONTRACT = "HERMES_TICK_SOURCE_V1"
INSTRUMENT_REGISTRY_VERSION = "v1"

FRESHNESS_STATES = ("FRESH", "STALE", "DEGRADED", "UNAVAILABLE")
STATUSES = ("OK", "WARN", "BLOCK", "ERROR", "UNAVAILABLE")
# fields a HERMES market-truth tick contract must NEVER carry (consumer-owned interpretation)
_FORBIDDEN_TOKENS = ("structure", "choch", "bos", "order_block", "regime", "signal", "setup",
                     "confidence", "permission", "risk", "compression", "break_quality",
                     "support", "resistance", "cockpit", "projection")
_AGGREGATE_KEY = "hermes:ticks:latest:v1"


def canonical_key(instrument: str) -> str:
    if not instrument or not isinstance(instrument, str):
        raise ValueError("GOV-TICK-CONTRACT-001: instrument required (fail-loud)")
    return f"hermes:ticks:{instrument}:latest:v1"


def aggregate_key() -> str:
    return _AGGREGATE_KEY


def _fmt(dt: datetime) -> str:
    """UTC ISO-8601 with millisecond precision and trailing Z."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def classify_freshness(age_seconds: float, source_warning: bool = False):
    """(freshness_state, status, reason_codes) from tick age (publish_time - source_received).
    Freshness is independent of completeness. Honest: never launder stale to fresh."""
    if age_seconds is None or age_seconds < 0:
        return "UNAVAILABLE", "UNAVAILABLE", ["NO_VALID_SOURCE_TICK"]
    if age_seconds > DEGRADED_MAX_S:
        return "STALE", "WARN", ["TICK_STALE"]
    if age_seconds > FRESH_MAX_S or source_warning:
        codes = ["TICK_DEGRADED"] + (["SOURCE_WARNING"] if source_warning else [])
        return "DEGRADED", "WARN", codes
    return "FRESH", "OK", []


def build_tick_contract(*, instrument, source_received_at_utc: datetime, generated_at_utc: datetime,
                        bid: float, ask: float, source: str = "oanda", source_warning: bool = False,
                        runtime_instance=None):
    """Build the per-instrument tick contract envelope. seq is ALWAYS null (no reliable real-time
    monotonic seq; stored-row seq is not a completeness proof). Fail-loud on malformed bid/ask."""
    key = canonical_key(instrument)
    if bid is None or ask is None:
        raise ValueError("GOV-TICK-CONTRACT-002: bid/ask required (fail-loud)")
    bid, ask = float(bid), float(ask)
    if bid <= 0 or ask <= 0 or ask < bid:
        raise ValueError(f"GOV-TICK-CONTRACT-003: invalid bid/ask ({bid}/{ask}) (fail-loud)")
    mid = round((bid + ask) / 2.0, 6)
    spread = round(ask - bid, 6)
    age = (generated_at_utc - source_received_at_utc).total_seconds()
    freshness, status, reason_codes = classify_freshness(age, source_warning)
    valid_until = generated_at_utc + timedelta(seconds=TTL_SECONDS)
    return {
        "schema_version": SCHEMA_VERSION, "service": SERVICE, "domain": DOMAIN,
        "contract": CONTRACT, "contract_version": CONTRACT_VERSION, "key": key,
        "generated_at_utc": _fmt(generated_at_utc), "valid_until_utc": _fmt(valid_until),
        "ttl_seconds": TTL_SECONDS, "freshness_state": freshness, "status": status,
        "reason_codes": reason_codes,
        "provenance": {"publisher": "HERMES", "source": source, "source_contract": SOURCE_CONTRACT,
                       "source_received_at_utc": _fmt(source_received_at_utc),
                       "published_at_utc": _fmt(generated_at_utc),
                       "instrument_registry_version": INSTRUMENT_REGISTRY_VERSION,
                       "runtime_instance": runtime_instance, "derivation": DERIVATION},
        "data": {"instrument": instrument, "received_at_utc": _fmt(source_received_at_utc),
                 "bid": bid, "ask": ask, "mid": mid, "spread": spread, "source": source,
                 "seq": None, "contract_version": CONTRACT_VERSION},
    }


def build_unavailable(*, instrument, generated_at_utc: datetime, reason_codes):
    """UNAVAILABLE envelope — no valid source tick / malformed / missing-disabled instrument /
    Redis write impossible. data carries the instrument only; no price fields."""
    valid_until = generated_at_utc + timedelta(seconds=TTL_SECONDS)
    return {
        "schema_version": SCHEMA_VERSION, "service": SERVICE, "domain": DOMAIN,
        "contract": CONTRACT, "contract_version": CONTRACT_VERSION,
        "key": canonical_key(instrument), "generated_at_utc": _fmt(generated_at_utc),
        "valid_until_utc": _fmt(valid_until), "ttl_seconds": TTL_SECONDS,
        "freshness_state": "UNAVAILABLE", "status": "UNAVAILABLE",
        "reason_codes": list(reason_codes) or ["NO_VALID_SOURCE_TICK"],
        "provenance": {"publisher": "HERMES", "source": None, "source_contract": SOURCE_CONTRACT,
                       "source_received_at_utc": None, "published_at_utc": _fmt(generated_at_utc),
                       "instrument_registry_version": INSTRUMENT_REGISTRY_VERSION,
                       "runtime_instance": None, "derivation": DERIVATION},
        "data": {"instrument": instrument, "received_at_utc": None, "bid": None, "ask": None,
                 "mid": None, "spread": None, "source": None, "seq": None,
                 "contract_version": CONTRACT_VERSION},
    }


def build_aggregate_discovery(*, instruments, generated_at_utc: datetime):
    """Aggregate DISCOVERY envelope (hermes:ticks:latest:v1) — catalog of per-instrument keys for
    dashboards/cockpit discovery ONLY. NOT a price tick; carries no bid/ask. Consumers hot-path
    read the per-instrument canonical keys, not this."""
    valid_until = generated_at_utc + timedelta(seconds=TTL_SECONDS)
    return {
        "schema_version": SCHEMA_VERSION, "service": SERVICE, "domain": DOMAIN,
        "contract": CONTRACT, "contract_version": CONTRACT_VERSION, "key": _AGGREGATE_KEY,
        "generated_at_utc": _fmt(generated_at_utc), "valid_until_utc": _fmt(valid_until),
        "ttl_seconds": TTL_SECONDS, "freshness_state": "FRESH", "status": "OK", "reason_codes": [],
        "provenance": {"publisher": "HERMES", "source": None, "source_contract": SOURCE_CONTRACT,
                       "source_received_at_utc": None, "published_at_utc": _fmt(generated_at_utc),
                       "instrument_registry_version": INSTRUMENT_REGISTRY_VERSION,
                       "runtime_instance": None, "derivation": DERIVATION},
        "data": {"instruments": list(instruments), "count": len(instruments),
                 "per_instrument_key_pattern": "hermes:ticks:{instrument}:latest:v1",
                 "purpose": "DISCOVERY_DASHBOARD_COCKPIT_ONLY", "contract_version": CONTRACT_VERSION},
    }


_UTC_MS = "%Y-%m-%dT%H:%M:%S.%f"


def _is_utc_ms(s: str) -> bool:
    if not isinstance(s, str) or not s.endswith("Z"):
        return False
    try:
        datetime.strptime(s[:-1], _UTC_MS)
        return True
    except ValueError:
        return False


_ENVELOPE_KEYS = ("schema_version", "service", "domain", "contract", "contract_version", "key",
                  "generated_at_utc", "valid_until_utc", "ttl_seconds", "freshness_state",
                  "status", "reason_codes", "provenance", "data")


def validate_tick_contract(payload: dict):
    """Fail-loud governance validation. Returns True or raises ValueError(GOV-TICK-CONTRACT-*)."""
    missing = [k for k in _ENVELOPE_KEYS if k not in payload]
    if missing:
        raise ValueError(f"GOV-TICK-CONTRACT-010: missing envelope fields {missing} (fail-loud)")
    if payload["service"] != "HERMES" or payload["provenance"].get("publisher") != "HERMES":
        raise ValueError("GOV-TICK-CONTRACT-011: publisher/service must be HERMES (HERMES owns this contract)")
    if payload["provenance"].get("derivation") != DERIVATION:
        raise ValueError("GOV-TICK-CONTRACT-012: derivation must be NONE_RAW_TICK (raw market truth)")
    if payload["freshness_state"] not in FRESHNESS_STATES:
        raise ValueError(f"GOV-TICK-CONTRACT-013: bad freshness_state {payload['freshness_state']}")
    if payload["status"] not in STATUSES:
        raise ValueError(f"GOV-TICK-CONTRACT-014: bad status {payload['status']}")
    if payload["ttl_seconds"] != TTL_SECONDS:
        raise ValueError(f"GOV-TICK-CONTRACT-015: ttl_seconds must be {TTL_SECONDS}")
    for ts in (payload["generated_at_utc"], payload["valid_until_utc"]):
        if not _is_utc_ms(ts):
            raise ValueError(f"GOV-TICK-CONTRACT-016: non-UTC-ms timestamp {ts}")
    g = datetime.strptime(payload["generated_at_utc"][:-1], _UTC_MS)
    v = datetime.strptime(payload["valid_until_utc"][:-1], _UTC_MS)
    if abs((v - g).total_seconds() - TTL_SECONDS) > 0.001:
        raise ValueError("GOV-TICK-CONTRACT-017: valid_until_utc must equal generated_at_utc + ttl_seconds")
    if payload["key"] == _AGGREGATE_KEY:
        # discovery catalog: validate it is a catalog, not a price tick
        if "instruments" not in payload["data"] or "count" not in payload["data"]:
            raise ValueError("GOV-TICK-CONTRACT-023: aggregate key must carry instruments+count (discovery)")
        if any(k in payload["data"] for k in ("bid", "ask", "mid", "spread")):
            raise ValueError("GOV-TICK-CONTRACT-024: aggregate discovery must not carry price fields")
        _scan_forbidden(payload)
        return True
    inst = payload["data"]["instrument"]
    if payload["key"] != canonical_key(inst):
        raise ValueError(f"GOV-TICK-CONTRACT-018: key {payload['key']} inconsistent with instrument {inst}")
    if payload["data"].get("seq") is not None:
        raise ValueError("GOV-TICK-CONTRACT-019: seq must be null (not a completeness proof)")
    d = payload["data"]
    if payload["freshness_state"] != "UNAVAILABLE":
        b, a, m, sp = d["bid"], d["ask"], d["mid"], d["spread"]
        if None in (b, a, m, sp) or b <= 0 or a <= 0 or a < b:
            raise ValueError(f"GOV-TICK-CONTRACT-020: invalid price block bid={b} ask={a}")
        if abs(m - (b + a) / 2.0) > 1e-6 or abs(sp - (a - b)) > 1e-6:
            raise ValueError("GOV-TICK-CONTRACT-021: mid/spread inconsistent with bid/ask")
    _scan_forbidden(payload)
    return True


def _scan_forbidden(o):
    """Fail loud if any key carries consumer-owned interpretation (structure/regime/signal/...)."""
    if isinstance(o, dict):
        for k, val in o.items():
            kl = str(k).lower()
            for tok in _FORBIDDEN_TOKENS:
                if tok in kl:
                    raise ValueError(f"GOV-TICK-CONTRACT-022: forbidden interpretive field '{k}' "
                                     "(HERMES owns raw market truth only, not structure/regime/etc)")
            _scan_forbidden(val)
    elif isinstance(o, list):
        for x in o:
            _scan_forbidden(x)
    return True
