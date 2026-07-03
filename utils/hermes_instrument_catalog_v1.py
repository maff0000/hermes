"""HERMES governed DETERMINISTIC INSTRUMENT CATALOG contract + builder foundation v1.
WO-HELM-HERMES-INSTRUMENT-CATALOG-CONTRACT-V1-0001.

CODE/DESIGN ONLY. Governed v1 contract for DETERMINISTIC instrument-identity + contract-DISCOVERY FACTS —
canonical instrument id, inbound aliases, supported/active/gated/blocked/not-implemented timeframes, the governed
market-data SURFACES + their contract KEY NAMES + schema versions + status, D1 gating policy, legacy/deprecated
surface representation, source/provenance metadata. NO Redis I/O / network / SQL / file writes at import OR in the
builder. Disabled by default; ENABLED-without-AUTHORISED -> SystemExit(101). UTC-only. NO auth. NO regime/risk/
decision/ARES-owned interpretation.

Ownership: HERMES owns deterministic instrument identity + market-data contract discovery. Interpretive fields
(regime, risk, go/no-go, setup quality, trade permission, ARES event/liquidity/smart-money/order-block meaning,
decision/gating conclusions) are ARES-owned and REJECTED as field keys. Key: hermes:instrument_catalog:XAU_USD:v1
(versioned; XAU_USD only; XAUUSD is an INBOUND ALIAS only, never an output publish key).

Nothing is published in this WO. The builder is PURE (payloads only from a supplied governed snapshot). D1-derived
surfaces stay GATED/PENDING (never falsely ACTIVE) until D1 latest is GREEN. Contract KEY names are composed via
the existing sibling key builders (no hard-coded key strings).
"""
from __future__ import annotations
from datetime import datetime

from utils import candle_contract_v1 as cc
from utils import candle_history_v1 as hist
from utils import hermes_indicators_v1 as ind
from utils import hermes_candle_features_v1 as cf
from utils import hermes_sessions_v1 as sess
from utils import hermes_levels_v1 as lvl
from utils import hermes_feed_health_v1 as fh
from utils import hermes_control_plane_v1 as ctl
from utils import hermes_quote_tick_contract_v1 as qt   # quote contract key (code-present dark) — referenced only
from utils import tick_contract_v1 as tickc             # EXISTING tick contract key — referenced, never rebuilt

SCHEMA_VERSION = "v1"
CONTRACT_NAME = "instrument_catalog"
PUBLISHER = "HERMES"
CANONICAL_INSTRUMENT = "XAU_USD"
INBOUND_ALIASES = ("XAUUSD",)                # inbound alias ONLY — never an output publish key
_ALIAS_DENY_OUTPUT = ("XAUUSD",)
SUPPORTED_TIMEFRAMES = ("M1", "M5", "M15", "H1", "H4", "D1")
_ACTIVE_GRID_TFS = ("M1", "M5", "M15", "H1", "H4")   # D1 is gated/pending until first clean seal
LEVEL_SCOPES = ("session", "intraday", "daily", "weekly")
TTL_SECONDS = 300                            # discovery surface; self-expiring liveness TTL

# Aggregate catalog status (deterministic, fail-loud).
STATUS_GREEN = "GREEN"
STATUS_AMBER_PARTIAL = "AMBER_PARTIAL"
STATUS_GATED = "GATED"
STATUS_BLOCKED = "BLOCKED"
STATUS_NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
STATUS_UNKNOWN = "UNKNOWN_NEEDS_PROBE"
CATALOG_STATUSES = (STATUS_GREEN, STATUS_AMBER_PARTIAL, STATUS_GATED, STATUS_BLOCKED,
                    STATUS_NOT_IMPLEMENTED, STATUS_UNKNOWN)

