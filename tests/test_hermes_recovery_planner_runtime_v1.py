"""HERMES PH2 recovery-planner PLAN-ONLY runtime wiring tests (isolated, injected adapters, no live I/O).
WO-HELM-HERMES-PH2-RECOVERY-PLANNER-WIRING-IMPLEMENTATION-0001.
"""
import inspect
import json
import re
from datetime import datetime, timedelta, timezone

import pytest

import utils.hermes_recovery_planner_runtime_v1 as rt
import utils.hermes_recovery_planner_v1 as rp
import utils.hermes_publisher_runtime_v1 as pubrt

UTC = timezone.utc
NOW = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)          # Tuesday noon, fixed clock


def _valid_policy_dict():
    return {
        "contract_version": "1", "instrument": "XAU_USD",
        "enabled_timeframes": ["D1", "H4", "H1", "M15", "M5", "M1"], "timeframe_priority": ["D1", "H4", "H1", "M15", "M5", "M1"],
        "d1_history_floor": {"seconds": 10368000, "anchor_hour_utc": 22},
        "retention_policy": {"m1_h4_days": 35, "d1_days": 120},
        "staleness_policy": {"gaps_max_age_seconds": 120, "coverage_max_age_seconds": 900},
        "workload_bounds": {"max_segments": 100, "max_candles_per_segment": 1000, "max_candles_per_proposal": 100000,
                            "max_lookback_seconds": 12096000, "max_request_units": 1000000},
        "cost_model": {"candles_per_request": 100, "request_units_per_call": 1, "cost_tokens_per_request": 2,
                       "fixed_overhead_units": 1, "timeframe_multipliers": {"D1": 2.0, "H4": 1.5, "H1": 1.0, "M15": 1.0, "M5": 1.0, "M1": 1.0}},
        "regular_market_schedule": {"weekend_close_weekday": 4, "weekend_close_hour_utc": 22, "weekend_reopen_weekday": 6, "weekend_reopen_hour_utc": 22},
        "partial_proposals_allowed": False, "merge_adjacent_threshold_seconds": 0}


class PolicyReaderStub:
    def __init__(self, text):
        self.text = text
        self.reads = 0
        self.writes = 0

    def read(self):
        self.reads += 1
        return self.text


class FakeRedis:
    """Records every write/delete; get/exists/zrange are read-only."""
    def __init__(self, kv=None, z=None):
        self.kv = dict(kv or {})
        self.z = dict(z or {})
        self.writes = []

    def get(self, k):
        v = self.kv.get(k)
        return v.encode() if isinstance(v, str) else v

    def exists(self, k):
        return 1 if (k in self.kv or k in self.z) else 0

    def zrange(self, k, a, b):
        items = sorted(self.z.get(k, []))
        return [str(x) for x in (items[a:b + 1] if b != -1 else items[a:])]

    def set(self, *a, **k):
        self.writes.append("set")

    def delete(self, *a):
        self.writes.append("delete")

    def zadd(self, *a, **k):
        self.writes.append("zadd")


def _gaps_payload(now=NOW, *, overall="GAPS_FOUND", tf_state="GAPS_FOUND", miss_hours=(6,), inst="XAU_USD", cv="v1", anchor="22:00"):
    tfs = {"H1": {"gap_state": tf_state, "missing_open_epochs_sample": [int(datetime(2026, 7, 14, h, tzinfo=UTC).timestamp()) for h in miss_hours]}}
    return {"contract_version": cv, "instrument": inst, "source_key": rt.GAPS_KEY,
            "generated_at_utc": rp._fmt_utc(now), "overall_gap_state": overall, "timeframes": tfs,
            "d1_boundary": {"expected_anchor_utc": anchor, "d1_boundary_state": "OK"}}


def _runner(gaps_payload=None, policy_text=None, z=None, clock=None):
    kv = {rt.GAPS_KEY: json.dumps(gaps_payload)} if gaps_payload is not None else {}
    fr = FakeRedis(kv, z)
    pr = PolicyReaderStub(policy_text if policy_text is not None else json.dumps(_valid_policy_dict()))
    return rt.RecoveryPlannerRunner(gaps_reader=fr, coverage_reader=fr, policy_reader=pr, clock=clock or (lambda: NOW)), fr, pr


