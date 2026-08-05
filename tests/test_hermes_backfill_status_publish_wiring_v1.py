"""HERMES PH2 backfill-status publish wiring — BackfillStatusPublisher.publish + gated runtime step + runner assembly.
WO-HELM-HERMES-PH2-BACKFILL-STATUS-PUBLISH-WIRING-0001.

Proves the previously-inert status surface now has a governed publication path that stays DARK by default and, only when
HERMES_BACKFILL_STATUS_PUBLISH_ENABLED + HERMES_BACKFILL_STATUS_PUBLISH_AUTHORISED are both set, publishes EXACTLY ONE key
(hermes:backfill:status:XAU_USD:v1). It never writes the gaps key, candle/history keys, or any other key; never deletes;
never executes/repairs/backfills; never invokes the D1 seed/backfill engine; never calls SQL/vendor/market_map/Falcon.
"""
import inspect
import json
import re
from datetime import datetime, timezone

import pytest

import utils.hermes_backfill_status_v1 as bfs
import utils.hermes_gaps_v1 as gaps
import utils.hermes_runtime_publisher_steps_v1 as steps
import utils.hermes_publisher_runtime_v1 as runtime


@pytest.fixture(autouse=True)
def _registry(monkeypatch):
    """WO-...-XAU-MODULE-ADOPTION-0001: XAU-active rollout -> enabled publisher selects exactly [XAU_USD] with no DB, so
    the single-key wiring behaviour holds as a CONSEQUENCE of the registry gap-detection flag, not a hard-coded ticker."""
    from tests.test_hermes_instrument_registry_v1 import rollout_rows
    import utils.hermes_instrument_registry_v1 as reg
    recs = reg.load_registry(rollout_rows())
    monkeypatch.setattr(reg, "load_from_db", lambda fetch=None: recs)


UTC = timezone.utc
GAPS_KEY = "hermes:gaps:XAU_USD:v1"
BFS_KEY = "hermes:backfill:status:XAU_USD:v1"
NOW = datetime(2026, 7, 13, 12, 30, tzinfo=UTC)


class FakeRedis:
    def __init__(self, kv=None):
        self.kv = dict(kv or {})
        self.sets = []
        self.deletes = []
        self.zadds = []

    def get(self, k):
        v = self.kv.get(k)
        return v.encode() if isinstance(v, str) else v

    def set(self, k, v, ex=None):
        self.sets.append((k, ex))
        self.kv[k] = v

    def delete(self, *a):
        self.deletes.extend(a)

    def zadd(self, k, m):
        self.zadds.append(k)


def _gaps_contract(gap_state="GAPS_FOUND", d1_state="OK"):
    tfb = {tf: {"timeframe": tf, "gap_state": gap_state, "market_phase": "OPEN",
                "history_depth": (34 if tf == "D1" else 500), "sufficient_depth": True, "missing_slots": 3}
           for tf in gaps.TIMEFRAMES}
    d1b = {"expected_anchor_utc": "22:00", "ny5pm_anchor": True, "latest_open_utc": "2026-07-12T22:00:00.000Z",
           "history_newest_open_utc": "2026-07-12T22:00:00.000Z", "latest_matches_history_newest": True,
           "sealed_complete": True, "source_count": 6, "expected_source_count": 6, "coverage": 1.0, "gap": "NONE",
           "invalid_anchor_count": 0, "non_22_anchor_count": 0, "forward_writer_enabled": True,
           "forward_writer_authorised": True, "weekend_d1_buckets": "NOT_EXPECTED", "d1_boundary_state": d1_state}
    return gaps.build_gaps_contract(instrument="XAU_USD", timeframes=tfb, d1_boundary=d1b, generated_at_utc=NOW)


def _live(gap_state="GAPS_FOUND"):
    return FakeRedis({GAPS_KEY: json.dumps(_gaps_contract(gap_state))})


def _enable(monkeypatch, *, enabled=True, authorised=True):
    (monkeypatch.setenv if enabled else monkeypatch.delenv)(bfs.STATUS_ENABLED_ENV, *(("true",) if enabled else (False,)))
    (monkeypatch.setenv if authorised else monkeypatch.delenv)(bfs.STATUS_AUTHORISED_ENV, *(("true",) if authorised else (False,)))


# ---- key + 1 & 2: disabled publisher / step are no-op ----------------------
def test_key_factory_per_instrument_xau_byte_identical():
    assert bfs.backfill_status_key("XAU_USD") == BFS_KEY == "hermes:backfill:status:XAU_USD:v1"
    assert bfs.backfill_status_key("EUR_USD") == "hermes:backfill:status:EUR_USD:v1"
    assert "XAUUSD" not in bfs.backfill_status_key("XAU_USD")


def test_disabled_publisher_and_step_noop(monkeypatch):
    _enable(monkeypatch, enabled=False, authorised=False)
    assert isinstance(bfs.build_backfill_status_publisher_from_env(), bfs.DisabledBackfillStatusPublisher)
    assert bfs.backfill_status_publish_enabled() is False
    r = _live()
    assert steps.backfill_status_step(r) == {"published": 0}
    assert r.sets == [] and r.deletes == [] and r.zadds == []


# ---- 3: existing runner set unchanged when disabled ------------------------
def test_runner_set_unchanged_when_disabled(monkeypatch):
    for e in (bfs.STATUS_ENABLED_ENV, bfs.STATUS_AUTHORISED_ENV, gaps.GAPS_ENABLED_ENV, gaps.GAPS_AUTHORISED_ENV):
        monkeypatch.delenv(e, raising=False)
    names = [s[0] for s in runtime.default_runner_specs()]
    assert "backfill_status" not in names
    assert names == ["control_plane", "indicators", "candle_features", "sessions_levels"]


