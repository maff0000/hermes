"""HERMES deterministic instrument-catalog contract v1 — code-only, zero I/O, no Redis writes.
WO-HELM-HERMES-INSTRUMENT-CATALOG-CONTRACT-V1-0001.
"""
import copy
import json
from datetime import datetime, timezone

import pytest

import utils.hermes_instrument_catalog_v1 as ic

UTC = timezone.utc
_NOW = datetime(2026, 7, 2, 12, 0, tzinfo=UTC)


def _build(**over):
    kw = dict(instrument="XAU_USD", generated_at_utc=_NOW, source_name="OANDA")
    kw.update(over)
    return ic.build_instrument_catalog_contract(**kw)


# ============================ identity / alias ============================
def test_canonical_instrument_xau_usd():
    p = _build()
    assert p["instrument"] == "XAU_USD" and p["canonical_instrument"] == "XAU_USD"
    assert p["aliases"]["inbound"] == ["XAUUSD"] and p["aliases"]["output_publish_denied"] == ["XAUUSD"]


def test_xauusd_alias_never_output_key():
    for bad in ("XAUUSD", "EUR_USD"):
        with pytest.raises(ValueError) as e:
            ic.instrument_catalog_key(bad)
        assert "GOV-HERMES-IC-004" in str(e.value)
    # no contract key anywhere in the payload is published under XAUUSD
    p = _build()
    assert all(":XAUUSD:" not in k for k in ic._iter_key_strings(p) if isinstance(k, str))
    # a smuggled XAUUSD key fails validation loud
    p2 = _build()
    p2["candle_contracts"]["M1"]["latest_key"] = "hermes:candles:XAUUSD:M1:latest:v1"
    with pytest.raises(ValueError) as e:
        ic.validate_instrument_catalog_contract(p2)
    assert "GOV-HERMES-IC-017" in str(e.value)


# ============================ versioning / ttl ============================
def test_key_versioned():
    assert ic.instrument_catalog_key("XAU_USD") == "hermes:instrument_catalog:XAU_USD:v1"


def test_payload_versioned_and_ttl_present():
    p = _build()
    assert p["schema_version"] == "v1" and p["contract"] == "instrument_catalog:v1"
    assert isinstance(p["ttl_seconds"], int) and p["ttl_seconds"] > 0
    assert p["provenance"]["deterministic_only"] is True


# ============================ surface representation ============================
def test_m1_h4_candle_surfaces_active():
    p = _build()
    for tf in ("M1", "M5", "M15", "H1", "H4"):
        assert p["candle_contracts"][tf]["latest_status"] == ic.SURFACE_ACTIVE
        assert p["candle_contracts"][tf]["latest_key"] == f"hermes:candles:XAU_USD:{tf}:latest:v1"
    assert set(p["active_timeframes"]) == {"M1", "M5", "M15", "H1", "H4"}
    assert p["status"] == ic.STATUS_GREEN


def test_d1_latest_gated_pending():
    p = _build()
    assert p["candle_contracts"]["D1"]["latest_status"] == ic.SURFACE_PENDING_FIRST_DAILY_SEAL
    assert "D1" in p["gated_timeframes"] and "D1" not in p["active_timeframes"]
    assert p["d1_policy"]["latest_status"] == ic.SURFACE_PENDING_FIRST_DAILY_SEAL
    assert p["d1_policy"]["anchor_utc"] == "22:00:00_FIXED" and p["d1_policy"]["dst_adjustment"] is False


def test_d1_derived_surfaces_remain_gated_unless_active():
    p = _build()
    assert p["indicator_contracts"]["D1"]["status"] == ic.SURFACE_GATED
    assert p["candle_feature_contracts"]["D1"]["status"] == ic.SURFACE_GATED
    assert p["level_contracts"]["daily"]["status"] == ic.SURFACE_GATED
    assert p["level_contracts"]["weekly"]["status"] == ic.SURFACE_GATED
    assert p["candle_contracts"]["D1"]["history_status"] == ic.SURFACE_BLOCKED and "D1" in p["blocked_timeframes"]


def test_feed_health_code_present_dark_by_default():
    p = _build()
    assert p["feed_health_contract"]["status"] == ic.SURFACE_CODE_PRESENT_DARK
    assert p["feed_health_contract"]["key"] == "hermes:feed_health:XAU_USD:v1"


