"""HERMES D1 daily-levels as_of type fix — regression test driving the FULL sessions_levels_step daily path
through build_level_contract (not merely _d1_daily_levels).
WO-HELM-HERMES-D1-DAILY-LEVELS-ASOF-TYPE-FIX-0001.

Reproduces the activation blocker (GOV-HERMES-LVL-001: timestamp must be a datetime (got str)) and proves the fix:
the daily block now passes a datetime as_of into build_level_contract, publishes hermes:levels:XAU_USD:daily:v1 as a
valid deterministic OHLC-derived contract, sourced ONLY from validated sealed D1 history. No SQL, no market_map, no
D1 latest fallback, no XAUUSD, 22:00 anchor + depth>=26 guards preserved.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

import utils.hermes_runtime_publisher_steps_v1 as steps
import utils.hermes_levels_v1 as lvl
import utils.candle_d1_history_v1 as d1h
import utils.candle_d1_derivation_v1 as d1d
import utils.candle_contract_v1 as cc

UTC = timezone.utc
INST = "XAU_USD"
# a broad windows dict so the session heartbeat publishes without SQL (_win_cache pre-seeded -> _load_windows skipped)
_WINDOWS = {"asia": ((0, 0), (7, 0)), "london": ((7, 0), (13, 0)), "overlap_ldn_ny": ((13, 0), (16, 0)),
            "newyork": ((16, 0), (21, 0)), "off_hours": ((21, 0), (24, 0))}


class _FakeRedis:
    def __init__(self):
        self.kv = {}
        self.z = {}
        self.sets = []
        self.deletes = []

    def get(self, k):
        v = self.kv.get(k)
        return v.encode() if isinstance(v, str) else v

    def exists(self, k):
        return 1 if (k in self.kv or k in self.z) else 0

    def set(self, k, v, ex=None):
        self.kv[k] = v
        self.sets.append(k)

    def zadd(self, k, m):
        self.z.setdefault(k, {}).update(m)

    def zrevrange(self, k, a, b):
        items = sorted(self.z.get(k, {}).items(), key=lambda kv: kv[1], reverse=True)
        return [m for m, _ in (items[a:b + 1] if b != -1 else items[a:])]

    def zcard(self, k):
        return len(self.z.get(k, {}))

    def delete(self, *a):
        self.deletes.extend(a)


def _sealed_d1(d1_open):
    opens = d1d.d1_child_h4_opens(d1_open)
    b = (d1_open - datetime(2026, 1, 1, tzinfo=UTC)).days
    specs = [(2000 + b, 2010 + b, 1990 + b, 2005 + b, 10), (2005 + b, 2030 + b, 1995 + b, 2020 + b, 11),
             (2020 + b, 2080 + b, 2010 + b, 2050 + b, 12), (2050 + b, 2060 + b, 2000 + b, 2030 + b, 13),
             (2030 + b, 2040 + b, 1900 + b, 1950 + b, 14), (1950 + b, 1975 + b, 1940 + b, 1970 + b, 15)]
    kids = [{"timestamp": opens[i], "open": specs[i][0], "high": specs[i][1], "low": specs[i][2],
             "close": specs[i][3], "volume": specs[i][4]} for i in range(6)]
    env, _ = d1d.derive_d1(instrument=INST, d1_open=d1_open, h4_children=kids,
                           generated_at_utc=cc.normalise_utc(d1_open) + timedelta(seconds=d1d.D1_SECONDS))
    return env


def _seed_d1(fake, *, days, first=datetime(2026, 5, 1, 22, 0, tzinfo=UTC)):
    idx = f"hermes:candles:{INST}:D1:history:v1:index"
    for d in range(days):
        env = _sealed_d1(first + timedelta(days=d))
        ep = int(cc.normalise_utc(first + timedelta(days=d)).timestamp())
        fake.kv[d1h.d1_history_key(INST, ep)] = json.dumps(env)
        fake.z.setdefault(idx, {})[str(ep)] = ep
    return fake


def _levels_env(monkeypatch, *, scopes, d1=False):
    monkeypatch.setenv("HERMES_SESSION_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("HERMES_SESSION_PUBLISH_AUTHORISED", "true")
    monkeypatch.setenv("HERMES_SESSION_PUBLISH_INSTRUMENTS", "XAU_USD")
    monkeypatch.setenv("HERMES_LEVEL_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("HERMES_LEVEL_PUBLISH_AUTHORISED", "true")
    monkeypatch.setenv("HERMES_LEVEL_PUBLISH_INSTRUMENTS", "XAU_USD")
    monkeypatch.setenv("HERMES_LEVEL_PUBLISH_SCOPES", scopes)
    monkeypatch.setenv("HERMES_LEVEL_D1_AUTHORISED", "true" if d1 else "false")


def _run_step(fake):
    return steps.sessions_levels_step(fake, _win_cache={"w": _WINDOWS})


# --------------------------------------------------------------------------- the fix (full daily path)
def test_daily_path_publishes_valid_contract_no_lvl001(monkeypatch):
    fake = _seed_d1(_FakeRedis(), days=30)
    _levels_env(monkeypatch, scopes="session,intraday,daily", d1=True)
    _run_step(fake)                                          # must NOT raise GOV-HERMES-LVL-001
    dkey = "hermes:levels:XAU_USD:daily:v1"
    assert dkey in fake.kv, "daily levels must publish after the as_of datetime fix"
    p = json.loads(fake.kv[dkey])
    lvl.validate_level_contract(p)                          # full contract validity (would raise on the old bug)
    assert p["instrument"] == "XAU_USD" and p["scope"] == "daily" and p["d1_derived"] is True
    lv = p["levels"]
    assert set(("previous_day_high", "previous_day_low", "previous_day_close", "adr_20")) <= set(lv)
    assert p["level_semantics"]["level_source_granularity"] == "D1"
    assert p["level_semantics"].get("as_of_candle_close_utc", p.get("as_of_candle_close_utc"))
    assert "XAUUSD" not in dkey and fake.deletes == []


def test_asof_is_datetime_into_build_level_contract(monkeypatch):
    """Directly prove the daily path hands build_level_contract a DATETIME as_of (regression for the string bug)."""
    seen = {}
    real_build = lvl.build_level_contract

    def _spy(**kw):
        if kw.get("scope") == "daily":
            seen["as_of_type"] = type(kw.get("as_of_candle_close_utc")).__name__
        return real_build(**kw)

    monkeypatch.setattr(lvl.LevelPublisher, "build", lambda self, **kw: _spy(**kw))
    fake = _seed_d1(_FakeRedis(), days=30)
    _levels_env(monkeypatch, scopes="daily", d1=True)
    _run_step(fake)
    assert seen.get("as_of_type") == "datetime", f"daily as_of must be a datetime, got {seen.get('as_of_type')!r}"


def test_old_string_asof_would_have_raised():
    """Confirms the contract genuinely rejects a STRING as_of (so the fix is load-bearing, not cosmetic)."""
    d1_open = datetime(2026, 6, 1, 22, 0, tzinfo=UTC)
    with pytest.raises(ValueError):
        lvl.build_level_contract(instrument=INST, scope="daily", generated_at_utc=d1_open,
                                 levels={"previous_day_high": 1.0, "previous_day_low": 0.5, "previous_day_close": 0.9},
                                 d1_latest_green=True, as_of_candle_close_utc=cc._fmt(d1_open))   # STRING -> raises


# --------------------------------------------------------------------------- safeguards preserved
def test_daily_dark_when_scope_not_authorised(monkeypatch):
    fake = _seed_d1(_FakeRedis(), days=30)
    _levels_env(monkeypatch, scopes="session,intraday", d1=False)   # no daily scope
    _run_step(fake)
    assert "hermes:levels:XAU_USD:daily:v1" not in fake.kv


def test_daily_blocked_below_depth(monkeypatch):
    fake = _seed_d1(_FakeRedis(), days=20)                  # < 26
    _levels_env(monkeypatch, scopes="session,intraday,daily", d1=True)
    _run_step(fake)
    assert "hermes:levels:XAU_USD:daily:v1" not in fake.kv  # depth guard blocks daily


def test_daily_source_is_d1_history_only_no_sql_no_marketmap_no_latest():
    import inspect
    src = inspect.getsource(steps.sessions_levels_step) + inspect.getsource(steps._read_d1_history_validated)
    assert "D1:history:v1" in src or "D1_TF" in src
    assert ":D1:latest:" not in inspect.getsource(steps._read_d1_history_validated)   # no D1 latest fallback
    assert "pymysql" not in inspect.getsource(steps._read_d1_history_validated)
    assert "get_db_config" not in inspect.getsource(steps._read_d1_history_validated)
    assert "market_map" not in inspect.getsource(steps._read_d1_history_validated)


def test_safeguards_still_present():
    for name in ("_d1_tf_if_ready", "_read_d1_history_validated", "_d1_derived_ready", "_d1_daily_levels"):
        assert hasattr(steps, name)
    src = __import__("inspect").getsource(steps._read_d1_history_validated)
    assert "assert_sealed_complete_d1" in src and "XAUUSD" in src and "(22, 0)" in src   # sealed + alias-deny + anchor


def test_xauusd_denied_in_d1_source():
    fake = _FakeRedis()
    env = _sealed_d1(datetime(2026, 5, 1, 22, 0, tzinfo=UTC))
    env["data"]["instrument"] = "XAUUSD"
    fake.kv[d1h.d1_history_key.__wrapped__(INST, 1) if hasattr(d1h.d1_history_key, "__wrapped__")
            else f"hermes:candles:{INST}:D1:history:v1:1"] = json.dumps(env)
    fake.z[f"hermes:candles:{INST}:D1:history:v1:index"] = {"1": 1}
    with pytest.raises(ValueError):
        steps._read_d1_history_validated(fake, 10)          # XAUUSD / non-canonical rejected
