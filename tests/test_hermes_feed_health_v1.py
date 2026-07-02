"""HERMES deterministic feed/ingestion health contract v1 — code-only, zero I/O, no Redis writes.
WO-HELM-HERMES-FEED-HEALTH-CONTRACT-V1-0001.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

import utils.hermes_feed_health_v1 as fh
import utils.candle_contract_v1 as cc

UTC = timezone.utc
_NOW = datetime(2026, 7, 2, 12, 0, tzinfo=UTC)
_TFS = ("M1", "M5", "M15", "H1", "H4", "D1")


def _fresh_closes(now=_NOW):
    """A last-closed candle per TF that is FRESH (age <= 2x span)."""
    return {tf: now - timedelta(seconds=cc.TF_SECONDS[tf]) for tf in _TFS if tf != "D1"}


def _build(**over):
    kw = dict(instrument="XAU_USD", generated_at_utc=_NOW, source_name="OANDA", connectivity=fh.CONN_CONNECTED,
              timeframes=_TFS, last_candle_close_utc_by_tf=_fresh_closes(), d1_latest_green=False)
    kw.update(over)
    return fh.build_feed_health_contract(**kw)


# ============================ status logic ============================
def test_fresh_all_timeframes_green():
    p = _build()
    assert p["status"] == fh.STATUS_GREEN and p["freshness"] == fh.FRESHNESS_FRESH
    assert set(p["healthy_timeframes"]) == {"M1", "M5", "M15", "H1", "H4"}
    assert p["gated_timeframes"] == ["D1"]                       # D1 gated (not green) -> does not break GREEN
    assert fh.validate_feed_health_contract(p) is True


def test_missing_latest_returns_red_missing():
    closes = _fresh_closes()
    closes["H1"] = None                                          # missing latest H1
    p = _build(last_candle_close_utc_by_tf=closes)
    assert p["status"] == fh.STATUS_RED_MISSING and "H1" in p["missing_timeframes"]
    assert p["freshness"] == fh.FRESHNESS_UNAVAILABLE


def test_stale_timeframe_returns_amber_stale():
    closes = _fresh_closes()
    closes["M1"] = _NOW - timedelta(seconds=cc.TF_SECONDS["M1"] * 5)   # way beyond 2x span
    p = _build(last_candle_close_utc_by_tf=closes)
    assert p["status"] == fh.STATUS_AMBER_STALE and "M1" in p["stale_timeframes"]
    assert p["freshness"] == fh.FRESHNESS_STALE


def test_missing_d1_stays_gated_not_red_until_green():
    # D1 has no last close AND is not green -> GATED, and does NOT make the aggregate RED
    p = _build(d1_latest_green=False)
    assert p["per_timeframe_health"]["D1"]["status"] == fh.STATUS_GATED
    assert "D1" not in p["missing_timeframes"] and p["status"] == fh.STATUS_GREEN
    # once D1 is green + a fresh D1 close exists -> D1 counts normally
    closes = _fresh_closes()
    closes["D1"] = _NOW - timedelta(seconds=cc.TF_SECONDS["D1"])
    p2 = _build(last_candle_close_utc_by_tf=closes, d1_latest_green=True)
    assert p2["per_timeframe_health"]["D1"]["status"] == fh.STATUS_GREEN and "D1" in p2["healthy_timeframes"]


def test_unknown_source_fails_loud_needs_probe():
    p = _build(connectivity=fh.CONN_UNKNOWN)
    assert p["status"] == fh.STATUS_UNKNOWN and p["freshness"] == fh.FRESHNESS_UNAVAILABLE


def test_offline_source_red_degraded_amber():
    assert _build(connectivity=fh.CONN_OFFLINE)["status"] == fh.STATUS_RED_MISSING
    assert _build(connectivity=fh.CONN_DEGRADED)["status"] == fh.STATUS_AMBER_STALE


# ============================ contract shape / policy ============================
def test_key_versioned_xau_only():
    assert fh.feed_health_key("XAU_USD") == "hermes:feed_health:XAU_USD:v1"
    for bad in ("XAUUSD", "EUR_USD"):
        with pytest.raises(ValueError) as e:
            fh.feed_health_key(bad)
        assert "GOV-HERMES-FH-004" in str(e.value)


def test_payload_versioned_and_ttl_freshness_present():
    p = _build()
    assert p["schema_version"] == "v1" and p["contract"] == "feed_health:v1"
    assert p["canonical_instrument"] == "XAU_USD"
    assert isinstance(p["ttl_seconds"], int) and p["ttl_seconds"] > 0
    assert p["freshness"] in (fh.FRESHNESS_FRESH, fh.FRESHNESS_STALE, fh.FRESHNESS_UNAVAILABLE)
    assert p["provenance"]["method_config"]["stale_tf_multiplier"] == fh.STALE_TF_MULTIPLIER


def test_all_timestamps_utc_z():
    p = _build(last_tick_utc=_NOW, last_quote_utc=_NOW)
    assert p["generated_at_utc"].endswith("Z")
    assert p["last_tick_utc"].endswith("Z") and p["last_quote_utc"].endswith("Z")
    for tf, v in p["last_candle_utc_by_tf"].items():
        assert v is None or v.endswith("Z")


@pytest.mark.parametrize("bad", ["regime", "risk_state", "go_no_go", "setup_quality", "trade_permission",
                                 "liquidity_block", "smart_money_zone", "order_block_meaning", "decision_gate",
                                 "gating_conclusion"])
def test_ares_owned_fields_rejected(bad):
    with pytest.raises(ValueError) as e:
        p = _build()
        p["notes"] = [{bad: 1}]
        fh.validate_feed_health_contract(p)
    assert "GOV-HERMES-FH-003" in str(e.value)


def test_deterministic_serialization():
    a = json.dumps(_build(), sort_keys=True)
    b = json.dumps(_build(), sort_keys=True)
    assert a == b                                               # same inputs -> identical serialization


# ============================ gating / no I/O / no auth ============================
def test_publisher_disabled_by_default(monkeypatch):
    monkeypatch.delenv(fh.ENABLED_ENV, raising=False)
    assert isinstance(fh.build_feed_health_publisher_from_env(), fh.DisabledFeedHealthPublisher)


def test_publisher_enabled_without_authorised_exits_101(monkeypatch):
    monkeypatch.setenv(fh.ENABLED_ENV, "true")
    monkeypatch.delenv(fh.AUTHORISED_ENV, raising=False)
    with pytest.raises(SystemExit) as e:
        fh.build_feed_health_publisher_from_env()
    assert e.value.code == 101


def test_no_redis_sql_network_io_or_auth_at_import():
    src = open(fh.__file__).read()
    assert "\nimport redis" not in src and "redis.Redis(" not in src and "\nfrom redis" not in src
    assert "pymysql" not in src and "\nimport socket" not in src and "urllib" not in src and "requests" not in src
    assert ".set(" not in src and ".zadd(" not in src and ".setex(" not in src   # builder/collector never write
    low = src.lower()
    for m in ("password=", "requirepass", ".auth(", "acl setuser", "username=", "ssl=", "192.168.", "6379", "localhost"):
        assert m not in low


def test_collector_reads_only_no_writes():
    class FakeRedis:
        def __init__(self):
            self.gets = 0
            now = _NOW - timedelta(seconds=60)
            env = {"status": "OK", "data": {"timestamp_utc": cc._fmt(now), "is_closed": True}}
            self.store = {f"hermes:candles:XAU_USD:{tf}:latest:v1": json.dumps(env) for tf in ("M1", "H1")}
            self.store["hermes:publisher:heartbeat:v1"] = json.dumps({"status": "OK", "updated_at_utc": cc._fmt(_NOW)})

        def get(self, k):
            self.gets += 1
            return self.store.get(k)

        def set(self, *a, **k):
            raise AssertionError("collector must not write")

    r = FakeRedis()
    snap = fh.collect_feed_health_snapshot(r, timeframes=("M1", "H1", "D1"), generated_at_utc=_NOW,
                                           source_name="OANDA", d1_latest_green=False)
    assert snap["connectivity"] == fh.CONN_CONNECTED and r.gets > 0
    assert snap["last_candle_close_utc_by_tf"]["D1"] is None      # no D1 latest -> None (stays GATED downstream)
    p = fh.build_feed_health_contract(**{k: v for k, v in snap.items()})
    assert p["status"] in fh.FEED_HEALTH_STATUSES and fh.validate_feed_health_contract(p) is True
