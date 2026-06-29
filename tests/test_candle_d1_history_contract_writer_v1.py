"""HERMES D1 history contract + writer support — code-only, in-memory fake Redis. Nothing activates.
WO-HELM-HERMES-GOLD-D1-HISTORY-CONTRACT-WRITER-0001.
"""
import copy
import json
from datetime import datetime, timedelta, timezone

import pytest

import utils.candle_history_v1 as chv
import utils.candle_history_forward_writer_v1 as fw
import utils.candle_d1_derivation_v1 as d1d
import utils.candle_contract_v1 as cc

UTC = timezone.utc
_D1O = datetime(2026, 6, 25, 22, 0, tzinfo=UTC)        # a valid D1 open (22:00Z)
_EPOCH = int(_D1O.timestamp())
INSERTED = datetime(2026, 6, 29, 19, 0, tzinfo=UTC)


class FakeRedis:
    def __init__(self): self.store = {}; self.zsets = {}; self.sets = []
    def get(self, k):
        v = self.store.get(k); return None if v is None else v[0]
    def set(self, k, v, ex=None):
        assert isinstance(v, (str, bytes)); self.store[k] = (v, ex); self.sets.append((k, v, ex)); return True
    def zadd(self, name, mapping):
        self.zsets.setdefault(name, {}).update(mapping); return len(mapping)


def _six_h4(d1_open=_D1O):
    opens = d1d.d1_child_h4_opens(d1_open)
    specs = [(2000, 2010, 1990, 2005, 100), (2005, 2030, 1995, 2020, 110), (2020, 2080, 2010, 2050, 120),
             (2050, 2060, 2000, 2030, 130), (2030, 2040, 1900, 1950, 140), (1950, 1975, 1940, 1970, 150)]
    return [{"timestamp": opens[i], "open": specs[i][0], "high": specs[i][1], "low": specs[i][2],
             "close": specs[i][3], "volume": specs[i][4]} for i in range(6)]


def _d1_env(d1_open=_D1O, children=6, is_closed=True):
    env, _ = d1d.derive_d1(instrument="XAU_USD", d1_open=d1_open, h4_children=_six_h4(d1_open)[:children],
                           generated_at_utc=cc.normalise_utc(d1_open) + timedelta(seconds=d1d.D1_SECONDS),
                           is_closed=is_closed)
    return env


# ============================ D1 history target / key shape ============================
def test_d1_history_key_and_index_accepted():
    assert chv.assert_history_target(f"hermes:candles:XAU_USD:D1:history:v1:{_EPOCH}") is True
    assert chv.assert_history_target("hermes:candles:XAU_USD:D1:history:v1:index") is True
    assert chv.history_key("XAU_USD", "D1", _EPOCH) == f"hermes:candles:XAU_USD:D1:history:v1:{_EPOCH}"
    assert "D1" in chv.HISTORY_TIMEFRAMES


def test_d1_latest_key_rejected_by_history_target():
    with pytest.raises(ValueError) as e:
        chv.assert_history_target("hermes:candles:XAU_USD:D1:latest:v1")
    assert "GOV-CANDLE-HIST-TGT-002" in str(e.value)


def test_d1_history_alias_nonxau_unversioned_legacyD_rejected():
    for k, code in (
        (f"hermes:candles:XAUUSD:D1:history:v1:{_EPOCH}", "GOV-CANDLE-HIST-TGT-004"),
        (f"hermes:candles:EUR_USD:D1:history:v1:{_EPOCH}", "GOV-CANDLE-HIST-TGT-005"),
        ("hermes:candles:XAU_USD:D1:history:1782424800", "GOV-CANDLE-HIST-TGT-003"),   # unversioned (6 parts)
        (f"hermes:candles:XAU_USD:D:history:v1:{_EPOCH}", "GOV-CANDLE-HIST-TGT-006"),    # legacy D excluded
    ):
        with pytest.raises(ValueError) as e:
            chv.assert_history_target(k)
        assert code in str(e.value), k


def test_d1_history_malformed_epoch_rejected():
    with pytest.raises(ValueError) as e:
        chv.assert_history_target("hermes:candles:XAU_USD:D1:history:v1:notanepoch")
    assert "GOV-CANDLE-HIST-TGT-007" in str(e.value)


