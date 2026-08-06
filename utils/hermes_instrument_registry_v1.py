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

# Effective Advanced-v1 state for a registry row (health/readiness vocabulary).
STATE_NOT_ENABLED = "NOT_ENABLED"                    # no Advanced-v1 capability -> capability metadata not required
STATE_ACTIVE = "ADVANCED_V1_ACTIVE"                  # >=1 capability -> full capability metadata contract enforced

# Capability -> the record fields that capability REQUIRES (present + valid). A field shared by several capabilities is
# listed under each; the required set for a row is the UNION over its enabled capabilities. Fields NOT listed here are
# either universal (validated always) or carry a governed NOT-NULL default. DATA-driven; no ticker logic.
_CAPABILITY_REQUIRED_FIELDS = {
    "tick_contract_enabled":      ("price_precision", "tick_size", "price_authority", "market_hours_policy", "expected_freshness_sec"),
    "indicator_contract_enabled": ("price_precision", "price_authority", "market_hours_policy", "expected_freshness_sec",
                                   "enabled_timeframes", "indicator_profile"),
    "gap_detection_enabled":      ("price_precision", "tick_size", "market_hours_policy", "expected_freshness_sec", "enabled_timeframes"),
}
# Capability-specific fields that may be NULL on a fully-inactive (NOT_ENABLED) row. Universal fields (symbol, category,
# enabled, oanda_compatible, capability flags, metadata_version) and NOT-NULL-default fields are NOT in this set.
_CAPABILITY_SPECIFIC_NULLABLE = ("price_precision", "tick_size", "market_hours_policy", "expected_freshness_sec",
                                 "enabled_timeframes")


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
    price_precision: Optional[int]                   # capability-specific: None on a NOT_ENABLED row
    tick_size: Optional[float]                       # capability-specific: None on a NOT_ENABLED row
    price_authority: str                             # NOT-NULL default 'mid'
    market_hours_policy: Optional[str]               # capability-specific: None on a NOT_ENABLED row
    expected_freshness_sec: Optional[int]            # capability-specific: None on a NOT_ENABLED row
    enabled_timeframes: Optional[Tuple[str, ...]]    # capability-specific: None on a NOT_ENABLED row
    indicator_profile: str                           # NOT-NULL default 'standard_v1'
    tick_contract_enabled: bool
    indicator_contract_enabled: bool
    gap_detection_enabled: bool
    backfill_policy: str                             # NOT-NULL default 'status_only'
    retention_policy: str                            # NOT-NULL default 'default_v1'
    metadata_version: str                            # universal (NOT-NULL default 'v1')

    def capability(self, name: str) -> bool:
        if name not in _CAPABILITIES:
            raise RegistryError(f"unknown capability: {name!r}")
        return bool(getattr(self, name))

    @property
    def capabilities(self) -> Dict[str, bool]:
        return {c: bool(getattr(self, c)) for c in _CAPABILITIES}

    @property
    def advanced_v1_active(self) -> bool:
        """True iff any Advanced-v1 capability is enabled (a capability-active row; full metadata contract enforced)."""
        return any(bool(getattr(self, c)) for c in _CAPABILITIES)

    @property
    def effective_state(self) -> str:
        return STATE_ACTIVE if self.advanced_v1_active else STATE_NOT_ENABLED

    def require_complete(self) -> "InstrumentRecord":
        """Guarded accessor: assert this record's capability-specific metadata is complete before capability-active use.
        A NOT_ENABLED record raises (it must never reach a family publisher). Defence-in-depth against None consumption."""
        if not self.advanced_v1_active:
            raise RegistryError(f"GOV-HERMES-REG-INACTIVE: {self.symbol} is NOT_ENABLED and must not be used by a family publisher")
        missing = [f for f in _CAPABILITY_SPECIFIC_NULLABLE
                   if f in _required_fields_for(self.capabilities) and getattr(self, f) is None]
        if missing:
            raise RegistryError(f"GOV-HERMES-REG-INCOMPLETE-ACTIVE: {self.symbol} missing required capability metadata {missing}")
        return self