# Per-surface status vocabulary.
SURFACE_ACTIVE = "ACTIVE"
SURFACE_GATED = "GATED"
SURFACE_BLOCKED = "BLOCKED"
SURFACE_NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
SURFACE_CODE_PRESENT_DARK = "CODE_PRESENT_DARK"      # merged/built but not activated (e.g. feed_health)
SURFACE_LEGACY = "LEGACY"
SURFACE_LEGACY_OR_PARTIAL = "LEGACY_OR_PARTIAL"
SURFACE_DEPRECATED = "DEPRECATED"
SURFACE_PENDING_FIRST_DAILY_SEAL = "PENDING_FIRST_DAILY_SEAL"
SURFACE_UNKNOWN = "UNKNOWN_NEEDS_PROBE"
SURFACE_STATUSES = (SURFACE_ACTIVE, SURFACE_GATED, SURFACE_BLOCKED, SURFACE_NOT_IMPLEMENTED,
                    SURFACE_CODE_PRESENT_DARK, SURFACE_LEGACY, SURFACE_LEGACY_OR_PARTIAL, SURFACE_DEPRECATED,
                    SURFACE_PENDING_FIRST_DAILY_SEAL, SURFACE_UNKNOWN)

_FORBIDDEN_FIELD_KEY_TOKENS = ("regime", "risk", "liquidity", "order_block", "smart_money", "decision", "trade",
                               "bias", "setup", "go_no_go", "confidence", "intent", "permission", "conclusion",
                               "password", "secret", "credential", "acl", "noauth")

ENABLED_ENV = "HERMES_INSTRUMENT_CATALOG_PUBLISH_ENABLED"
AUTHORISED_ENV = "HERMES_INSTRUMENT_CATALOG_PUBLISH_AUTHORISED"
INSTRUMENTS_ENV = "HERMES_INSTRUMENT_CATALOG_PUBLISH_INSTRUMENTS"
SOURCE_NAME_ENV = "HERMES_INSTRUMENT_CATALOG_SOURCE_NAME"
HALT_CODE = 101


def _utc(dt):
    if not isinstance(dt, datetime):
        raise ValueError(f"GOV-HERMES-IC-001: timestamp must be a datetime (got {type(dt).__name__})")
    return cc._fmt(cc.normalise_utc(dt))


def _assert_utc(value, field):
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"GOV-HERMES-IC-002: {field} must be a UTC ISO string ending 'Z' (got {value!r})")
    return True


def _scan_no_forbidden_field_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = str(k).lower()
            for tok in _FORBIDDEN_FIELD_KEY_TOKENS:
                if tok in kl:
                    raise ValueError(f"GOV-HERMES-IC-003: forbidden catalog field key {k!r} (token {tok!r}) "
                                     "— HERMES publishes deterministic discovery facts only; no regime/risk/"
                                     "decision/ARES interpretation")
            _scan_no_forbidden_field_keys(v)
    elif isinstance(obj, (list, tuple)):
        for x in obj:
            _scan_no_forbidden_field_keys(x)
    return True


def _assert_instrument(instrument):
    if instrument in _ALIAS_DENY_OUTPUT or instrument != CANONICAL_INSTRUMENT:
        raise ValueError(f"GOV-HERMES-IC-004: instrument {instrument!r} not allowed as output (canonical XAU_USD "
                         "only; XAUUSD is an inbound alias, never an output publish key)")


def instrument_catalog_key(instrument):
    _assert_instrument(instrument)
    return f"hermes:{CONTRACT_NAME}:{CANONICAL_INSTRUMENT}:{SCHEMA_VERSION}"


def _safe_key(fn, *args):
    """Compose a governed contract key via a sibling key builder; None if that surface has no key (e.g. D1
    history is BLOCKED so history_index_key rejects D1) — the None is an explicit discovery fact, not hidden."""
    try:
        return fn(*args)
    except Exception:  # noqa: BLE001 - a rejected key = surface has no governed key (represented explicitly)
        return None


