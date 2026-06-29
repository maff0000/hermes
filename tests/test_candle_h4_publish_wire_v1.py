"""HERMES H4 canonical publish wiring (derived from H1) — code-only, in-memory fake Redis.
WO-HELM-HERMES-GOLD-H4-CANONICAL-PUBLISH-WIRE-0001.
"""
import json
from datetime import datetime, timedelta, timezone

import utils.candle_h4_publish_wire_v1 as wire
import utils.candle_publisher_v1 as cp
import utils.candle_contract_v1 as cc

UTC = timezone.utc
_BO = datetime(2026, 6, 2, 2, 0, tzinfo=UTC)        # an H4 bucket open (02:00 UTC)


class FakeRedis:
    def __init__(self): self.store = {}; self.sets = []
    def set(self, k, v, ex=None):
        assert isinstance(v, (str, bytes)); self.store[k] = (v, ex); self.sets.append((k, v, ex)); return True


class _H1:
    def __init__(self, open_dt, o=2000.0, hi=2008.0, lo=1998.0, c=2004.0, v=10, instrument="XAU_USD"):
        self.instrument = instrument; self.timeframe = "H1"; self.timestamp = open_dt
        self.open, self.high, self.low, self.close, self.volume = o, hi, lo, c, v


def _cfg():
    return cp.CandlePublisherConfig(publish_enabled=True, publish_authorised=True, shadow_publish_enabled=False,
                                    shadow_authorised=False, namespace="hermes", contract_version="v1",
                                    redis_host="192.168.11.10", redis_port=6379, redis_db=0)


def _producer(client=None, allowed=("XAU_USD",)):
    w = cp.SerializingCandleCanonicalWriter(config=_cfg(), redis_client=client or FakeRedis())
    return wire.CanonicalH4Producer(w, allowed_instruments=allowed)


# ---------------- writer key grid: H4 accepted, forbidden rejected ----------------
def test_assert_canonical_key_accepts_h4_xau():
    assert cp.assert_canonical_key("hermes:candles:XAU_USD:H4:latest:v1") is True


def test_assert_canonical_key_rejects_xauusd_legacyD_unversioned_h4():
    # writer-level key guard: alias (004), legacy "D" token not in publish grid (005), unversioned (002).
    # NOTE: D1 is now publish-grid-eligible via the governed D1 producer (WO-...-D1-CANONICAL-PUBLISH-WIRE-0001);
    # the direct seam still refuses D1. Non-XAU instruments are rejected at the producer/seam allowlist.
    for k, code in (("hermes:candles:XAUUSD:H4:latest:v1", "GOV-CANDLE-PUB-CANON-KEY-004"),
                    ("hermes:candles:XAU_USD:D:latest:v1", "GOV-CANDLE-PUB-CANON-KEY-005"),
                    ("hermes:candles:XAU_USD:H4:latest", "GOV-CANDLE-PUB-CANON-KEY-002")):
        try:
            cp.assert_canonical_key(k); assert False, k
        except ValueError as e:
            assert code in str(e)
    assert cp.assert_canonical_key("hermes:candles:XAU_USD:D1:latest:v1") is True   # D1 now governed-publishable


# ---------------- producer: derive from 4 H1 children, publish H4 latest ----------------
def test_producer_publishes_complete_h4_on_roll():
    r = FakeRedis(); p = _producer(r)
    for hh in range(4):                                  # 02,03,04,05 -> bucket 02:00
        assert p.on_h1_close(_H1(_BO + timedelta(hours=hh)))["published"] is False
    res = p.on_h1_close(_H1(_BO + timedelta(hours=4)))   # 06:00 opens next bucket -> seals 02:00
    assert res["published"] is True and res["status"] == "OK"
    assert res["key"] == "hermes:candles:XAU_USD:H4:latest:v1"
    assert res["source_count"] == 4 and res["source_coverage"] == 1.0
    env = json.loads(r.store["hermes:candles:XAU_USD:H4:latest:v1"][0])
    assert cc.validate_candle_contract(env) is True
    d = env["data"]
    assert d["timeframe"] == "H4" and d["instrument"] == "XAU_USD"
    assert env["provenance"]["derivation"] == cc.DERIVATION_DERIVED
    assert env["provenance"]["source_timeframe"] == "H1"
    assert d["derivation_policy"] == cc.DERIVATION_POLICY_H4_FROM_H1
    assert p.metrics["h4_published_ok"] == 1