def _required_fields_for(capabilities: Mapping) -> frozenset:
    """The UNION of required capability-specific fields over the enabled capabilities of a row."""
    req = set()
    for cap, on in capabilities.items():
        if on and cap in _CAPABILITY_REQUIRED_FIELDS:
            req.update(_CAPABILITY_REQUIRED_FIELDS[cap])
    return frozenset(req)


def _as_bool(v) -> bool:
    """Permissive boolean coercion for NON-authority universal flags (enabled, oanda_compatible)."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int,)):
        return v != 0
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


def _parse_capability_flag(value, *, field: str, symbol: str, metadata_version) -> bool:
    """STRICT parser for Advanced-v1 capability AUTHORITY fields. Accepts ONLY the governed loader-contract forms —
    bool True/False and int 0/1 (the SQL tinyint(1) driver forms). EVERYTHING ELSE (None, strings incl. '0'/'1'/
    'true', int 2/-1, floats, lists, dicts, bytes, objects) is a CONFIGURATION_INVALID fault and raises — it is NEVER
    coerced to False and NEVER interpreted as NOT_ENABLED. Booleans are checked before ints (bool is an int subclass)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):                                  # bool already handled above
        if value in (0, 1):
            return bool(value)
    safe = value if isinstance(value, (int, bool)) else f"<{type(value).__name__}>"
    raise RegistryError(
        f"GOV-HERMES-REG-INVALID-CAPABILITY-FLAG: {symbol} capability field {field!r} has malformed authority "
        f"value={safe} (type={type(value).__name__}); accepted: int 0/1 or bool True/False; "
        f"state=CONFIGURATION_INVALID; metadata_version={metadata_version}")


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
    """Validate one raw registry row into a typed InstrumentRecord — CAPABILITY-AWARE, fail-closed.

    Universal identity fields (symbol, category, enabled, oanda_compatible, capability flags, metadata_version) and the
    NOT-NULL-default fields (price_authority, indicator_profile, backfill_policy, retention_policy) are validated for
    EVERY row. Capability-specific metadata (price_precision, tick_size, market_hours_policy, expected_freshness_sec,
    enabled_timeframes) is: (a) validated whenever PRESENT (a present value must be valid, even on an inactive row);
    (b) REQUIRED (must be present + valid) when the capability that needs it is enabled. A fully-inactive row (all
    Advanced-v1 capabilities false) may load with those fields NULL and is classified NOT_ENABLED. No defaults are
    invented for missing capability-specific metadata; no ticker-specific exemption exists."""
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

    # Capability AUTHORITY fields are STRICTLY parsed (int 0/1 or bool only); malformed -> CONFIGURATION_INVALID fault.
    _mdv = raw.get("metadata_version")
    caps = {c: _parse_capability_flag(raw.get(c, 0), field=c, symbol=symbol, metadata_version=_mdv) for c in _CAPABILITIES}
    required = _required_fields_for(caps)                        # union of fields the ENABLED capabilities need

    def _needs(field):
        return field in required

    def _fault(field, cap_hint=""):
        active = [c for c, on in caps.items() if on]
        raise RegistryError(f"GOV-HERMES-REG-INCOMPLETE-ACTIVE: {symbol} capability-active {active} requires {field!r} "
                            f"but it is missing/invalid (metadata_version={raw.get('metadata_version')})")

    def _present(key):
        return key in raw and raw[key] is not None

    # --- capability-specific fields: validate if present; require if a capability needs it ---
    precision = None
    if _present("price_precision"):
        try:
            precision = int(raw["price_precision"])
        except (TypeError, ValueError):
            raise RegistryError(f"invalid price_precision for {symbol}")
        if precision < 0 or precision > 12:
            raise RegistryError(f"price_precision out of range for {symbol}: {precision}")
    elif _needs("price_precision"):
        _fault("price_precision")

    tick = None
    if _present("tick_size"):
        try:
            tick = float(raw["tick_size"])
        except (TypeError, ValueError):
            raise RegistryError(f"invalid tick_size for {symbol}")
        if tick <= 0:
            raise RegistryError(f"tick_size must be > 0 for {symbol}")
    elif _needs("tick_size"):
        _fault("tick_size")

    mhp = None
    if _present("market_hours_policy"):
        mhp = str(raw["market_hours_policy"]).strip()
        if mhp not in VALID_MARKET_HOURS_POLICY:
            raise RegistryError(f"unknown market_hours_policy {mhp!r} for {symbol}")
    elif _needs("market_hours_policy"):
        _fault("market_hours_policy")

    freshness = None
    if _present("expected_freshness_sec"):
        try:
            freshness = int(raw["expected_freshness_sec"])
        except (TypeError, ValueError):
            raise RegistryError(f"invalid expected_freshness_sec for {symbol}")
        if freshness <= 0:
            raise RegistryError(f"expected_freshness_sec must be > 0 for {symbol}")
    elif _needs("expected_freshness_sec"):
        _fault("expected_freshness_sec")

    tfs = None
    if _present("enabled_timeframes"):
        tfs = _parse_timeframes(raw["enabled_timeframes"])
    elif _needs("enabled_timeframes"):
        _fault("enabled_timeframes")

    # --- NOT-NULL-default fields: validated for every row (present via schema default) ---
    price_authority = str(raw.get("price_authority", "mid")).strip().lower()
    if price_authority not in VALID_PRICE_AUTHORITY:
        raise RegistryError(f"invalid price_authority {price_authority!r} for {symbol}")
    backfill = str(raw.get("backfill_policy", "status_only")).strip()
    if backfill not in VALID_BACKFILL_POLICY:
        raise RegistryError(f"invalid backfill_policy {backfill!r} for {symbol}")
    indicator_profile = str(raw.get("indicator_profile", "standard_v1")).strip()

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
        indicator_profile=indicator_profile,
        tick_contract_enabled=caps["tick_contract_enabled"],
        indicator_contract_enabled=caps["indicator_contract_enabled"],
        gap_detection_enabled=caps["gap_detection_enabled"],
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


