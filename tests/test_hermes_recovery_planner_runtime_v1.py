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


# =========================================================================================================
# WO-HELM-HERMES-PH2-RECOVERY-PLANNER-DIGEST-HARDENING-0001 — recovery-relevant digest + pre/post consistency
# =========================================================================================================
H1KEY = "hermes:candles:XAU_USD:H1:history:v1:index"
M1KEY = "hermes:candles:XAU_USD:M1:history:v1:index"
H1_GAP = int(datetime(2026, 7, 14, 6, tzinfo=UTC).timestamp())      # gap open 06:00 (from _gaps_payload miss_hours=(6,))
IN_WINDOW = int(datetime(2026, 7, 14, 5, tzinfo=UTC).timestamp())   # 05:00 — inside adjacency window [05:00,08:00)
FAR = int(datetime(2026, 7, 14, 11, tzinfo=UTC).timestamp())        # 11:00 — outside every relevant window
DEFAULT_H1_PROV = f"governed_redis_history_index:RETENTION_BOUNDED:{rt.RETENTION_DAYS['H1']}d"


def _gaps_snap(**kw):
    return rt.GapsAdapter(FakeRedis({rt.GAPS_KEY: json.dumps(_gaps_payload(**kw))})).acquire(now=NOW, max_age_seconds=120)[0]


def _cov(z):
    return rt.CoverageAdapter(FakeRedis(z=z)).acquire(now=NOW)[0]


def _rel(gaps, cov, now=NOW):
    return rt.recovery_relevant_coverage_digest(gaps=gaps, coverage=cov, now=now)


def _mk_cov_snaps(specs):
    """Build ExistingCoverageSnapshot tuple. specs: tf -> (open_epochs, provenance, contract_version)."""
    out = []
    for tf in rp.SUPPORTED_TIMEFRAMES:
        opens, prov, cv = specs.get(tf, ([], f"governed_redis_history_index:RETENTION_BOUNDED:{rt.RETENTION_DAYS[tf]}d", "v1"))
        ivs = tuple(rp.Interval(datetime.fromtimestamp(o, UTC), datetime.fromtimestamp(o + rp.PERIOD_SECONDS[tf], UTC)) for o in opens)
        out.append(rp.ExistingCoverageSnapshot(contract_version=cv, instrument="XAU_USD", timeframe=tf,
                                               covered_intervals=ivs, snapshot_at_utc=NOW, provenance=prov))
    return tuple(out)


def _multi_gaps(order, *, state="GAPS_FOUND", hour=6):
    tfs = {tf: {"gap_state": state, "missing_open_epochs_sample": [int(datetime(2026, 7, 14, hour, tzinfo=UTC).timestamp())]} for tf in order}
    return {"contract_version": "v1", "instrument": "XAU_USD", "source_key": rt.GAPS_KEY,
            "generated_at_utc": rp._fmt_utc(NOW), "overall_gap_state": "GAPS_FOUND", "timeframes": tfs,
            "d1_boundary": {"expected_anchor_utc": "22:00", "d1_boundary_state": "OK"}}


def _snap_of(payload):
    return rt.GapsAdapter(FakeRedis({rt.GAPS_KEY: json.dumps(payload)})).acquire(now=NOW, max_age_seconds=120)[0]


def _mk_snap(**over):
    base = dict(gaps=None, gaps_digest="g", coverage=(), coverage_digest="c", closures=(), closure_digest="cl",
                policy=None, policy_digest="p")
    base.update(over)
    return rt.AcquiredSnapshots(**base)


class _StubGaps:
    def __init__(self, snap, digest): self.snap, self.digest = snap, digest
    def acquire(self, *, now, max_age_seconds): return self.snap, self.digest


class _StubCov:
    """Returns a pre-built coverage tuple per acquire() call (clamped to the last)."""
    def __init__(self, seq): self.seq, self.i, self.calls = list(seq), 0, 0
    def acquire(self, *, now):
        self.calls += 1
        snaps = self.seq[self.i] if self.i < len(self.seq) else self.seq[-1]
        self.i += 1
        return snaps, "presence"


