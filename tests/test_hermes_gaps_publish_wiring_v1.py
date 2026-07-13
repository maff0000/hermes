"""HERMES PH2 gaps publish wiring — GapsPublisher.publish + gated runtime step + runner-spec assembly.
WO-HELM-HERMES-PH2-GAPS-SURFACE-PUBLISH-WIRING-0001.

Proves the previously-inert PH2 gaps surface now has a governed publication path that stays DARK by default and, only when
HERMES_GAPS_PUBLISH_ENABLED + HERMES_GAPS_PUBLISH_AUTHORISED are both set, publishes EXACTLY ONE key
(hermes:gaps:XAU_USD:v1) with the read-only gap-detection contract. No repair, no backfill, no delete, no candle/history
write, no SQL, no market_map, no Falcon; consumer_live/repair_executed/backfill_executed stay hard false. Code-only, no live I/O.
"""
import inspect
import json
from datetime import datetime, timedelta, timezone

import pytest

import utils.hermes_gaps_v1 as gaps
import utils.hermes_runtime_publisher_steps_v1 as steps
import utils.hermes_publisher_runtime_v1 as runtime
import utils.candle_d1_derivation_v1 as d1d
import utils.candle_contract_v1 as cc

UTC = timezone.utc
INST = "XAU_USD"
NOW_OPEN = datetime(2026, 7, 8, 12, 0, tzinfo=UTC)      # Wednesday noon -> market OPEN
NOW_CLOSED = datetime(2026, 7, 12, 13, 0, tzinfo=UTC)   # Sunday 13:00 -> market CLOSED_WEEKEND (< 22:00)


class FakeRedis:
    """Records every write/delete so tests can prove the publisher writes ONLY the gaps key and never mutates candles."""
    def __init__(self):
        self.kv = {}
        self.z = {}
        self.sets = []       # (key, ex)
        self.deletes = []
        self.zadds = []

    def get(self, k):
        v = self.kv.get(k)
        return v.encode() if isinstance(v, str) else v

    def exists(self, k):
        return 1 if (k in self.kv or k in self.z) else 0

    def zrange(self, k, a, b):
        items = sorted(self.z.get(k, {}).items(), key=lambda kv: kv[1])
        return [m for m, _ in (items[a:b + 1] if b != -1 else items[a:])]

    def zcard(self, k):
        return len(self.z.get(k, {}))

    def set(self, k, v, ex=None):
        self.sets.append((k, ex))
        self.kv[k] = v

    def delete(self, *a):
        self.deletes.extend(a)

    def zadd(self, k, m):
        self.zadds.append(k)
        self.z.setdefault(k, {}).update(m)


def _latest_env(tf, open_dt, status="OK"):
    return {"status": status, "data": {"instrument": INST, "timeframe": tf,
            "timestamp_utc": cc._fmt(cc.normalise_utc(open_dt)), "open": 2000.0, "high": 2010.0, "low": 1990.0,
            "close": 2005.0}}


def _sealed_d1(d1_open=datetime(2026, 7, 7, 22, 0, tzinfo=UTC)):
    opens = d1d.d1_child_h4_opens(d1_open)
    specs = [(2000, 2010, 1990, 2005, 10), (2005, 2030, 1995, 2020, 11), (2020, 2080, 2010, 2050, 12),
             (2050, 2060, 2000, 2030, 13), (2030, 2040, 1900, 1950, 14), (1950, 1975, 1940, 1970, 15)]
    kids = [{"timestamp": opens[i], "open": specs[i][0], "high": specs[i][1], "low": specs[i][2],
             "close": specs[i][3], "volume": specs[i][4]} for i in range(6)]
    env, _ = d1d.derive_d1(instrument=INST, d1_open=d1_open, h4_children=kids,
                           generated_at_utc=cc.normalise_utc(d1_open) + timedelta(seconds=d1d.D1_SECONDS))
    return env


def _seed(now=NOW_OPEN, *, h1_open_hist=False):
    """Minimal, fast seed: D1 latest+history (so the D1 boundary is exercised) and optionally a full H1 open-market
    history (to exercise the weekend/market-closed path end-to-end). Empty tf surfaces classify SOURCE_MISSING — still a
    VALID contract, which is all the wiring needs."""
    r = FakeRedis()
    d1_open = datetime(2026, 7, 7, 22, 0, tzinfo=UTC)
    ep = int(d1_open.timestamp())
    r.kv[gaps._latest_key("D1")] = json.dumps(_sealed_d1(d1_open))
    r.z.setdefault(gaps._hist_index_key("D1"), {})[str(ep)] = ep
    if h1_open_hist:
        now_e = int(now.timestamp()); p = gaps.PERIOD_SECONDS["H1"]
        floor = now_e - gaps.RETENTION_DAYS["H1"] * 86400
        opens = {o for o in gaps._grid_opens("H1", floor - gaps._OOR_LOOKBACK_PERIODS * p, now_e)
                 if o >= floor and gaps._period_fully_open(o, "H1")}
        idx = gaps._hist_index_key("H1")
        for o in opens:
            r.z.setdefault(idx, {})[str(o)] = o
        r.kv[gaps._latest_key("H1")] = json.dumps(_latest_env("H1", datetime.fromtimestamp(max(opens), UTC)))
    return r


