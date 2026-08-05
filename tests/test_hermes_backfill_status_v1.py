"""HERMES PH2 backfill-status surface (hermes:backfill:status:XAU_USD:v1) — status-only, code-only, no live I/O.
WO-HELM-HERMES-PH2-BACKFILL-STATUS-SURFACE-0001.

Proves the surface is telemetry-only: it consumes the live gaps contract and reports recovery readiness, never executes/
repairs/writes/deletes. Missing/invalid gaps -> GAPS_SURFACE_MISSING (fail-closed); GAPS_FOUND -> non-OK recovery-needed;
invariants execution_enabled/backfill_executed/repair_executed/consumer_live hard false; active_job/completed_pct null.
"""
import inspect
import json
import re
from datetime import datetime, timedelta, timezone

import pytest

import utils.hermes_backfill_status_v1 as bfs
import utils.hermes_gaps_v1 as gaps
import utils.candle_d1_derivation_v1 as d1d
import utils.candle_contract_v1 as cc


@pytest.fixture(autouse=True)
def _registry(monkeypatch):
    """WO-...-XAU-MODULE-ADOPTION-0001: backfill-status selection follows the gap capability in the canonical registry.
    Patch the loader to the XAU-active rollout so the publisher selects exactly [XAU_USD] with no DB (single-key behaviour
    is a CONSEQUENCE of the registry gap-detection flag, not a hard-coded ticker)."""
    from tests.test_hermes_instrument_registry_v1 import rollout_rows
    import utils.hermes_instrument_registry_v1 as reg
    recs = reg.load_registry(rollout_rows())
    monkeypatch.setattr(reg, "load_from_db", lambda fetch=None: recs)


UTC = timezone.utc
INST = "XAU_USD"
GAPS_KEY = "hermes:gaps:XAU_USD:v1"                     # XAU gaps source key (byte-identical to former aggregate)
BFS_KEY = "hermes:backfill:status:XAU_USD:v1"           # XAU backfill-status key (byte-identical to former aggregate)
NOW = datetime(2026, 7, 13, 6, 51, tzinfo=UTC)


class FakeRedis:
    """Records every write/delete so tests can prove the status surface performs NONE."""
    def __init__(self, kv=None):
        self.kv = kv or {}
        self.sets = []
        self.deletes = []

    def get(self, k):
        v = self.kv.get(k)
        return v.encode() if isinstance(v, str) else v

    def set(self, k, v, ex=None):
        self.sets.append((k, ex))
        self.kv[k] = v

    def delete(self, *a):
        self.deletes.extend(a)


def _sealed_d1(d1_open=datetime(2026, 7, 12, 22, 0, tzinfo=UTC)):
    opens = d1d.d1_child_h4_opens(d1_open)
    specs = [(2000, 2010, 1990, 2005, 10), (2005, 2030, 1995, 2020, 11), (2020, 2080, 2010, 2050, 12),
             (2050, 2060, 2000, 2030, 13), (2030, 2040, 1900, 1950, 14), (1950, 1975, 1940, 1970, 15)]
    kids = [{"timestamp": opens[i], "open": specs[i][0], "high": specs[i][1], "low": specs[i][2],
             "close": specs[i][3], "volume": specs[i][4]} for i in range(6)]
    env, _ = d1d.derive_d1(instrument=INST, d1_open=d1_open, h4_children=kids,
                           generated_at_utc=cc.normalise_utc(d1_open) + timedelta(seconds=d1d.D1_SECONDS))
    return env


def _live_gaps_contract(now=NOW, *, gap_state="GAPS_FOUND", d1_depth=34, d1_state="OK"):
    """Build a real, VALID gaps contract via the deployed gaps builder with explicit per-tf states (deterministic, fast)."""
    tfb = {}
    for tf in gaps.TIMEFRAMES:
        tfb[tf] = {"timeframe": tf, "gap_state": gap_state, "market_phase": "OPEN",
                   "history_depth": (d1_depth if tf == "D1" else 500), "sufficient_depth": True, "missing_slots": 3}
    d1b = {"expected_anchor_utc": "22:00", "ny5pm_anchor": True, "latest_open_utc": "2026-07-12T22:00:00.000Z",
           "history_newest_open_utc": "2026-07-12T22:00:00.000Z", "latest_matches_history_newest": True,
           "sealed_complete": True, "source_count": 6, "expected_source_count": 6, "coverage": 1.0, "gap": "NONE",
           "invalid_anchor_count": 0, "non_22_anchor_count": 0, "forward_writer_enabled": True,
           "forward_writer_authorised": True, "weekend_d1_buckets": "NOT_EXPECTED", "d1_boundary_state": d1_state}
    return gaps.build_gaps_contract(instrument=INST, timeframes=tfb, d1_boundary=d1b, generated_at_utc=now)