def capability_instruments(records: Sequence[InstrumentRecord], capability: str) -> Tuple[str, ...]:
    """The reusable, registry-driven authority the Advanced-v1 pipeline uses to decide WHICH instruments a
    capability publishes for — replacing per-module hard-coded XAU authority constants and env allowlists.
    Returns the enabled instruments whose capability flag is set (sorted). Currently {XAU_USD}; the 7 new
    instruments are absent (NOT_ENABLED). Fail-closed on an unknown capability."""
    if capability not in _CAPABILITIES:
        raise RegistryError(f"unknown capability: {capability!r} (known: {_CAPABILITIES})")
    return tuple(sorted(r.symbol for r in records if r.enabled and getattr(r, capability)))


def assert_records_complete(records: Sequence[InstrumentRecord], symbols) -> None:
    """Family-boundary guard (defence-in-depth): assert every `symbol` a family is about to consume maps to a loaded,
    capability-ACTIVE, metadata-COMPLETE record. Raises RegistryError if a symbol is absent, NOT_ENABLED, or has
    incomplete required capability metadata. Uses the central require_complete() policy — no duplicated matrix. Selectors
    remain the primary cohort boundary; this catches a directly-injected/constructed/stale record that bypassed loading."""
    by = {r.symbol: r for r in records}
    for s in symbols:
        rec = by.get(s)
        if rec is None:
            raise RegistryError(f"GOV-HERMES-REG-SELECTED-ABSENT: selected instrument {s!r} is not in the loaded registry")
        rec.require_complete()


def registry_effective_summary(records: Sequence[InstrumentRecord]) -> Dict:
    """Truthful scope-aware summary of the loaded registry (deployment-preflight + health). Distinguishes registry
    membership from Advanced-v1 activation: total rows, capability-ACTIVE rows (full metadata enforced), NOT_ENABLED
    rows (capability-inactive; capability metadata not required), and the per-capability selection. Never reports an
    inactive metadata-incomplete row as unhealthy for fields its disabled capabilities do not need."""
    active = sorted(r.symbol for r in records if r.advanced_v1_active)
    not_enabled = sorted(r.symbol for r in records if not r.advanced_v1_active)
    return {
        "registry_rows": len(records),
        "advanced_v1_active": active,
        "not_enabled": not_enabled,
        "not_enabled_count": len(not_enabled),
        "selection": {cap.replace("_contract_enabled", "").replace("_detection_enabled", ""): capability_instruments(records, cap)
                      for cap in _CAPABILITIES},
    }


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