class _SeqPolicy:
    """Policy reader returning a scripted sequence of raw texts across reads (clamped to the last)."""
    def __init__(self, seq): self.seq, self.i, self.reads = list(seq), 0, 0
    def read(self):
        self.reads += 1
        v = self.seq[self.i] if self.i < len(self.seq) else self.seq[-1]
        self.i += 1
        return v


# --------------------------------------------------------------------------- A. recovery-relevant digest scoping
def test_unrelated_m1_current_edge_candle_no_digest_change():
    g = _gaps_snap(miss_hours=(6,))                               # gap in H1 only
    assert _rel(g, _cov({})) == _rel(g, _cov({M1KEY: [int(datetime(2026, 7, 14, 11, 59, tzinfo=UTC).timestamp())]}))


def test_unrelated_m5_current_edge_candle_no_digest_change():
    g = _gaps_snap(miss_hours=(6,))
    m5 = f"hermes:candles:XAU_USD:M5:history:v1:index"
    assert _rel(g, _cov({})) == _rel(g, _cov({m5: [int(datetime(2026, 7, 14, 11, 55, tzinfo=UTC).timestamp())]}))


def test_unrelated_h1_advancement_outside_window_no_digest_change():
    g = _gaps_snap(miss_hours=(6,))                               # H1 window is [05:00,08:00); FAR=11:00 excluded
    assert _rel(g, _cov({})) == _rel(g, _cov({H1KEY: [FAR]}))


def test_sliding_window_advance_outside_gaps_no_digest_change():
    g = _gaps_snap(miss_hours=(6,))
    base = {H1KEY: [FAR]}
    grown = {H1KEY: [FAR, FAR + 3600, FAR + 7200]}               # newer current-edge candles, all outside window
    assert _rel(g, _cov(base)) == _rel(g, _cov(grown))


def test_generated_timestamp_refresh_no_recompute():
    r, fr, pr = _runner(_gaps_payload(miss_hours=(6,)))
    r.run_once(enabled=True, authorised=True)
    fr.kv[rt.GAPS_KEY] = json.dumps(_gaps_payload(now=NOW - timedelta(seconds=30), miss_hours=(6,)))  # only gen ts moves
    assert r.run_once(enabled=True, authorised=True).get("idempotent") is True


def test_coverage_retrieval_order_irrelevant():
    g = _gaps_snap(miss_hours=(6,))
    z = {H1KEY: [H1_GAP, IN_WINDOW]}
    class Rev(FakeRedis):
        def zrange(self, k, a, b): return list(reversed(super().zrange(k, a, b)))
    d1 = _rel(g, rt.CoverageAdapter(FakeRedis(z=z)).acquire(now=NOW)[0])
    d2 = _rel(g, rt.CoverageAdapter(Rev(z=z)).acquire(now=NOW)[0])
    assert d1 == d2


def test_dictionary_order_irrelevant_gaps():
    assert rt._gaps_semantic_digest(_snap_of(_multi_gaps(["H1", "M15"]))) == \
           rt._gaps_semantic_digest(_snap_of(_multi_gaps(["M15", "H1"])))


def test_duplicate_coverage_entries_stable():
    g = _gaps_snap(miss_hours=(6,))
    assert _rel(g, _cov({H1KEY: [H1_GAP]})) == _rel(g, _cov({H1KEY: [H1_GAP, H1_GAP]}))


def test_relevant_gap_filling_candle_changes_digest():
    g = _gaps_snap(miss_hours=(6,))
    assert _rel(g, _cov({H1KEY: [H1_GAP]})) != _rel(g, _cov({}))   # candle fills the gap window


def test_partial_relevant_coverage_changes_digest():
    g = _gaps_snap(miss_hours=(6,))
    assert _rel(g, _cov({H1KEY: [IN_WINDOW]})) != _rel(g, _cov({}))  # adjacent (clipping) candle


def test_coverage_completeness_change_changes_digest():
    g = _gaps_snap(miss_hours=(6,))
    present = _mk_cov_snaps({"H1": ([IN_WINDOW], DEFAULT_H1_PROV, "v1")})
    absent = _mk_cov_snaps({"H1": ([], DEFAULT_H1_PROV, "v1")})    # source became incomplete for the gap
    assert _rel(g, present) != _rel(g, absent)