def default_catalog_snapshot():
    """The governed catalog DECLARATION (current reality): which HERMES surfaces exist in code + their gating.
    ACTIVE = contract defined + expected active per governed config; D1 is gated/pending; feed_health is
    CODE_PRESENT_DARK (merged not activated); quote (PR #68) + tick (existing tick_contract_v1) are CODE_PRESENT_DARK
    (merged/present but not live/activated); legacy surfaces LEGACY/partial. This is a declaration input to the PURE
    builder, not a live probe — a future activate WO reconciles it against runtime."""
    return {
        "candle_latest": {**{tf: SURFACE_ACTIVE for tf in _ACTIVE_GRID_TFS}, "D1": SURFACE_PENDING_FIRST_DAILY_SEAL},
        "candle_history": {**{tf: SURFACE_ACTIVE for tf in _ACTIVE_GRID_TFS}, "D1": SURFACE_BLOCKED},
        "indicators": {**{tf: SURFACE_ACTIVE for tf in _ACTIVE_GRID_TFS}, "D1": SURFACE_GATED},
        "candle_features": {**{tf: SURFACE_ACTIVE for tf in _ACTIVE_GRID_TFS}, "D1": SURFACE_GATED},
        "sessions": SURFACE_ACTIVE,
        "levels": {"session": SURFACE_ACTIVE, "intraday": SURFACE_ACTIVE,
                   "daily": SURFACE_GATED, "weekly": SURFACE_GATED},
        "feed_health": SURFACE_CODE_PRESENT_DARK,
        "control_plane": SURFACE_ACTIVE,
        "quote": SURFACE_CODE_PRESENT_DARK,     # PR #68 merged inert -> code present, NOT live/activated
        "tick": SURFACE_CODE_PRESENT_DARK,      # existing tick_contract_v1 -> code present/shadow, NOT live/activated
        "d1_latest": SURFACE_PENDING_FIRST_DAILY_SEAL,
    }


def _assert_surface_status(status, ctx):
    if status not in SURFACE_STATUSES:
        raise ValueError(f"GOV-HERMES-IC-006: surface status {status!r} for {ctx} not in governed vocab")
    return status


def _aggregate_status(snapshot):
    """GREEN when every EXPECTED-ACTIVE surface (candle latest/history M1-H4, indicators/features M1-H4, sessions,
    levels session/intraday, control_plane) is ACTIVE. UNKNOWN anywhere -> UNKNOWN_NEEDS_PROBE. Otherwise
    AMBER_PARTIAL. D1 (gated/pending), quote/tick (not-impl), feed_health (dark), daily/weekly levels (gated) are
    EXPECTED non-active and do NOT degrade GREEN."""
    expected_active = []
    for tf in _ACTIVE_GRID_TFS:
        expected_active += [snapshot["candle_latest"][tf], snapshot["candle_history"][tf],
                            snapshot["indicators"][tf], snapshot["candle_features"][tf]]
    expected_active += [snapshot["sessions"], snapshot["levels"]["session"], snapshot["levels"]["intraday"],
                        snapshot["control_plane"]]
    # UNKNOWN anywhere in the whole snapshot fails loud
    all_states = list(expected_active) + [snapshot["candle_latest"]["D1"], snapshot["feed_health"],
                                          snapshot["quote"], snapshot["tick"], snapshot["d1_latest"]]
    if any(s == SURFACE_UNKNOWN for s in all_states):
        return STATUS_UNKNOWN
    if all(s == SURFACE_ACTIVE for s in expected_active):
        return STATUS_GREEN
    return STATUS_AMBER_PARTIAL


