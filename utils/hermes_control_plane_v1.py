"""HERMES governed Redis CONTROL-PLANE v1 — contract manifest / publisher heartbeat / candle catalog / health.
WO-HELM-HERMES-REDIS-CONTROL-PLANE-MANIFEST-HEALTH-0001.

CODE/DESIGN ONLY. This module builds the governed v1 control-plane PAYLOADS so downstream engines (ARES,
Falcon) can discover what HERMES publishes, what is live, gated, blocked, missing, legacy, or out of scope.
It performs NO Redis I/O, NO network calls, NO SQL, NO file writes — at import OR anywhere. The actual publish
of these keys is a SEPARATE, later, authorised WO.

Gating: DISABLED by default. ENABLED without AUTHORISED -> terminal halt SystemExit(101). ENABLED+AUTHORISED ->
a pure payload builder (still no Redis client, no I/O). UTC-only timestamps. NO authentication / ACL. NO regime
or risk DATA fields (HERMES owns deterministic market data only; ARES owns regime/risk/interpretive context).

Future versioned keys (NO unversioned aliases authorised):
  hermes:contract:manifest:v1 · hermes:publisher:heartbeat:v1 · hermes:catalog:candles:v1 · hermes:health:v1
"""
from __future__ import annotations
from datetime import datetime, timezone

from utils import candle_contract_v1 as cc   # UTC helpers (normalise_utc/_fmt) only — no I/O

# --------------------------------------------------------------------------- keys / identity
KEY_CONTRACT_MANIFEST = "hermes:contract:manifest:v1"
KEY_PUBLISHER_HEARTBEAT = "hermes:publisher:heartbeat:v1"
KEY_CATALOG_CANDLES = "hermes:catalog:candles:v1"
KEY_HEALTH = "hermes:health:v1"
CONTROL_PLANE_KEYS = (KEY_CONTRACT_MANIFEST, KEY_PUBLISHER_HEARTBEAT, KEY_CATALOG_CANDLES, KEY_HEALTH)
SCHEMA_VERSION = "v1"
PUBLISHER = "HERMES"
SERVICE_IDENTITY_DEFAULT = "hermes-signal"
CANONICAL_INSTRUMENTS = ("XAU_USD",)

# --------------------------------------------------------------------------- gating
ENABLED_ENV = "HERMES_REDIS_CONTROL_PLANE_ENABLED"
AUTHORISED_ENV = "HERMES_REDIS_CONTROL_PLANE_AUTHORISED"
HALT_CODE = 101

# --------------------------------------------------------------------------- status vocab
STATUS_ACTIVE = "ACTIVE"
STATUS_PENDING = "PENDING"
STATUS_PENDING_FIRST_DAILY_SEAL = "PENDING_FIRST_DAILY_SEAL"
STATUS_BLOCKED = "BLOCKED"
STATUS_BLOCKED_UNTIL_D1_LATEST_GREEN = "BLOCKED_UNTIL_D1_LATEST_GREEN"
STATUS_ABSENT = "ABSENT"
STATUS_NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
STATUS_GATED = "GATED"
STATUS_LEGACY_DEPRECATED = "LEGACY_DEPRECATED"
STATUS_FROZEN_PENDING_CONSUMER_CUTOVER = "FROZEN_PENDING_CONSUMER_CUTOVER"
STATUS_OWNERSHIP_PENDING = "OWNERSHIP_PENDING"
STATUS_INVENTORY_PENDING = "INVENTORY_PENDING"
STATUS_PARTIAL = "PARTIAL"
STATUS_LEGACY_OR_PARTIAL_CATALOG = "LEGACY_OR_PARTIAL_CATALOG"
STATUS_FAULT = "FAULT"

FAMILY_STATUS_VOCAB = frozenset({
    STATUS_ACTIVE, STATUS_PENDING, STATUS_PENDING_FIRST_DAILY_SEAL, STATUS_BLOCKED,
    STATUS_BLOCKED_UNTIL_D1_LATEST_GREEN, STATUS_ABSENT, STATUS_NOT_IMPLEMENTED, STATUS_GATED,
    STATUS_LEGACY_DEPRECATED, STATUS_FROZEN_PENDING_CONSUMER_CUTOVER, STATUS_OWNERSHIP_PENDING,
    STATUS_INVENTORY_PENDING, STATUS_PARTIAL, STATUS_LEGACY_OR_PARTIAL_CATALOG, STATUS_FAULT,
})
OVERALL_STATUS_VOCAB = frozenset({"OK", "WARN", "FAIL"})

