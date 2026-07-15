"""Tests for the pure recovery-proposal publication eligibility validator.
WO-HELM-HERMES-PH2-RECOVERY-PROPOSAL-PUBLICATION-ELIGIBILITY-VALIDATOR-0001. No Redis/SQL/network/Docker/live-HERMES.
"""
import ast
import copy
import dataclasses
import datetime as dt
import inspect
import pathlib
import threading

import pytest

import utils.hermes_proposal_validator_v1 as E

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 7, 15, 10, 31, 18, 0, tzinfo=UTC)
DIG = "a" * 64
DIG2 = "b" * 64
PID = "prop_" + "5" * 24


def _cand(**over):
    base = dict(
        proposal_id=PID, proposal_generation=2, instrument="XAU_USD", planner_status="READY", planner_version="v1",
        policy_version="1", policy_digest=DIG, gaps_contract_version="v1", gaps_semantic_digest=DIG,
        coverage_contract_version="v1", coverage_semantic_digest=DIG, closure_model_version="1", closure_digest=DIG,
        snapshot_consistency_status="CONSISTENT", generated_at_utc=NOW, source_as_of_utc=NOW,
        segment_count=35, estimated_request_units=81, payload_bytes=1200,
        scoped_intervals=(E.GapInterval("H1", dt.datetime(2026, 7, 14, 6, tzinfo=UTC), dt.datetime(2026, 7, 14, 7, tzinfo=UTC)),))
    base.update(over)
    return E.CandidateProposal(**base)


def _ctx(**over):
    base = dict(
        publisher_enabled=True, publisher_authorised=True, current_holder_proposal_id=PID, current_generation=1,
        latest_published_generation=1, current_policy_version="1", current_policy_digest=DIG,
        gaps_present=True, current_gaps_version="v1", gaps_generated_at_utc=NOW, current_gaps_digest=DIG,
        coverage_present=True, current_coverage_version="v1", coverage_snapshot_at_utc=NOW, current_coverage_digest=DIG,
        closure_complete=True, current_closure_model_version="1", current_closure_digest=DIG,
        exceptional_closure_status="NONE_IN_SCOPE")
    base.update(over)
    return E.ValidationContext(**base)


CFG = E.PublicationConfig()


def _run(cand=None, ctx=None, cfg=CFG, now=NOW):
    return E.validate_publication_eligibility(cand or _cand(), ctx or _ctx(), cfg, now)


# --------------------------------------------------------------------------- reconciliation
def test_exactly_33_governed_codes():
    assert len(E.ALL_PUB_CODES) == 33
    assert set(E.CODE_ORDER) == E.ALL_PUB_CODES and len(E.CODE_ORDER) == 33


def test_design_catalogue_matches_implementation():
    cat = pathlib.Path("docs/design/recovery_proposal_publication/fault_code_catalogue.md")
    import re
    design = set(re.findall(r"PUB_[A-Z_]+", cat.read_text()))
    assert design == E.ALL_PUB_CODES, f"delta: {design ^ E.ALL_PUB_CODES}"


# --------------------------------------------------------------------------- positive
def test_eligible_ready():
    d = _run()
    assert d.eligible and d.decision_code == E.REC_ELIGIBLE and d.primary_refusal_code is None
    assert d.expires_at_utc == NOW + dt.timedelta(seconds=CFG.proposal_ttl_seconds)
    assert d.execution_authority is False and d.refusal_codes == ()


def test_eligible_held_current_revalidated_fresh():
    d = _run(cand=_cand(planner_status="HELD_CURRENT"),
             ctx=_ctx(last_validated_at_utc=NOW - dt.timedelta(seconds=60)))
    assert d.eligible, d.refusal_codes


def test_semantic_digest_stable_with_fresh_timestamps_eligible():
    # digests unchanged, gaps/coverage fresh but generated a moment ago -> still eligible
    d = _run(ctx=_ctx(gaps_generated_at_utc=NOW - dt.timedelta(seconds=30),
                      coverage_snapshot_at_utc=NOW - dt.timedelta(seconds=120)))
    assert d.eligible