def build_instrument_catalog_contract(*, instrument, generated_at_utc, source_name, snapshot=None,
                                      source_dependencies=None, fault_counters=None, notes=None):
    """hermes:instrument_catalog:XAU_USD:v1 payload — DETERMINISTIC identity + contract-discovery FACTS. Pure;
    no I/O. Missing/gated/not-implemented are EXPLICIT. deterministic_only=true; no regime/risk/decision/ARES."""
    _assert_instrument(instrument)
    snap = snapshot if snapshot is not None else default_catalog_snapshot()
    for tf in SUPPORTED_TIMEFRAMES:
        for fam in ("candle_latest", "candle_history", "indicators", "candle_features"):
            _assert_surface_status(snap[fam][tf], f"{fam}:{tf}")
    for ctx in ("sessions", "feed_health", "control_plane", "quote", "tick", "d1_latest"):
        _assert_surface_status(snap[ctx], ctx)
    for sc in LEVEL_SCOPES:
        _assert_surface_status(snap["levels"][sc], f"levels:{sc}")

    active = [tf for tf in SUPPORTED_TIMEFRAMES if snap["candle_latest"][tf] == SURFACE_ACTIVE]
    gated = [tf for tf in SUPPORTED_TIMEFRAMES if snap["candle_latest"][tf] in (SURFACE_GATED, SURFACE_PENDING_FIRST_DAILY_SEAL)]
    blocked = [tf for tf in SUPPORTED_TIMEFRAMES if snap["candle_history"][tf] == SURFACE_BLOCKED]
    not_impl = [tf for tf in SUPPORTED_TIMEFRAMES if snap["candle_latest"][tf] == SURFACE_NOT_IMPLEMENTED]

    candle_contracts = {tf: {"latest_key": _safe_key(cc.canonical_key, CANONICAL_INSTRUMENT, tf),
                             "latest_status": snap["candle_latest"][tf],
                             "history_index_key": _safe_key(hist.history_index_key, CANONICAL_INSTRUMENT, tf),
                             "history_status": snap["candle_history"][tf]} for tf in SUPPORTED_TIMEFRAMES}
    indicator_contracts = {tf: {"key": _safe_key(ind.indicator_key, CANONICAL_INSTRUMENT, tf),
                                "status": snap["indicators"][tf]} for tf in SUPPORTED_TIMEFRAMES}
    candle_feature_contracts = {tf: {"key": _safe_key(cf.candle_features_key, CANONICAL_INSTRUMENT, tf),
                                     "status": snap["candle_features"][tf]} for tf in SUPPORTED_TIMEFRAMES}
    session_contracts = {"key": _safe_key(sess.sessions_key, CANONICAL_INSTRUMENT), "status": snap["sessions"]}
    level_contracts = {sc: {"key": _safe_key(lvl.levels_key, CANONICAL_INSTRUMENT, sc),
                            "status": snap["levels"][sc]} for sc in LEVEL_SCOPES}
    feed_health_contract = {"key": _safe_key(fh.feed_health_key, CANONICAL_INSTRUMENT), "status": snap["feed_health"]}
    # quote: NEW governed key hermes:quote:XAU_USD:v1 (PR #68). tick: EXISTING key hermes:ticks:XAU_USD:latest:v1
    # (tick_contract_v1) — referenced, NEVER a duplicate hermes:tick:* key. Both CODE_PRESENT_DARK -> key present
    # as a discovery fact but NOT a claim that the surface is live/published.
    quote_contract = {"key": _safe_key(qt.quote_key, CANONICAL_INSTRUMENT), "status": snap["quote"],
                      "live": False}
    tick_contract = {"key": _safe_key(tickc.canonical_key, CANONICAL_INSTRUMENT), "status": snap["tick"],
                     "live": False, "note": "existing governed tick surface (tick_contract_v1); referenced, not rebuilt"}
    legacy_contracts = [{"pattern": "hermes:signals:*", "status": SURFACE_LEGACY,
                         "note": "legacy signal surface — preserved, not deleted; consumer cutover is a separate WO"},
                        {"pattern": "hermes:market_map:*", "status": SURFACE_LEGACY_OR_PARTIAL,
                         "note": "legacy market_map surface FROZEN_PENDING_CONSUMER_CUTOVER — preserved, not deleted"}]
    deprecated_contracts = []

    control_plane_contracts = {"manifest_key": ctl.KEY_CONTRACT_MANIFEST, "heartbeat_key": ctl.KEY_PUBLISHER_HEARTBEAT,
                               "catalog_key": ctl.KEY_CATALOG_CANDLES, "health_key": ctl.KEY_HEALTH,
                               "status": snap["control_plane"]}

    surfaces = {"candles": {tf: snap["candle_latest"][tf] for tf in SUPPORTED_TIMEFRAMES},
                "candle_history": {tf: snap["candle_history"][tf] for tf in SUPPORTED_TIMEFRAMES},
                "indicators": {tf: snap["indicators"][tf] for tf in SUPPORTED_TIMEFRAMES},
                "candle_features": {tf: snap["candle_features"][tf] for tf in SUPPORTED_TIMEFRAMES},
                "sessions": snap["sessions"], "levels": dict(snap["levels"]),
                "feed_health": snap["feed_health"], "control_plane": snap["control_plane"],
                "quote": snap["quote"], "tick": snap["tick"]}

    d1_policy = {"anchor_utc": "22:00:00_FIXED", "dst_adjustment": False, "source": "6xH4",
                 "latest_status": snap["d1_latest"], "history_status": snap["candle_history"]["D1"],
                 "gated_derived": ["indicators:D1", "candle_features:D1", "levels:daily", "levels:weekly"],
                 "note": "D1 latest not ACTIVE until a genuine clean 6/6 H4 live seal; derived D1 surfaces gated"}

    faults = dict(fault_counters or {})
    payload = {
        "publisher": PUBLISHER, "schema_version": SCHEMA_VERSION, "contract": f"{CONTRACT_NAME}:{SCHEMA_VERSION}",
        "instrument": CANONICAL_INSTRUMENT, "canonical_instrument": CANONICAL_INSTRUMENT,
        "aliases": {"inbound": list(INBOUND_ALIASES), "output_publish_denied": list(_ALIAS_DENY_OUTPUT)},
        "generated_at_utc": _utc(generated_at_utc),
        "source": {"name": str(source_name) if source_name else "UNKNOWN"},
        "status": _aggregate_status(snap),
        "fault": {"state": "FAULTS_PRESENT" if any(int(v) > 0 for v in faults.values()) else "NONE", "counters": faults},
        "supported_timeframes": list(SUPPORTED_TIMEFRAMES),
        "active_timeframes": active, "gated_timeframes": gated, "blocked_timeframes": blocked,
        "not_implemented_timeframes": not_impl,
        "surfaces": surfaces,
        "contracts": {"candle": "candle_contract:v1", "indicators": "indicators:v1",
                      "candle_features": "candle_features:v1", "sessions": "sessions:v1", "levels": "levels:v1",
                      "feed_health": "feed_health:v1", "control_plane": "control_plane:v1",
                      "instrument_catalog": f"{CONTRACT_NAME}:{SCHEMA_VERSION}"},
        "contract_keys": {"instrument_catalog": instrument_catalog_key(CANONICAL_INSTRUMENT),
                          "feed_health": feed_health_contract["key"], "sessions": session_contracts["key"],
                          "quote": quote_contract["key"], "tick": tick_contract["key"],
                          "control_plane": control_plane_contracts},
        "timeframe_contracts": {tf: {"candle": candle_contracts[tf], "indicators": indicator_contracts[tf],
                                     "candle_features": candle_feature_contracts[tf]} for tf in SUPPORTED_TIMEFRAMES},
        "candle_contracts": candle_contracts,
        "indicator_contracts": indicator_contracts,
        "candle_feature_contracts": candle_feature_contracts,
        "session_contracts": session_contracts,
        "level_contracts": level_contracts,
        "feed_health_contract": feed_health_contract,
        "quote_contract": quote_contract,
        "tick_contract": tick_contract,
        "legacy_contracts": legacy_contracts,
        "deprecated_contracts": deprecated_contracts,
        "d1_policy": d1_policy,
        "source_dependencies": list(source_dependencies) if source_dependencies else [],
        "publisher_identity": "hermes-signal",
        "ttl_seconds": TTL_SECONDS,
        "provenance": {"deterministic_only": True, "declaration": "governed_catalog_declaration",
                       "supported_timeframes": list(SUPPORTED_TIMEFRAMES)},
        "notes": list(notes) if notes else [],
        "deterministic_only": True,
    }
    validate_instrument_catalog_contract(payload)
    return payload


