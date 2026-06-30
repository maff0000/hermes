"""HERMES governed DETERMINISTIC SESSION-FACT Redis contract + publisher foundation v1.
WO-HELM-HERMES-MARKET-MAP-CONTAINMENT-SESSION-LEVELS-INVENTORY-0001.

CODE/DESIGN ONLY. Governed v1 contract for DETERMINISTIC session/market-time FACTS (current session by fixed UTC
windows, session open/close times) so the host-loose market_map.py can be contained inside HERMES and migrated off
the legacy hermes:market_map:* keys. NO Redis I/O, NO network, NO SQL, NO file writes at import OR anywhere.
Disabled by default; ENABLED-without-AUTHORISED -> SystemExit(101). UTC-only. NO auth. NO regime/risk/decision fields.

Ownership: a SESSION FACT is HERMES-owned ONLY when it is a deterministic calendar/UTC-window fact (which session
is open now; its open/close times) — NOT a risk/event interpretation (those are ARES). Future key:
  hermes:sessions:XAU_USD:v1   (not published in this WO).
"""
from __future__ import annotations
from datetime import datetime, timezone

from utils import candle_contract_v1 as cc

SCHEMA_VERSION = "v1"
PUBLISHER = "HERMES"
SESSION_SET_VERSION = "v1"
CANONICAL_INSTRUMENT = "XAU_USD"
_ALIAS_DENY = ("XAUUSD",)
# Deterministic UTC session windows recognised (names only; the actual windows come from governed config at runtime).
SESSION_NAMES = ("asia", "london", "newyork", "overlap_ldn_ny", "off_hours")
# Interpretive / ARES-owned tokens forbidden as field keys.
_FORBIDDEN_FIELD_KEY_TOKENS = ("regime", "risk", "liquidity", "order_block", "smart_money", "decision", "trade",
                               "signal", "bias", "setup", "go_no_go", "confidence", "intent", "permission",
                               "password", "secret", "credential", "acl", "noauth")

ENABLED_ENV = "HERMES_SESSION_PUBLISH_ENABLED"
AUTHORISED_ENV = "HERMES_SESSION_PUBLISH_AUTHORISED"
INSTRUMENTS_ENV = "HERMES_SESSION_PUBLISH_INSTRUMENTS"
HALT_CODE = 101


def _utc(dt):
    if not isinstance(dt, datetime):
        raise ValueError(f"GOV-HERMES-SESS-001: timestamp must be a datetime (got {type(dt).__name__})")
    return cc._fmt(cc.normalise_utc(dt))


def _assert_utc(value, field):
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"GOV-HERMES-SESS-002: {field} must be a UTC ISO string ending 'Z' (got {value!r})")
    return True


def _scan_no_forbidden_field_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = str(k).lower()
            for tok in _FORBIDDEN_FIELD_KEY_TOKENS:
                if tok in kl:
                    raise ValueError(f"GOV-HERMES-SESS-003: forbidden session field key {k!r} (token {tok!r}) "
                                     "— HERMES sessions are deterministic UTC facts only; no regime/risk/decision fields")
            _scan_no_forbidden_field_keys(v)
    elif isinstance(obj, (list, tuple)):
        for x in obj:
            _scan_no_forbidden_field_keys(x)
    return True


def sessions_key(instrument):
    if instrument in _ALIAS_DENY or instrument != CANONICAL_INSTRUMENT:
        raise ValueError(f"GOV-HERMES-SESS-004: instrument {instrument!r} not allowed (canonical XAU_USD only; no XAUUSD alias)")
    return f"hermes:sessions:{CANONICAL_INSTRUMENT}:{SCHEMA_VERSION}"