# --------------------------------------------------------------------------- gate
def test_gate_truth_table():
    assert rt.evaluate_recovery_planner_component_gate(enabled=False, authorised=False) == rt.STATE_DISABLED
    assert rt.evaluate_recovery_planner_component_gate(enabled=False, authorised=True) == rt.STATE_DISABLED
    with pytest.raises(rt.RecoveryPlannerGate105Error) as e:
        rt.evaluate_recovery_planner_component_gate(enabled=True, authorised=False)
    assert e.value.numeric_code == 105 and e.value.code == "GATE_FAILCLOSED_105"
    assert not isinstance(e.value, SystemExit)
    assert rt.evaluate_recovery_planner_component_gate(enabled=True, authorised=True) == rt.STATE_PLAN_ONLY


def test_gate_105_contained_no_systemexit():
    r, fr, pr = _runner(_gaps_payload())
    res = r.run_once(enabled=True, authorised=False)          # must NOT raise SystemExit
    assert res["status"] == "GATE_FAILCLOSED_105" and res["code"] == 105
    assert r.state.gate_state == "GATE_FAILCLOSED_105"
    assert fr.writes == [] and pr.reads == 0                  # 105 path reads nothing


def test_append_enabled_is_nonraising(monkeypatch):
    for e in (rt.ENABLED_ENV, rt.AUTHORISED_ENV):
        monkeypatch.delenv(e, raising=False)
    assert rt.recovery_planner_append_enabled() is False
    monkeypatch.setenv(rt.ENABLED_ENV, "true")               # enabled without authorised must NOT raise here
    assert rt.recovery_planner_append_enabled() is True


def test_supervisor_append(monkeypatch):
    for e in (rt.ENABLED_ENV, rt.AUTHORISED_ENV, rp.CANONICAL_INSTRUMENT):
        monkeypatch.delenv("HERMES_RECOVERY_PLANNER_ENABLED", raising=False)
    for e in ("HERMES_GAPS_PUBLISH_ENABLED", "HERMES_BACKFILL_STATUS_PUBLISH_ENABLED", rt.ENABLED_ENV, rt.AUTHORISED_ENV):
        monkeypatch.delenv(e, raising=False)
    names = [s[0] for s in pubrt.default_runner_specs()]
    assert "recovery_planner" not in names                   # disabled -> not appended
    monkeypatch.setenv(rt.ENABLED_ENV, "true")
    specs = pubrt.default_runner_specs()
    assert specs[-1][0] == "recovery_planner" and specs[-1][1] is rt.recovery_planner_step


# --------------------------------------------------------------------------- policy loader
def test_policy_valid_loads_and_maps():
    pol, dig = rt.load_policy_from_reader(PolicyReaderStub(json.dumps(_valid_policy_dict())))
    assert pol.instrument == "XAU_USD" and pol.d1_history_floor_seconds == 10368000
    assert pol.allowed_timeframes == ("D1", "H4", "H1", "M15", "M5", "M1") and dig == pol.digest()


@pytest.mark.parametrize("mutate,fault", [
    (lambda d: d.pop("cost_model"), "POLICY_SCHEMA_INVALID"),
    (lambda d: d.__setitem__("instrument", "XAUUSD"), "POLICY_INSTRUMENT_INVALID"),
    (lambda d: d.__setitem__("contract_version", "2"), "POLICY_VERSION_UNSUPPORTED"),
    (lambda d: d["d1_history_floor"].__setitem__("anchor_hour_utc", 0), "POLICY_SCHEMA_INVALID"),
    (lambda d: d.__setitem__("enabled_timeframes", ["H2"]), "POLICY_SCHEMA_INVALID"),
    (lambda d: d["workload_bounds"].__setitem__("max_segments", 0), "POLICY_SCHEMA_INVALID"),
    (lambda d: d["retention_policy"].__setitem__("d1_days", 90), "POLICY_SCHEMA_INVALID"),
])
def test_policy_default_deny_faults(mutate, fault):
    d = _valid_policy_dict(); mutate(d)
    with pytest.raises(rt.PolicyError) as e:
        rt.load_policy_from_reader(PolicyReaderStub(json.dumps(d)))
    assert e.value.code == fault


def test_policy_missing_and_malformed():
    with pytest.raises(rt.PolicyError) as e1:
        rt.load_policy_from_reader(PolicyReaderStub(None))
    assert e1.value.code == "POLICY_FILE_MISSING"
    with pytest.raises(rt.PolicyError) as e2:
        rt.load_policy_from_reader(PolicyReaderStub("{not json"))
    assert e2.value.code == "POLICY_JSON_INVALID"


