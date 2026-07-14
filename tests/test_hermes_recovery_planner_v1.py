"""HERMES PH2 recovery planner — deterministic DRY-RUN-ONLY planning tests (pure, no live I/O).
WO-HELM-HERMES-PH2-RECOVERY-PLANNER-DESIGN-0001.
"""
import inspect
import json
import re
from datetime import datetime, timedelta, timezone

import pytest

import utils.hermes_recovery_planner_v1 as rp

UTC = timezone.utc
NOW = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)          # Tuesday noon (fixed clock)


def iv(a, b):
    return rp.Interval(a, b)


def dt(y=2026, mo=7, d=14, h=0, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=UTC)


def _cost():
    return rp.CostModel(candles_per_request=100, request_units_per_call=1, cost_tokens_per_request=2,
                        fixed_overhead_units=1, timeframe_multipliers=(("D1", 2.0), ("H4", 1.5), ("H1", 1.0),
                                                                       ("M15", 1.0), ("M5", 1.0), ("M1", 1.0)))


def policy(**over):
    base = dict(contract_version="v1", instrument="XAU_USD",
                allowed_timeframes=("M1", "M5", "M15", "H1", "H4", "D1"),
                timeframe_priority=("D1", "H4", "H1", "M15", "M5", "M1"),
                priority_tiers=(("D1", 1), ("H4", 2), ("H1", 2), ("M15", 3), ("M5", 3), ("M1", 3)),
                max_segments=100, max_candles_per_segment=1000, max_candles_per_proposal=100000,
                max_lookback_seconds=140 * 86400, max_request_units=1_000_000,
                merge_adjacent_threshold_seconds=0, d1_history_floor_seconds=120 * 86400,
                regular_weekend_closure_utc=(4, 22, 6, 22), accepted_closure_authorities=("GOVERNED",),
                uncertain_to_unclassified=True, stale_gaps_max_age_seconds=3600, allow_partial=True, cost_model=_cost())
    base.update(over)
    return rp.RecoveryPlanningPolicy(**base)


def gaps(records, *, gen=None, d1_state="OK", d1_anchor=22, inst="XAU_USD", cv="v1"):
    return rp.GapSurfaceSnapshot(contract_version=cv, instrument=inst, source_key="hermes:gaps:XAU_USD:v1",
                                 source_generated_at_utc=gen or (NOW - timedelta(minutes=5)),
                                 overall_gap_state="GAPS_FOUND", gap_records=tuple(records),
                                 d1_boundary_state=d1_state, d1_anchor_hour_utc=d1_anchor)


def cov(tf, intervals, inst="XAU_USD"):
    return rp.ExistingCoverageSnapshot("v1", inst, tf, tuple(intervals), NOW, "local_redis_history")


def plan(records, *, coverage=(), closures=(), pol=None, now=NOW, **gk):
    return rp.build_recovery_proposal(gaps=gaps(records, **gk), coverage=tuple(coverage), closures=tuple(closures),
                                      policy=pol or policy(), now_utc=now)


# --------------------------------------------------------------------------- canonical / valid
def test_valid_canonical_proposal():
    wl = plan([rp.GapRecord("H1", (iv(dt(h=6), dt(h=11)),), "GAPS_FOUND")])
    assert wl.overall_plan_status == "PROPOSAL_READY"
    assert wl.target_instrument == "XAU_USD" and wl.planning_mode == "DRY_RUN_ONLY"
    assert (wl.execution_enabled, wl.backfill_executed, wl.repair_executed, wl.consumer_live) == (False, False, False, False)
    assert rp.validate_proposed_workload(json.loads(wl.to_json())) is True


def test_xauusd_rejected_in_inputs():
    with pytest.raises(rp.PlannerError, match="XAUUSD_REJECTED"):
        gaps([rp.GapRecord("H1", (iv(dt(h=6), dt(h=7)),), "GAPS_FOUND")], inst="XAUUSD")
    with pytest.raises(rp.PlannerError, match="XAUUSD_REJECTED"):
        cov("H1", (iv(dt(h=6), dt(h=7)),), inst="XAUUSD")
    with pytest.raises(rp.PlannerError, match="INVALID_INSTRUMENT|XAUUSD"):
        policy(instrument="XAUUSD")