# ============================ assert_d1_history_payload ============================
def test_valid_d1_payload_passes_and_write_plan_shapes():
    env = _d1_env()
    assert chv.assert_d1_history_payload(env) is True
    plan = chv.build_history_write_plan(env)
    assert plan["key"] == f"hermes:candles:XAU_USD:D1:history:v1:{_EPOCH}"
    assert plan["index_key"] == "hermes:candles:XAU_USD:D1:history:v1:index"
    assert plan["ttl_seconds"] == chv.HISTORY_TTL_SECONDS == 3024000
    assert plan["index_score"] == _EPOCH and plan["index_member"] == str(_EPOCH)


def test_d1_payload_requires_2200_anchor():
    env = _d1_env()
    env["data"]["timestamp_utc"] = "2026-06-26T00:00:00.000Z"      # UTC-midnight
    with pytest.raises(ValueError) as e:
        chv.assert_d1_history_payload(env)
    assert "GOV-CANDLE-HIST-D1-003" in str(e.value)


def test_d1_payload_requires_source_h4():
    env = _d1_env(); env["data"]["source_timeframe"] = "H1"        # 24xH1 / non-H4 source
    with pytest.raises(ValueError) as e:
        chv.assert_d1_history_payload(env)
    assert "GOV-CANDLE-HIST-D1-004" in str(e.value)


def test_d1_payload_requires_six_count_and_full_coverage():
    env = _d1_env(); env["data"]["source_count"] = 5
    with pytest.raises(ValueError) as e:
        chv.assert_d1_history_payload(env)
    assert "GOV-CANDLE-HIST-D1-006" in str(e.value)
    env2 = _d1_env(); env2["data"]["source_coverage"] = 0.75
    with pytest.raises(ValueError) as e:
        chv.assert_d1_history_payload(env2)
    assert "GOV-CANDLE-HIST-D1-007" in str(e.value)


def test_incomplete_d1_cannot_be_written_as_ok():
    env = _d1_env(children=5)                                       # 5/6 -> status SOURCE_INCOMPLETE
    assert env["status"] != "OK"
    with pytest.raises(ValueError) as e:
        chv.assert_d1_history_payload(env)
    assert "GOV-CANDLE-HIST-D1-002" in str(e.value)
    with pytest.raises(ValueError):                                # the write plan also refuses it
        chv.build_history_write_plan(env)


def test_d1_payload_rejects_direct_candles_d1_and_24xh1_provenance():
    for bad in ("candles_D1", "24xH1", "DIRECT_D1"):
        env = _d1_env(); env["history"] = {"history_contract_version": "v1", "backfill_run_id": "x",
                                           "backfill_inserted_at_utc": cc._fmt(INSERTED),
                                           "source_table": f"bad:{bad}", "source_timestamp_utc": env["data"]["timestamp_utc"]}
        with pytest.raises(ValueError) as e:
            chv.assert_d1_history_payload(env)
        assert "GOV-CANDLE-HIST-D1-008" in str(e.value)


def test_d1_payload_rejects_regime_via_validate():
    env = _d1_env(); env["data"]["regime_confidence"] = 0.9
    with pytest.raises(ValueError) as e:
        chv.build_history_write_plan(env)                          # validate runs first -> CONTRACT-029
    assert "GOV-CANDLE-CONTRACT-029" in str(e.value)


# ============================ forward writer: D1 denied by default ============================
def test_parse_forward_timeframes_d1_denied_by_default():
    with pytest.raises(ValueError) as e:
        fw.parse_forward_timeframes("M1,M5,D1")
    assert "GOV-CANDLE-HIST-FWD-D1-001" in str(e.value)
    # legacy "D" always denied
    with pytest.raises(ValueError) as e:
        fw.parse_forward_timeframes("M1,D")
    assert "GOV-CANDLE-HIST-FWD-005" in str(e.value)


def test_parse_forward_timeframes_d1_allowed_when_authorised():
    assert fw.parse_forward_timeframes("M1,M5,H4,D1", allow_d1=True) == ("M1", "M5", "H4", "D1")