# --------------------------------------------------------------------------- §11 freshness NOT waived by sameness
def test_stale_gaps_refused_even_when_digest_matches():
    d = _run(ctx=_ctx(gaps_generated_at_utc=NOW - dt.timedelta(seconds=121)))  # >120 max, digest still equal
    assert not d.eligible and E.PUB_GAPS_STALE in d.refusal_codes


def test_held_current_stale_by_missed_revalidation():
    d = _run(cand=_cand(planner_status="HELD_CURRENT"),
             ctx=_ctx(last_validated_at_utc=NOW - dt.timedelta(seconds=121)))
    assert not d.eligible and E.PUB_PROPOSAL_STALE in d.refusal_codes


# --------------------------------------------------------------------------- boundaries (§13)
def test_gaps_age_exactly_max_is_fresh():
    assert _run(ctx=_ctx(gaps_generated_at_utc=NOW - dt.timedelta(seconds=120))).eligible


def test_gaps_age_one_microsecond_over_is_stale():
    d = _run(ctx=_ctx(gaps_generated_at_utc=NOW - dt.timedelta(seconds=120, microseconds=1)))
    assert not d.eligible and E.PUB_GAPS_STALE in d.refusal_codes


def test_expiry_exactly_now_is_expired():
    old = NOW - dt.timedelta(seconds=60)
    d = _run(cand=_cand(generated_at_utc=old, source_as_of_utc=old, expires_at_utc=NOW))
    assert not d.eligible and E.PUB_PROPOSAL_EXPIRED in d.refusal_codes


def test_expiry_one_microsecond_future_is_not_expired():
    assert _run(cand=_cand(expires_at_utc=NOW + dt.timedelta(microseconds=1))).eligible


# --------------------------------------------------------------------------- every governed code reachable
CASES = {
    E.PUB_UNSUPPORTED_CONTRACT_VERSION: dict(cfg=E.PublicationConfig(supported_gaps_versions=("v9",))),
    E.PUB_SCHEMA_VALIDATION_FAILED: dict(ctx=dict(schema_valid=False)),
    E.PUB_GATES_DISABLED: dict(ctx=dict(publisher_enabled=False)),
    E.PUB_GATE_MISMATCH: dict(ctx=dict(publisher_enabled=True, publisher_authorised=False)),
    E.PUB_NON_CANONICAL_INSTRUMENT: dict(cand=dict(instrument="XAUUSD")),
    E.PUB_NO_CURRENT_PROPOSAL: dict(ctx=dict(current_holder_proposal_id=None)),
    E.PUB_PROPOSAL_STATUS_INELIGIBLE: dict(cand=dict(planner_status="WEIRD")),
    E.PUB_BLOCKED: dict(cand=dict(planner_status="FAILED")),
    E.PUB_REVOKED: dict(cand=dict(revoked=True)),
    E.PUB_SUPERSEDED: dict(ctx=dict(newer_proposal_exists=True)),
    E.PUB_PROPOSAL_EXPIRED: dict(cand=dict(generated_at_utc=NOW - dt.timedelta(seconds=60),
                                           source_as_of_utc=NOW - dt.timedelta(seconds=60),
                                           expires_at_utc=NOW - dt.timedelta(seconds=1))),
    E.PUB_PROPOSAL_STALE: dict(cand=dict(planner_status="HELD_CURRENT"), ctx=dict(last_validated_at_utc=NOW - dt.timedelta(seconds=200))),
    E.PUB_POLICY_ABSENT: dict(ctx=dict(policy_present=False)),
    E.PUB_POLICY_INVALID: dict(ctx=dict(policy_schema_valid=False)),
    E.PUB_POLICY_CHANGED: dict(ctx=dict(current_policy_digest=DIG2)),
    E.PUB_UNSUPPORTED_PLANNER_VERSION: dict(cand=dict(planner_version="v9")),
    E.PUB_GAPS_MISSING: dict(ctx=dict(gaps_present=False)),
    E.PUB_GAPS_STALE: dict(ctx=dict(gaps_generated_at_utc=NOW - dt.timedelta(seconds=300))),
    E.PUB_GAPS_DIGEST_MISMATCH: dict(ctx=dict(current_gaps_digest=DIG2)),
    E.PUB_COVERAGE_MISSING: dict(ctx=dict(coverage_present=False)),
    E.PUB_COVERAGE_STALE: dict(ctx=dict(coverage_snapshot_at_utc=NOW - dt.timedelta(seconds=1000))),
    E.PUB_COVERAGE_DIGEST_MISMATCH: dict(ctx=dict(current_coverage_digest=DIG2)),
    E.PUB_CLOSURE_INCOMPLETE: dict(ctx=dict(closure_complete=False)),
    E.PUB_UNRESOLVED_EXCEPTIONAL_CLOSURE: dict(ctx=dict(exceptional_closure_status="UNRESOLVED")),
    E.PUB_INCONSISTENT_SNAPSHOT: dict(ctx=dict(snapshot_consistent=False)),
    E.PUB_OUT_OF_RETENTION: dict(ctx=dict(within_retention=False)),
    E.PUB_UNCLASSIFIED_PRESENT: dict(cand=dict(unclassified_count=1)),
    E.PUB_GAP_INVARIANT_VIOLATION: dict(cand=dict(scoped_intervals=(E.GapInterval("H1", dt.datetime(2026, 7, 14, 6, tzinfo=UTC), dt.datetime(2026, 7, 14, 6, 30, tzinfo=UTC)),))),
    E.PUB_PAYLOAD_TOO_LARGE: dict(cand=dict(payload_bytes=99999)),
    E.PUB_DUPLICATE_PUBLISHER: dict(ctx=dict(duplicate_writer_detected=True)),
    E.PUB_STALE_WRITER: dict(cand=dict(proposal_generation=1)),
    E.PUB_REDIS_UNAVAILABLE: dict(ctx=dict(redis_available=False)),
    E.PUB_ATOMIC_PUBLICATION_FAILED: dict(ctx=dict(atomic_precondition_ok=False)),
}