def test_xauusd_rejected_in_output_validation():
    wl = plan([rp.GapRecord("H1", (iv(dt(h=6), dt(h=7)),), "GAPS_FOUND")])
    d = json.loads(wl.to_json())
    d["prioritized_segments_list"][0]["required_source"] = "candles:XAUUSD:H1"
    with pytest.raises(rp.PlannerError, match="XAUUSD_REJECTED"):
        rp.validate_proposed_workload(d)


# --------------------------------------------------------------------------- priority tiers + sorting
def test_priority_tiers_and_deterministic_sort():
    recs = [rp.GapRecord("M1", (iv(dt(h=9), dt(h=9, mi=5)),), "GAPS_FOUND"),
            rp.GapRecord("D1", (iv(dt(d=7, h=22), dt(d=8, h=22)),), "GAPS_FOUND"),   # Tue->Wed D1 (open market)
            rp.GapRecord("H4", (iv(dt(h=2), dt(h=6)),), "GAPS_FOUND")]
    wl = plan(recs)
    order = [(s.timeframe, s.priority_tier) for s in wl.prioritized_segments_list]
    assert order[0][0] == "D1" and order[0][1] == 1          # D1 tier 1 first
    assert order[1][0] == "H4" and order[1][1] == 2          # H4 tier 2
    assert order[-1][0] == "M1" and order[-1][1] == 3        # M1 tier 3 last
    ranks = [s.priority_rank for s in wl.prioritized_segments_list]
    assert ranks == sorted(ranks)                            # non-decreasing rank


def test_deterministic_proposal_id_and_full_output():
    recs = [rp.GapRecord("H1", (iv(dt(h=6), dt(h=8)),), "GAPS_FOUND")]
    a, b = plan(recs), plan(recs)
    assert a.proposal_id == b.proposal_id
    assert a.to_json() == b.to_json()                       # identical inputs+policy+clock -> identical output


def test_generated_at_is_the_only_volatile_field():
    recs = [rp.GapRecord("H1", (iv(dt(h=6), dt(h=8)),), "GAPS_FOUND")]
    a = plan(recs, now=NOW)
    b = plan(recs, now=NOW + timedelta(seconds=1))
    da, db = json.loads(a.to_json()), json.loads(b.to_json())
    assert da["generated_at_utc"] != db["generated_at_utc"]
    da.pop("generated_at_utc"); db.pop("generated_at_utc")
    assert da == db and a.proposal_id == b.proposal_id      # proposal_id excludes volatile clock


# --------------------------------------------------------------------------- D1 doctrine
def test_d1_history_floor_precedence_and_beyond_floor_excluded():
    p = policy(d1_history_floor_seconds=30 * 86400)
    within = iv(dt(d=1, h=22), dt(d=2, h=22))               # ~within 30d of NOW(2026-07-14)
    beyond = iv(dt(mo=5, d=1, h=22), dt(mo=5, d=2, h=22))   # far older than 30d floor
    wl = plan([rp.GapRecord("D1", (within, beyond), "GAPS_FOUND")], pol=p)
    tfs = [s.timeframe for s in wl.prioritized_segments_list]
    assert "D1" in tfs                                       # within-floor D1 is a candidate
    # beyond-floor start must not appear as an accepted segment
    assert all(s.interval.start_utc >= dt(mo=6, d=14, h=0) for s in wl.prioritized_segments_list if s.timeframe == "D1")


def test_d1_2200_accepted_0000_rejected():
    ok = plan([rp.GapRecord("D1", (iv(dt(d=12, h=22), dt(d=13, h=22)),), "GAPS_FOUND")], d1_anchor=22)
    assert ok.overall_plan_status in ("PROPOSAL_READY", "PARTIAL_BOUNDED_PROPOSAL")
    bad = plan([rp.GapRecord("D1", (iv(dt(d=12, h=0), dt(d=13, h=0)),), "GAPS_FOUND")], d1_anchor=0)
    assert bad.overall_plan_status == "BLOCKED_D1_BOUNDARY"
    assert bad.prioritized_segments_list == ()              # no executable-looking segments
    assert bad.faults[0]["code"] == "D1_BOUNDARY_FAILURE"