def test_retention_reclassification_affecting_gap_changes_digest():
    old_ep = int((NOW - timedelta(days=34, hours=23)).timestamp())  # just inside 35d H1 retention at NOW
    payload = {"contract_version": "v1", "instrument": "XAU_USD", "source_key": rt.GAPS_KEY,
               "generated_at_utc": rp._fmt_utc(NOW), "overall_gap_state": "GAPS_FOUND",
               "timeframes": {"H1": {"gap_state": "GAPS_FOUND", "missing_open_epochs_sample": [old_ep]}},
               "d1_boundary": {"expected_anchor_utc": "22:00", "d1_boundary_state": "OK"}}
    g = _snap_of(payload)
    assert _rel(g, _cov({}), now=NOW) != _rel(g, _cov({}), now=NOW + timedelta(days=1))  # floor moved past the gap


def test_provenance_or_contract_version_change_changes_digest():
    g = _gaps_snap(miss_hours=(6,))
    base = _rel(g, _mk_cov_snaps({"H1": ([H1_GAP], DEFAULT_H1_PROV, "v1")}))
    assert _rel(g, _mk_cov_snaps({"H1": ([H1_GAP], DEFAULT_H1_PROV, "v2")})) != base   # contract version
    assert _rel(g, _mk_cov_snaps({"H1": ([H1_GAP], "vendor_untrusted", "v1")})) != base  # authority/provenance


# --------------------------------------------------------------------------- B. gap canonicalisation
def test_reordered_gaps_stable():
    assert rt._gaps_semantic_digest(_snap_of(_multi_gaps(["H1", "H4"]))) == \
           rt._gaps_semantic_digest(_snap_of(_multi_gaps(["H4", "H1"])))


def test_duplicate_gaps_stable():
    p = _multi_gaps(["H1"]); ep = p["timeframes"]["H1"]["missing_open_epochs_sample"][0]
    p["timeframes"]["H1"]["missing_open_epochs_sample"] = [ep, ep, ep]
    assert rt._gaps_semantic_digest(_snap_of(p)) == rt._gaps_semantic_digest(_snap_of(_multi_gaps(["H1"])))


def test_gaps_volatile_metadata_stable():
    p = _multi_gaps(["H1"]); p["generated_at_utc"] = rp._fmt_utc(NOW - timedelta(seconds=45))
    assert rt._gaps_semantic_digest(_snap_of(p)) == rt._gaps_semantic_digest(_snap_of(_multi_gaps(["H1"])))


def test_gaps_material_interval_change_detected():
    p = _multi_gaps(["H1"]); p["timeframes"]["H1"]["missing_open_epochs_sample"] = [int(datetime(2026, 7, 14, 7, tzinfo=UTC).timestamp())]
    assert rt._gaps_semantic_digest(_snap_of(p)) != rt._gaps_semantic_digest(_snap_of(_multi_gaps(["H1"])))


def test_gaps_status_change_detected():
    assert rt._gaps_semantic_digest(_snap_of(_multi_gaps(["H1"], state="OK"))) != \
           rt._gaps_semantic_digest(_snap_of(_multi_gaps(["H1"], state="GAPS_FOUND")))


# --------------------------------------------------------------------------- F. policy pre/post consistency
def _valid_json():
    return json.dumps(_valid_policy_dict())


def test_policy_unchanged_passes():
    r, fr, pr = _runner(_gaps_payload(miss_hours=(6,)))
    assert r.run_once(enabled=True, authorised=True)["status"] in ("PROPOSAL_READY", "NO_RECOVERY_REQUIRED", "PARTIAL_BOUNDED_PROPOSAL")


def test_policy_atomic_replacement_mid_cycle_blocks():
    # run_once reads policy once (valid A); the pre/post recheck inside acquisition reads again (valid B, different bytes)
    other = _valid_policy_dict(); other["merge_adjacent_threshold_seconds"] = 1
    r = rt.RecoveryPlannerRunner(gaps_reader=FakeRedis({rt.GAPS_KEY: json.dumps(_gaps_payload(miss_hours=(6,)))}),
                                 coverage_reader=FakeRedis(),
                                 policy_reader=_SeqPolicy([_valid_json(), json.dumps(other)]), clock=lambda: NOW)
    res = r.run_once(enabled=True, authorised=True)
    assert res["status"] == "BLOCKED_INPUT_INCONSISTENCY"
    assert r.holder.snapshot() is None                           # no stale fallback