# --------------------------------------------------------------------------- key + 1: missing gaps -> GAPS_SURFACE_MISSING
def test_backfill_status_key_is_per_instrument_xau_byte_identical():
    assert bfs.backfill_status_key("XAU_USD") == BFS_KEY == "hermes:backfill:status:XAU_USD:v1"
    assert bfs.backfill_status_key("EUR_USD") == "hermes:backfill:status:EUR_USD:v1"   # generic per instrument
    assert "XAUUSD" not in bfs.backfill_status_key("XAU_USD")


def test_missing_gaps_surface_is_gaps_surface_missing():
    r = FakeRedis({})                                              # no gaps key
    c = bfs.analyze_backfill_status(r, instrument=INST, now=NOW, gate_values={})
    assert c["overall_status"] == "GAPS_SURFACE_MISSING"
    assert c["gaps_source"]["present"] is False
    assert r.sets == [] and r.deletes == []                       # 10 & 11: no writes/deletes


def test_unparseable_gaps_fails_closed():
    r = FakeRedis({GAPS_KEY: "{not json"})
    c = bfs.analyze_backfill_status(r, instrument=INST, now=NOW, gate_values={})
    assert c["overall_status"] == "GAPS_SURFACE_MISSING"          # 18: invalid gaps fails closed


def test_invalid_gaps_contract_fails_closed():
    r = FakeRedis({GAPS_KEY: json.dumps({"instrument": "XAUUSD"})})   # wrong-instrument gaps contract
    c = bfs.analyze_backfill_status(r, instrument=INST, now=NOW, gate_values={})
    assert c["overall_status"] == "GAPS_SURFACE_MISSING"
    assert "XAUUSD" not in json.dumps(c)                          # alias never leaks


# --------------------------------------------------------------------------- 2 & 6: GAPS_FOUND -> non-OK recovery
def test_gaps_found_yields_recovery_needed_not_ok():
    gc = _live_gaps_contract(gap_state="GAPS_FOUND")
    assert gc["overall_gap_state"] == "GAPS_FOUND"                # worst-of all-tf GAPS_FOUND
    r = FakeRedis({GAPS_KEY: json.dumps(gc)})
    c = bfs.analyze_backfill_status(r, instrument=INST, now=NOW, gate_values={})
    assert c["overall_status"] == "READY_FOR_BACKFILL_DESIGN"
    assert c["overall_status"] != "OK"
    assert c["blocked_reason"] == bfs.NO_EXECUTOR_REASON
    # per-tf mirrors safely; no tf silently OK while gaps GAPS_FOUND
    for tf, b in c["timeframes"].items():
        assert b["status"] in bfs.STATUS_ORDER
        assert not (b["gap_state"] == "GAPS_FOUND" and b["status"] == "OK")


def test_gaps_ok_is_idle_never_ok():
    gc = _live_gaps_contract(gap_state="OK")
    assert gc["overall_gap_state"] == "OK"
    r = FakeRedis({GAPS_KEY: json.dumps(gc)})
    c = bfs.analyze_backfill_status(r, instrument=INST, now=NOW, gate_values={})
    assert c["overall_status"] == "IDLE"                          # gaps OK -> IDLE, never claims OK/complete
    assert c["overall_status"] != "OK"


# --------------------------------------------------------------------------- 3,4,5: status-only invariants
def test_status_only_invariants():
    gc = _live_gaps_contract()
    r = FakeRedis({GAPS_KEY: json.dumps(gc)})
    c = bfs.analyze_backfill_status(r, instrument=INST, now=NOW, gate_values={})
    assert c["consumer_live"] is False
    assert c["execution_enabled"] is False
    assert c["backfill_executed"] is False
    assert c["repair_executed"] is False
    assert c["active_job"] is None
    assert c["completed_pct"] is None
    assert bfs.validate_backfill_status_contract(c) is True       # 17: validates


# --------------------------------------------------------------------------- 7: D1 block reports gates without action
def test_d1_block_reports_boundary_and_forward_gates():
    gc = _live_gaps_contract()
    r = FakeRedis({GAPS_KEY: json.dumps(gc)})
    c = bfs.analyze_backfill_status(r, instrument=INST, now=NOW,
                                    gate_values={bfs.D1_FWD_ENABLED_ENV: True, bfs.D1_FWD_AUTHORISED_ENV: True,
                                                 bfs.D1_BACKFILL_ENABLED_ENV: True, bfs.D1_BACKFILL_AUTHORISED_ENV: True})
    d1 = c["d1"]
    for k in ("history_depth", "min_required_depth", "latest_open_utc", "history_newest_open_utc", "d1_boundary_state",
              "forward_writer_enabled", "forward_writer_authorised", "backfill_path_available", "status"):
        assert k in d1
    assert d1["backfill_path_available"] is True                  # D1 has a governed seed/backfill engine
    assert d1["d1_boundary_state"] == "OK"
    # D1 tf backfill_gates reported with values, but this surface still never executes
    d1_tf = c["timeframes"]["D1"]
    assert d1_tf["backfill_gates"]["enabled"] is True and d1_tf["backfill_gates"]["authorised"] is True
    assert d1_tf["backfill_gates"]["dry_run"] is True            # dry-run default even when enabled+authorised