def _enable(monkeypatch, *, enabled=True, authorised=True):
    if enabled:
        monkeypatch.setenv(gaps.GAPS_ENABLED_ENV, "true")
    else:
        monkeypatch.delenv(gaps.GAPS_ENABLED_ENV, raising=False)
    if authorised:
        monkeypatch.setenv(gaps.GAPS_AUTHORISED_ENV, "true")
    else:
        monkeypatch.delenv(gaps.GAPS_AUTHORISED_ENV, raising=False)


# ---- key constant ---------------------------------------------------------
def test_gaps_key_is_single_canonical_key():
    assert gaps.GAPS_KEY == "hermes:gaps:XAU_USD:v1"
    assert "XAUUSD" not in gaps.GAPS_KEY


# ---- 1 & 10: disabled publisher / step performs NO Redis writes -----------
def test_disabled_publisher_and_step_are_noop(monkeypatch):
    _enable(monkeypatch, enabled=False, authorised=False)
    assert isinstance(gaps.build_gaps_publisher_from_env(), gaps.DisabledGapsPublisher)
    assert gaps.gaps_publish_enabled() is False
    r = _seed()
    assert steps.gaps_step(r) == {"published": 0}
    assert r.sets == [] and r.deletes == [] and r.zadds == []


# ---- 2 & 11: enabled-without-authorised fails closed ----------------------
def test_enabled_without_authorised_fails_closed(monkeypatch):
    _enable(monkeypatch, enabled=True, authorised=False)
    with pytest.raises(SystemExit) as e1:
        gaps.build_gaps_publisher_from_env(redis_client=FakeRedis())
    assert e1.value.code == gaps.HALT_CODE == 101
    with pytest.raises(SystemExit) as e2:
        gaps.gaps_publish_enabled()
    assert e2.value.code == 101
    with pytest.raises(SystemExit) as e3:
        steps.gaps_step(FakeRedis())
    assert e3.value.code == 101


# ---- 3, 5, 12: enabled+authorised publishes EXACTLY the one gaps key ------
def test_enabled_authorised_publishes_single_key(monkeypatch):
    _enable(monkeypatch)
    r = _seed(h1_open_hist=True)
    res = steps.gaps_step(r)
    assert res["published"] == 1 and res["key"] == gaps.GAPS_KEY
    # exactly ONE write, and it is the gaps key, persistent (no TTL)
    assert r.sets == [(gaps.GAPS_KEY, None)]
    assert gaps.GAPS_KEY in r.kv
    # 5: invariants in the returned result AND the published payload
    payload = json.loads(r.kv[gaps.GAPS_KEY])
    assert payload["consumer_live"] is False
    assert payload["repair_executed"] is False
    assert payload["backfill_executed"] is False


# ---- 4: published payload validates against the PR#89 schema --------------
def test_published_payload_validates(monkeypatch):
    _enable(monkeypatch)
    r = _seed(h1_open_hist=True)
    steps.gaps_step(r)
    payload = json.loads(r.kv[gaps.GAPS_KEY])
    assert gaps.validate_gaps_contract(payload) is True
    for f in ("schema_version", "publisher", "instrument", "generated_at_utc", "source", "calendar_source",
              "consumer_live", "repair_executed", "backfill_executed", "overall_gap_state", "severity_order",
              "timeframes", "d1_boundary"):
        assert f in payload, f"missing invariant field {f}"
    assert payload["instrument"] == "XAU_USD"
    assert list(payload["severity_order"]) == ["SOURCE_MISSING", "INVALID_ANCHOR", "INSUFFICIENT_HISTORY",
                                               "GAPS_FOUND", "STALE", "MARKET_CLOSED", "OUT_OF_RETENTION", "OK"]
    assert set(payload["timeframes"]) == {"M1", "M5", "M15", "H1", "H4", "D1"}


# ---- 6 & 7 & 8 & 9: publisher NEVER writes candle/history, deletes, zadd --
def test_publish_writes_only_gaps_key_no_candle_no_delete(monkeypatch):
    _enable(monkeypatch)
    r = _seed(h1_open_hist=True)
    steps.gaps_step(r)
    assert [k for k, _ in r.sets] == [gaps.GAPS_KEY]                        # only the gaps key
    assert not any(k.startswith("hermes:candles:") for k, _ in r.sets)     # never a candle/history key
    assert r.deletes == []                                                 # never deletes
    assert r.zadds == []                                                   # never zadd (no history mutation)