# ---- 4: enabled-without-authorised fails closed everywhere -----------------
def test_enabled_without_authorised_fails_closed(monkeypatch):
    _enable(monkeypatch, enabled=True, authorised=False)
    with pytest.raises(SystemExit) as e1:
        bfs.build_backfill_status_publisher_from_env(redis_client=FakeRedis())
    assert e1.value.code == 101
    with pytest.raises(SystemExit) as e2:
        bfs.backfill_status_publish_enabled()
    assert e2.value.code == 101
    with pytest.raises(SystemExit) as e3:
        steps.backfill_status_step(FakeRedis())
    assert e3.value.code == 101
    with pytest.raises(SystemExit) as e4:
        runtime.default_runner_specs()
    assert e4.value.code == 101


# ---- 5,6,7,8,9: enabled+authorised publishes EXACTLY one key ---------------
def test_enabled_authorised_publishes_single_key(monkeypatch):
    _enable(monkeypatch)
    r = _live("GAPS_FOUND")
    res = steps.backfill_status_step(r)
    assert res["published"] == 1 and res["keys"] == [BFS_KEY]
    assert r.sets == [(BFS_KEY, None)]              # exactly one write, no TTL
    assert r.deletes == [] and r.zadds == []
    # 8: never wrote the gaps key or any other key
    assert [k for k, _ in r.sets] == [BFS_KEY]
    assert GAPS_KEY not in [k for k, _ in r.sets]
    assert not any(k.startswith("hermes:candles:") for k, _ in r.sets)
    payload = json.loads(r.kv[BFS_KEY])
    assert bfs.validate_backfill_status_contract(payload) is True  # 6: validates
    for f in ("consumer_live", "execution_enabled", "backfill_executed", "repair_executed"):
        assert payload[f] is False                                 # 7
    assert payload["active_job"] is None and payload["completed_pct"] is None


def test_runner_appended_when_enabled(monkeypatch):
    _enable(monkeypatch)
    for e in (gaps.GAPS_ENABLED_ENV, gaps.GAPS_AUTHORISED_ENV):
        monkeypatch.delenv(e, raising=False)
    specs = runtime.default_runner_specs()
    names = [s[0] for s in specs]
    assert names[-1] == "backfill_status"
    assert [s for s in specs if s[0] == "backfill_status"][0][1] is steps.backfill_status_step
    assert names[:4] == ["control_plane", "indicators", "candle_features", "sessions_levels"]


# ---- 16,17,18,19: status semantics preserved through publish ---------------
def test_missing_gaps_publishes_gaps_surface_missing(monkeypatch):
    _enable(monkeypatch)
    r = FakeRedis({})                                              # no gaps key
    steps.backfill_status_step(r)
    assert json.loads(r.kv[BFS_KEY])["overall_status"] == "GAPS_SURFACE_MISSING"


def test_gaps_found_publishes_recovery_not_ok(monkeypatch):
    _enable(monkeypatch)
    r = _live("GAPS_FOUND")
    steps.backfill_status_step(r)
    p = json.loads(r.kv[BFS_KEY])
    assert p["overall_status"] == "READY_FOR_BACKFILL_DESIGN"
    assert p["overall_status"] != "OK"


def test_ok_cannot_be_published(monkeypatch):
    # a forced-OK contract must be rejected by the pre-SET validation (publish never emits OK)
    c = bfs.build_backfill_status_contract(instrument="XAU_USD", gaps_contract=_gaps_contract(), now=NOW)
    c["overall_status"] = "OK"
    with pytest.raises(ValueError, match="GOV-HERMES-BFS-006"):
        bfs.validate_backfill_status_contract(c)


def test_xauusd_never_leaks_through_publish(monkeypatch):
    _enable(monkeypatch)
    r = FakeRedis({GAPS_KEY: json.dumps({"instrument": "XAUUSD"})})   # poisoned gaps -> fail-closed
    steps.backfill_status_step(r)
    p = json.loads(r.kv[BFS_KEY])
    assert p["overall_status"] == "GAPS_SURFACE_MISSING"
    assert p["instrument"] == "XAU_USD" and "XAUUSD" not in json.dumps(p)


# ---- 10-15: publish/step code has no execution/forbidden paths -------------
def _code_only(*objs):
    out = []
    for o in objs:
        src = inspect.getsource(o)
        src = re.sub(r'"""(?:.|\n)*?"""', "", src)
        out.append("\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#")))
    return "\n".join(out)


def test_publish_and_step_have_no_execution_paths():
    src = _code_only(bfs.BackfillStatusPublisher.publish, bfs.backfill_status_publish_enabled, steps.backfill_status_step)
    for tok in ("pymysql", "get_db_config", "sqlalchemy", "cursor(", "market_map", "falcon", "requests.", "urllib",
                "oanda", "execute_backfill", "run_backfill", "seed_backfill", "repair(", "subprocess", "consumer_live=True"):
        assert tok not in src, f"publish-wiring code must not reference {tok!r}"
    pub_src = _code_only(bfs.BackfillStatusPublisher.publish)
    assert pub_src.count(".set(") == 1                             # exactly one SET (the status key)
    for tok in (".delete(", ".zadd(", ".zrem(", ".expire(", ".hset(", ".lpush(", ".rpush("):
        assert tok not in pub_src, f"publish() must not call {tok!r}"
    step_src = _code_only(steps.backfill_status_step)
    for tok in (".set(", ".delete(", ".zadd("):                   # the step delegates writing to publish()
        assert tok not in step_src, f"backfill_status_step must not directly call {tok!r}"