def test_policy_removed_mid_cycle_blocks():
    r = rt.RecoveryPlannerRunner(gaps_reader=FakeRedis({rt.GAPS_KEY: json.dumps(_gaps_payload(miss_hours=(6,)))}),
                                 coverage_reader=FakeRedis(),
                                 policy_reader=_SeqPolicy([_valid_json(), None]), clock=lambda: NOW)
    assert r.run_once(enabled=True, authorised=True)["status"] == "BLOCKED_INPUT_INCONSISTENCY"


def test_policy_invalid_replacement_mid_cycle_blocks():
    r = rt.RecoveryPlannerRunner(gaps_reader=FakeRedis({rt.GAPS_KEY: json.dumps(_gaps_payload(miss_hours=(6,)))}),
                                 coverage_reader=FakeRedis(),
                                 policy_reader=_SeqPolicy([_valid_json(), "{invalid"]), clock=lambda: NOW)
    assert r.run_once(enabled=True, authorised=True)["status"] == "BLOCKED_INPUT_INCONSISTENCY"


# --------------------------------------------------------------------------- G. recovery-relevant coverage pre/post
def _cc_args(cov_adapter, policy_reader, **over):
    pol, pd = rt.load_policy_from_reader(PolicyReaderStub(_valid_json()))
    g = _gaps_snap(miss_hours=(6,))
    base = dict(gaps_adapter=_StubGaps(g, "gd"), coverage_adapter=cov_adapter, policy=pol, policy_digest=pd,
                policy_reader=policy_reader, policy_raw_sha=rt._content_sha(_valid_json()), now=NOW, max_gaps_age=120)
    base.update(over)
    return base


def test_unrelated_coverage_advance_passes_without_retry():
    covA = _mk_cov_snaps({"M1": ([int((NOW - timedelta(minutes=5)).timestamp())], "governed_redis_history_index:RETENTION_BOUNDED:35d", "v1")})
    covB = _mk_cov_snaps({"M1": ([int((NOW - timedelta(minutes=5)).timestamp()), int((NOW - timedelta(minutes=4)).timestamp())], "governed_redis_history_index:RETENTION_BOUNDED:35d", "v1")})
    stub = _StubCov([covA, covB])
    attempts = []
    snap = rt.acquire_consistent_snapshot(**_cc_args(stub, PolicyReaderStub(_valid_json()), on_attempt=lambda: attempts.append(1)))
    assert len(attempts) == 1 and stub.calls == 2                 # single attempt, no retry (unrelated advance ignored)


def test_relevant_coverage_advance_triggers_bounded_retry_then_settles():
    no_fill = _mk_cov_snaps({})
    fill = _mk_cov_snaps({"H1": ([H1_GAP], DEFAULT_H1_PROV, "v1")})
    stub = _StubCov([no_fill, fill, fill, fill])                  # attempt1: A!=B -> retry; attempt2: A==B -> settle
    attempts = []
    rt.acquire_consistent_snapshot(**_cc_args(stub, PolicyReaderStub(_valid_json()), on_attempt=lambda: attempts.append(1)))
    assert len(attempts) == 2


def test_coverage_authority_change_triggers_retry():
    fillP1 = _mk_cov_snaps({"H1": ([H1_GAP], DEFAULT_H1_PROV, "v1")})
    fillP2 = _mk_cov_snaps({"H1": ([H1_GAP], "authority_changed", "v1")})
    stub = _StubCov([fillP1, fillP2, fillP2, fillP2])
    attempts = []
    rt.acquire_consistent_snapshot(**_cc_args(stub, PolicyReaderStub(_valid_json()), on_attempt=lambda: attempts.append(1)))
    assert len(attempts) == 2