@pytest.mark.parametrize("code", sorted(CASES))
def test_each_governed_code_reachable(code):
    spec = CASES[code]
    cand = _cand(**spec.get("cand", {}))
    ctx = _ctx(**spec.get("ctx", {}))
    cfg = spec.get("cfg", CFG)
    d = E.validate_publication_eligibility(cand, ctx, cfg, NOW)
    assert not d.eligible, f"{code} case should be ineligible"
    assert code in d.refusal_codes, f"{code} not raised; got {d.refusal_codes}"


def test_all_33_codes_covered_by_cases():
    assert set(CASES) == E.ALL_PUB_CODES


# --------------------------------------------------------------------------- ordering / determinism (§10, §22)
def test_refusal_ordering_deterministic_and_primary_is_lowest_rank():
    # break gates AND policy AND gaps -> primary must be the gate code (earlier rank)
    d = _run(ctx=_ctx(publisher_enabled=False, policy_present=False, gaps_present=False))
    assert d.primary_refusal_code == E.PUB_GATES_DISABLED
    idx = [E._ORDER_INDEX[c] for c in d.refusal_codes]
    assert idx == sorted(idx)
    # repeated evaluation identical
    d2 = _run(ctx=_ctx(publisher_enabled=False, policy_present=False, gaps_present=False))
    assert d.refusal_codes == d2.refusal_codes and d.primary_refusal_code == d2.primary_refusal_code


def test_identical_inputs_identical_output():
    a, b = _run(), _run()
    assert a == b


# --------------------------------------------------------------------------- adversarial / property (§22)
def test_adding_a_failure_never_makes_eligible():
    base = _run()
    assert base.eligible
    worse = _run(ctx=_ctx(gaps_present=False))
    assert not worse.eligible