def validate_instrument_catalog_contract(p):
    if p.get("publisher") != PUBLISHER or p.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("GOV-HERMES-IC-010: catalog publisher/schema_version must be HERMES/v1")
    if p.get("contract") != f"{CONTRACT_NAME}:{SCHEMA_VERSION}":
        raise ValueError("GOV-HERMES-IC-011: catalog contract must be instrument_catalog:v1")
    if p.get("instrument") != CANONICAL_INSTRUMENT or p.get("canonical_instrument") != CANONICAL_INSTRUMENT:
        raise ValueError("GOV-HERMES-IC-012: catalog instrument must be XAU_USD (no alias/non-XAU output)")
    if p.get("status") not in CATALOG_STATUSES:
        raise ValueError("GOV-HERMES-IC-013: catalog status not in governed vocab")
    if p.get("deterministic_only") is not True:
        raise ValueError("GOV-HERMES-IC-014: catalog payload must be deterministic_only=true")
    if not isinstance(p.get("ttl_seconds"), int) or p["ttl_seconds"] <= 0:
        raise ValueError("GOV-HERMES-IC-015: ttl_seconds must be a positive int")
    _assert_utc(p.get("generated_at_utc"), "generated_at_utc")
    if not isinstance(p.get("supported_timeframes"), list) or not p["supported_timeframes"]:
        raise ValueError("GOV-HERMES-IC-016: supported_timeframes must be a non-empty list")
    # No output contract key may be published under the XAUUSD alias.
    for kv in _iter_key_strings(p):
        if isinstance(kv, str) and ":XAUUSD:" in kv:
            raise ValueError("GOV-HERMES-IC-017: no output contract key may be published under the XAUUSD alias")
    _scan_no_forbidden_field_keys(p)
    return True