def test_m1h4_have_no_executor_path():
    gc = _live_gaps_contract()
    r = FakeRedis({GAPS_KEY: json.dumps(gc)})
    c = bfs.analyze_backfill_status(r, instrument=INST, now=NOW, gate_values={})
    for tf in ("M1", "M5", "M15", "H1", "H4"):
        b = c["timeframes"][tf]
        assert b["backfill_path_available"] is False
        assert b["backfill_gates"] is None
        if b["status"] in ("READY_FOR_BACKFILL_DESIGN", "SOURCE_MISSING", "INSUFFICIENT_HISTORY", "STALE", "GAPS_FOUND"):
            assert b["blocked_reason"] == bfs.NO_EXECUTOR_REASON


# --------------------------------------------------------------------------- schema completeness
def test_contract_has_all_required_fields():
    gc = _live_gaps_contract()
    r = FakeRedis({GAPS_KEY: json.dumps(gc)})
    c = bfs.analyze_backfill_status(r, instrument=INST, now=NOW, gate_values={})
    for f in ("schema_version", "publisher", "instrument", "generated_at_utc", "source", "consumer_live",
              "execution_enabled", "backfill_executed", "repair_executed", "overall_status", "active_job",
              "last_completed_job", "blocked_reason", "rate_limit_tokens", "completed_pct", "timeframes", "d1",
              "gaps_source", "caveats"):
        assert f in c, f"missing top-level field {f}"
    assert set(c["timeframes"]) == {"M1", "M5", "M15", "H1", "H4", "D1"}
    for tf, b in c["timeframes"].items():
        for f in ("timeframe", "gap_state", "history_depth", "min_required_depth", "sufficient", "retention_policy",
                  "backfill_path_available", "backfill_gates", "forward_writer_gates", "last_backfill_run_marker",
                  "status", "blocked_reason"):
            assert f in b, f"{tf} missing {f}"


# --------------------------------------------------------------------------- 16: XAUUSD denied
def test_xauusd_denied_in_validation():
    gc = _live_gaps_contract()
    c = bfs.build_backfill_status_contract(instrument=INST, gaps_contract=gc, now=NOW)
    poisoned = dict(c); poisoned["instrument"] = "XAUUSD"
    with pytest.raises(ValueError, match="GOV-HERMES-BFS-00[12]"):
        bfs.validate_backfill_status_contract(poisoned)


def test_ok_overall_status_is_rejected_by_validation():
    gc = _live_gaps_contract()
    c = bfs.build_backfill_status_contract(instrument=INST, gaps_contract=gc, now=NOW)
    c2 = dict(c); c2["overall_status"] = "OK"
    with pytest.raises(ValueError, match="GOV-HERMES-BFS-006"):
        bfs.validate_backfill_status_contract(c2)


# --------------------------------------------------------------------------- dark publisher
def test_publisher_disabled_by_default(monkeypatch):
    for e in (bfs.STATUS_ENABLED_ENV, bfs.STATUS_AUTHORISED_ENV):
        monkeypatch.delenv(e, raising=False)
    assert isinstance(bfs.build_backfill_status_publisher_from_env(), bfs.DisabledBackfillStatusPublisher)


def test_publisher_enabled_without_authorised_fails_closed(monkeypatch):
    monkeypatch.setenv(bfs.STATUS_ENABLED_ENV, "true")
    monkeypatch.delenv(bfs.STATUS_AUTHORISED_ENV, raising=False)
    with pytest.raises(SystemExit) as e:
        bfs.build_backfill_status_publisher_from_env(redis_client=FakeRedis())
    assert e.value.code == 101


# --------------------------------------------------------------------------- 8-15: no execution / forbidden deps / no writes
def _code_only(*objs):
    out = []
    for o in objs:
        src = inspect.getsource(o)
        doc = getattr(o, "__doc__", "") or ""
        out.append("\n".join(l for l in src.replace(doc, "").splitlines() if not l.lstrip().startswith("#")))
    return "\n".join(out)