def test_d1_boundary_defect_blocks_not_segmentised():
    bad = plan([rp.GapRecord("D1", (iv(dt(d=12, h=22), dt(d=13, h=22)),), "GAPS_FOUND")], d1_state="LATEST_HISTORY_INCONSISTENT")
    assert bad.overall_plan_status == "BLOCKED_D1_BOUNDARY" and bad.prioritized_segments_list == ()


# --------------------------------------------------------------------------- market closure doctrine
def test_regular_weekend_excluded_as_intentionally_unavailable():
    # gap spanning Fri 20:00 -> Mon 02:00 across the weekend closure (Fri22->Sun22)
    wl = plan([rp.GapRecord("H1", (iv(dt(d=10, h=20), dt(d=13, h=2)),), "GAPS_FOUND")])
    assert wl.intentionally_unavailable_segments, "weekend must be INTENTIONALLY_UNAVAILABLE"
    for u in wl.intentionally_unavailable_segments:
        assert u.segment_class == "INTENTIONALLY_UNAVAILABLE"
    # no accepted candidate may fall inside Fri22->Sun22
    for s in wl.prioritized_segments_list:
        assert not (s.interval.start_utc >= dt(d=10, h=22) and s.interval.end_utc <= dt(d=12, h=22))


def test_governed_holiday_excluded():
    hol = rp.MarketClosureSnapshot("v1", "XAU_USD", iv(dt(d=8, h=0), dt(d=9, h=0)), "HOLIDAY", "gov_calendar",
                                   "GOVERNED", NOW)
    wl = plan([rp.GapRecord("H1", (iv(dt(d=7, h=20), dt(d=9, h=4)),), "GAPS_FOUND")], closures=(hol,))
    assert any(u.interval.overlaps(iv(dt(d=8, h=0), dt(d=9, h=0))) for u in wl.intentionally_unavailable_segments)


def test_unverified_holiday_not_silently_excluded():
    unv = rp.MarketClosureSnapshot("v1", "XAU_USD", iv(dt(d=8, h=0), dt(d=9, h=0)), "HOLIDAY", "rumour",
                                   "UNVERIFIED", NOW)
    wl = plan([rp.GapRecord("H1", (iv(dt(d=7, h=20), dt(d=9, h=4)),), "GAPS_FOUND")], closures=(unv,))
    # not excluded as intentionally unavailable; surfaced honestly as unclassified + warning
    assert not any(u.interval.overlaps(iv(dt(d=8, h=1), dt(d=8, h=23))) for u in wl.intentionally_unavailable_segments)
    assert wl.unclassified_segments and any("uncertain" in w or "not accepted" in w for w in wl.warnings)


# --------------------------------------------------------------------------- overlap clipping matrix
def _residual_intervals(wl):
    return sorted((s.interval.start_utc, s.interval.end_utc) for s in wl.prioritized_segments_list)


def test_clip_no_overlap():
    wl = plan([rp.GapRecord("H1", (iv(dt(h=6), dt(h=8)),), "GAPS_FOUND")],
              coverage=[cov("H1", (iv(dt(h=10), dt(h=11)),))])
    assert _residual_intervals(wl) == [(dt(h=6), dt(h=8))]


def test_clip_full_overlap():
    wl = plan([rp.GapRecord("H1", (iv(dt(h=6), dt(h=8)),), "GAPS_FOUND")],
              coverage=[cov("H1", (iv(dt(h=5), dt(h=9)),))])
    assert wl.prioritized_segments_list == () and wl.excluded_existing_coverage_segments


def test_clip_left_right_and_internal():
    wl = plan([rp.GapRecord("H1", (iv(dt(h=6), dt(h=12)),), "GAPS_FOUND")],
              coverage=[cov("H1", (iv(dt(h=5), dt(h=7)), iv(dt(h=9), dt(h=10)), iv(dt(h=11), dt(h=13))))])
    assert _residual_intervals(wl) == [(dt(h=7), dt(h=9)), (dt(h=10), dt(h=11))]


def test_clip_multiple_duplicate_unsorted_coverage():
    wl = plan([rp.GapRecord("H1", (iv(dt(h=6), dt(h=12)),), "GAPS_FOUND")],
              coverage=[cov("H1", (iv(dt(h=9), dt(h=10)), iv(dt(h=9), dt(h=10)), iv(dt(h=7), dt(h=8))))])
    assert _residual_intervals(wl) == [(dt(h=6), dt(h=7)), (dt(h=8), dt(h=9)), (dt(h=10), dt(h=12))]