def test_advancing_now_cannot_revive_expired():
    cand = _cand(expires_at_utc=NOW + dt.timedelta(seconds=10))
    assert _run(cand=cand, now=NOW).eligible
    later = _run(cand=cand, ctx=_ctx(gaps_generated_at_utc=NOW + dt.timedelta(seconds=20),
                                     coverage_snapshot_at_utc=NOW + dt.timedelta(seconds=20)),
                 now=NOW + dt.timedelta(seconds=20))
    assert not later.eligible and E.PUB_PROPOSAL_EXPIRED in later.refusal_codes


def test_reducing_freshness_cannot_improve_eligibility():
    for extra in (0, 200, 5000):
        d = _run(ctx=_ctx(gaps_generated_at_utc=NOW - dt.timedelta(seconds=extra)))
        if extra > CFG.max_gaps_age_seconds:
            assert not d.eligible


def test_changing_governed_digest_always_refuses():
    for fld, ctxkey in [("policy_digest", None), ("gaps_semantic_digest", "current_gaps_digest"),
                        ("coverage_semantic_digest", "current_coverage_digest")]:
        d = _run(cand=_cand(**{fld: DIG2}))  # candidate digest differs from context's DIG
        assert not d.eligible


def test_xauusd_always_refuses_property():
    for gen in (2, 3, 10):
        d = _run(cand=_cand(instrument="XAUUSD", proposal_generation=gen))
        assert not d.eligible and E.PUB_NON_CANONICAL_INSTRUMENT in d.refusal_codes


@pytest.mark.parametrize("flag", ["execution_authorised", "execution_started", "executor_bound", "consumer_live",
                                  "backfill_executed", "repair_executed"])
def test_any_unsafe_flag_true_always_refuses(flag):
    d = _run(cand=_cand(**{flag: True}))
    assert not d.eligible and E.PUB_SCHEMA_VALIDATION_FAILED in d.refusal_codes


def test_publication_only_false_refuses():
    d = _run(cand=_cand(publication_only=False))
    assert not d.eligible and E.PUB_SCHEMA_VALIDATION_FAILED in d.refusal_codes


def test_lower_generation_never_eligible():
    for g in (1, 0 + 1):
        d = _run(cand=_cand(proposal_generation=g))
        assert not d.eligible and E.PUB_STALE_WRITER in d.refusal_codes


def test_equal_generation_to_current_refused():
    d = _run(cand=_cand(proposal_generation=1), ctx=_ctx(current_generation=1))
    assert not d.eligible and E.PUB_STALE_WRITER in d.refusal_codes


# --------------------------------------------------------------------------- neutralisation recommendation (§7)
def test_drift_on_current_holder_recommends_neutralise():
    d = _run(ctx=_ctx(current_policy_digest=DIG2))  # policy changed under the current holder
    assert d.decision_code == E.REC_REFUSE_AND_NEUTRALISE and d.neutralise_prior_current is True
    assert d.neutralisation_reason == E.PUB_POLICY_CHANGED


def test_no_current_key_recommends_no_current_key():
    d = _run(ctx=_ctx(current_holder_proposal_id=None))
    assert d.decision_code == E.REC_REFUSE_NO_CURRENT_KEY and d.neutralise_prior_current is False


def test_retryable_classification():
    assert _run(ctx=_ctx(redis_available=False)).retryable is True
    assert _run(ctx=_ctx(policy_present=False)).retryable is False


# --------------------------------------------------------------------------- §12 time rules
def test_naive_datetime_fails_construction():
    with pytest.raises(E.ModelConstructionError):
        _cand(generated_at_utc=dt.datetime(2026, 7, 15, 10, 31, 18))


def test_non_utc_offset_fails_construction():
    tz = dt.timezone(dt.timedelta(hours=2))
    with pytest.raises(E.ModelConstructionError):
        _cand(generated_at_utc=dt.datetime(2026, 7, 15, 12, 31, 18, tzinfo=tz))


def test_expires_before_generated_fails_construction():
    with pytest.raises(E.ModelConstructionError):
        _cand(expires_at_utc=NOW - dt.timedelta(seconds=1))


def test_future_source_beyond_skew_is_inconsistent():
    d = _run(cand=_cand(source_as_of_utc=NOW + dt.timedelta(seconds=10)))
    assert not d.eligible and E.PUB_INCONSISTENT_SNAPSHOT in d.refusal_codes