def build_session_contract(*, instrument, generated_at_utc, current_session, sessions, freshness_state="FRESH"):
    """hermes:sessions:XAU_USD:v1 payload — DETERMINISTIC session facts. `sessions` maps session name ->
    {open_time_utc, close_time_utc}; `current_session` is the deterministic session open now. Pure; no I/O."""
    if instrument in _ALIAS_DENY or instrument != CANONICAL_INSTRUMENT:
        raise ValueError(f"GOV-HERMES-SESS-004: instrument {instrument!r} not allowed (canonical XAU_USD only)")
    if current_session not in SESSION_NAMES:
        raise ValueError(f"GOV-HERMES-SESS-005: current_session {current_session!r} not in {SESSION_NAMES}")
    payload = {
        "publisher": PUBLISHER, "schema_version": SCHEMA_VERSION, "contract_version": "v1",
        "instrument": CANONICAL_INSTRUMENT,
        "generated_at_utc": _utc(generated_at_utc),
        "session_set_version": SESSION_SET_VERSION,
        "source_inputs": ["hermes_market_hours", "trading_windows (governed UTC session config)"],
        "current_session": current_session,
        "sessions": dict(sessions),
        "freshness_state": freshness_state,
        "deterministic_only": True,
    }
    validate_session_contract(payload)
    return payload


def validate_session_contract(p):
    if p.get("publisher") != PUBLISHER or p.get("contract_version") != "v1":
        raise ValueError("GOV-HERMES-SESS-010: session publisher/contract_version must be HERMES/v1")
    if p.get("instrument") != CANONICAL_INSTRUMENT:
        raise ValueError("GOV-HERMES-SESS-011: session instrument must be XAU_USD (no alias/non-XAU)")
    if p.get("deterministic_only") is not True:
        raise ValueError("GOV-HERMES-SESS-012: session payload must be deterministic_only=true")
    _assert_utc(p.get("generated_at_utc"), "generated_at_utc")
    if p.get("current_session") not in SESSION_NAMES:
        raise ValueError("GOV-HERMES-SESS-013: current_session not in vocab")
    if not isinstance(p.get("sessions"), dict) or not p["sessions"]:
        raise ValueError("GOV-HERMES-SESS-014: sessions must be a non-empty dict")
    _scan_no_forbidden_field_keys(p)
    return True


# --------------------------------------------------------------------------- publisher foundation (DISABLED)
def parse_session_instruments(raw):
    if raw is None or not str(raw).strip():
        raise ValueError(f"GOV-HERMES-SESS-020: {INSTRUMENTS_ENV} required and non-empty (fail-closed)")
    items = [x.strip() for x in str(raw).split(",") if x.strip()]
    if not items:
        raise ValueError(f"GOV-HERMES-SESS-020: {INSTRUMENTS_ENV} required and non-empty (fail-closed)")
    for inst in items:
        if inst == "XAUUSD" or inst != CANONICAL_INSTRUMENT:
            raise ValueError(f"GOV-HERMES-SESS-021: instrument {inst!r} not allowed (canonical XAU_USD only; no XAUUSD/non-XAU)")
    return frozenset({CANONICAL_INSTRUMENT})


class DisabledSessionPublisher:
    enabled = False

    def status(self):
        return {"enabled": False}


class SessionPublisher:
    enabled = True

    def __init__(self, *, allowed_instruments):
        if frozenset(allowed_instruments) != frozenset({CANONICAL_INSTRUMENT}):
            raise ValueError("GOV-HERMES-SESS-021: session allowlist must be exactly {XAU_USD}")
        self.allowed_instruments = frozenset(allowed_instruments)

    def status(self):
        return {"enabled": True, "instruments": sorted(self.allowed_instruments)}

    def build(self, **kw):
        return build_session_contract(**kw)

    def key(self, instrument):
        return sessions_key(instrument)


def build_session_publisher_from_env():
    """DEFAULT DISABLED -> DisabledSessionPublisher. ENABLED without AUTHORISED -> SystemExit(101).
    ENABLED + AUTHORISED -> SessionPublisher (payloads only, no Redis I/O). No hidden defaults; no I/O at import."""
    from env_config import get_env, get_env_bool   # lazy
    if not get_env_bool(ENABLED_ENV, False):
        return DisabledSessionPublisher()
    if not get_env_bool(AUTHORISED_ENV, False):
        raise SystemExit(HALT_CODE)
    instruments = parse_session_instruments(get_env(INSTRUMENTS_ENV, default=None))
    return SessionPublisher(allowed_instruments=instruments)