def test_inputs_not_mutated():
    covs = [iv(dt(h=7), dt(h=8))]
    snap = cov("H1", covs)
    before = tuple(snap.covered_intervals)
    plan([rp.GapRecord("H1", (iv(dt(h=6), dt(h=12)),), "GAPS_FOUND")], coverage=[snap])
    assert snap.covered_intervals == before                 # frozen inputs unchanged


# --------------------------------------------------------------------------- adjacency / coalescing
def test_adjacent_residuals_merge_when_threshold_allows():
    p = policy(merge_adjacent_threshold_seconds=3600)        # allow 1h gap merge
    wl = plan([rp.GapRecord("H1", (iv(dt(h=6), dt(h=8)), iv(dt(h=9), dt(h=11))), "GAPS_FOUND")], pol=p)
    assert len(wl.prioritized_segments_list) == 1           # [6,8)+[9,11) merged (1h gap <= threshold)


def test_closure_between_prevents_merge():
    p = policy(merge_adjacent_threshold_seconds=7 * 86400)   # very permissive threshold
    # two gaps straddling the weekend; closure between them must block merge
    wl = plan([rp.GapRecord("H1", (iv(dt(d=10, h=20), dt(d=10, h=22)), iv(dt(d=12, h=22), dt(d=13, h=0))), "GAPS_FOUND")], pol=p)
    starts = {s.interval.start_utc for s in wl.prioritized_segments_list}
    assert len(wl.prioritized_segments_list) >= 2 and dt(d=10, h=20) in starts and dt(d=12, h=22) in starts


# --------------------------------------------------------------------------- bounds
def test_max_segment_candle_bound_splits():
    p = policy(max_candles_per_segment=2)
    wl = plan([rp.GapRecord("H1", (iv(dt(h=0), dt(h=6)),), "GAPS_FOUND")], pol=p)
    assert all(s.candle_count <= 2 for s in wl.prioritized_segments_list)
    assert len(wl.prioritized_segments_list) == 3           # 6 candles / 2 = 3 segments


def test_max_segments_bound_defers_partial():
    p = policy(max_segments=1, merge_adjacent_threshold_seconds=0)
    wl = plan([rp.GapRecord("H1", (iv(dt(h=0), dt(h=2)), iv(dt(h=4), dt(h=6))), "GAPS_FOUND")], pol=p)
    assert wl.overall_plan_status == "PARTIAL_BOUNDED_PROPOSAL"
    assert len(wl.prioritized_segments_list) == 1 and wl.deferred_segments
    assert all(s.segment_class == "DEFERRED_BOUND" for s in wl.deferred_segments)


def test_request_unit_bound_defers():
    cm = rp.CostModel(candles_per_request=1, request_units_per_call=1, cost_tokens_per_request=1, fixed_overhead_units=0,
                      timeframe_multipliers=(("H1", 1.0),))
    p = policy(max_request_units=2, cost_model=cm, merge_adjacent_threshold_seconds=0)
    wl = plan([rp.GapRecord("H1", (iv(dt(h=0), dt(h=5)),), "GAPS_FOUND")], pol=p)
    assert wl.estimated_request_units <= 2 and wl.overall_plan_status == "PARTIAL_BOUNDED_PROPOSAL" and wl.deferred_segments


def test_partial_disallowed_blocks():
    p = policy(max_segments=1, allow_partial=False, merge_adjacent_threshold_seconds=0)
    wl = plan([rp.GapRecord("H1", (iv(dt(h=0), dt(h=2)), iv(dt(h=4), dt(h=6))), "GAPS_FOUND")], pol=p)
    assert wl.overall_plan_status == "BLOCKED_POLICY" and wl.prioritized_segments_list == ()


# --------------------------------------------------------------------------- stale / invalid inputs
def test_stale_gaps_rejected():
    wl = plan([rp.GapRecord("H1", (iv(dt(h=6), dt(h=7)),), "GAPS_FOUND")], gen=NOW - timedelta(hours=2))
    assert wl.overall_plan_status == "BLOCKED_STALE_GAPS" and wl.prioritized_segments_list == ()