def test_quote_tick_code_present_dark():
    # WO-HELM-HERMES-CATALOG-RECONCILE-QUOTE-TICK-DARK-STATE-0001: quote (PR #68) + tick (existing tick_contract_v1)
    # are CODE_PRESENT_DARK — merged/present but NOT live/activated. Keys are discovery facts, not liveness claims.
    p = _build()
    assert p["quote_contract"]["status"] == ic.SURFACE_CODE_PRESENT_DARK
    assert p["quote_contract"]["key"] == "hermes:quote:XAU_USD:v1" and p["quote_contract"]["live"] is False
    assert p["tick_contract"]["status"] == ic.SURFACE_CODE_PRESENT_DARK
    assert p["tick_contract"]["key"] == "hermes:ticks:XAU_USD:latest:v1" and p["tick_contract"]["live"] is False


def test_no_duplicate_tick_key_introduced():
    p = _build()
    # tick references the EXISTING hermes:ticks:...latest:v1; NO duplicate singular hermes:tick:XAU_USD:v1 anywhere
    assert all("hermes:tick:XAU_USD:v1" != k for k in ic._iter_key_strings(p))
    assert p["tick_contract"]["key"] == "hermes:ticks:XAU_USD:latest:v1"
    assert p["contract_keys"]["quote"] == "hermes:quote:XAU_USD:v1"
    assert p["contract_keys"]["tick"] == "hermes:ticks:XAU_USD:latest:v1"


def test_quote_tick_not_active_and_not_expected_active():
    # CODE_PRESENT_DARK must NOT make the catalog claim quote/tick ACTIVE, and must not degrade the core GREEN.
    p = _build()
    assert p["quote_contract"]["status"] != ic.SURFACE_ACTIVE and p["tick_contract"]["status"] != ic.SURFACE_ACTIVE
    assert p["status"] == ic.STATUS_GREEN                       # core surfaces still GREEN; dark quote/tick don't degrade
    assert "quote" not in p["active_timeframes"] and "tick" not in p["active_timeframes"]


def test_d1_default_not_hardcoded_active():
    # a verified live D1 seal must NOT hard-code the catalog default to ACTIVE — default stays PENDING/gated
    p = _build()
    assert p["candle_contracts"]["D1"]["latest_status"] == ic.SURFACE_PENDING_FIRST_DAILY_SEAL
    assert p["d1_policy"]["latest_status"] == ic.SURFACE_PENDING_FIRST_DAILY_SEAL
    # D1 only ACTIVE when the snapshot EXPLICITLY provides it
    snap = ic.default_catalog_snapshot()
    snap["candle_latest"]["D1"] = ic.SURFACE_ACTIVE
    snap["d1_latest"] = ic.SURFACE_ACTIVE
    p2 = _build(snapshot=snap)
    assert p2["candle_contracts"]["D1"]["latest_status"] == ic.SURFACE_ACTIVE


def test_legacy_surfaces_represented_not_deleted():
    p = _build()
    pats = {c["pattern"]: c["status"] for c in p["legacy_contracts"]}
    assert pats["hermes:signals:*"] == ic.SURFACE_LEGACY
    assert pats["hermes:market_map:*"] == ic.SURFACE_LEGACY_OR_PARTIAL


def test_missing_surface_explicit_not_omitted():
    # every supported timeframe is present in every family map (never silently dropped)
    p = _build()
    for fam in ("candle_contracts", "indicator_contracts", "candle_feature_contracts"):
        assert set(p[fam].keys()) == set(ic.SUPPORTED_TIMEFRAMES)


def test_unknown_surface_fails_loud_needs_probe():
    snap = ic.default_catalog_snapshot()
    snap["indicators"]["H1"] = ic.SURFACE_UNKNOWN
    p = _build(snapshot=snap)
    assert p["status"] == ic.STATUS_UNKNOWN


def test_partial_when_expected_active_missing():
    snap = ic.default_catalog_snapshot()
    snap["candle_latest"]["M5"] = ic.SURFACE_BLOCKED
    p = _build(snapshot=snap)
    assert p["status"] == ic.STATUS_AMBER_PARTIAL


# ============================ policy / hygiene ============================
@pytest.mark.parametrize("bad", ["regime", "risk_state", "go_no_go", "setup_quality", "trade_permission",
                                 "liquidity_block", "smart_money_zone", "order_block_meaning", "decision_gate"])
def test_ares_owned_fields_rejected(bad):
    p = _build()
    p["notes"] = [{bad: 1}]
    with pytest.raises(ValueError) as e:
        ic.validate_instrument_catalog_contract(p)
    assert "GOV-HERMES-IC-003" in str(e.value)


def test_utc_timestamp():
    assert _build()["generated_at_utc"].endswith("Z")


def test_deterministic_serialization():
    assert json.dumps(_build(), sort_keys=True) == json.dumps(_build(), sort_keys=True)