def _iter_key_strings(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _iter_key_strings(v)
    elif isinstance(obj, (list, tuple)):
        for x in obj:
            yield from _iter_key_strings(x)
    elif isinstance(obj, str):
        yield obj


# --------------------------------------------------------------------------- publisher foundation (DISABLED)
def parse_catalog_instruments(raw):
    if raw is None or not str(raw).strip():
        raise ValueError(f"GOV-HERMES-IC-020: {INSTRUMENTS_ENV} required and non-empty (fail-closed)")
    items = [x.strip() for x in str(raw).split(",") if x.strip()]
    if not items:
        raise ValueError(f"GOV-HERMES-IC-020: {INSTRUMENTS_ENV} required and non-empty (fail-closed)")
    for inst in items:
        if inst == "XAUUSD" or inst != CANONICAL_INSTRUMENT:
            raise ValueError(f"GOV-HERMES-IC-021: instrument {inst!r} not allowed (canonical XAU_USD only; XAUUSD is inbound alias)")
    return frozenset({CANONICAL_INSTRUMENT})


class DisabledInstrumentCatalogPublisher:
    enabled = False

    def status(self):
        return {"enabled": False}


class InstrumentCatalogPublisher:
    enabled = True

    def __init__(self, *, allowed_instruments, source_name):
        if frozenset(allowed_instruments) != frozenset({CANONICAL_INSTRUMENT}):
            raise ValueError("GOV-HERMES-IC-021: catalog allowlist must be exactly {XAU_USD}")
        self.allowed_instruments = frozenset(allowed_instruments)
        self.source_name = source_name

    def status(self):
        return {"enabled": True, "instruments": sorted(self.allowed_instruments), "source_name": self.source_name}

    def build(self, **kw):
        return build_instrument_catalog_contract(**kw)

    def key(self, instrument):
        return instrument_catalog_key(instrument)


def build_instrument_catalog_publisher_from_env():
    """DEFAULT DISABLED -> DisabledInstrumentCatalogPublisher (no-op, no Redis client, no I/O). ENABLED without
    AUTHORISED -> SystemExit(101). ENABLED + AUTHORISED -> InstrumentCatalogPublisher (payloads only, NO Redis
    I/O). No hidden defaults; NO Redis/SQL/network at import or here."""
    from env_config import get_env, get_env_bool   # lazy; HERMES-owned config only
    if not get_env_bool(ENABLED_ENV, False):
        return DisabledInstrumentCatalogPublisher()
    if not get_env_bool(AUTHORISED_ENV, False):
        raise SystemExit(HALT_CODE)
    instruments = parse_catalog_instruments(get_env(INSTRUMENTS_ENV, default=None))
    source_name = get_env(SOURCE_NAME_ENV, default="UNKNOWN") or "UNKNOWN"
    return InstrumentCatalogPublisher(allowed_instruments=instruments, source_name=source_name)