def test_naive_timestamp_rejected():
    with pytest.raises(rp.PlannerError, match="NAIVE_TIMESTAMP"):
        rp.Interval(datetime(2026, 7, 14, 6), dt(h=7))


def test_invalid_interval_rejected():
    with pytest.raises(rp.PlannerError, match="INVALID_INTERVAL"):
        rp.Interval(dt(h=8), dt(h=6))
    with pytest.raises(rp.PlannerError, match="INVALID_INTERVAL"):
        rp.Interval(dt(h=6), dt(h=6))


def test_unsupported_timeframe_rejected():
    with pytest.raises(rp.PlannerError, match="UNSUPPORTED_TIMEFRAME"):
        rp.GapRecord("H2", (iv(dt(h=6), dt(h=7)),), "GAPS_FOUND")


def test_source_policy_instrument_mismatch():
    # policy on canonical; gaps with a (valid canonical) instrument still passes; mismatch simulated via contract version
    wl = plan([rp.GapRecord("H1", (iv(dt(h=6), dt(h=7)),), "GAPS_FOUND")], cv="v2")
    assert wl.overall_plan_status == "BLOCKED_INVALID_INPUT" and wl.faults[0]["code"] == "INVALID_CONTRACT_VERSION"


def test_missing_gaps_payload():
    wl = plan([])
    assert wl.overall_plan_status == "BLOCKED_INVALID_INPUT" and wl.faults[0]["code"] == "MISSING_GAPS_PAYLOAD"


def test_no_recovery_required():
    # gap fully covered -> no candidates, no unavailable -> NO_RECOVERY_REQUIRED
    wl = plan([rp.GapRecord("H1", (iv(dt(h=6), dt(h=8)),), "GAPS_FOUND")],
              coverage=[cov("H1", (iv(dt(h=6), dt(h=8)),))])
    assert wl.overall_plan_status in ("NO_RECOVERY_REQUIRED", "PROPOSAL_READY")
    assert wl.prioritized_segments_list == ()


# --------------------------------------------------------------------------- cost model
def test_cost_model_deterministic():
    cm = rp.CostModel(candles_per_request=10, request_units_per_call=2, cost_tokens_per_request=5, fixed_overhead_units=3,
                      timeframe_multipliers=(("H1", 1.0),))
    p = policy(cost_model=cm, max_candles_per_segment=1000)
    wl = plan([rp.GapRecord("H1", (iv(dt(h=0), dt(h=5)),), "GAPS_FOUND")], pol=p)  # 5 candles -> 1 call
    # units = ceil(5/10)=1 * 2 * 1.0 + overhead 3 = 5 ; tokens = 1 * 5 = 5
    assert wl.estimated_request_units == 5 and wl.estimated_api_cost_tokens == 5


def test_invalid_cost_model_rejected():
    with pytest.raises(rp.PlannerError, match="INVALID_COST_MODEL"):
        rp.CostModel(candles_per_request=0, request_units_per_call=1, cost_tokens_per_request=1, fixed_overhead_units=0,
                     timeframe_multipliers=())


# --------------------------------------------------------------------------- gate truth table
def test_gate_truth_table():
    assert rp.evaluate_recovery_planner_gate(enabled=False, authorised=False)["state"] == "DISABLED"
    assert rp.evaluate_recovery_planner_gate(enabled=False, authorised=True)["state"] == "DISABLED"
    with pytest.raises(SystemExit) as e:
        rp.evaluate_recovery_planner_gate(enabled=True, authorised=False)
    assert e.value.code == 105
    g = rp.evaluate_recovery_planner_gate(enabled=True, authorised=True)
    assert g["state"] == "ENABLED_AUTHORISED_PLAN_ONLY" and g["planning_permitted"] is True and g["execution_permitted"] is False


def test_gate_from_env(monkeypatch):
    for e in (rp.ENABLED_ENV, rp.AUTHORISED_ENV):
        monkeypatch.delenv(e, raising=False)
    assert rp.evaluate_recovery_planner_gate_from_env()["state"] == "DISABLED"
    monkeypatch.setenv(rp.ENABLED_ENV, "true")
    with pytest.raises(SystemExit) as e:
        rp.evaluate_recovery_planner_gate_from_env()
    assert e.value.code == 105