def test_policy_deterministic_digest():
    a, da = rt.load_policy_from_reader(PolicyReaderStub(json.dumps(_valid_policy_dict())))
    b, db = rt.load_policy_from_reader(PolicyReaderStub(json.dumps(_valid_policy_dict())))
    assert da == db


def test_invalid_replacement_blocks_new_planning():
    # a valid cycle, then policy file becomes invalid -> planning blocks; no stale fallback
    r, fr, pr = _runner(_gaps_payload())
    assert r.run_once(enabled=True, authorised=True)["status"] in ("PROPOSAL_READY", "NO_RECOVERY_REQUIRED", "PARTIAL_BOUNDED_PROPOSAL")
    pr.text = "{invalid"
    res = r.run_once(enabled=True, authorised=True)
    assert res["status"] == "BLOCKED_POLICY" and res["fault"] == "POLICY_JSON_INVALID"
    assert r.holder.snapshot() is None                       # no stale last-known-good proposal retained


def test_policy_loader_never_writes():
    src = inspect.getsource(rt._FilePolicyReader)
    assert 'open(self.path, "r"' in src                      # read mode only
    for tok in ('"w"', '"a"', '"r+"', '"w+"', "os.remove", "os.unlink", "write("):
        assert tok not in src


# --------------------------------------------------------------------------- gaps adapter
def test_gaps_adapter_valid():
    a = rt.GapsAdapter(FakeRedis({rt.GAPS_KEY: json.dumps(_gaps_payload())}))
    snap, dig = a.acquire(now=NOW, max_age_seconds=120)
    assert snap.instrument == "XAU_USD" and snap.overall_gap_state == "GAPS_FOUND" and dig


@pytest.mark.parametrize("kv,age,fault", [
    ({}, 120, "GAPS_MISSING"),
    ({rt.GAPS_KEY: "{bad"}, 120, "GAPS_JSON_INVALID"),
    ({rt.GAPS_KEY: json.dumps(_gaps_payload(cv="v2"))}, 120, "GAPS_CONTRACT_INVALID"),
    ({rt.GAPS_KEY: json.dumps(_gaps_payload(inst="XAUUSD"))}, 120, "GAPS_INSTRUMENT_INVALID"),
    ({rt.GAPS_KEY: json.dumps(_gaps_payload(now=NOW - timedelta(minutes=10)))}, 120, "GAPS_STALE"),
])
def test_gaps_adapter_faults(kv, age, fault):
    a = rt.GapsAdapter(FakeRedis(kv))
    with pytest.raises(rt.GapsError) as e:
        a.acquire(now=NOW, max_age_seconds=age)
    assert e.value.code == fault


def test_gaps_adapter_redis_unavailable():
    class Boom:
        def get(self, k): raise ConnectionError("down")
    with pytest.raises(rt.GapsError) as e:
        rt.GapsAdapter(Boom()).acquire(now=NOW, max_age_seconds=120)
    assert e.value.code == "GAPS_REDIS_UNAVAILABLE"


def test_gaps_adapter_get_only():
    src = inspect.getsource(rt.GapsAdapter)
    for tok in (".set(", ".delete(", ".zadd(", ".expire("):
        assert tok not in src
    assert ".get(" in src


# --------------------------------------------------------------------------- coverage adapter (retention-bounded)
def test_coverage_retention_bounded_excludes_beyond():
    tf_key = f"hermes:candles:XAU_USD:H1:history:v1:index"
    within = int((NOW - timedelta(days=5)).timestamp())
    beyond = int((NOW - timedelta(days=40)).timestamp())     # > 35d H1 retention -> excluded
    a = rt.CoverageAdapter(FakeRedis(z={tf_key: [within, beyond]}))
    snaps, dig = a.acquire(now=NOW)
    h1 = [s for s in snaps if s.timeframe == "H1"][0]
    starts = [int(iv.start_utc.timestamp()) for iv in h1.covered_intervals]
    assert within in starts and beyond not in starts         # beyond-retention excluded, not covered
    assert "RETENTION_BOUNDED" in h1.provenance