# --------------------------------------------------------------------------- governance facts
_LATEST_TFS = ("M1", "M5", "M15", "H1", "H4")
HISTORY_TTL_SECONDS = 3024000      # 35 days (mirrors candle_history_v1; declared, not imported, to avoid coupling)

SOURCE_POLICIES = {
    "h4": "H4_FROM_H1",                        # H4 derives from H1
    "d1": "D1_FROM_6_OK_H4",                    # D1 derives from six complete OK H4 children
    "rejected_sources": ["DIRECT_CANDLES_H4_STALE", "DIRECT_CANDLES_D1_UTC_MIDNIGHT",
                         "TWENTYFOUR_X_H1_D1_PRODUCTION_SHORTCUT"],
    "interpretive_ownership": "NO_REGIME_OR_RISK_OWNERSHIP_IN_HERMES",   # value names it; no DATA field for it
}

OWNERSHIP_BOUNDARIES = {
    "hermes_owns": ["deterministic market data", "candles", "candle history", "deterministic indicators",
                    "deterministic candle features",
                    "deterministic levels (H/L sweeps, mathematical pivots, deterministic midpoints, "
                    "Fibonacci anchors, candle-derived levels, indicator values)",
                    "feed health", "instrument/catalog metadata", "Redis publication contracts"],
    "ares_owns": ["regime", "risk", "event/calendar impact", "liquidity/risk context",
                  "interpretive levels (liquidity blocks, risk-gated order blocks)", "decision gating"],
    "falcon": "consumes HERMES/ARES/HELIOS contracts later; does not define HERMES internals",
    "hermes_must_not": ["publish, determine, or imply market regime", "publish risk conclusions",
                        "expose interpretive levels"],
}

# Forbidden FIELD-KEY tokens: the control-plane must not carry regime/risk DATA fields or auth material as
# field KEYS. Descriptive ownership TEXT VALUES may name regime/risk (declaring them ARES-owned).
_FORBIDDEN_FIELD_KEY_TOKENS = ("regime", "risk", "order_block", "liquidity", "password", "secret",
                               "credential", "apikey", "api_key", "acl", "noauth")


# --------------------------------------------------------------------------- helpers
def _utc(dt):
    """Format an (aware/naive-UTC) datetime as a governed UTC ISO string ending in 'Z'. Fail-loud on non-datetime."""
    if not isinstance(dt, datetime):
        raise ValueError(f"GOV-HERMES-CP-001: timestamp must be a datetime (got {type(dt).__name__})")
    return cc._fmt(cc.normalise_utc(dt))


def _assert_utc_field(value, field):
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"GOV-HERMES-CP-002: {field} must be a UTC ISO string ending 'Z' (got {value!r})")
    try:
        datetime.strptime(value[:-1], "%Y-%m-%dT%H:%M:%S.%f")
    except ValueError:
        raise ValueError(f"GOV-HERMES-CP-002: {field} not a valid UTC ISO timestamp ({value!r})")
    return True


