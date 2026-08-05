"""HERMES reusable canonical instrument registry loader (v1).

WO-HELM-HERMES-ADVANCED-V1-REGISTRY-FOUNDATION-0001.

ONE authority: tradingSignals.instruments. Every Advanced-v1 pipeline component derives instrument
discovery, metadata and per-capability policy from THIS loader — never a hard-coded ticker list and never
an `if instrument == "..."` branch. Instrument-specific behaviour is expressed as DATA/POLICY (reusable
policy keys such as market_hours_policy / indicator_profile / backfill_policy) selected by metadata.

Fail-closed: invalid or incomplete registry data raises RegistryError; the loader NEVER silently falls back
to a hard-coded instrument set. Pure validation (no I/O); the DB read is injected so tests need no database.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, Mapping, Optional, Sequence, Tuple

GOVERNED_CATEGORIES = frozenset(
    {"precious_metals", "base_metals", "forex_major", "forex_minor", "indices", "crypto", "energy"}
)
SUPPORTED_TIMEFRAMES = ("M1", "M5", "M15", "H1", "H4", "D1")
_TF_SET = frozenset(SUPPORTED_TIMEFRAMES)
VALID_PRICE_AUTHORITY = frozenset({"mid", "bid", "ask"})
VALID_BACKFILL_POLICY = frozenset({"disabled", "status_only", "bounded", "full"})
VALID_MARKET_HOURS_POLICY = frozenset({"fx_24x5", "metals", "index_cash", "energy"})

_CAPABILITIES = ("tick_contract_enabled", "indicator_contract_enabled", "gap_detection_enabled")


class RegistryError(Exception):
    """Fail-closed registry validation/authority error."""


@dataclass(frozen=True)
class InstrumentRecord:
    symbol: str
    broker_symbol: Optional[str]
    name: str
    category: str
    enabled: bool
    oanda_compatible: bool
    price_precision: int
    tick_size: float
    price_authority: str
    market_hours_policy: str
    expected_freshness_sec: int
    enabled_timeframes: Tuple[str, ...]
    indicator_profile: str
    tick_contract_enabled: bool
    indicator_contract_enabled: bool
    gap_detection_enabled: bool
    backfill_policy: str
    retention_policy: str
    metadata_version: str

    def capability(self, name: str) -> bool:
        if name not in _CAPABILITIES:
            raise RegistryError(f"unknown capability: {name!r}")
        return bool(getattr(self, name))

    @property
    def capabilities(self) -> Dict[str, bool]:
        return {c: bool(getattr(self, c)) for c in _CAPABILITIES}


def _as_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int,)):
        return v != 0
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


def _parse_timeframes(v) -> Tuple[str, ...]:
    if v is None:
        raise RegistryError("enabled_timeframes is required")
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except json.JSONDecodeError as e:
            raise RegistryError(f"enabled_timeframes is not valid JSON: {e}")
    if not isinstance(v, (list, tuple)) or not v:
        raise RegistryError("enabled_timeframes must be a non-empty list")
    tfs = tuple(str(t) for t in v)
    for t in tfs:
        if t not in _TF_SET:
            raise RegistryError(f"unsupported timeframe {t!r} (allowed: {SUPPORTED_TIMEFRAMES})")
    # preserve canonical order, dedupe
    seen, ordered = set(), []
    for t in SUPPORTED_TIMEFRAMES:
        if t in tfs and t not in seen:
            ordered.append(t); seen.add(t)
    return tuple(ordered)


def validate_record(raw: Mapping) -> InstrumentRecord:
    """Validate one raw registry row into a typed InstrumentRecord. Fail-closed on any invalid/missing field."""
    def req(key):
        if key not in raw or raw[key] is None:
            raise RegistryError(f"missing required registry field: {key}")
        return raw[key]

    symbol = str(req("symbol")).strip()
    if not symbol:
        raise RegistryError("empty instrument symbol")
    category = str(req("category")).strip()
    if category not in GOVERNED_CATEGORIES:
        raise RegistryError(f"unsupported asset category {category!r} for {symbol} (governed: {sorted(GOVERNED_CATEGORIES)})")
    try:
        precision = int(req("price_precision"))
    except (TypeError, ValueError):
        raise RegistryError(f"invalid price_precision for {symbol}")
    if precision < 0 or precision > 12:
        raise RegistryError(f"price_precision out of range for {symbol}: {precision}")
    try:
        tick = float(req("tick_size"))
    except (TypeError, ValueError):
        raise RegistryError(f"invalid tick_size for {symbol}")
    if tick <= 0:
        raise RegistryError(f"tick_size must be > 0 for {symbol}")
    price_authority = str(raw.get("price_authority", "mid")).strip().lower()
    if price_authority not in VALID_PRICE_AUTHORITY:
        raise RegistryError(f"invalid price_authority {price_authority!r} for {symbol}")
    mhp = str(req("market_hours_policy")).strip()
    if mhp not in VALID_MARKET_HOURS_POLICY:
        raise RegistryError(f"unknown market_hours_policy {mhp!r} for {symbol}")
    try:
        freshness = int(req("expected_freshness_sec"))
    except (TypeError, ValueError):
        raise RegistryError(f"invalid expected_freshness_sec for {symbol}")
    if freshness <= 0:
        raise RegistryError(f"expected_freshness_sec must be > 0 for {symbol}")
    tfs = _parse_timeframes(req("enabled_timeframes"))
    backfill = str(raw.get("backfill_policy", "status_only")).strip()
    if backfill not in VALID_BACKFILL_POLICY:
        raise RegistryError(f"invalid backfill_policy {backfill!r} for {symbol}")

    return InstrumentRecord(
        symbol=symbol,
        broker_symbol=(str(raw["broker_symbol"]).strip() if raw.get("broker_symbol") else None),
        name=str(raw.get("name", symbol)),
        category=category,
        enabled=_as_bool(raw.get("enabled", 0)),
        oanda_compatible=_as_bool(raw.get("oanda_compatible", 0)),
        price_precision=precision,
        tick_size=tick,
        price_authority=price_authority,
        market_hours_policy=mhp,
        expected_freshness_sec=freshness,
        enabled_timeframes=tfs,
        indicator_profile=str(raw.get("indicator_profile", "standard_v1")).strip(),
        tick_contract_enabled=_as_bool(raw.get("tick_contract_enabled", 0)),
        indicator_contract_enabled=_as_bool(raw.get("indicator_contract_enabled", 0)),
        gap_detection_enabled=_as_bool(raw.get("gap_detection_enabled", 0)),
        backfill_policy=backfill,
        retention_policy=str(raw.get("retention_policy", "default_v1")).strip(),
        metadata_version=str(raw.get("metadata_version", "v1")).strip(),
    )


def load_registry(rows: Iterable[Mapping]) -> Tuple[InstrumentRecord, ...]:
    """Validate all rows; reject duplicate canonical symbols and duplicate broker mappings. Returns the
    ENABLED records (sorted by symbol). Fail-closed; never returns a hard-coded fallback."""
    records = [validate_record(r) for r in rows]
    seen_symbol: Dict[str, int] = {}
    seen_broker: Dict[str, str] = {}
    for rec in records:
        if rec.symbol in seen_symbol:
            raise RegistryError(f"duplicate canonical symbol in registry: {rec.symbol}")
        seen_symbol[rec.symbol] = 1
        if rec.broker_symbol:
            if rec.broker_symbol in seen_broker and seen_broker[rec.broker_symbol] != rec.symbol:
                raise RegistryError(
                    f"duplicate broker mapping {rec.broker_symbol!r} for {rec.symbol} and {seen_broker[rec.broker_symbol]}"
                )
            seen_broker[rec.broker_symbol] = rec.symbol
    enabled = tuple(sorted((r for r in records if r.enabled), key=lambda r: r.symbol))
    if not enabled:
        raise RegistryError("registry has no enabled instruments (fail-closed; refusing empty authority)")
    return enabled


def enabled_instruments(records: Sequence[InstrumentRecord]) -> Tuple[str, ...]:
    """The single instrument authority every pipeline stage/test iterates."""
    return tuple(r.symbol for r in records)


# --- runtime DB path (injectable; NO hard-coded fallback) ---------------------------------------------
_SELECT = (
    "SELECT symbol, mt5_symbol AS broker_symbol, name, category, enabled, oanda_compatible, "
    "price_precision, tick_size, price_authority, market_hours_policy, expected_freshness_sec, "
    "enabled_timeframes, indicator_profile, tick_contract_enabled, indicator_contract_enabled, "
    "gap_detection_enabled, backfill_policy, retention_policy, metadata_version "
    "FROM instruments"
)


def _default_fetch() -> Sequence[Mapping]:
    import pymysql  # runtime-only
    from env_config import get_db_config
    cfg = get_db_config()
    conn = pymysql.connect(host=cfg["host"], port=cfg["port"], user=cfg["user"],
                           password=cfg["password"], database=cfg["database"])
    try:
        cur = conn.cursor(pymysql.cursors.DictCursor)
        cur.execute(_SELECT)
        return list(cur.fetchall())
    finally:
        conn.close()


def load_from_db(fetch: Optional[Callable[[], Sequence[Mapping]]] = None) -> Tuple[InstrumentRecord, ...]:
    """Load + validate the registry from SQL (default) or an injected fetcher (tests). Fail-closed: any error
    raises RegistryError — it NEVER falls back to a hard-coded instrument list."""
    try:
        rows = (fetch or _default_fetch)()
    except RegistryError:
        raise
    except Exception as e:  # DB unreachable / query error -> fail closed, no fallback
        raise RegistryError(f"instrument registry unavailable (fail-closed, no fallback): {e}")
    return load_registry(rows)


def registry_component_state(fetch: Optional[Callable[[], Sequence[Mapping]]] = None) -> Dict:
    """§17 health foundation: reusable registry component state that /health /ready /status /metrics can
    consume WITHOUT manual instrument enumeration. Never reports the 7 new Advanced-v1 instruments as active
    — their per-capability status is NOT_ENABLED until a registry flag is flipped. Fail-closed on load error.
    Component states: LOADED | UNAVAILABLE | INVALID."""
    try:
        recs = load_from_db(fetch)
    except RegistryError as e:
        return {"component": "instrument_registry", "state": "UNAVAILABLE", "fault": str(e),
                "enabled_count": 0, "instruments": {}}
    instruments = {}
    for r in recs:
        caps = r.capabilities
        instruments[r.symbol] = {
            "category": r.category,
            "capabilities": {k: ("ACTIVE" if v else "NOT_ENABLED") for k, v in caps.items()},
        }
    return {"component": "instrument_registry", "state": "LOADED", "fault": None,
            "enabled_count": len(recs), "instruments": instruments}


def consistency_check(records: Sequence[InstrumentRecord], env_instruments: Sequence[str]) -> None:
    """DEPRECATED env INSTRUMENTS is a fail-closed VALIDATOR only, never an authority: every env-named
    instrument MUST be enabled in the SQL registry. The registry is the single source of truth."""
    enabled = set(enabled_instruments(records))
    unknown = [i for i in env_instruments if i and i not in enabled]
    if unknown:
        raise RegistryError(
            f"env INSTRUMENTS names instrument(s) not enabled in the canonical registry: {unknown} "
            f"(the SQL registry is the sole authority; env is a deprecated read-only validator)"
        )