def test_no_execution_path_no_forbidden_deps():
    # scan the whole module CODE (strip ALL docstrings — module + function/class negative declarations — and comments)
    src = inspect.getsource(bfs)
    src = re.sub(r'"""(?:.|\n)*?"""', "", src)
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    for tok in ("pymysql", "get_db_config", "sqlalchemy", "cursor(", "market_map", "falcon", "requests.", "urllib",
                "oanda", "vendor", "execute_backfill", "run_backfill", "repair(", "consumer_live=True"):
        assert tok not in code, f"backfill-status module CODE must not reference {tok!r}"
    # WO-...-XAU-MODULE-ADOPTION-0001: the ONLY mutation the module performs is publish()'s single SET STATEMENT of the
    # per-instrument backfill-status key, executed once per registry-selected instrument. No delete/zadd/expire/zrem/hset/
    # lpush/rpush anywhere; SET appears exactly once (one call site in publish()).
    for tok in (".zadd(", ".delete(", ".expire(", ".zrem(", ".hset(", ".lpush(", ".rpush("):
        assert tok not in code, f"status surface must not call {tok!r}"
    assert code.count(".set(") == 1, "exactly one SET call site (per-instrument backfill-status key) may exist"
    pub_src = inspect.getsource(bfs.BackfillStatusPublisher.publish)
    assert pub_src.count(".set(") == 1 and "backfill_status_key(instrument)" in pub_src
    # analyze uses only .get( for Redis (never writes)
    ag = inspect.getsource(bfs.analyze_backfill_status)
    assert ".get(" in ag
    for tok in (".set(", ".zadd(", ".delete("):
        assert tok not in ag


# --------------------------------------------------------------------------- registry-driven per-instrument adoption
def _records(bfs_symbols):
    """Registry where exactly `bfs_symbols` are gap-detection-enabled (backfill-status follows gap capability)."""
    from tests.test_hermes_instrument_registry_v1 import _row
    import utils.hermes_instrument_registry_v1 as reg
    rows = [_row(s, "precious_metals", 3, 0.001, "metals", gap_cap=(1 if s in bfs_symbols else 0))
            for s in ("XAU_USD", "EUR_USD", "GBP_USD")]
    return reg.load_registry(rows)


def test_publish_xau_only_byte_identical_single_key():
    r = FakeRedis({GAPS_KEY: json.dumps(_live_gaps_contract())})
    pub = bfs.BackfillStatusPublisher(redis_client=r, records=_records({"XAU_USD"}))
    res = pub.publish(now=NOW)
    assert res["published"] == 1 and res["keys"] == [BFS_KEY]      # XAU byte-identical, single key
    assert r.sets == [(BFS_KEY, None)] and r.deletes == []
    assert res["execution_enabled"] is False and res["backfill_executed"] is False


def test_publish_multi_instrument_no_collision(monkeypatch):
    # multi-instrument = EXPANSION scope -> requires the runtime master/scope gate (EUR_USD calendar-eligible 'metals').
    monkeypatch.setenv("HERMES_ADVANCED_V1_MASTER_ENABLED", "true")
    monkeypatch.setenv("HERMES_ADVANCED_V1_PUBLISHER_MODE", "ACTIVE")
    eff_now = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)   # within the governed calendar effective window
    r = FakeRedis({GAPS_KEY: json.dumps(_live_gaps_contract()),
                   "hermes:gaps:EUR_USD:v1": json.dumps(_live_gaps_contract())})
    pub = bfs.BackfillStatusPublisher(redis_client=r, records=_records({"XAU_USD", "EUR_USD"}))
    res = pub.publish(now=eff_now)
    assert res["published"] == 2
    assert set(res["keys"]) == {BFS_KEY, "hermes:backfill:status:EUR_USD:v1"}
    for inst in ("XAU_USD", "EUR_USD"):
        c = json.loads(r.kv[bfs.backfill_status_key(inst)])
        assert c["instrument"] == inst and c["gaps_source"]["key"] == gaps.gaps_key(inst)
        bfs.validate_backfill_status_contract(c)


def test_publish_zero_selection_is_no_publication():
    r = FakeRedis({})
    pub = bfs.BackfillStatusPublisher(redis_client=r, records=_records(set()))
    res = pub.publish(now=NOW)
    assert res["published"] == 0 and res["keys"] == [] and r.sets == [] and r.deletes == []


def test_seven_new_never_published_via_capability_only():
    from tests.test_hermes_instrument_registry_v1 import rollout_rows
    import utils.hermes_instrument_registry_v1 as reg
    r = FakeRedis({GAPS_KEY: json.dumps(_live_gaps_contract())})
    pub = bfs.BackfillStatusPublisher(redis_client=r, records=reg.load_registry(rollout_rows()))
    res = pub.publish(now=NOW)
    assert res["keys"] == [BFS_KEY]                                # only the gap-enabled pilot
    for other in ("XAG_USD", "EUR_USD", "GBP_USD", "AUD_USD", "USD_JPY", "SPX500_USD", "WTICO_USD"):
        assert bfs.backfill_status_key(other) not in r.kv          # 7 new NOT_ENABLED -> never written