def test_validator_requires_utc_now():
    with pytest.raises(E.ModelConstructionError):
        E.validate_publication_eligibility(_cand(), _ctx(), CFG, dt.datetime(2026, 7, 15, 10, 31, 18))


# --------------------------------------------------------------------------- exceptional closure (§17)
@pytest.mark.parametrize("status,ok", [("NONE_IN_SCOPE", True), ("RESOLVED", True),
                                       ("UNRESOLVED", False), ("UNKNOWN", False), ("CONFLICTING", False)])
def test_exceptional_closure_matrix(status, ok):
    d = _run(ctx=_ctx(exceptional_closure_status=status))
    assert d.eligible == ok
    if not ok:
        assert E.PUB_UNRESOLVED_EXCEPTIONAL_CLOSURE in d.refusal_codes


def test_unknown_exceptional_status_fails_construction():
    with pytest.raises(E.ModelConstructionError):
        _ctx(exceptional_closure_status="MAYBE")


# --------------------------------------------------------------------------- gap invariant (§16)
def test_gap_invariant_holds_by_construction_intervals():
    assert _run().eligible


def test_gap_invariant_violation_detected():
    bad = (E.GapInterval("D1", dt.datetime(2026, 7, 14, tzinfo=UTC), dt.datetime(2026, 7, 14, 23, tzinfo=UTC)),)
    d = _run(cand=_cand(scoped_intervals=bad))
    assert not d.eligible and E.PUB_GAP_INVARIANT_VIOLATION in d.refusal_codes


def test_gap_invariant_none_status_fails_closed_without_intervals():
    d = _run(cand=_cand(scoped_intervals=()), ctx=_ctx(gap_invariant_ok=None))
    assert not d.eligible and E.PUB_GAP_INVARIANT_VIOLATION in d.refusal_codes


def test_gap_invariant_caller_true_without_intervals_ok():
    assert _run(cand=_cand(scoped_intervals=()), ctx=_ctx(gap_invariant_ok=True)).eligible


# --------------------------------------------------------------------------- immutability (§23) + side effects (§21)
def test_inputs_not_mutated():
    cand, ctx, cfg = _cand(), _ctx(), E.PublicationConfig()
    bc, bx, bcfg = copy.deepcopy(cand), copy.deepcopy(ctx), copy.deepcopy(cfg)
    E.validate_publication_eligibility(cand, ctx, cfg, NOW)
    assert cand == bc and ctx == bx and cfg == bcfg


def test_decision_is_frozen():
    d = _run()
    with pytest.raises(dataclasses.FrozenInstanceError):
        d.eligible = False  # type: ignore


def test_models_are_frozen():
    for obj in (_cand(), _ctx(), E.PublicationConfig(),
                E.GapInterval("M1", NOW, NOW + dt.timedelta(seconds=60))):
        with pytest.raises(dataclasses.FrozenInstanceError):
            obj.instrument = "x"  # type: ignore


def test_import_and_validate_create_no_threads():
    before = threading.active_count()
    _run()
    assert threading.active_count() == before