def test_published_h4_anchor_is_ny5pm():
    r = FakeRedis(); p = _producer(r)
    for hh in range(4):
        p.on_h1_close(_H1(_BO + timedelta(hours=hh)))
    p.on_h1_close(_H1(_BO + timedelta(hours=4)))
    env = json.loads(r.store["hermes:candles:XAU_USD:H4:latest:v1"][0])
    # data.timestamp_utc open hour must be one of the ratified anchors
    hour = int(env["data"]["timestamp_utc"][11:13])
    assert hour in (22, 2, 6, 10, 14, 18)


def test_incomplete_3of4_published_never_ok():
    r = FakeRedis(); p = _producer(r)
    for hh in range(3):                                  # only 02,03,04 -> 3 children
        p.on_h1_close(_H1(_BO + timedelta(hours=hh)))
    res = p.on_h1_close(_H1(_BO + timedelta(hours=4)))   # seals 02:00 with 3 children
    assert res["published"] is True and res["status"] == "SOURCE_INCOMPLETE" and res["status"] != "OK"
    assert res["source_count"] == 3 and res["source_coverage"] == 0.75 and res["gap_state"] == "INCOMPLETE"
    assert p.metrics["h4_published_incomplete"] == 1


def test_no_publish_until_bucket_rolls():
    r = FakeRedis(); p = _producer(r)
    for hh in range(4):
        assert p.on_h1_close(_H1(_BO + timedelta(hours=hh)))["published"] is False
    assert r.sets == []                                  # nothing written until the next bucket starts


# ---------------- guards / scope ----------------
def test_non_h1_candle_ignored():
    p = _producer()
    c = _H1(_BO); c.timeframe = "M5"
    assert p.on_h1_close(c)["reason"] == "NOT_H1" and p.metrics["h4_skipped_non_h1"] == 1


def test_non_allowlisted_instrument_skipped():
    r = FakeRedis(); p = _producer(r, allowed=("XAU_USD",))
    # feed two buckets of a non-allowlisted instrument; nothing publishes
    p.on_h1_close(_H1(_BO, instrument="EUR_USD"))
    res = p.on_h1_close(_H1(_BO + timedelta(hours=4), instrument="EUR_USD"))
    assert res["reason"] == "INSTRUMENT_NOT_ALLOWLISTED" and r.sets == []
    assert p.metrics["h4_skipped_not_allowlisted"] >= 1


def test_xauusd_alias_canonicalised_publishes_xau_key():
    r = FakeRedis(); p = _producer(r, allowed=("XAU_USD",))
    for hh in range(4):
        p.on_h1_close(_H1(_BO + timedelta(hours=hh), instrument="XAUUSD"))
    res = p.on_h1_close(_H1(_BO + timedelta(hours=4), instrument="XAUUSD"))
    assert res["published"] is True and res["key"] == "hermes:candles:XAU_USD:H4:latest:v1"
    assert all("XAUUSD" not in k for k in r.store)


def test_producer_requires_nonempty_allowlist():
    try:
        _producer(allowed=()); assert False
    except ValueError as e:
        assert "GOV-CANDLE-H4-WIRE-002" in str(e)


def test_disabled_producer_is_noop():
    d = wire.DisabledH4Producer()
    assert d.enabled is False
    assert d.on_h1_close(_H1(_BO))["reason"] == "H4_PUBLISH_DISABLED"
    assert d.status() == {"enabled": False}


def test_from_env_disabled_by_default(monkeypatch):
    for k in ("HERMES_CANDLE_FORWARD_ENABLED", "HERMES_CANDLE_FORWARD_SINK", "HERMES_CANDLE_H4_PUBLISH_ENABLED"):
        monkeypatch.delenv(k, raising=False)
    assert isinstance(wire.build_h4_producer_from_env(), wire.DisabledH4Producer)


def test_from_env_disabled_when_h4_flag_off(monkeypatch):
    monkeypatch.setenv("HERMES_CANDLE_FORWARD_ENABLED", "true")
    monkeypatch.setenv("HERMES_CANDLE_FORWARD_SINK", "canonical")
    monkeypatch.delenv("HERMES_CANDLE_H4_PUBLISH_ENABLED", raising=False)   # H4 flag off
    assert isinstance(wire.build_h4_producer_from_env(), wire.DisabledH4Producer)


def test_no_sql_no_stale_table_no_m15_no_regime_in_code():
    # check CODE literals (not the docstring's negative prose): no DB/SQL, no stale-table/M15/regime usage.
    src = open(wire.__file__).read()
    assert "pymysql" not in src and ".execute(" not in src          # producer takes H1 candles; no DB query
    assert '"candles_H4"' not in src and "'candles_H4'" not in src  # no stale direct-table literal
    assert '"M15"' not in src and "'M15'" not in src                # no M15 fallback literal
    assert '"regime"' not in src and "regime_confidence" not in src
    assert wire.h4d.H1_TIMEFRAME == "H1"                            # derives from H1 only
