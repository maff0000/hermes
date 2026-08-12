"""HERMES forward-history writer — code-only, in-memory fake Redis. Nothing activates, no live I/O.
WO-HELM-HERMES-GOLD-MTF-FORWARD-HISTORY-WRITER-0001.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

import utils.candle_history_forward_writer_v1 as fw
import utils.candle_history_v1 as chv
import utils.candle_contract_v1 as cc
import utils.candle_h4_derivation_v1 as h4d

UTC = timezone.utc
_TS = datetime(2026, 6, 2, 2, 0, tzinfo=UTC)        # an H4 bucket open (02:00 UTC) and a clean grid point
INSERTED = datetime(2026, 6, 28, 14, 0, tzinfo=UTC)


class FakeRedis:
    def __init__(self):
        self.store = {}        # key -> (value, ex)
        self.zsets = {}        # index -> {member: score}
        self.sets = []

    def get(self, k):
        v = self.store.get(k)
        return None if v is None else v[0]

    def set(self, k, v, ex=None):
        assert isinstance(v, (str, bytes))
        self.store[k] = (v, ex)
        self.sets.append((k, v, ex))
        return True

    def zadd(self, name, mapping):
        z = self.zsets.setdefault(name, {})
        for member, score in mapping.items():
            z[member] = score
        return len(mapping)

    def zcard(self, name):
        return len(self.zsets.get(name, {}))


def _direct(tf, ts=_TS, o=2000.0, h=2008.0, low=1998.0, c=2004.0, v=10, instrument="XAU_USD"):
    """A closed, status-OK DIRECT canonical candle envelope (the shape written to :latest for M1/M5/M15/H1)."""
    close = cc.normalise_utc(ts) + timedelta(seconds=cc.TF_SECONDS[tf])
    return cc.build_candle_contract(
        instrument=instrument, timeframe=tf, timestamp_utc=ts,
        ohlc={"open": o, "high": h, "low": low, "close": c, "volume": v}, is_closed=True,
        generated_at_utc=close, source_timeframe=tf, source_count=1, expected_source_count=1,
        derivation=cc.DERIVATION_DIRECT, derivation_policy=cc.DERIVATION_POLICY_DIRECT,
        source_policy_epoch="DIRECT_NATIVE_V1", market_open=True)


def _h4(children=4, is_closed=True):
    kids = [{"timestamp": _TS + timedelta(hours=i), "open": 2000.0 + i, "high": 2009.0 + i,
             "low": 1998.0 + i, "close": 2004.0 + i, "volume": 10 + i} for i in range(children)]
    close = _TS + timedelta(hours=4)
    env, _meta = h4d.derive_h4(instrument="XAU_USD", h4_open=_TS, h1_children=kids,
                               generated_at_utc=close, is_closed=is_closed, market_open=True)
    return env


def _writer(client=None, tfs=("M1", "M5", "M15", "H1", "H4"), allowed=("XAU_USD",)):
    return fw.CandleHistoryForwardWriter(redis_client=client or FakeRedis(),
                                         allowed_instruments=allowed, timeframes=tfs)


# ============================ disabled / gating ============================
def test_disabled_writer_is_noop():
    d = fw.DisabledHistoryForwardWriter()
    assert d.enabled is False
    assert d.on_canonical_close(_direct("M1"), inserted_at_utc=INSERTED)["reason"] == fw.REASON_DISABLED
    assert d.on_h4_sealed(_h4(), inserted_at_utc=INSERTED)["reason"] == fw.REASON_DISABLED
    assert d.status() == {"enabled": False}


def test_from_env_disabled_by_default(monkeypatch):
    for k in (fw.ENABLED_ENV, fw.AUTHORISED_ENV, fw.TIMEFRAMES_ENV, fw.INSTRUMENTS_ENV):
        monkeypatch.delenv(k, raising=False)
    assert isinstance(fw.build_history_forward_writer_from_env(), fw.DisabledHistoryForwardWriter)


def test_from_env_enabled_unauthorised_fails_loud(monkeypatch):
    monkeypatch.setenv(fw.ENABLED_ENV, "true")
    monkeypatch.delenv(fw.AUTHORISED_ENV, raising=False)
    with pytest.raises(ValueError) as e:
        fw.build_history_forward_writer_from_env()
    assert "GOV-CANDLE-HIST-FWD-002" in str(e.value)


def test_from_env_missing_timeframes_fails_loud(monkeypatch):
    monkeypatch.setenv(fw.ENABLED_ENV, "true")
    monkeypatch.setenv(fw.AUTHORISED_ENV, "true")
    monkeypatch.delenv(fw.TIMEFRAMES_ENV, raising=False)
    monkeypatch.setenv(fw.INSTRUMENTS_ENV, "XAU_USD")
    with pytest.raises(ValueError) as e:
        fw.build_history_forward_writer_from_env()
    assert "GOV-CANDLE-HIST-FWD-003" in str(e.value)


def test_from_env_missing_instruments_fails_loud(monkeypatch):
    monkeypatch.setenv(fw.ENABLED_ENV, "true")
    monkeypatch.setenv(fw.AUTHORISED_ENV, "true")
    monkeypatch.setenv(fw.TIMEFRAMES_ENV, "M1,M5,M15,H1,H4")
    monkeypatch.delenv(fw.INSTRUMENTS_ENV, raising=False)
    with pytest.raises(ValueError) as e:
        fw.build_history_forward_writer_from_env()
    assert "GOV-CANDLE-HIST-FWD-004" in str(e.value)


def test_parse_empty_allowlist_and_timeframes_fail_loud():
    for raw in (None, "", "   ", " , , "):
        with pytest.raises(ValueError) as e:
            fw.parse_forward_instruments(raw)
        assert "GOV-CANDLE-HIST-FWD-004" in str(e.value)
        with pytest.raises(ValueError) as e:
            fw.parse_forward_timeframes(raw)
        assert "GOV-CANDLE-HIST-FWD-003" in str(e.value)


def test_parse_d1_timeframe_rejected():
    for raw in ("D1", "M1,D1", "D"):
        with pytest.raises(ValueError) as e:
            fw.parse_forward_timeframes(raw)
        assert "GOV-CANDLE-HIST-FWD-005" in str(e.value)


def test_parse_multi_instrument_accepted():
    # WO-...-CORE-CANDLE-WICK-HISTORY: the forward-history lane now serves the configured multi-instrument set.
    assert fw.parse_forward_instruments("EUR_USD") == frozenset({"EUR_USD"})
    assert fw.parse_forward_instruments("XAU_USD,GBP_USD,XAG_USD") == frozenset({"XAU_USD", "GBP_USD", "XAG_USD"})


def test_parse_alias_output_rejected():
    # The raw XAUUSD alias is rejected as an output-key instrument request (use canonical XAU_USD).
    with pytest.raises(ValueError) as e:
        fw.parse_forward_instruments("XAUUSD")
    assert "GOV-CANDLE-HIST-FWD-006" in str(e.value)


def test_writer_accepts_multi_instrument():
    w = _writer(allowed=("XAU_USD", "EUR_USD", "USD_JPY"))
    assert {"XAU_USD", "EUR_USD", "USD_JPY"} == set(w.allowed_instruments)





# ============================ guards on write ============================
def test_non_allowlisted_instrument_skipped_no_write():
    r = FakeRedis(); w = _writer(r, allowed=("XAU_USD",))
    res = w.on_canonical_close(_direct("M1", instrument="EUR_USD"), inserted_at_utc=INSERTED)
    assert res["wrote"] is False and res["reason"] == fw.REASON_NOT_ALLOWLISTED
    assert r.sets == [] and w.metrics["history_skipped_instrument"] == 1


def test_tf_not_configured_skipped():
    r = FakeRedis(); w = _writer(r, tfs=("M1",))     # only M1 enabled
    res = w.on_canonical_close(_direct("H1"), inserted_at_utc=INSERTED)
    assert res["wrote"] is False and res["reason"] == fw.REASON_TF_NOT_CONFIGURED
    assert r.sets == []


def test_d1_candle_rejected_fail_loud():
    w = _writer()
    env = _direct("H1"); env["data"]["timeframe"] = "D1"
    with pytest.raises(ValueError) as e:
        w.on_canonical_close(env, inserted_at_utc=INSERTED)
    assert "GOV-CANDLE-HIST-FWD-014" in str(e.value)


def test_forming_candle_not_written():
    r = FakeRedis(); w = _writer(r)
    env = _direct("M5"); env["data"]["is_closed"] = False
    res = w.write_closed_envelope(env, inserted_at_utc=INSERTED, source_table="t")
    assert res["wrote"] is False and res["reason"] == fw.REASON_FORMING and r.sets == []


def test_xauusd_alias_never_emits_alias_key():
    r = FakeRedis(); w = _writer(r)
    env = _direct("M1"); env["data"]["instrument"] = "XAUUSD"        # force alias on the candle
    res = w.on_canonical_close(env, inserted_at_utc=INSERTED)
    assert res["wrote"] is True and res["key"] == "hermes:candles:XAU_USD:M1:history:v1:" + str(int(_TS.timestamp()))
    assert all("XAUUSD" not in k for k in r.store)


# ============================ history target shape ============================
@pytest.mark.parametrize("tf", ["M1", "M5", "M15", "H1"])
def test_direct_closed_candle_written_to_history_shape(tf):
    r = FakeRedis(); w = _writer(r)
    res = w.on_canonical_close(_direct(tf), inserted_at_utc=INSERTED)
    open_epoch = int(_TS.timestamp())
    assert res["wrote"] is True
    assert res["key"] == f"hermes:candles:XAU_USD:{tf}:history:v1:{open_epoch}"
    assert res["index_key"] == f"hermes:candles:XAU_USD:{tf}:history:v1:index"
    # written value validates, carries the history block, and is NOT a latest key
    val, ex = r.store[res["key"]]
    env = json.loads(val)
    assert cc.validate_candle_contract(env) is True
    assert env["history"]["history_contract_version"] == "v1"
    assert env["history"]["backfill_run_id"] == fw.FORWARD_RUN_MARKER
    assert chv.assert_history_target(res["key"]) is True
    assert ":latest:" not in res["key"]
    # TTL applied + index score==member==open_epoch
    assert ex == chv.HISTORY_TTL_SECONDS
    assert r.zsets[res["index_key"]] == {str(open_epoch): open_epoch}


def test_h4_complete_written_to_history_shape():
    r = FakeRedis(); w = _writer(r)
    res = w.on_h4_sealed(_h4(children=4), inserted_at_utc=INSERTED)
    open_epoch = int(_TS.timestamp())
    assert res["wrote"] is True
    assert res["key"] == f"hermes:candles:XAU_USD:H4:history:v1:{open_epoch}"
    env = json.loads(r.store[res["key"]][0])
    assert env["data"]["source_count"] == 4 and env["data"]["source_coverage"] == 1.0
    assert env["provenance"]["derivation"] == cc.DERIVATION_DERIVED
    assert env["history"]["source_table"] == "canonical_latest_forward:DERIVED_H4_FROM_H1"
    assert chv.assert_history_target(res["key"]) is True


def test_h4_incomplete_not_written_as_ok():
    r = FakeRedis(); w = _writer(r)
    env = _h4(children=3)                                  # 3/4 -> status SOURCE_INCOMPLETE
    assert env["status"] != "OK"
    res = w.on_h4_sealed(env, inserted_at_utc=INSERTED)
    assert res["wrote"] is False and res["reason"] == fw.REASON_STATUS_NOT_OK
    assert r.sets == [] and w.metrics["history_skipped_status"] == 1


def test_h4_forming_not_written():
    r = FakeRedis(); w = _writer(r)
    env = _h4(children=4, is_closed=False)                 # FORMING
    res = w.on_h4_sealed(env, inserted_at_utc=INSERTED)
    assert res["wrote"] is False and res["reason"] in (fw.REASON_FORMING, fw.REASON_STATUS_NOT_OK)
    assert r.sets == []


# ============================ guard hooks: latest / unversioned rejected ============================
def test_writer_never_targets_latest_or_unversioned():
    # the history-target guard the writer calls before every write rejects latest & unversioned keys
    for bad in ("hermes:candles:XAU_USD:M1:latest:v1", "hermes:candles:XAU_USD:M1:history:1"):
        with pytest.raises(ValueError):
            chv.assert_history_target(bad)
    # and the writer's own produced key always passes the guard
    r = FakeRedis(); w = _writer(r)
    res = w.on_canonical_close(_direct("H1"), inserted_at_utc=INSERTED)
    assert chv.assert_history_target(res["key"]) is True


# ============================ validate-before-write ============================
def test_validate_runs_before_write_blocks_forbidden_field():
    r = FakeRedis(); w = _writer(r)
    env = _direct("M1")
    env["data"]["regime_confidence"] = 0.9                 # forbidden interpretive field
    with pytest.raises(ValueError) as e:
        w.write_closed_envelope(env, inserted_at_utc=INSERTED, source_table="t")
    assert "GOV-CANDLE-CONTRACT-029" in str(e.value)
    assert r.sets == []                                    # nothing written


def test_no_regime_tokens_in_module_or_payload():
    # check CODE literals (not docstring prose): no regime/interpretive field used as a key
    src = open(fw.__file__).read()
    assert "regime_confidence" not in src
    assert '"regime"' not in src and "'regime'" not in src
    r = FakeRedis(); w = _writer(r)
    w.on_canonical_close(_direct("M5"), inserted_at_utc=INSERTED)
    blob = json.dumps(r.store).lower()
    for tok in ("regime", "structure", "choch", "order_block", "signal"):
        assert tok not in blob


# ============================ idempotency ============================
def test_idempotent_rewrite_same_candle_safe():
    r = FakeRedis(); w = _writer(r)
    first = w.on_canonical_close(_direct("M1"), inserted_at_utc=INSERTED)
    assert first["idempotent_duplicate"] is False
    second = w.on_canonical_close(_direct("M1"), inserted_at_utc=INSERTED + timedelta(hours=1))
    assert second["wrote"] is True and second["idempotent_duplicate"] is True
    assert w.metrics["history_written"] == 1 and w.metrics["history_idempotent_duplicate"] == 1
    # single index member, no duplicate
    assert r.zcard(second["index_key"]) == 1


def test_conflicting_candle_same_epoch_fails_loud():
    r = FakeRedis(); w = _writer(r)
    w.on_canonical_close(_direct("M1", c=2004.0), inserted_at_utc=INSERTED)
    with pytest.raises(ValueError) as e:
        w.on_canonical_close(_direct("M1", c=2006.0), inserted_at_utc=INSERTED)   # different close (valid), same epoch
    assert "GOV-CANDLE-HIST-FWD-020" in str(e.value)


def test_latest_envelope_not_mutated_by_writer():
    r = FakeRedis(); w = _writer(r)
    env = _direct("H1")
    before = json.dumps(env, sort_keys=True)
    w.on_canonical_close(env, inserted_at_utc=INSERTED)
    assert json.dumps(env, sort_keys=True) == before        # deep-copied; caller's latest envelope untouched
    assert "history" not in env                              # history block only on the written copy