def _scan_no_forbidden_field_keys(obj):
    """Recursively assert no dict KEY contains a forbidden regime/risk/auth token. String VALUES are allowed
    to NAME regime/risk (ownership declarations) — only field KEYS are policed. Fail-loud."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = str(k).lower()
            for tok in _FORBIDDEN_FIELD_KEY_TOKENS:
                if tok in kl:
                    raise ValueError(f"GOV-HERMES-CP-003: forbidden control-plane field key {k!r} "
                                     f"(token {tok!r}) — HERMES control-plane carries no regime/risk/auth DATA fields")
            _scan_no_forbidden_field_keys(v)
    elif isinstance(obj, (list, tuple)):
        for x in obj:
            _scan_no_forbidden_field_keys(x)
    return True


def redact_redis_target(host, port, db):
    """Build a secrets-redacted redis target string 'host:port/dbN'. Fail-loud if any credential material slips in."""
    s = f"{host}:{port}/db{db}"
    if "@" in s or any(t in s.lower() for t in ("password", "secret", "credential")):
        raise ValueError("GOV-HERMES-CP-004: redis_target must be secrets-redacted (no credentials)")
    return s


# --------------------------------------------------------------------------- 1. contract manifest
def build_contract_manifest(*, generated_at_utc, environment=None, run_env=None, deployed_sha=None,
                            service_identity=None):
    """hermes:contract:manifest:v1 payload — advertises every HERMES family: active / gated / blocked /
    not-implemented / legacy, plus ownership boundaries + source policies. Pure; no I/O."""
    m = {
        "publisher": PUBLISHER, "schema_version": SCHEMA_VERSION, "contract_version": "v1",
        "environment": environment, "run_env": run_env, "deployed_sha": deployed_sha,
        "service_identity": service_identity or SERVICE_IDENTITY_DEFAULT,
        "generated_by": service_identity or SERVICE_IDENTITY_DEFAULT,
        "generated_at_utc": _utc(generated_at_utc),
        "canonical_instruments": list(CANONICAL_INSTRUMENTS),
        "active_families": {
            "candle_latest": {tf: STATUS_ACTIVE for tf in _LATEST_TFS},
            "candle_history": {tf: STATUS_ACTIVE for tf in _LATEST_TFS},
            "forward_history": STATUS_ACTIVE,
        },
        "gated_families": {
            "candle_latest_d1": {"status": STATUS_PENDING_FIRST_DAILY_SEAL,
                                 "explanation": "D1 latest is armed but not GREEN until the first live "
                                                "6x complete-OK H4 D1 seal validates"},
        },
        "blocked_families": {
            "candle_history_d1": {"status": STATUS_BLOCKED_UNTIL_D1_LATEST_GREEN,
                                  "explanation": "no D1 history writes until D1 latest is proven live GREEN"},
        },
        "not_implemented_families": {
            "indicators": STATUS_NOT_IMPLEMENTED,
            "candle_features": STATUS_NOT_IMPLEMENTED,
            "feed_health": STATUS_INVENTORY_PENDING,        # legacy producer code exists (SQL); no v1 Redis surface
            "sessions": STATUS_OWNERSHIP_PENDING,           # HERMES-owned ruling pending vs deterministic session metadata
            "instrument_catalog": STATUS_PARTIAL,           # legacy hermes:instrument:* exists, unversioned
        },
        "legacy_deprecated_families": {
            "hermes:signals:*": STATUS_FROZEN_PENDING_CONSUMER_CUTOVER,
            "hermes:market_map:*": STATUS_FROZEN_PENDING_CONSUMER_CUTOVER,
            "hermes:instrument:*": STATUS_LEGACY_OR_PARTIAL_CATALOG,
        },
        "ownership_boundaries": OWNERSHIP_BOUNDARIES,
        "source_policies": SOURCE_POLICIES,
        "interpretive_ownership_note": "HERMES declares NO regime ownership and publishes no regime or risk "
                                       "conclusions; regime/risk/interpretive context is ARES-owned",
    }
    validate_manifest(m)
    return m


def validate_manifest(m):
    if m.get("publisher") != PUBLISHER:
        raise ValueError("GOV-HERMES-CP-010: manifest publisher must be HERMES")
    if m.get("contract_version") != "v1":
        raise ValueError("GOV-HERMES-CP-011: manifest contract_version must be v1")
    if list(m.get("canonical_instruments") or []) != list(CANONICAL_INSTRUMENTS):
        raise ValueError("GOV-HERMES-CP-012: manifest canonical_instruments must be exactly [XAU_USD]")
    _assert_utc_field(m.get("generated_at_utc"), "generated_at_utc")
    for tf in _LATEST_TFS:
        if m["active_families"]["candle_latest"].get(tf) != STATUS_ACTIVE:
            raise ValueError(f"GOV-HERMES-CP-013: candle_latest {tf} must be ACTIVE")
        if m["active_families"]["candle_history"].get(tf) != STATUS_ACTIVE:
            raise ValueError(f"GOV-HERMES-CP-014: candle_history {tf} must be ACTIVE")
    if m["gated_families"]["candle_latest_d1"]["status"] != STATUS_PENDING_FIRST_DAILY_SEAL:
        raise ValueError("GOV-HERMES-CP-015: D1 latest must be PENDING_FIRST_DAILY_SEAL")
    if m["blocked_families"]["candle_history_d1"]["status"] != STATUS_BLOCKED_UNTIL_D1_LATEST_GREEN:
        raise ValueError("GOV-HERMES-CP-016: D1 history must be BLOCKED_UNTIL_D1_LATEST_GREEN")
    _scan_no_forbidden_field_keys(m)
    return True


# --------------------------------------------------------------------------- 2. publisher heartbeat
def build_publisher_heartbeat(*, updated_at_utc, generated_at_utc=None, service_identity=None,
                              deployed_sha=None, run_env=None, redis_target=None, status="OK",
                              enabled_publishers=None, active_timeframes=None, last_publish_utc=None,
                              latest_key_freshness=None, history_forward_state=None, d1_state=None,
                              fault_counters_summary=None, skip_counters_summary=None):
    """hermes:publisher:heartbeat:v1 payload — publisher liveness + per-timeframe freshness + fault/skip
    summaries. UTC-only. Pure; no I/O. redis_target must be secrets-redacted by the caller."""
    if status not in OVERALL_STATUS_VOCAB:
        raise ValueError(f"GOV-HERMES-CP-020: heartbeat status must be OK/WARN/FAIL (got {status!r})")
    hb = {
        "schema_version": SCHEMA_VERSION, "service_identity": service_identity or SERVICE_IDENTITY_DEFAULT,
        "updated_at_utc": _utc(updated_at_utc),
        "generated_at_utc": _utc(generated_at_utc) if generated_at_utc is not None else _utc(updated_at_utc),
        "deployed_sha": deployed_sha, "run_env": run_env, "redis_target": redis_target,
        "status": status,
        "enabled_publishers": list(enabled_publishers) if enabled_publishers is not None else [],
        "active_timeframes": list(active_timeframes) if active_timeframes is not None else [],
        "last_publish_utc": dict(last_publish_utc) if last_publish_utc else {},
        "latest_key_freshness": dict(latest_key_freshness) if latest_key_freshness else {},
        "history_forward_state": history_forward_state,
        "d1_state": d1_state,
        "fault_counters_summary": dict(fault_counters_summary) if fault_counters_summary else {},
        "skip_counters_summary": dict(skip_counters_summary) if skip_counters_summary else {},
    }
    validate_heartbeat(hb)
    return hb


def validate_heartbeat(hb):
    if hb.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("GOV-HERMES-CP-021: heartbeat schema_version must be v1")
    if hb.get("status") not in OVERALL_STATUS_VOCAB:
        raise ValueError("GOV-HERMES-CP-022: heartbeat status must be OK/WARN/FAIL")
    for f in ("updated_at_utc", "generated_at_utc"):
        _assert_utc_field(hb.get(f), f)
    for tf, ts in (hb.get("last_publish_utc") or {}).items():
        _assert_utc_field(ts, f"last_publish_utc[{tf}]")
    if "deployed_sha" not in hb or "service_identity" not in hb or "fault_counters_summary" not in hb:
        raise ValueError("GOV-HERMES-CP-023: heartbeat missing required field")
    if hb.get("redis_target") is not None:
        rt = str(hb["redis_target"])
        if "@" in rt or any(t in rt.lower() for t in ("password", "secret", "credential")):
            raise ValueError("GOV-HERMES-CP-024: heartbeat redis_target must be secrets-redacted")
    _scan_no_forbidden_field_keys(hb)
    return True


# --------------------------------------------------------------------------- 3. candle catalog
_CATALOG_TF = {
    "M1":  {"src": None, "policy": "NONE_DIRECT",      "exp": None, "anchor": "NATIVE_UTC_GRID"},
    "M5":  {"src": None, "policy": "NONE_DIRECT",      "exp": None, "anchor": "NATIVE_UTC_GRID"},
    "M15": {"src": None, "policy": "NONE_DIRECT",      "exp": None, "anchor": "NATIVE_UTC_GRID"},
    "H1":  {"src": None, "policy": "NONE_DIRECT",      "exp": None, "anchor": "NATIVE_UTC_GRID"},
    "H4":  {"src": "H1", "policy": "DERIVED_H4_FROM_H1", "exp": 4,  "anchor": "FIXED_2200_UTC_NY5PM (22/02/06/10/14/18)"},
    "D1":  {"src": "H4", "policy": "DERIVED_D1_FROM_H4", "exp": 6,  "anchor": "FIXED_2200_UTC_NY5PM_DAILY (22:00->22:00)"},
}


def _catalog_entry(tf):
    meta = _CATALOG_TF[tf]
    latest_status = STATUS_PENDING_FIRST_DAILY_SEAL if tf == "D1" else STATUS_ACTIVE
    history_status = STATUS_BLOCKED_UNTIL_D1_LATEST_GREEN if tf == "D1" else STATUS_ACTIVE
    fwd_status = STATUS_BLOCKED_UNTIL_D1_LATEST_GREEN if tf == "D1" else STATUS_ACTIVE
    notes = None
    if tf == "D1":
        notes = "D1 latest armed, awaiting first live 6x complete-OK H4 daily seal; history blocked until D1 latest GREEN"
    return {
        "timeframe": tf,
        "latest_key": f"hermes:candles:XAU_USD:{tf}:latest:v1",
        "latest_status": latest_status,
        "history_key_pattern": f"hermes:candles:XAU_USD:{tf}:history:v1:{{open_epoch}}",
        "history_index_key": f"hermes:candles:XAU_USD:{tf}:history:v1:index",
        "history_status": history_status,
        "source_timeframe": meta["src"],
        "derivation_policy": meta["policy"],
        "expected_source_count": meta["exp"],
        "anchor": meta["anchor"],
        "ttl_seconds_history": HISTORY_TTL_SECONDS,
        "retention_days_history": 35,
        "freshness_policy": "latest valid for one timeframe period; consumers freshness-gate on valid_until_utc",
        "forward_history_status": fwd_status,
        "contract_version": "v1",
        "notes": notes,
    }


def build_candle_catalog(*, generated_at_utc):
    """hermes:catalog:candles:v1 payload — per-timeframe latest/history mapping + source policy + status. Pure; no I/O."""
    cat = {
        "publisher": PUBLISHER, "schema_version": SCHEMA_VERSION, "contract_version": "v1",
        "generated_at_utc": _utc(generated_at_utc), "canonical_instruments": list(CANONICAL_INSTRUMENTS),
        "timeframes": {tf: _catalog_entry(tf) for tf in ("M1", "M5", "M15", "H1", "H4", "D1")},
        "source_policies": SOURCE_POLICIES,
    }
    validate_candle_catalog(cat)
    return cat


def validate_candle_catalog(cat):
    _assert_utc_field(cat.get("generated_at_utc"), "generated_at_utc")
    tfs = cat.get("timeframes") or {}
    for tf in ("M1", "M5", "M15", "H1", "H4", "D1"):
        e = tfs.get(tf)
        if not e:
            raise ValueError(f"GOV-HERMES-CP-030: catalog missing timeframe {tf}")
        if not e["latest_key"].endswith(":latest:v1") or ":history:v1:" not in e["history_key_pattern"]:
            raise ValueError(f"GOV-HERMES-CP-031: catalog {tf} key patterns malformed")
        if e["latest_status"] not in FAMILY_STATUS_VOCAB or e["history_status"] not in FAMILY_STATUS_VOCAB:
            raise ValueError(f"GOV-HERMES-CP-032: catalog {tf} status not in vocab")
    if tfs["H4"]["source_timeframe"] != "H1":
        raise ValueError("GOV-HERMES-CP-033: H4 source_timeframe must be H1")
    if tfs["D1"]["source_timeframe"] != "H4" or tfs["D1"]["expected_source_count"] != 6:
        raise ValueError("GOV-HERMES-CP-034: D1 source_timeframe must be H4 / expected 6")
    if tfs["D1"]["latest_status"] != STATUS_PENDING_FIRST_DAILY_SEAL:
        raise ValueError("GOV-HERMES-CP-035: D1 latest must be PENDING_FIRST_DAILY_SEAL")
    if tfs["D1"]["history_status"] != STATUS_BLOCKED_UNTIL_D1_LATEST_GREEN:
        raise ValueError("GOV-HERMES-CP-036: D1 history must be BLOCKED_UNTIL_D1_LATEST_GREEN")
    _scan_no_forbidden_field_keys(cat)
    return True


# --------------------------------------------------------------------------- 4. health summary
def build_health_summary(*, generated_at_utc, overall_status="OK"):
    """hermes:health:v1 payload — per-family health with explicit absence semantics (ACTIVE/PENDING/BLOCKED/
    NOT_IMPLEMENTED/LEGACY_DEPRECATED/OWNERSHIP_PENDING/FAULT). No regime/risk fields. Pure; no I/O."""
    if overall_status not in OVERALL_STATUS_VOCAB:
        raise ValueError(f"GOV-HERMES-CP-040: overall_status must be OK/WARN/FAIL (got {overall_status!r})")
    h = {
        "publisher": PUBLISHER, "schema_version": SCHEMA_VERSION, "contract_version": "v1",
        "generated_at_utc": _utc(generated_at_utc), "overall_status": overall_status,
        "per_family_health": {
            "candle_latest": STATUS_ACTIVE, "candle_history": STATUS_ACTIVE, "forward_history": STATUS_ACTIVE,
            "candle_latest_d1": STATUS_PENDING_FIRST_DAILY_SEAL,
            "candle_history_d1": STATUS_BLOCKED_UNTIL_D1_LATEST_GREEN,
            "indicators": STATUS_NOT_IMPLEMENTED, "candle_features": STATUS_NOT_IMPLEMENTED,
            "feed_health": STATUS_INVENTORY_PENDING, "sessions": STATUS_OWNERSHIP_PENDING,
            "instrument_catalog": STATUS_PARTIAL, "control_plane": STATUS_PENDING,
        },
        "per_timeframe_freshness": {tf: STATUS_ACTIVE for tf in _LATEST_TFS},
        "candle_latest_health": STATUS_ACTIVE,
        "candle_history_health": STATUS_ACTIVE,
        "forward_history_health": STATUS_ACTIVE,
        "d1_health": {"latest": STATUS_PENDING_FIRST_DAILY_SEAL, "history": STATUS_BLOCKED_UNTIL_D1_LATEST_GREEN},
        "control_plane_health": STATUS_PENDING,            # this control-plane is code-only, not yet activated
        "missing_but_expected_families": ["indicators", "candle_features", "feed_health",
                                          "sessions", "contract_manifest", "publisher_heartbeat",
                                          "candle_catalog", "health_summary"],
        "intentionally_absent_families": ["regime (ARES-owned)", "interpretive_levels (ARES-owned)",
                                          "decision_gating (ARES-owned)"],
        "legacy_deprecated_families": ["hermes:signals:*", "hermes:market_map:*", "hermes:instrument:* (partial)"],
        "fault_codes": [],
        "warning_codes": [],
        "ownership_notes": "HERMES owns deterministic market data + Redis contracts; regime/risk/interpretive "
                           "context is ARES-owned. HERMES publishes no regime or risk conclusions.",
    }
    validate_health(h)
    return h


def validate_health(h):
    if h.get("overall_status") not in OVERALL_STATUS_VOCAB:
        raise ValueError("GOV-HERMES-CP-041: health overall_status must be OK/WARN/FAIL")
    _assert_utc_field(h.get("generated_at_utc"), "generated_at_utc")
    pf = h.get("per_family_health") or {}
    if not pf:
        raise ValueError("GOV-HERMES-CP-042: health per_family_health required")
    for fam, st in pf.items():
        if st not in FAMILY_STATUS_VOCAB:
            raise ValueError(f"GOV-HERMES-CP-043: health family {fam} status {st!r} not in vocab")
    for req in ("missing_but_expected_families", "intentionally_absent_families", "legacy_deprecated_families"):
        if req not in h:
            raise ValueError(f"GOV-HERMES-CP-044: health missing {req}")
    if pf.get("candle_latest_d1") != STATUS_PENDING_FIRST_DAILY_SEAL:
        raise ValueError("GOV-HERMES-CP-045: health D1 latest must be PENDING_FIRST_DAILY_SEAL")
    _scan_no_forbidden_field_keys(h)
    return True


# --------------------------------------------------------------------------- gated factory
class DisabledControlPlane:
    """Safe no-op (default). No Redis client, no connection, no I/O. Builds nothing until enabled+authorised."""
    enabled = False

    def status(self):
        return {"enabled": False}


class ControlPlaneBuilder:
    """ENABLED + AUTHORISED. Builds the governed v1 control-plane PAYLOADS only — still NO Redis client and NO
    I/O (publishing is a separate, later, authorised WO)."""
    enabled = True

    def status(self):
        return {"enabled": True, "keys": list(CONTROL_PLANE_KEYS)}

    def manifest(self, **kw):
        return build_contract_manifest(**kw)

    def heartbeat(self, **kw):
        return build_publisher_heartbeat(**kw)

    def candle_catalog(self, **kw):
        return build_candle_catalog(**kw)

    def health(self, **kw):
        return build_health_summary(**kw)


def build_control_plane_from_env():
    """Boot factory. DEFAULT DISABLED -> DisabledControlPlane (no-op, no Redis client, no I/O). ENABLED without
    AUTHORISED -> terminal halt SystemExit(101). ENABLED + AUTHORISED -> ControlPlaneBuilder (payloads only, no
    Redis I/O). No hidden defaults; lazy env read; NO Redis/network/SQL/file I/O at import or here."""
    from env_config import get_env_bool   # lazy; HERMES-owned config only
    if not get_env_bool(ENABLED_ENV, False):
        return DisabledControlPlane()
    if not get_env_bool(AUTHORISED_ENV, False):
        raise SystemExit(HALT_CODE)        # fail-loud terminal halt (exit 101) — enabled without authorisation
    return ControlPlaneBuilder()
