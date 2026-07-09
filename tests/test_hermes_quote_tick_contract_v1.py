"""HERMES deterministic quote contract + tick reconciliation v1 — code-only, zero I/O, no Redis writes.
WO-HELM-HERMES-QUOTE-TICK-CONTRACT-RECONCILIATION-V1-0001.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

import utils.hermes_quote_tick_contract_v1 as qt
import utils.tick_contract_v1 as tickc
import utils.candle_contract_v1 as cc

UTC = timezone.utc
_NOW = datetime(2026, 7, 2, 12, 0, 0, tzinfo=UTC)


def _build(**over):
    kw = dict(instrument="XAU_USD", generated_at_utc=_NOW, source_name="OANDA", bid=2000.0, ask=2000.4,
              source_timestamp_utc=_NOW - timedelta(seconds=1))
    kw.update(over)
    return qt.build_quote_contract(**kw)


# ============================ keys / identity ============================
def test_quote_key_versioned():
    assert qt.quote_key("XAU_USD") == "hermes:quote:XAU_USD:v1"


def test_tick_surface_referenced_not_rebuilt():
    ref = qt.governed_tick_surface_reference()
    assert ref["key"] == "hermes:ticks:XAU_USD:latest:v1"           # EXISTING tick surface, versioned
    assert "does not rebuild" in ref["note"]


def test_canonical_instrument_and_alias_denied_output():
    p = _build()
    assert p["instrument"] == "XAU_USD" and p["canonical_instrument"] == "XAU_USD"
    for bad in ("XAUUSD", "EUR_USD"):
        with pytest.raises(ValueError) as e:
            qt.quote_key(bad)
        assert "GOV-HERMES-QT-004" in str(e.value)
    assert all(":XAUUSD:" not in k for k in _iter_strings(p))


def _iter_strings(o):
    if isinstance(o, dict):
        for v in o.values():
            yield from _iter_strings(v)
    elif isinstance(o, (list, tuple)):
        for x in o:
            yield from _iter_strings(x)
    elif isinstance(o, str):
        yield o


# ============================ arithmetic / validation ============================
def test_payload_versioned_and_ttl_freshness():
    p = _build()
    assert p["schema_version"] == "v1" and p["contract"] == "quote:v1"
    assert isinstance(p["ttl_seconds"], int) and p["ttl_seconds"] > 0
    assert p["freshness"] in (qt.FRESHNESS_FRESH, qt.FRESHNESS_STALE, qt.FRESHNESS_UNAVAILABLE)


def test_bid_ask_mid_spread_correct():
    p = _build(bid=2000.0, ask=2000.4)
    assert p["mid"] == 2000.2 and p["spread"] == 0.4
    assert p["spread_bps"] == round((0.4 / 2000.2) * 10000, 4)
    assert p["status"] == qt.STATUS_GREEN


def test_spread_points_only_with_point_size():
    assert _build()["spread_points"] is None
    assert _build(point_size=0.01)["spread_points"] == round(0.4 / 0.01, 4)


def test_inverted_quote_fails_loud():
    with pytest.raises(ValueError) as e:
        _build(bid=2000.4, ask=2000.0)
    assert "GOV-HERMES-QT-006" in str(e.value)


def test_missing_bid_ask_red_missing():
    p = _build(bid=None, ask=None)
    assert p["status"] == qt.STATUS_RED_MISSING and p["bid"] is None and p["freshness"] == qt.FRESHNESS_UNAVAILABLE


def test_stale_timestamp_amber():
    p = _build(source_timestamp_utc=_NOW - timedelta(seconds=60))     # age 60 > 10s threshold
    assert p["status"] == qt.STATUS_AMBER_STALE and p["age_seconds"] == 60.0


def test_unknown_source_and_missing_ts_fail_loud():
    assert _build(connectivity=qt.CONN_UNKNOWN)["status"] == qt.STATUS_UNKNOWN
    # bid/ask present but NO source timestamp -> cannot prove freshness -> not GREEN
    p = _build(source_timestamp_utc=None, last_quote_utc=None)
    assert p["status"] == qt.STATUS_UNKNOWN


def test_offline_red_degraded_amber():
    assert _build(connectivity=qt.CONN_OFFLINE)["status"] == qt.STATUS_RED_MISSING
    assert _build(connectivity=qt.CONN_DEGRADED)["status"] == qt.STATUS_AMBER_STALE


def test_validate_rejects_inverted_and_bad_arithmetic():
    p = _build()
    p["ask"], p["bid"] = 1.0, 2.0
    with pytest.raises(ValueError) as e:
        qt.validate_quote_contract(p)
    assert "GOV-HERMES-QT-018" in str(e.value)


def test_utc_timestamps():
    p = _build(last_quote_utc=_NOW, received_at_utc=_NOW)
    assert p["generated_at_utc"].endswith("Z") and p["source_timestamp_utc"].endswith("Z")
    assert p["last_quote_utc"].endswith("Z") and p["received_at_utc"].endswith("Z")


# ============================ reconciliation ============================
def test_reconcile_from_governed_tick_envelope():
    env = tickc.build_tick_contract(instrument="XAU_USD", source_received_at_utc=_NOW - timedelta(seconds=1),
                                    generated_at_utc=_NOW, bid=2000.0, ask=2000.4, source="oanda")
    p = qt.reconcile_quote_from_tick_envelope(env, generated_at_utc=_NOW, source_name="oanda")
    assert p["bid"] == 2000.0 and p["ask"] == 2000.4 and p["mid"] == 2000.2 and p["spread"] == 0.4
    assert p["status"] == qt.STATUS_GREEN
    assert "hermes:ticks:XAU_USD:latest:v1" in p["source_dependencies"]


# --- WO-HELM-HERMES-QUOTE-PROVENANCE-SOURCE-LABEL-0001: source-label propagation from upstream tick provenance ---

def _tick_env(source="oanda", bid=2000.0, ask=2000.4):
    return tickc.build_tick_contract(instrument="XAU_USD", source_received_at_utc=_NOW - timedelta(seconds=1),
                                     generated_at_utc=_NOW, bid=bid, ask=ask, source=source)


def test_provenance_helper_reads_governed_tick_source():
    # tick_provenance_source reads provenance.source first, then data.source; None when absent (never invents).
    env = _tick_env(source="oanda")
    assert env["provenance"]["source"] == "oanda" and env["data"]["source"] == "oanda"
    assert qt.tick_provenance_source(env) == "oanda"
    assert qt.tick_provenance_source({"data": {"source": "oanda"}}) == "oanda"    # data.source fallback
    assert qt.tick_provenance_source({"provenance": {"source": None}, "data": {"source": None}}) is None
    assert qt.tick_provenance_source(None) is None


def test_source_label_propagates_from_tick_when_caller_default_unknown():
    # ROOT CAUSE FIX: caller default sentinel "UNKNOWN" must NOT shadow the real tick provenance source.
    env = _tick_env(source="oanda")
    for caller in (None, "", qt.SOURCE_NAME_UNKNOWN):
        p = qt.reconcile_quote_from_tick_envelope(env, generated_at_utc=_NOW, source_name=caller)
        assert p["source"]["name"] == "oanda", f"caller={caller!r} should propagate tick provenance 'oanda'"
    # and the direct resolver honours the same precedence
    assert qt.resolve_quote_source_label(qt.SOURCE_NAME_UNKNOWN, env) == "oanda"
    assert qt.resolve_quote_source_label(None, env) == "oanda"


def test_explicit_caller_source_label_overrides_tick():
    # an operator-set explicit (real, non-sentinel) label still wins over the tick provenance.
    env = _tick_env(source="oanda")
    p = qt.reconcile_quote_from_tick_envelope(env, generated_at_utc=_NOW, source_name="oanda_demo")
    assert p["source"]["name"] == "oanda_demo"
    assert qt.resolve_quote_source_label("oanda_demo", env) == "oanda_demo"


def test_source_label_safe_fallback_when_tick_has_no_source():
    # tick provenance absent (UNAVAILABLE-style) + caller default -> safe UNKNOWN sentinel, never fabricated.
    envless = {"provenance": {"source": None}, "data": {"source": None, "bid": 2000.0, "ask": 2000.4,
                                                        "received_at_utc": tickc._fmt(_NOW - timedelta(seconds=1))}}
    assert qt.resolve_quote_source_label(None, envless) == qt.SOURCE_NAME_UNKNOWN
    assert qt.resolve_quote_source_label(qt.SOURCE_NAME_UNKNOWN, envless) == qt.SOURCE_NAME_UNKNOWN
    # explicit override still respected even with a source-less tick
    assert qt.resolve_quote_source_label("oanda", envless) == "oanda"


def test_provenance_fix_preserves_deterministic_pricing_and_key():
    # source-label change must not touch pricing/freshness/key/instrument/version/dependency.
    env = _tick_env(source="oanda", bid=2000.0, ask=2000.4)
    p = qt.reconcile_quote_from_tick_envelope(env, generated_at_utc=_NOW, source_name=None)
    assert p["bid"] == 2000.0 and p["ask"] == 2000.4                 # copied from tick
    assert p["mid"] == (2000.0 + 2000.4) / 2 and p["spread"] == round(2000.4 - 2000.0, 10)
    assert p["ask"] >= p["bid"]
    assert p["instrument"] == "XAU_USD" and "XAUUSD" not in p["instrument"]
    assert p["schema_version"] == "v1" and p["status"] == qt.STATUS_GREEN and p["freshness"] == qt.FRESHNESS_FRESH
    assert qt.quote_key("XAU_USD") == "hermes:quote:XAU_USD:v1"
    assert p["source_dependencies"] == ["hermes:ticks:XAU_USD:latest:v1"]
    assert p["notes"] == ["reconciled_from_governed_tick_surface"]
    assert p.get("consumer_live") is None                            # reconciler never asserts consumer-live


def test_reconcile_from_legacy_signal_drops_interpretation():
    legacy = {"bid": "2000.0", "ask": "2000.4", "mid": "9999", "spread": "9999",  # legacy mid/spread ignored+recomputed
              "timestamp_utc": cc._fmt(_NOW - timedelta(seconds=1)), "source": "oanda",
              "signal_type": "LONG", "decision": "GO", "regime": "TRENDING", "setup_quality": "A"}
    p = qt.reconcile_quote_from_legacy_snapshot(legacy, generated_at_utc=_NOW, origin="legacy_signal")
    assert p["bid"] == 2000.0 and p["ask"] == 2000.4 and p["mid"] == 2000.2 and p["spread"] == 0.4  # recomputed
    # NO interpretation preserved anywhere in the payload
    keys = {k.lower() for k in _all_keys(p)}
    assert not any(t in k for k in keys for t in ("signal", "decision", "regime", "setup"))
    assert qt.validate_quote_contract(p) is True


def test_reconcile_from_legacy_market_map_deterministic_only():
    mm = {"bid": 2000.0, "ask": 2000.4, "timestamp_utc": cc._fmt(_NOW - timedelta(seconds=1)),
          "session_bias": "BULLISH", "liquidity_zone": "HIGH", "order_block": "DEMAND"}
    p = qt.reconcile_quote_from_legacy_snapshot(mm, generated_at_utc=_NOW, origin="legacy_market_map")
    assert p["bid"] == 2000.0 and p["ask"] == 2000.4
    keys = {k.lower() for k in _all_keys(p)}
    assert not any(t in k for k in keys for t in ("bias", "liquidity", "order_block"))


def test_reconcile_missing_legacy_quote_fields_explicit():
    p = qt.reconcile_quote_from_legacy_snapshot({"source": "oanda"}, generated_at_utc=_NOW)
    assert p["status"] == qt.STATUS_RED_MISSING and p["bid"] is None and p["ask"] is None


def _all_keys(o):
    if isinstance(o, dict):
        for k, v in o.items():
            yield k
            yield from _all_keys(v)
    elif isinstance(o, (list, tuple)):
        for x in o:
            yield from _all_keys(x)


# ============================ policy / hygiene ============================
@pytest.mark.parametrize("bad", ["regime", "risk_state", "go_no_go", "trade_permission", "setup_quality",
                                 "smart_money_zone", "order_block_meaning", "decision_gate", "entry_advice",
                                 "signal_type"])
def test_ares_owned_fields_rejected(bad):
    p = _build()
    p["notes"] = [{bad: 1}]
    with pytest.raises(ValueError) as e:
        qt.validate_quote_contract(p)
    assert "GOV-HERMES-QT-003" in str(e.value)


def test_deterministic_serialization():
    assert json.dumps(_build(), sort_keys=True) == json.dumps(_build(), sort_keys=True)


def test_no_redis_sql_network_io_or_auth_at_import():
    src = open(qt.__file__).read()
    assert "\nimport redis" not in src and "redis.Redis(" not in src and "\nfrom redis" not in src
    assert "pymysql" not in src and "\nimport socket" not in src and "urllib" not in src and "requests" not in src
    assert ".set(" not in src and ".zadd(" not in src and ".setex(" not in src
    low = src.lower()
    for m in ("password=", "requirepass", ".auth(", "acl setuser", "username=", "ssl=", "192.168.", "6379", "localhost"):
        assert m not in low


def test_publisher_disabled_by_default_and_101(monkeypatch):
    monkeypatch.delenv(qt.QUOTE_ENABLED_ENV, raising=False)
    assert isinstance(qt.build_quote_publisher_from_env(), qt.DisabledQuotePublisher)
    monkeypatch.setenv(qt.QUOTE_ENABLED_ENV, "true")
    monkeypatch.delenv(qt.QUOTE_AUTHORISED_ENV, raising=False)
    with pytest.raises(SystemExit) as e:
        qt.build_quote_publisher_from_env()
    assert e.value.code == 101