def test_module_has_no_singleton_or_io_import():
    src = inspect.getsource(E)
    tree = ast.parse(src)
    mods = {(n.module or "") for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    mods |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    forbidden = {"redis", "pymysql", "sqlalchemy", "psycopg2", "requests", "httpx", "socket", "subprocess", "os",
                 "http", "urllib", "threading", "asyncio", "pathlib"}
    assert not (mods & forbidden), f"pure validator must not import {mods & forbidden}"


def test_no_forbidden_tokens_in_executable_source():
    tree = ast.parse(inspect.getsource(E))
    class Strip(ast.NodeTransformer):
        def visit_Constant(self, n):
            return ast.copy_location(ast.Constant(""), n) if isinstance(n.value, str) else n
    code = ast.unparse(Strip().visit(tree)).lower()
    # Precise call/import patterns — NOT bare words (PUB_REDIS_UNAVAILABLE / redis_available are legitimate identifiers).
    for tok in ("import redis", "redis.redis", "redis.strictredis", "pymysql", "sqlalchemy", "psycopg",
                "requests.", "httpx.", "cursor(", "subprocess", "os.system", "os.popen", "os.environ", "getenv",
                "socket.socket", ".now(", "time.time(", "import threading", "threading.thread", "import asyncio",
                "open(", "recovery_executor", "recovery_planner_runtime", "recoverylibrary"):
        assert tok not in code, f"forbidden token in executable source: {tok!r}"


# --------------------------------------------------------------------------- §20 schema/fixture integration
import json  # noqa: E402
from jsonschema import Draft7Validator  # noqa: E402

_SCHEMA = pathlib.Path("schemas/recovery_proposal/recovery_proposal_publication.v1.schema.json")
_FIX = pathlib.Path("fixtures/recovery_proposal")


def _schema_validator():
    s = json.loads(_SCHEMA.read_text())
    Draft7Validator.check_schema(s)
    return Draft7Validator(s)


def test_merged_schema_still_validates_valid_and_rejects_invalid_fixtures():
    v = _schema_validator()
    for f in ("eligible.json", "stale.json", "revocation_supersession.json", "superseding_gen2.json"):
        assert v.is_valid(json.loads((_FIX / f).read_text())), f
    for f in sorted(_FIX.glob("invalid_*.json")):
        assert not v.is_valid(json.loads(f.read_text())), f.name


def _candidate_from_envelope(env):
    """Map a published-envelope fixture onto validator inputs (design alignment; the envelope is the published form)."""
    s = env["safety"]
    return _cand(
        proposal_id=env["proposal_id"], proposal_generation=env["proposal_generation"], instrument=env["instrument"],
        planner_version=env["planner_version"], policy_version=env["policy_version"], policy_digest=env["policy_digest"],
        gaps_semantic_digest=env["gaps_semantic_digest"], coverage_semantic_digest=env["coverage_semantic_digest"],
        closure_digest=env["closure_digest"], segment_count=env["summary"]["segment_count"],
        unclassified_count=env["summary"]["unclassified_count"],
        execution_authorised=s["execution_authorised"], execution_started=s["execution_started"],
        publication_only=s["publication_only"], executor_bound=s["executor_bound"], consumer_live=s["consumer_live"],
        backfill_executed=s["backfill_executed"], repair_executed=s["repair_executed"], scoped_intervals=())


def test_eligible_envelope_maps_to_eligible_decision():
    env = json.loads((_FIX / "eligible.json").read_text())
    cand = _candidate_from_envelope(env)              # proposal_generation 1 (first publication)
    ctx = _ctx(gap_invariant_ok=True, current_generation=0, latest_published_generation=0,
               current_holder_proposal_id=env["proposal_id"],
               current_policy_digest=env["policy_digest"], current_gaps_digest=env["gaps_semantic_digest"],
               current_coverage_digest=env["coverage_semantic_digest"], current_closure_digest=env["closure_digest"])
    d = E.validate_publication_eligibility(cand, ctx, CFG, NOW)
    assert d.eligible, d.refusal_codes


def test_schema_valid_does_not_imply_eligible():
    # 'stale.json' is schema-VALID (status STALE) but must not be publication-eligible; map its digest as drifted context
    env = json.loads((_FIX / "stale.json").read_text())
    cand = _candidate_from_envelope(env)
    ctx = _ctx(gap_invariant_ok=True, current_policy_digest="f" * 64)  # policy drift -> ineligible despite schema-valid
    assert not E.validate_publication_eligibility(cand, ctx, CFG, NOW).eligible


def test_schema_invalid_execution_flag_never_eligible():
    env = json.loads((_FIX / "invalid_execution_true.json").read_text())
    cand = _candidate_from_envelope(env)  # execution_authorised True
    d = E.validate_publication_eligibility(cand, _ctx(gap_invariant_ok=True), CFG, NOW)
    assert not d.eligible and E.PUB_SCHEMA_VALIDATION_FAILED in d.refusal_codes