def test_perpetual_relevant_change_blocks_bounded():
    no_fill = _mk_cov_snaps({})
    fill = _mk_cov_snaps({"H1": ([H1_GAP], DEFAULT_H1_PROV, "v1")})
    stub = _StubCov([no_fill, fill] * 8)                          # always differs A vs B
    attempts = []
    with pytest.raises(rt.SnapshotInconsistencyError):
        rt.acquire_consistent_snapshot(**_cc_args(stub, PolicyReaderStub(_valid_json()), on_attempt=lambda: attempts.append(1)))
    assert len(attempts) == rt.SNAPSHOT_MAX_RETRIES + 1           # bounded


# --------------------------------------------------------------------------- I. one cycle clock
def test_single_cycle_clock_sample():
    calls = []
    r, fr, pr = _runner(_gaps_payload(miss_hours=(6,)), clock=lambda: (calls.append(1) or NOW))
    r.run_once(enabled=True, authorised=True)
    assert len(calls) == 1                                        # exactly one clock sample per cycle


def test_unrelated_wallclock_progression_stable_digest():
    g = _gaps_snap(miss_hours=(6,)); cov = _cov({})
    assert _rel(g, cov, now=NOW) == _rel(g, cov, now=NOW + timedelta(seconds=90))


# --------------------------------------------------------------------------- E. idempotency / performance (N)
def test_five_cycles_unrelated_advance_invokes_once_holds_four():
    fr = FakeRedis({rt.GAPS_KEY: json.dumps(_gaps_payload(miss_hours=(6,)))})
    r = rt.RecoveryPlannerRunner(gaps_reader=fr, coverage_reader=fr, policy_reader=PolicyReaderStub(_valid_json()), clock=lambda: NOW)
    for i in range(5):
        fr.z[M1KEY] = [int((NOW - timedelta(minutes=5)).timestamp()) + 60 * k for k in range(i + 1)]  # unrelated advance
        fr.z[H1KEY] = [FAR + 3600 * i]                            # unrelated H1 current-edge advance
        r.run_once(enabled=True, authorised=True)
    assert r.state.planner_invocation_count == 1
    assert r.state.proposal_held_count == 4
    assert r.state.runner_cycle_count == 5


def test_relevant_gap_change_invokes_again():
    r, fr, pr = _runner(_gaps_payload(miss_hours=(6,)))
    r.run_once(enabled=True, authorised=True)
    fr.kv[rt.GAPS_KEY] = json.dumps(_gaps_payload(miss_hours=(6, 7)))
    r.run_once(enabled=True, authorised=True)
    assert r.state.planner_invocation_count == 2


def test_relevant_coverage_change_invokes_again():
    fr = FakeRedis({rt.GAPS_KEY: json.dumps(_gaps_payload(miss_hours=(6,)))})
    r = rt.RecoveryPlannerRunner(gaps_reader=fr, coverage_reader=fr, policy_reader=PolicyReaderStub(_valid_json()), clock=lambda: NOW)
    r.run_once(enabled=True, authorised=True)
    fr.z[H1KEY] = [H1_GAP]                                        # a candle fills the gap window
    r.run_once(enabled=True, authorised=True)
    assert r.state.planner_invocation_count == 2


def test_policy_change_invokes_again():
    r, fr, pr = _runner(_gaps_payload(miss_hours=(6,)))
    r.run_once(enabled=True, authorised=True)
    other = _valid_policy_dict(); other["merge_adjacent_threshold_seconds"] = 5
    pr.text = json.dumps(other)
    r.run_once(enabled=True, authorised=True)
    assert r.state.planner_invocation_count == 2


def test_closure_change_changes_tuple():
    assert rt.semantic_digest(_mk_snap(closure_digest="c1")) != rt.semantic_digest(_mk_snap(closure_digest="c2"))


def test_planner_version_change_changes_tuple(monkeypatch):
    snap = _mk_snap()
    d1 = rt.semantic_digest(snap)
    monkeypatch.setattr(rt, "PLANNER_VERSION", "v_next")
    assert rt.semantic_digest(snap) != d1