def test_from_env_d1_in_timeframes_without_auth_fails_loud(monkeypatch):
    monkeypatch.setenv(fw.ENABLED_ENV, "true"); monkeypatch.setenv(fw.AUTHORISED_ENV, "true")
    monkeypatch.setenv(fw.TIMEFRAMES_ENV, "M1,M5,M15,H1,H4,D1")
    monkeypatch.setenv(fw.INSTRUMENTS_ENV, "XAU_USD")
    monkeypatch.delenv(fw.D1_HISTORY_FORWARD_AUTHORISED_ENV, raising=False)
    for k in ("HERMES_CANDLE_CANONICAL_REDIS_HOST", "HERMES_CANDLE_CANONICAL_REDIS_PORT", "HERMES_CANDLE_CANONICAL_REDIS_DB"):
        monkeypatch.setenv(k, "0")
    with pytest.raises(ValueError) as e:
        fw.build_history_forward_writer_from_env()
    assert "GOV-CANDLE-HIST-FWD-D1-001" in str(e.value)


def test_from_env_default_excludes_d1(monkeypatch):
    for k in (fw.ENABLED_ENV, fw.AUTHORISED_ENV, fw.D1_HISTORY_FORWARD_AUTHORISED_ENV):
        monkeypatch.delenv(k, raising=False)
    assert isinstance(fw.build_history_forward_writer_from_env(), fw.DisabledHistoryForwardWriter)


# ============================ forward writer: D1 write path (when authorised) ============================
def _d1_writer(client, run_id=fw.D1_FORWARD_RUN_MARKER):
    return fw.CandleHistoryForwardWriter(redis_client=client, allowed_instruments=("XAU_USD",),
                                         timeframes=("D1",), run_id=run_id)


def test_d1_writer_writes_complete_d1_to_history_shape():
    r = FakeRedis(); w = _d1_writer(r)
    res = w.on_d1_sealed(_d1_env(), inserted_at_utc=INSERTED)
    assert res["wrote"] is True
    assert res["key"] == f"hermes:candles:XAU_USD:D1:history:v1:{_EPOCH}"
    env = json.loads(r.store[res["key"]][0])
    assert cc.validate_candle_contract(env) is True
    assert env["data"]["timeframe"] == "D1" and env["data"]["source_count"] == 6
    assert env["history"]["source_table"] == "canonical_latest_forward:DERIVED_D1_FROM_H4"
    assert r.store[res["key"]][1] == chv.HISTORY_TTL_SECONDS                       # TTL applied
    assert r.zsets[res["index_key"]] == {str(_EPOCH): _EPOCH}                      # score=member=open_epoch


def test_d1_writer_skips_incomplete_d1_status():
    r = FakeRedis(); w = _d1_writer(r)
    res = w.on_d1_sealed(_d1_env(children=5), inserted_at_utc=INSERTED)            # SOURCE_INCOMPLETE
    assert res["wrote"] is False and res["reason"] == fw.REASON_STATUS_NOT_OK
    assert r.sets == []


def test_d1_writer_idempotent_same_payload_and_conflict_fails_loud():
    r = FakeRedis(); w = _d1_writer(r)
    first = w.on_d1_sealed(_d1_env(), inserted_at_utc=INSERTED)
    assert first["idempotent_duplicate"] is False
    second = w.on_d1_sealed(_d1_env(), inserted_at_utc=INSERTED + timedelta(hours=1))
    assert second["wrote"] is True and second["idempotent_duplicate"] is True       # same candle -> safe
    # divergent payload at same open_epoch -> fail loud. Re-derive with a changed child (different volume) so
    # the candle is VALID but its truth differs.
    diff_children = _six_h4(); diff_children[2] = {**diff_children[2], "volume": 999}
    diff, _ = d1d.derive_d1(instrument="XAU_USD", d1_open=_D1O, h4_children=diff_children,
                            generated_at_utc=cc.normalise_utc(_D1O) + timedelta(seconds=d1d.D1_SECONDS), is_closed=True)
    with pytest.raises(ValueError) as e:
        w.on_d1_sealed(diff, inserted_at_utc=INSERTED)
    assert "GOV-CANDLE-HIST-FWD-020" in str(e.value)


def test_d1_writer_latest_not_targeted_and_no_regime():
    r = FakeRedis(); w = _d1_writer(r)
    res = w.on_d1_sealed(_d1_env(), inserted_at_utc=INSERTED)
    assert ":latest:" not in res["key"] and "XAUUSD" not in res["key"]
    blob = json.dumps(r.store).lower()
    for tok in ("regime", "structure", "shadow", "candles_d1", "24xh1"):
        assert tok not in blob