# ---- runner-spec assembly: dark by default, appended only when gated ------
def test_runner_specs_exclude_gaps_by_default(monkeypatch):
    _enable(monkeypatch, enabled=False, authorised=False)
    names = [s[0] for s in runtime.default_runner_specs()]
    assert "gaps" not in names
    assert names == ["control_plane", "indicators", "candle_features", "sessions_levels"]   # 13: 4 defaults intact


def test_runner_specs_include_gaps_when_enabled_authorised(monkeypatch):
    _enable(monkeypatch)
    specs = runtime.default_runner_specs()
    names = [s[0] for s in specs]
    assert names[-1] == "gaps"                                             # appended as an ADDITIONAL runner
    gaps_spec = [s for s in specs if s[0] == "gaps"][0]
    assert gaps_spec[1] is steps.gaps_step
    assert names[:4] == ["control_plane", "indicators", "candle_features", "sessions_levels"]  # defaults unchanged


def test_runner_specs_fail_closed_when_enabled_without_authorised(monkeypatch):
    _enable(monkeypatch, enabled=True, authorised=False)
    with pytest.raises(SystemExit) as e:
        runtime.default_runner_specs()
    assert e.value.code == 101


# ---- 16: XAUUSD alias can never leak into the published contract ----------
def test_xauusd_alias_never_leaks_through_publish(monkeypatch):
    _enable(monkeypatch)
    r = _seed()
    # poison the D1 latest source with the forbidden alias — the publisher must NOT propagate it (fail-closed: the
    # poisoned source is treated as NOT_SEALED and its instrument string never enters the published contract).
    r.kv[gaps._latest_key("D1")] = json.dumps({"status": "OK", "data": {"instrument": "XAUUSD",
                                              "timestamp_utc": cc._fmt(datetime(2026, 7, 7, 22, 0, tzinfo=UTC))}})
    res = steps.gaps_step(r)
    assert res["published"] == 1
    payload = json.loads(r.kv[gaps.GAPS_KEY])
    assert payload["instrument"] == "XAU_USD"
    assert "XAUUSD" not in json.dumps(payload)                            # alias never reaches the published key


# ---- weekend/market-closed doctrine preserved end-to-end through publish --
def test_weekend_closed_not_gaps_through_publish():
    # publish() takes an explicit `now` (the runtime step uses wall-clock _now()); drive a deterministic weekend now.
    r = _seed(now=NOW_CLOSED, h1_open_hist=True)
    gaps.GapsPublisher(redis_client=r).publish(now=NOW_CLOSED, forward_enabled=False, forward_authorised=False)
    h1 = json.loads(r.kv[gaps.GAPS_KEY])["timeframes"]["H1"]
    assert h1["market_phase"] == "CLOSED_WEEKEND"
    assert h1["gap_state"] != "GAPS_FOUND"                                 # closed-market slots never GAPS_FOUND
    assert r.sets == [(gaps.GAPS_KEY, None)] and r.deletes == []           # still one key, still no delete


# ---- 8 & 17: no SQL / market_map / Falcon / consumer-live in NEW code -----
def _code_only(*objs):
    out = []
    for o in objs:
        src = inspect.getsource(o)
        doc = getattr(o, "__doc__", "") or ""
        src = src.replace(doc, "")
        out.append("\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#")))
    return "\n".join(out)


def test_new_code_has_no_forbidden_deps_or_semantics():
    src = _code_only(gaps.GapsPublisher.publish, gaps.gaps_publish_enabled, steps.gaps_step)
    for tok in ("pymysql", "get_db_config", "sqlalchemy", "cursor(", "market_map", "falcon",
                "consumer_live=True", "consumer_live = True", "regime", "risk_", "backfill(", "repair("):
        assert tok not in src, f"new publish-wiring code must not reference {tok!r}"
    # mutation discipline: publish() performs exactly ONE .set( and no other write/delete op
    pub_src = _code_only(gaps.GapsPublisher.publish)
    assert pub_src.count(".set(") == 1
    for tok in (".delete(", ".zadd(", ".zrem(", ".expire(", ".hset(", ".lpush(", ".rpush("):
        assert tok not in pub_src, f"publish() must not call {tok!r}"
    # the gaps_step delegates writing to publish() — it must not itself write
    step_src = _code_only(steps.gaps_step)
    for tok in (".set(", ".delete(", ".zadd("):
        assert tok not in step_src, f"gaps_step must not directly call {tok!r}"