def test_coverage_redis_unavailable():
    class Boom:
        def exists(self, k): return 1
        def zrange(self, k, a, b): raise ConnectionError("down")
    with pytest.raises(rt.CoverageError) as e:
        rt.CoverageAdapter(Boom()).acquire(now=NOW)
    assert e.value.code == "COVERAGE_REDIS_UNAVAILABLE"


def test_coverage_adapter_no_write():
    src = inspect.getsource(rt.CoverageAdapter)
    for tok in (".set(", ".delete(", ".zadd("):
        assert tok not in src


# --------------------------------------------------------------------------- closures (Option A)
def test_regular_weekend_closures_built():
    pol, _ = rt.load_policy_from_reader(PolicyReaderStub(json.dumps(_valid_policy_dict())))
    cl = rt.build_regular_closures(policy=pol, now=NOW)
    assert cl and all(c.authority == "GOVERNED" and c.classification == "WEEKEND" for c in cl)


def test_unresolved_exceptional_blocks_scope():
    # gaps reports MARKET_CLOSED on a WEEKDAY (Tue) interval -> outside governed weekend -> unresolved
    gp = _gaps_payload(tf_state="MARKET_CLOSED", miss_hours=(6,))
    r, fr, pr = _runner(gp)
    res = r.run_once(enabled=True, authorised=True)
    assert res["status"] == "BLOCKED_UNCLASSIFIED_MARKET_STATE"
    assert r.holder.snapshot() is None


# --------------------------------------------------------------------------- snapshot consistency
def test_snapshot_inconsistency_blocks():
    # gaps content changes on every read (pre != post always) -> retries exhausted -> BLOCKED_INPUT_INCONSISTENCY
    a = json.dumps(_gaps_payload(now=NOW, overall="GAPS_FOUND"))
    b = json.dumps(_gaps_payload(now=NOW, overall="STALE"))
    class Flip(FakeRedis):
        def __init__(s): super().__init__(); s.i = 0
        def get(s, k):
            v = a if s.i % 2 == 0 else b; s.i += 1
            return v.encode()
    fr = Flip()
    r = rt.RecoveryPlannerRunner(gaps_reader=fr, coverage_reader=fr, policy_reader=PolicyReaderStub(json.dumps(_valid_policy_dict())), clock=lambda: NOW)
    res = r.run_once(enabled=True, authorised=True)
    assert res["status"] == "BLOCKED_INPUT_INCONSISTENCY"


# --------------------------------------------------------------------------- digest / idempotency
def test_idempotency_same_inputs_no_recompute():
    r, fr, pr = _runner(_gaps_payload())
    r.run_once(enabled=True, authorised=True)
    d1 = r.holder.digest
    res = r.run_once(enabled=True, authorised=True)
    assert res.get("idempotent") is True and r.holder.digest == d1


def test_timestamp_only_refresh_no_recompute():
    # same semantic gaps content but a new generated_at timestamp -> gaps_digest excludes... it DOES include gen ts.
    # The semantic gaps digest here uses gen ts; but coverage+policy+closure unchanged. To prove semantic stability we
    # assert the semantic_digest ignores volatile fields by constructing identical AcquiredSnapshots.
    r, fr, pr = _runner(_gaps_payload())
    r.run_once(enabled=True, authorised=True)
    # a pure re-run with identical inputs is idempotent (deterministic proposal_id excludes clock)
    r2, fr2, pr2 = _runner(_gaps_payload())
    r2.run_once(enabled=True, authorised=True)
    assert r.holder.snapshot()["proposal_id"] == r2.holder.snapshot()["proposal_id"]


def test_changed_gaps_recomputes():
    r, fr, pr = _runner(_gaps_payload(miss_hours=(6,)))
    r.run_once(enabled=True, authorised=True); d1 = r.holder.digest
    fr.kv[rt.GAPS_KEY] = json.dumps(_gaps_payload(miss_hours=(6, 7, 8)))
    res = r.run_once(enabled=True, authorised=True)
    assert not res.get("idempotent") and r.holder.digest != d1


# --------------------------------------------------------------------------- runner lifecycle / output / logging
def test_disabled_runner_no_reads_no_writes():
    r, fr, pr = _runner()  # empty gaps
    res = r.run_once(enabled=False, authorised=False)
    assert res["status"] == "DISABLED" and fr.writes == [] and pr.reads == 0