def test_no_redis_sql_network_io_or_auth_at_import():
    src = open(ic.__file__).read()
    assert "\nimport redis" not in src and "redis.Redis(" not in src and "\nfrom redis" not in src
    assert "pymysql" not in src and "\nimport socket" not in src and "urllib" not in src and "requests" not in src
    assert ".set(" not in src and ".zadd(" not in src and ".setex(" not in src   # builder never writes
    low = src.lower()
    for m in ("password=", "requirepass", ".auth(", "acl setuser", "username=", "ssl=", "192.168.", "6379", "localhost"):
        assert m not in low


def test_publisher_disabled_by_default_and_101(monkeypatch):
    monkeypatch.delenv(ic.ENABLED_ENV, raising=False)
    assert isinstance(ic.build_instrument_catalog_publisher_from_env(), ic.DisabledInstrumentCatalogPublisher)
    monkeypatch.setenv(ic.ENABLED_ENV, "true")
    monkeypatch.delenv(ic.AUTHORISED_ENV, raising=False)
    with pytest.raises(SystemExit) as e:
        ic.build_instrument_catalog_publisher_from_env()
    assert e.value.code == 101


# === WO-HELM-HERMES-CATALOG-SELF-SURFACE-PENDING-RUNTIME-MARKERS-0001 (delta) ===
def test_catalog_self_surface_code_present_dark():
    p = _build()
    assert p["surfaces"]["instrument_catalog"] == ic.SURFACE_CODE_PRESENT_DARK
    assert p["instrument_catalog_contract"]["status"] == ic.SURFACE_CODE_PRESENT_DARK
    assert p["instrument_catalog_contract"]["key"] == "hermes:instrument_catalog:XAU_USD:v1"
    assert p["instrument_catalog_contract"]["live"] is False


def test_catalog_self_surface_not_active_and_not_degrading_green():
    p = _build()
    assert p["instrument_catalog_contract"]["status"] != ic.SURFACE_ACTIVE
    assert p["status"] == ic.STATUS_GREEN                      # dark self-surface must not degrade core GREEN


def test_pending_runtime_deployment_markers_present():
    p = _build()
    prd = p["pending_runtime_deployment"]
    assert prd["runtime_live"] is False
    assert set(prd["dark_surfaces"]) == {"feed_health", "instrument_catalog", "quote", "tick"}
    assert "not the same as live publication" in prd["note"] or "not live/published" in prd["note"]


def test_feed_health_marker_live_false():
    assert _build()["feed_health_contract"]["live"] is False


def test_pr69_quote_tick_behaviour_unchanged():
    p = _build()
    assert p["quote_contract"]["status"] == ic.SURFACE_CODE_PRESENT_DARK
    assert p["quote_contract"]["key"] == "hermes:quote:XAU_USD:v1" and p["quote_contract"]["live"] is False
    assert p["tick_contract"]["status"] == ic.SURFACE_CODE_PRESENT_DARK
    assert p["tick_contract"]["key"] == "hermes:ticks:XAU_USD:latest:v1" and p["tick_contract"]["live"] is False
    # no duplicate hermes:tick:* ; no :XAUUSD: output key
    assert all("hermes:tick:XAU_USD:v1" != k for k in ic._iter_key_strings(p))
    assert all(":XAUUSD:" not in k for k in ic._iter_key_strings(p) if isinstance(k, str))


def test_d1_default_still_pending_after_delta():
    p = _build()
    assert p["candle_contracts"]["D1"]["latest_status"] == ic.SURFACE_PENDING_FIRST_DAILY_SEAL
    assert p["d1_policy"]["latest_status"] == ic.SURFACE_PENDING_FIRST_DAILY_SEAL


def test_self_surface_backward_compatible_missing_key():
    # a snapshot built without the new key still validates (defaults to CODE_PRESENT_DARK)
    snap = ic.default_catalog_snapshot(); del snap["instrument_catalog"]
    p = ic.build_instrument_catalog_contract(instrument="XAU_USD", generated_at_utc=_NOW, source_name="OANDA", snapshot=snap)
    assert p["instrument_catalog_contract"]["status"] == ic.SURFACE_CODE_PRESENT_DARK


def test_self_surface_unknown_fails_loud():
    snap = ic.default_catalog_snapshot(); snap["instrument_catalog"] = ic.SURFACE_UNKNOWN
    assert ic.build_instrument_catalog_contract(instrument="XAU_USD", generated_at_utc=_NOW, source_name="OANDA", snapshot=snap)["status"] == ic.STATUS_UNKNOWN
