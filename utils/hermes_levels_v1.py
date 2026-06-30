"""HERMES governed DETERMINISTIC LEVEL-FACT Redis contract + publisher foundation v1.
WO-HELM-HERMES-MARKET-MAP-CONTAINMENT-SESSION-LEVELS-INVENTORY-0001.

CODE/DESIGN ONLY. Governed v1 contract for DETERMINISTIC LEVEL FACTS — session high/low ranges, prior day/week
high/low, range midpoints, mathematically-derived pivots, deterministic Fibonacci anchors, deterministic H/L
sweep facts. NO Redis I/O / network / SQL / file writes at import OR anywhere. Disabled by default;
ENABLED-without-AUTHORISED -> SystemExit(101). UTC-only. NO auth. NO regime/risk/decision/interpretive fields.

Ownership: deterministic levels are HERMES-owned; interpretive levels (liquidity blocks, risk-gated order blocks,
smart-money meaning) are ARES-owned (REJECTED). Scoped per-key: hermes:levels:XAU_USD:{scope}:v1 where scope in
(session, intraday, daily, weekly). The DAILY/WEEKLY scopes are D1-DERIVED (prior-day/week H/L, ADR) and remain
GATED until D1 latest is GREEN. Not published in this WO.
"""
from __future__ import annotations
from datetime import datetime, timezone

from utils import candle_contract_v1 as cc

SCHEMA_VERSION = "v1"
PUBLISHER = "HERMES"
LEVEL_SET_VERSION = "v1"
CANONICAL_INSTRUMENT = "XAU_USD"
_ALIAS_DENY = ("XAUUSD",)
LEVEL_SCOPES = ("session", "intraday", "daily", "weekly")
_D1_DERIVED_SCOPES = ("daily", "weekly")        # prior-day/week H/L + ADR -> D1-derived -> gated until D1 GREEN
_FORBIDDEN_FIELD_KEY_TOKENS = ("regime", "risk", "liquidity", "order_block", "smart_money", "decision", "trade",
                               "signal", "bias", "setup", "go_no_go", "confidence", "intent", "permission",
                               "password", "secret", "credential", "acl", "noauth")

ENABLED_ENV = "HERMES_LEVEL_PUBLISH_ENABLED"
AUTHORISED_ENV = "HERMES_LEVEL_PUBLISH_AUTHORISED"
INSTRUMENTS_ENV = "HERMES_LEVEL_PUBLISH_INSTRUMENTS"
SCOPES_ENV = "HERMES_LEVEL_PUBLISH_SCOPES"
D1_AUTHORISED_ENV = "HERMES_LEVEL_D1_AUTHORISED"   # D1-derived (daily/weekly) levels gated until D1 latest GREEN
HALT_CODE = 101


def _utc(dt):
    if not isinstance(dt, datetime):
        raise ValueError(f"GOV-HERMES-LVL-001: timestamp must be a datetime (got {type(dt).__name__})")
    return cc._fmt(cc.normalise_utc(dt))


def _assert_utc(value, field):
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"GOV-HERMES-LVL-002: {field} must be a UTC ISO string ending 'Z' (got {value!r})")
    return True


def _scan_no_forbidden_field_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = str(k).lower()
            for tok in _FORBIDDEN_FIELD_KEY_TOKENS:
                if tok in kl:
                    raise ValueError(f"GOV-HERMES-LVL-003: forbidden level field key {k!r} (token {tok!r}) "
                                     "— HERMES levels are deterministic facts only; no regime/risk/interpretive fields")
            _scan_no_forbidden_field_keys(v)
    elif isinstance(obj, (list, tuple)):
        for x in obj:
            _scan_no_forbidden_field_keys(x)
    return True


def is_d1_derived_scope(scope):
    return scope in _D1_DERIVED_SCOPES


def levels_key(instrument, scope):
    if instrument in _ALIAS_DENY or instrument != CANONICAL_INSTRUMENT:
        raise ValueError(f"GOV-HERMES-LVL-004: instrument {instrument!r} not allowed (canonical XAU_USD only; no XAUUSD alias)")
    if scope not in LEVEL_SCOPES:
        raise ValueError(f"GOV-HERMES-LVL-005: scope {scope!r} not in {LEVEL_SCOPES}")
    return f"hermes:levels:{CANONICAL_INSTRUMENT}:{scope}:{SCHEMA_VERSION}"


def build_level_contract(*, instrument, scope, generated_at_utc, levels, source_inputs=None,
                         freshness_state="FRESH", d1_latest_green=False):
    """hermes:levels:XAU_USD:{scope}:v1 payload — DETERMINISTIC level facts. A D1-derived scope (daily/weekly)
    requires d1_latest_green=True (gated). Pure; no I/O. deterministic_only=true; no regime/risk/decision fields."""
    if instrument in _ALIAS_DENY or instrument != CANONICAL_INSTRUMENT:
        raise ValueError(f"GOV-HERMES-LVL-004: instrument {instrument!r} not allowed (canonical XAU_USD only)")
    if scope not in LEVEL_SCOPES:
        raise ValueError(f"GOV-HERMES-LVL-005: scope {scope!r} not in {LEVEL_SCOPES}")
    if is_d1_derived_scope(scope) and not d1_latest_green:
        raise ValueError(f"GOV-HERMES-LVL-D1-001: scope {scope!r} is D1-derived and GATED until D1 latest is GREEN")
    payload = {
        "publisher": PUBLISHER, "schema_version": SCHEMA_VERSION, "contract_version": "v1",
        "instrument": CANONICAL_INSTRUMENT, "scope": scope,
        "generated_at_utc": _utc(generated_at_utc),
        "level_set_version": LEVEL_SET_VERSION,
        "source_inputs": list(source_inputs) if source_inputs else ["hermes:candles:XAU_USD:* (governed)"],
        "d1_derived": is_d1_derived_scope(scope),
        "levels": dict(levels),
        "freshness_state": freshness_state,
        "deterministic_only": True,
    }
    validate_level_contract(payload)
    return payload