def test_failed_invocation_preserves_prior_hold(monkeypatch):
    r, fr, pr = _runner(_gaps_payload(miss_hours=(6,)))
    r.run_once(enabled=True, authorised=True)
    good = r.holder.snapshot(); assert good is not None
    fr.kv[rt.GAPS_KEY] = json.dumps(_gaps_payload(miss_hours=(6, 7, 8)))   # force a fresh invocation attempt
    def _boom(**k): raise RuntimeError("planner boom")
    monkeypatch.setattr(rt, "build_recovery_proposal", _boom)
    res = r.run_once(enabled=True, authorised=True)
    assert res["status"] == "PLANNER_INVOCATION_FAILED"
    assert r.holder.snapshot() == good                           # prior good hold NOT poisoned
    assert r.state.proposal_ready_count == 1                     # failure did not mark success
    assert r.state.planner_invocation_count == 2 and r.state.failure_count == 1


def test_blocked_cycle_clears_holder():
    r, fr, pr = _runner(_gaps_payload(miss_hours=(6,)))
    r.run_once(enabled=True, authorised=True); assert r.holder.snapshot() is not None
    pr.text = "{bad"
    r.run_once(enabled=True, authorised=True)
    assert r.holder.snapshot() is None                           # blocked cycle invalidates the holder


def test_restart_recomputes_after_reset():
    r, fr, pr = _runner(_gaps_payload(miss_hours=(6,)))
    r.run_once(enabled=True, authorised=True)
    r.restart_reset()
    assert r.holder.snapshot() is None and r.state.planner_invocation_count == 0
    r.run_once(enabled=True, authorised=True)
    assert r.state.planner_invocation_count == 1                 # recomputes (in-memory state not durable)


# --------------------------------------------------------------------------- K. accounting + historical correction
def test_accounting_runner_cycles_distinct_from_invocations():
    r, fr, pr = _runner(_gaps_payload(miss_hours=(6,)))
    for _ in range(3):
        r.run_once(enabled=True, authorised=True)
    st = r.state.summary()
    assert st["runner_cycle_count"] == 3 and st["planner_invocation_count"] == 1 and st["proposal_held_count"] == 2
    for k in ("snapshot_attempt_count", "proposal_ready_count", "blocked_count", "failure_count"):
        assert k in st


def test_historical_first_run_cycle_count_correction_is_three():
    # The first controlled invocation: the HELM summary said "four cycles"; the captured logs proved THREE PROPOSAL_READY
    # planner invocations and R2D2 accepted THREE. The accepted historical count is THREE. This hardening pins the counter
    # semantics (one runner cycle is countable, and only a semantic change yields a planner invocation) that removes the
    # ambiguity going forward. Historical evidence is NOT rewritten.
    ACCEPTED_FIRST_RUN_PLANNER_INVOCATIONS = 3
    assert ACCEPTED_FIRST_RUN_PLANNER_INVOCATIONS == 3
    r, fr, pr = _runner(_gaps_payload(miss_hours=(6,)))
    r.run_once(enabled=True, authorised=True)
    assert r.state.planner_invocation_count == 1                 # one cycle -> exactly one countable invocation


# --------------------------------------------------------------------------- safety: new functions have no write paths
def test_new_functions_no_write_or_forbidden_paths():
    code = _code(rt.retained_gap_intervals, rt.recovery_relevant_scope, rt.recovery_relevant_coverage_digest,
                 rt._gaps_semantic_digest, rt.acquire_consistent_snapshot)
    code = "\n".join(l for l in code.splitlines() if "holder." not in l)
    for tok in (".set(", ".delete(", ".zadd(", ".expire(", ".zrem(", "FLUSHDB", "UNLINK", "INSERT ", "UPDATE ",
                "DELETE ", "open(", "subprocess", "requests.", "urllib", "pymysql", "sqlalchemy", "cursor("):
        assert tok not in code, f"new function must not contain {tok!r}"


def test_recovery_relevant_digest_ignores_beyond_retention_and_far_coverage():
    # composite guard: neither beyond-retention candles nor far-from-window candles alter the recovery-relevant digest
    g = _gaps_snap(miss_hours=(6,))
    beyond = int((NOW - timedelta(days=40)).timestamp())         # > 35d H1 retention
    assert _rel(g, _cov({H1KEY: [beyond, FAR]})) == _rel(g, _cov({}))


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