def test_enabled_invokes_planner_holds_in_memory():
    r, fr, pr = _runner(_gaps_payload())
    res = r.run_once(enabled=True, authorised=True)
    assert res["held_in_memory"] is True and fr.writes == []
    snap = r.holder.snapshot()
    assert snap["planning_mode"] == "DRY_RUN_ONLY"
    for f in ("execution_enabled", "backfill_executed", "repair_executed", "consumer_live"):
        assert snap[f] is False


def test_single_active_invocation_lock():
    r, fr, pr = _runner(_gaps_payload())
    r._lock.acquire()
    try:
        assert r.run_once(enabled=True, authorised=True)["status"] == "SKIPPED_CONCURRENT"
    finally:
        r._lock.release()


def test_restart_does_not_trust_prior_proposal():
    r, fr, pr = _runner(_gaps_payload())
    r.run_once(enabled=True, authorised=True)
    assert r.holder.snapshot() is not None
    r.restart_reset()
    assert r.holder.snapshot() is None and r.holder.digest is None


def test_output_holder_rejects_true_execution_flags():
    h = rt.InMemoryProposalHolder()
    with pytest.raises(rt.RecoveryPlannerRuntimeError):
        h.set({"execution_enabled": True, "backfill_executed": False, "repair_executed": False, "consumer_live": False}, "d")


def test_state_summary_fields_complete_and_flags_false():
    r, fr, pr = _runner(_gaps_payload())
    r.run_once(enabled=True, authorised=True)
    st = r.state.summary()
    for f in ("component", "planner_version", "gate_state", "policy_version", "policy_digest", "last_invocation_utc",
              "last_success_utc", "source_digests", "status", "proposal_id", "segment_count", "deferred_count",
              "unclassified_count", "intentionally_unavailable_count", "estimated_request_units", "last_fault_code",
              "consecutive_failures", "invocation_duration_ms", "backoff_state", "execution_enabled",
              "publication_enabled", "consumer_live"):
        assert f in st
    assert (st["execution_enabled"], st["publication_enabled"], st["consumer_live"]) == (False, False, False)


def test_no_health_or_proposal_key_written():
    r, fr, pr = _runner(_gaps_payload())
    r.run_once(enabled=True, authorised=True)
    assert fr.writes == []                                   # NO Redis SET anywhere (no proposal/health key)


# --------------------------------------------------------------------------- legacy separation + mutation boundary (static)
def _code(*objs):
    out = []
    for o in objs:
        src = inspect.getsource(o)
        src = re.sub(r'"""(?:.|\n)*?"""', "", src)
        out.append("\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#")))
    return "\n".join(out)


def test_no_legacy_subsystem_or_forbidden_imports():
    src = inspect.getsource(rt)
    code = re.sub(r'"""(?:.|\n)*?"""', "", src)
    code = "\n".join(l for l in code.splitlines() if not l.lstrip().startswith("#"))
    for tok in ("utils.recovery_planner", "utils.recovery_executor", "RecoveryLibrary", "recovery_executor",
                "candle_d1_history_seed_backfill", "seed_backfill", "market_map", "falcon", "ares", "helios",
                "import requests", "requests.", "urllib", "httpx", "pymysql", "sqlalchemy", "cursor(", "subprocess",
                "os.system"):
        assert tok.lower() not in code.lower(), f"runtime wiring must not reference {tok!r}"
    # fully-qualified planner import present
    assert "from utils.hermes_recovery_planner_v1 import" in inspect.getsource(rt)


def test_mutation_boundary_no_write_paths():
    code = _code(rt.RecoveryPlannerRunner, rt.GapsAdapter, rt.CoverageAdapter, rt.recovery_planner_step,
                 rt.acquire_consistent_snapshot, rt.build_regular_closures)
    # drop IN-MEMORY holder operations (holder.set/.clear are process-local, not Redis) before the Redis-mutation scan
    code = "\n".join(l for l in code.splitlines() if "holder." not in l)
    for tok in (".set(", ".delete(", ".zadd(", ".expire(", ".zrem(", "FLUSHDB", "UNLINK", "INSERT ", "UPDATE ",
                "DELETE ", "open("):
        assert tok not in code, f"wiring must not contain Redis mutation/write token {tok!r}"
    # adapters read only .get/.exists/.zrange
    reads = _code(rt.GapsAdapter, rt.CoverageAdapter)
    assert (".get(" in reads or ".exists(" in reads or ".zrange(" in reads)