def validate_level_contract(p):
    if p.get("publisher") != PUBLISHER or p.get("contract_version") != "v1":
        raise ValueError("GOV-HERMES-LVL-010: level publisher/contract_version must be HERMES/v1")
    if p.get("instrument") != CANONICAL_INSTRUMENT:
        raise ValueError("GOV-HERMES-LVL-011: level instrument must be XAU_USD (no alias/non-XAU)")
    if p.get("scope") not in LEVEL_SCOPES:
        raise ValueError("GOV-HERMES-LVL-012: level scope not in vocab")
    if p.get("deterministic_only") is not True:
        raise ValueError("GOV-HERMES-LVL-013: level payload must be deterministic_only=true")
    _assert_utc(p.get("generated_at_utc"), "generated_at_utc")
    if not isinstance(p.get("levels"), dict) or not p["levels"]:
        raise ValueError("GOV-HERMES-LVL-014: levels must be a non-empty dict")
    _scan_no_forbidden_field_keys(p)
    return True


# --------------------------------------------------------------------------- publisher foundation (DISABLED)
def parse_level_instruments(raw):
    if raw is None or not str(raw).strip():
        raise ValueError(f"GOV-HERMES-LVL-020: {INSTRUMENTS_ENV} required and non-empty (fail-closed)")
    items = [x.strip() for x in str(raw).split(",") if x.strip()]
    if not items:
        raise ValueError(f"GOV-HERMES-LVL-020: {INSTRUMENTS_ENV} required and non-empty (fail-closed)")
    for inst in items:
        if inst == "XAUUSD" or inst != CANONICAL_INSTRUMENT:
            raise ValueError(f"GOV-HERMES-LVL-021: instrument {inst!r} not allowed (canonical XAU_USD only; no XAUUSD/non-XAU)")
    return frozenset({CANONICAL_INSTRUMENT})


def parse_level_scopes(raw, *, allow_d1=False):
    """Explicit, fail-closed scope list. D1-derived scopes (daily/weekly) GATED unless allow_d1 (D1 latest GREEN)."""
    if raw is None or not str(raw).strip():
        raise ValueError(f"GOV-HERMES-LVL-022: {SCOPES_ENV} required and non-empty (fail-closed)")
    items = [x.strip() for x in str(raw).split(",") if x.strip()]
    if not items:
        raise ValueError(f"GOV-HERMES-LVL-022: {SCOPES_ENV} required and non-empty (fail-closed)")
    out = []
    for sc in items:
        if is_d1_derived_scope(sc) and not allow_d1:
            raise ValueError(f"GOV-HERMES-LVL-D1-001: scope {sc!r} is D1-derived and GATED until D1 latest GREEN — set "
                             f"{D1_AUTHORISED_ENV}=true only after D1 latest GREEN")
        if sc not in LEVEL_SCOPES:
            raise ValueError(f"GOV-HERMES-LVL-023: scope {sc!r} not in {LEVEL_SCOPES}")
        if sc not in out:
            out.append(sc)
    return tuple(out)


class DisabledLevelPublisher:
    enabled = False

    def status(self):
        return {"enabled": False}


class LevelPublisher:
    enabled = True

    def __init__(self, *, allowed_instruments, scopes):
        if frozenset(allowed_instruments) != frozenset({CANONICAL_INSTRUMENT}):
            raise ValueError("GOV-HERMES-LVL-021: level allowlist must be exactly {XAU_USD}")
        if not scopes:
            raise ValueError("GOV-HERMES-LVL-024: level scopes must be non-empty")
        self.allowed_instruments = frozenset(allowed_instruments)
        self.scopes = tuple(scopes)

    def status(self):
        return {"enabled": True, "instruments": sorted(self.allowed_instruments), "scopes": list(self.scopes)}

    def build(self, **kw):
        return build_level_contract(**kw)

    def key(self, instrument, scope):
        return levels_key(instrument, scope)


def build_level_publisher_from_env():
    """DEFAULT DISABLED -> DisabledLevelPublisher. ENABLED without AUTHORISED -> SystemExit(101). ENABLED +
    AUTHORISED -> LevelPublisher (payloads only, no Redis I/O). D1-derived scopes gated until
    HERMES_LEVEL_D1_AUTHORISED=true (D1 latest GREEN). No hidden defaults; no I/O at import."""
    from env_config import get_env, get_env_bool   # lazy
    if not get_env_bool(ENABLED_ENV, False):
        return DisabledLevelPublisher()
    if not get_env_bool(AUTHORISED_ENV, False):
        raise SystemExit(HALT_CODE)
    instruments = parse_level_instruments(get_env(INSTRUMENTS_ENV, default=None))
    allow_d1 = get_env_bool(D1_AUTHORISED_ENV, False)
    scopes = parse_level_scopes(get_env(SCOPES_ENV, default=None), allow_d1=allow_d1)
    return LevelPublisher(allowed_instruments=instruments, scopes=scopes)