def test_enabled_authorised_does_not_execute():
    # the gate grants permission only; it must not construct a planner, publish, or mutate anything
    g = rp.evaluate_recovery_planner_gate(enabled=True, authorised=True)
    assert g["execution_permitted"] is False
    src = inspect.getsource(rp.evaluate_recovery_planner_gate)
    src = re.sub(r'"""(?:.|\n)*?"""', "", src)              # strip docstring (negative declarations mention publish/execute)
    for tok in (".set(", ".delete(", "publish", "build_recovery_proposal", "backfill", "repair"):
        assert tok not in src


# --------------------------------------------------------------------------- serialisation
def test_serialisation_round_trip_and_validation():
    wl = plan([rp.GapRecord("D1", (iv(dt(d=12, h=22), dt(d=13, h=22)),), "GAPS_FOUND")])
    s = wl.to_json(indent=1)
    d = json.loads(s)
    assert rp.validate_proposed_workload(d) is True
    assert d["generated_at_utc"].endswith("Z")
    assert "NaN" not in s and "Infinity" not in s
    # no python-specific reprs
    assert "datetime.datetime" not in s and "(" not in d["prioritized_segments_list"][0].get("segment_id", "")


def test_ok_status_forbidden_in_validation():
    wl = plan([rp.GapRecord("H1", (iv(dt(h=6), dt(h=7)),), "GAPS_FOUND")])
    d = json.loads(wl.to_json())
    d["overall_plan_status"] = "OK"
    with pytest.raises(rp.PlannerError):
        rp.validate_proposed_workload(d)


# --------------------------------------------------------------------------- static: no prohibited I/O / cross-app / mutation
def test_no_prohibited_imports_or_paths():
    src = inspect.getsource(rp)
    code = re.sub(r'"""(?:.|\n)*?"""', "", src)
    code = "\n".join(l for l in code.splitlines() if not l.lstrip().startswith("#"))
    for tok in ("import redis", "redis.Redis", "pymysql", "sqlalchemy", "cursor(", "import requests", "requests.",
                "urllib", "http.client", "import socket", "subprocess", "os.system", "import docker", "systemd",
                "import cron", "oanda", "broker", "seed_backfill", "candle_d1_history_seed_backfill",
                "falcon", "ares", "helios", "market_map"):
        assert tok.lower() not in code.lower(), f"planner module must not reference {tok!r}"
    # no Redis/DB mutation verbs
    for tok in (".set(", ".delete(", ".zadd(", ".expire(", "FLUSHDB", "UNLINK", "INSERT ", "UPDATE ", "DELETE ", "DROP "):
        assert tok not in code, f"planner must not contain mutation token {tok!r}"
    # (canonical-only is proven by the dedicated alias test below)


def test_alias_constant_is_declaration_only():
    # every XAUUSD occurrence in the module is a DENY/REJECT context (deny constant, fault-code name, or reject message) —
    # never an accepted/constructed instrument value.
    src = inspect.getsource(rp)
    occurrences = [l.strip() for l in src.splitlines() if "XAUUSD" in l]
    assert occurrences, "expected the deny declaration to exist"
    allowed_markers = ("_ALIAS_DENY", "XAUUSD_REJECTED", "forbidden alias", "alias present", "in json.dumps")
    for o in occurrences:
        assert any(m in o for m in allowed_markers), f"XAUUSD in non-deny context: {o!r}"
    # never accepted as a value
    assert 'instrument="XAUUSD"' not in src and "== \"XAUUSD\"" not in src.replace("'", '"')


def test_no_mutable_default_args_and_no_local_time():
    # inspect SIGNATURES for mutable defaults (local dict/list initialisations are fine)
    for name, obj in inspect.getmembers(rp, inspect.isfunction):
        for pname, param in inspect.signature(obj).parameters.items():
            if param.default is not inspect._empty:
                assert not isinstance(param.default, (list, dict, set)), f"{name}({pname}) has mutable default"
    # no wall-clock: clock is injected (datetime.now/utcnow must not appear)
    src = inspect.getsource(rp)
    assert "datetime.now(" not in src and "utcnow(" not in src and ".now()" not in src
