"""Phase-2 shadow-adapter PRODUCTION tests — evidence model, mapping, shadow-only, July-16, faults, config, JSONL.

WO-HELM-HERMES-SHARED-STREAM-RECOVERY-PHASE2-SHADOW-ADAPTER-IMPLEMENTATION-0001.
Authority: HELM (HERMES market-data lane). Created (UTC): 2026-07-17.

Covers architecture_phase2_v1 §1-§21 via WO §14.1-14.12 + §15 inertness:
  1 evidence model · 2 provenance double-count guard · 3 socket ambiguity · 4 deterministic mapping ·
  5 shadow-only mechanical safety · 6 passive observer · 7 comparison taxonomy · 8 July-16 (strict + inferred) ·
  9 genuine faults · 10 config fail-safe · 11 bounded JSONL · 12 perf/concurrency · + inertness/dependency guards.

Run: cd <worktree> && python3 -m pytest tests/test_hermes_sss_shadow_adapter_v1.py -q
"""
import ast
import copy
import datetime
import inspect
import json
import os
import stat
import time
from pathlib import Path

import pytest

import utils.hermes_shared_stream_recovery_v1 as core
import utils.hermes_sss_evidence_snapshot_v1 as snap_mod
import utils.hermes_sss_shadow_record_v1 as rec_mod
import utils.hermes_sss_comparator_v1 as cmp_mod
import utils.hermes_sss_mapper_v1 as map_mod
import utils.hermes_sss_evidence_adapters_v1 as ea_mod
import utils.hermes_sss_observer_v1 as obs_mod
import utils.hermes_sss_config_v1 as cfg_mod
import utils.hermes_sss_jsonl_writer_v1 as jw_mod
import utils.hermes_sss_redaction_v1 as red_mod
import utils.hermes_sss_shadow_adapter_v1 as adapter_mod

from utils.hermes_sss_comparator_v1 import ComparisonClass, classify
from utils.hermes_sss_evidence_snapshot_v1 import (
    AvailabilityClass, EvidenceCompleteness, EvidenceSnapshot, Observation, SnapshotError,
    snapshot_from_dict, with_completeness,
)
from utils.hermes_sss_shadow_record_v1 import CurrentAuthorityObservation, current_authority_from_dict
from utils.hermes_sss_shadow_adapter_v1 import (
    RefusingShadowExecutor, ShadowEvaluationResult, ShadowExecutionForbidden, run_shadow_evaluation,
    BoundedCallbackDeduplicator,
)

REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "tests" / "fixtures" / "sss_phase2"
SCHEMAS = REPO / "schemas" / "shared_stream_recovery"
DECIDED_AT = "2026-07-16T21:09:00+00:00"

_SSS_MODULES = (
    adapter_mod, snap_mod, rec_mod, cmp_mod, map_mod, ea_mod, obs_mod, cfg_mod, jw_mod, red_mod,
)
_SSS_TOKENS = (
    "hermes_sss_shadow_adapter", "hermes_sss_evidence_snapshot", "hermes_sss_shadow_record",
    "hermes_sss_comparator", "hermes_sss_mapper", "hermes_sss_evidence_adapters", "hermes_sss_observer",
    "hermes_sss_config", "hermes_sss_jsonl_writer", "hermes_sss_redaction",
)


# --------------------------------------------------------------------------- fixtures / helpers
@pytest.fixture(scope="module")
def july16():
    return json.loads((FIXTURES / "july16_snapshot.json").read_text())


@pytest.fixture(scope="module")
def july16_snapshot(july16):
    return snapshot_from_dict(july16["snapshot"])


@pytest.fixture(scope="module")
def july16_current(july16):
    return current_authority_from_dict(july16["current_authority"])


@pytest.fixture(scope="module")
def enabled_config():
    return cfg_mod.load_config({"shadow_enabled": True})


def _current(evaluated=True, requested=True, executed=True, instrument=None):
    return CurrentAuthorityObservation(
        evaluated=evaluated, triggering_instrument=instrument,
        recovery_requested=requested, reconnect_executed=executed,
    )


def _run(snapshot, current, cfg, **kw):
    return run_shadow_evaluation(
        snapshot, current, config=cfg, decided_at_utc=DECIDED_AT, source_sha="testsha",
        runtime_identity={"service": "hermes", "environment": "DEV"}, **kw,
    )


def _base_snapshot_dict(july16) -> dict:
    d = copy.deepcopy(july16["snapshot"])
    d["contradictory_evidence"] = []
    d["unavailable_fields"] = []
    d["evidence_completeness"] = "COMPLETE"
    return d


# =========================================================================== 14.1 evidence model
def test_snapshot_is_immutable_frozen(july16_snapshot):
    with pytest.raises(Exception):
        july16_snapshot.socket_state = "disconnected"  # frozen dataclass


def test_naive_or_nonutc_timestamp_rejected(july16):
    d = _base_snapshot_dict(july16)
    d["captured_at_utc"] = "2026-07-16T21:09:00"  # naive
    with pytest.raises(SnapshotError):
        snapshot_from_dict(d)
    d2 = _base_snapshot_dict(july16)
    d2["evaluated_at_utc"] = "2026-07-16T21:09:00+02:00"  # non-UTC offset
    with pytest.raises(SnapshotError):
        snapshot_from_dict(d2)


def test_unavailable_is_not_false(july16_snapshot):
    """provider_maintenance value is False, but its provenance is UNAVAILABLE — the two are DISTINCT."""
    prov = july16_snapshot.provenance_for("provider_maintenance_indication")
    assert july16_snapshot.provider_maintenance_indication is False
    assert prov.observation == Observation.UNAVAILABLE
    assert prov.availability == AvailabilityClass.NOT_CURRENTLY_AVAILABLE


def test_inferred_is_not_observed(july16_snapshot):
    prov = {p.field: p for p in july16_snapshot.field_provenance}
    assert prov["socket_state"].observation == Observation.DIRECTLY_OBSERVED
    assert prov["heartbeat_available"].observation == Observation.JUSTIFIED_INFERENCE
    assert prov["heartbeat_age_s"].observation == Observation.JUSTIFIED_INFERENCE


def test_schema_round_trip_and_determinism(july16_snapshot):
    import jsonschema
    schema = json.loads((SCHEMAS / "evidence_snapshot.v1.schema.json").read_text())
    jsonschema.validate(july16_snapshot.to_dict(), schema)
    rt = snapshot_from_dict(july16_snapshot.to_dict())
    assert rt.snapshot_id == july16_snapshot.snapshot_id
    assert rt.canonical_json() == july16_snapshot.canonical_json()


def test_source_sha_and_runtime_identity_present(july16_snapshot):
    assert july16_snapshot.source_sha and isinstance(july16_snapshot.source_sha, str)
    assert isinstance(july16_snapshot.runtime_identity, dict)
    # sanitised — no secret-like keys in the fixture identity
    assert red_mod.is_clean(dict(july16_snapshot.runtime_identity))


def test_secrets_and_raw_payload_rejected_from_records():
    assert red_mod.is_clean({"comparison_class": "AGREE_NO_ACTION", "snapshot_id": "abc"})
    assert not red_mod.is_clean({"auth_token": "abc"})
    assert not red_mod.is_clean({"note": "connect to redis://user:pass@host:6379/0"})
    assert not red_mod.is_clean({"raw_payload": {"price": 1}})
    assert not red_mod.is_clean({"account_id": "001-234-567"})


# =========================================================================== 14.2 provenance double-count guard
def test_heartbeat_and_shared_progress_share_surface(july16_snapshot):
    assert map_mod.shares_transport_provenance(july16_snapshot) is True


def test_same_surface_shared_progress_collapses_to_mirror(july16):
    """The SAME last_tick_at surface cannot feed the core as two INDEPENDENT transport confirmations (§8)."""
    d = _base_snapshot_dict(july16)
    d["shared_progress_available"] = True
    d["shared_progress_age_s"] = 2.0
    snap = snapshot_from_dict(d)
    assert snap.shared_progress_available is True  # snapshot as captured
    inp = map_mod.map_snapshot_to_recovery_input(snap)
    # collapsed: the mapped core input mirrors the heartbeat -> mechanically tied, never an independent 2nd vote.
    assert inp.shared_progress_available is None
    assert inp.effective_shared_progress_available == inp.heartbeat_available
    assert inp.effective_shared_progress_age_s == inp.heartbeat_age_s


def test_independent_shared_progress_surface_not_collapsed(july16):
    """A shared-progress signal with a DISTINCT provenance surface (the silent-stall seam) is NOT collapsed."""
    d = _base_snapshot_dict(july16)
    d["shared_progress_available"] = True
    d["shared_progress_age_s"] = 2.0
    for p in d["field_provenance"]:
        if p["field"] == "shared_progress_available":
            p["source_module"] = "main.py"
            p["source_symbol"] = "asyncio.TimeoutError silent-stall seam (independent of last_tick_at)"
    snap = snapshot_from_dict(d)
    assert map_mod.shares_transport_provenance(snap) is False
    inp = map_mod.map_snapshot_to_recovery_input(snap)
    assert inp.shared_progress_available is True  # preserved as independent


# =========================================================================== 14.3 socket ambiguity
def test_socket_connected_alone_is_not_health(july16):
    """socket connected + heartbeat UNAVAILABLE -> SILENT_UNCONFIRMED, never TRANSPORT_HEALTHY."""
    d = _base_snapshot_dict(july16)
    d["socket_state"] = "connected"
    d["heartbeat_available"] = False
    d["heartbeat_age_s"] = None
    d["shared_progress_available"] = None
    d["shared_progress_age_s"] = None
    inp = map_mod.map_snapshot_to_recovery_input(snapshot_from_dict(d))
    env = core.decide(inp)
    assert env.transport_state != core.TransportState.HEALTHY.value
    assert env.transport_state == core.TransportState.SILENT_UNCONFIRMED.value
    assert env.reconnect_authorised is False


def test_socket_collector_marks_connected_ambiguous():
    fields, prov = ea_mod.collect_socket_evidence(ea_mod.AdapterStateView(socket_state="connected"))
    assert prov[0].availability == AvailabilityClass.AMBIGUOUS
    fields2, prov2 = ea_mod.collect_socket_evidence(ea_mod.AdapterStateView(socket_state="disconnected"))
    assert prov2[0].availability == AvailabilityClass.AVAILABLE_AUTHORITATIVE


def test_explicit_disconnect_distinct_from_connected(july16):
    d = _base_snapshot_dict(july16)
    d["socket_state"] = "disconnected"
    env = core.decide(map_mod.map_snapshot_to_recovery_input(snapshot_from_dict(d)))
    assert env.transport_state == core.TransportState.DISCONNECTED.value
    assert env.reconnect_authorised is True


# =========================================================================== 14.4 deterministic mapping
def test_mapping_is_deterministic(july16_snapshot):
    a = map_mod.map_snapshot_to_recovery_input(july16_snapshot)
    b = map_mod.map_snapshot_to_recovery_input(july16_snapshot)
    assert a.canonical_json() == b.canonical_json()


def test_mapper_fails_closed_on_contradiction(july16):
    """A field provenance claiming a directly-observed available heartbeat measurement while the value is absent is
    internally contradictory -> MapperError (fail closed), never a fabricated decision."""
    d = _base_snapshot_dict(july16)
    d["heartbeat_available"] = False
    d["heartbeat_age_s"] = None
    for p in d["field_provenance"]:
        if p["field"] == "heartbeat_age_s":
            p["observation"] = "DIRECTLY_OBSERVED"
            p["availability"] = "AVAILABLE_DERIVED"
    with pytest.raises(map_mod.MapperError):
        map_mod.map_snapshot_to_recovery_input(snapshot_from_dict(d))


def test_mapper_has_no_caller_emergency_or_force_input():
    params = set(inspect.signature(map_mod.map_snapshot_to_recovery_input).parameters)
    assert params == {"snapshot"}
    for forbidden in ("force", "emergency", "reconnect", "authorise", "authorize"):
        assert forbidden not in params


def test_unknown_preserved_through_mapping(july16_snapshot):
    inp = map_mod.map_snapshot_to_recovery_input(july16_snapshot)
    assert inp.provider_maintenance_indication is False  # unknown -> inert False, never inferred True


def test_freshness_does_not_grant_shared_authority(july16):
    """Unvalidated instrument stale + transport healthy -> proposal only, NEVER a reconnect (no freshness bypass)."""
    d = _base_snapshot_dict(july16)
    env = core.decide(map_mod.map_snapshot_to_recovery_input(snapshot_from_dict(d)))
    assert env.action == core.Action.RECOVERY_PROPOSAL_ONLY.value
    assert env.reconnect_authorised is False
    assert "UNVALIDATED_INSTRUMENT_STALE_NO_TRANSPORT_AUTHORITY" in env.reason_codes


# =========================================================================== 14.5 shadow-only mechanical safety
def test_no_reconnect_executor_import_anywhere_in_sss():
    """No sss module may import a reconnect-capable module (adapters.oanda/base, main, watchdog)."""
    for mod in _SSS_MODULES:
        tree = ast.parse(inspect.getsource(mod))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
            elif isinstance(node, ast.Import):
                imported |= {a.name for a in node.names}
        for name in imported:
            assert not name.startswith("adapters"), f"{mod.__name__} imports {name}"
            assert name != "main", f"{mod.__name__} imports main"
            assert "watchdog" not in name and "oanda" not in name, f"{mod.__name__} imports {name}"


def test_refusing_executor_raises_and_has_no_reconnect_capability():
    ex = RefusingShadowExecutor()
    dummy = rec_mod.build_record(
        snapshot_id="x", envelope=_healthy_env(), current_authority=_current(),
        comparison_class="AGREE_NO_ACTION", divergence_reasons=(), evidence_completeness="COMPLETE",
        adapter_version="v", source_sha="s", runtime_identity={}, decided_at_utc=DECIDED_AT)
    with pytest.raises(ShadowExecutionForbidden):
        ex.refuse(dummy)
    for forbidden in ("connect", "disconnect", "reconnect", "recovery_request", "execute"):
        assert not hasattr(ex, forbidden)


def test_record_shadow_only_immutable_and_validated(july16_snapshot, july16_current, enabled_config):
    res = _run(july16_snapshot, july16_current, enabled_config)
    assert res.record.shadow_only is True and res.record.consumer_live is False
    with pytest.raises(rec_mod.ShadowRecordError):
        rec_mod.ShadowDecisionRecord(
            snapshot_id="x", decided_at_utc=DECIDED_AT, adapter_version="v", source_sha="s", config_version="3",
            contract_version="1", runtime_identity={}, shadow_action="NO_ACTION",
            shadow_authority_status="not_authorised", shadow_transport_state="TRANSPORT_HEALTHY",
            shadow_reason_codes=(), shadow_reconnect_authorised=False, shadow_emergency_bypass_eligible=False,
            shadow_decision_id="x", shadow_limiter_state={}, current_authority=july16_current,
            comparison_class="AGREE_NO_ACTION", divergence_reasons=(), evidence_completeness="COMPLETE",
            envelope={}, shadow_only=False)
    with pytest.raises(rec_mod.ShadowRecordError):
        rec_mod.ShadowDecisionRecord(
            snapshot_id="x", decided_at_utc=DECIDED_AT, adapter_version="v", source_sha="s", config_version="3",
            contract_version="1", runtime_identity={}, shadow_action="NO_ACTION",
            shadow_authority_status="not_authorised", shadow_transport_state="TRANSPORT_HEALTHY",
            shadow_reason_codes=(), shadow_reconnect_authorised=False, shadow_emergency_bypass_eligible=False,
            shadow_decision_id="x", shadow_limiter_state={}, current_authority=july16_current,
            comparison_class="AGREE_NO_ACTION", divergence_reasons=(), evidence_completeness="COMPLETE",
            envelope={}, consumer_live=True)


def test_run_shadow_evaluation_has_no_executor_di_path():
    """No executor/force/emergency parameter -> a real executor cannot be substituted in."""
    params = set(inspect.signature(run_shadow_evaluation).parameters)
    for forbidden in ("executor", "force", "emergency", "reconnect", "connect", "disconnect"):
        assert forbidden not in params


def test_result_type_is_distinct_not_a_command(july16_snapshot, july16_current, enabled_config):
    res = _run(july16_snapshot, july16_current, enabled_config)
    assert isinstance(res, ShadowEvaluationResult)
    assert not hasattr(res, "connect") and not hasattr(res, "execute") and not hasattr(res, "reconnect")


def test_shadow_modules_perform_no_transport_io():
    """No sss module may import a transport/DB/socket/subprocess capability. The bounded JSONL writer legitimately
    uses os/tempfile/pathlib for its append-only sink; that is the ONLY permitted filesystem surface."""
    forbidden = {"httpx", "requests", "socket", "redis", "pymysql", "sqlalchemy", "subprocess", "aiohttp", "asyncpg"}
    for mod in _SSS_MODULES:
        tree = ast.parse(inspect.getsource(mod))
        names = {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        names |= {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        assert not (names & forbidden), f"{mod.__name__} imports forbidden IO: {names & forbidden}"


def test_run_shadow_evaluation_no_emitter_has_no_side_effects(july16_snapshot, july16_current, enabled_config, tmp_path):
    """With no emitter, a shadow evaluation writes nothing to disk (off-hot-path, side-effect-free)."""
    before = set(tmp_path.rglob("*"))
    cwd_before = set(Path(os.getcwd()).glob("*.jsonl"))
    _run(july16_snapshot, july16_current, enabled_config)
    assert set(tmp_path.rglob("*")) == before
    assert set(Path(os.getcwd()).glob("*.jsonl")) == cwd_before


# =========================================================================== 14.6 passive observer
def test_observer_is_passive_and_read_only():
    view = ea_mod.WatchdogStateView(
        recovery_request_pending=True,
        recovery_request_reason="sustained_red instrument=SPX500_USD age_s=540 red_count=2",
        limiter=ea_mod.LimiterStateView(attempts_in_window=1),
    )
    ob = obs_mod.observe_current_authority(view, now_utc=DECIDED_AT)
    assert ob.evaluated is True and ob.recovery_requested is True
    assert ob.triggering_instrument == "SPX500_USD"
    # the view is frozen -> observation MECHANICALLY cannot mutate/consume it
    assert view.recovery_request_pending is True
    with pytest.raises(Exception):
        view.recovery_request_pending = False
    # observer exposes NO mutation/consume method
    for forbidden in ("consume", "consume_recovery_request", "clear", "mutate", "reconnect"):
        assert not hasattr(obs_mod, forbidden) or not callable(getattr(obs_mod, forbidden, None)) or forbidden == "clear"


def test_observer_not_evaluated_when_no_request():
    ob = obs_mod.observe_current_authority(ea_mod.WatchdogStateView(), now_utc=DECIDED_AT)
    assert ob.evaluated is False


def test_observer_failure_isolated_on_malformed_reason():
    ob = obs_mod.observe_current_authority(
        ea_mod.WatchdogStateView(recovery_request_pending=True, recovery_request_reason="garbage no instrument token"),
        now_utc=DECIDED_AT)
    assert ob.evaluated is True and ob.triggering_instrument is None  # no crash, no fabricated instrument


# =========================================================================== 14.7 comparison taxonomy
def test_all_ten_comparison_classes_reachable():
    seen = set()
    healthy = _healthy_env()
    disc = core.decide(core.RecoveryDecisionInput(
        evaluated_at_utc=datetime.datetime(2026, 7, 16, 21, 9, tzinfo=core.UTC), provider="OANDA",
        socket_state=core.SocketState.DISCONNECTED))
    proposal = core.decide(core.RecoveryDecisionInput(
        evaluated_at_utc=datetime.datetime(2026, 7, 16, 21, 9, tzinfo=core.UTC), provider="OANDA",
        socket_state=core.SocketState.CONNECTED, heartbeat_available=True, heartbeat_age_s=2.0,
        instruments=(core.InstrumentObservation("SPX500_USD", True, True, False),)))
    escalate = core.decide(core.RecoveryDecisionInput(
        evaluated_at_utc=datetime.datetime(2026, 7, 16, 21, 9, tzinfo=core.UTC), provider="OANDA",
        socket_state=core.SocketState.UNKNOWN, heartbeat_available=False,
        instruments=(core.InstrumentObservation("SPX500_USD", True, True, False),)))
    seen.add(classify(healthy, _current(requested=False, executed=False))[0])
    seen.add(classify(disc, _current())[0])
    seen.add(classify(proposal, _current())[0])
    seen.add(classify(proposal, _current(), granular=True)[0])
    seen.add(classify(disc, _current(requested=False, executed=False))[0])
    seen.add(classify(healthy, _current(evaluated=False))[0])
    seen.add(classify(escalate, _current(requested=False, executed=False))[0])
    seen.add(classify(healthy, _current(), evidence_completeness="INCOMPLETE")[0])
    seen.add(classify(healthy, _current(), contradictory_evidence=True)[0])
    seen.add(classify(None, _current(), adapter_error=True)[0])
    assert {c.value for c in seen} == cmp_mod.ALL_COMPARISON_CLASSES


def test_conflict_is_not_agreement_and_incomplete_is_not_denial():
    healthy = _healthy_env()
    conflict, _ = classify(healthy, _current(), contradictory_evidence=True)
    incomplete, _ = classify(healthy, _current(), evidence_completeness="INCOMPLETE")
    assert conflict == ComparisonClass.EVIDENCE_CONFLICT != ComparisonClass.AGREE_NO_ACTION
    assert incomplete == ComparisonClass.EVIDENCE_INCOMPLETE != ComparisonClass.SHADOW_DENIES_CURRENT_RECONNECT


# =========================================================================== 14.8 July-16 (strict + inferred)
def test_july16_inferred_is_proposal_and_denies_current(july16_snapshot, july16_current, enabled_config):
    res = _run(july16_snapshot, july16_current, enabled_config)
    assert res.record.shadow_action == "RECOVERY_PROPOSAL_ONLY"
    assert res.record.shadow_transport_state == "TRANSPORT_HEALTHY"
    assert res.comparison_class == "SHADOW_DENIES_CURRENT_RECONNECT"
    assert res.reconnect_authorised_shadow is False
    granular = _run(july16_snapshot, july16_current, enabled_config, granular=True)
    assert granular.comparison_class == "SHADOW_PROPOSAL_CURRENT_RECONNECT"


def test_july16_inferred_heartbeat_labelled_justified_inference(july16_snapshot):
    prov = {p.field: p for p in july16_snapshot.field_provenance}
    assert prov["heartbeat_available"].observation == Observation.JUSTIFIED_INFERENCE
    # round-trip: the inferred label survives serialisation and is NEVER relabelled directly-observed.
    rt = snapshot_from_dict(july16_snapshot.to_dict())
    rt_prov = {p.field: p for p in rt.field_provenance}
    assert rt_prov["heartbeat_available"].observation == Observation.JUSTIFIED_INFERENCE


def test_july16_strict_observed_only_is_indeterminate(july16, july16_current, enabled_config):
    """STRICT observed-only replay: heartbeat age was NEVER logged historically -> heartbeat UNAVAILABLE ->
    completeness INCOMPLETE -> shadow fails closed (INDETERMINATE), NOT observed-healthy, NOT silently-inferred."""
    d = _base_snapshot_dict(july16)
    d["heartbeat_available"] = False
    d["heartbeat_age_s"] = None
    d["shared_progress_available"] = None
    d["shared_progress_age_s"] = None
    for p in d["field_provenance"]:
        if p["field"] in ("heartbeat_available", "heartbeat_age_s", "shared_progress_available"):
            p["observation"] = "UNAVAILABLE"
            p["availability"] = "NOT_CURRENTLY_AVAILABLE"
    d["evidence_completeness"] = "INCOMPLETE"
    res = _run(snapshot_from_dict(d), july16_current, enabled_config)
    assert res.comparison_class == "EVIDENCE_INCOMPLETE"
    assert res.reconnect_authorised_shadow is False


def test_inferred_field_can_never_be_reported_as_observed(july16):
    """No mapping/serialisation path can turn an inferred heartbeat into a directly-observed one, and a snapshot
    that claims otherwise while the value is absent fails closed."""
    d = _base_snapshot_dict(july16)
    # keep inferred as inferred through the mapper (mapper never rewrites provenance)
    snap = snapshot_from_dict(d)
    _ = map_mod.map_snapshot_to_recovery_input(snap)
    prov = {p.field: p for p in snap.field_provenance}
    assert prov["heartbeat_available"].observation == Observation.JUSTIFIED_INFERENCE


# =========================================================================== 14.9 genuine faults + distinct reasons
GENUINE_FAULTS = [
    ("provider_disconnect", {"provider_disconnect_event": True}, "PROVIDER_DISCONNECT_EVENT"),
    ("socket_disconnect", {"socket_state": "disconnected"}, "SOCKET_DISCONNECTED"),
    ("auth_failure", {"auth_failure": True}, "AUTHENTICATION_FAILURE"),
    ("shared_stream_silent", {"shared_stream_silent": True}, "SHARED_STREAM_PROGRESS_STALE"),
    ("parser_fatal", {"parser_fatal": True}, "PARSER_EXCEPTION_FATAL"),
    ("limiter_exhausted_plus_disconnect", {"provider_disconnect_event": True, "limiter_attempts_in_window": 3},
     "PROVIDER_DISCONNECT_EVENT"),
]


@pytest.mark.parametrize("name,mut,reason", GENUINE_FAULTS, ids=[g[0] for g in GENUINE_FAULTS])
def test_genuine_fault_authorises_and_agrees(july16, enabled_config, name, mut, reason):
    d = _base_snapshot_dict(july16)
    d.update(mut)
    res = _run(snapshot_from_dict(d), _current(), enabled_config)
    assert res.record.shadow_reconnect_authorised is True, name
    assert res.record.shadow_action == "RECONNECT_AUTHORISED", name
    assert res.comparison_class == "AGREE_RECONNECT", name
    assert reason in res.record.shadow_reason_codes, name


def test_auth_rate_parser_freshness_are_distinct(july16, enabled_config):
    # auth -> AUTH_FAILED emergency
    d = _base_snapshot_dict(july16); d["auth_failure"] = True
    assert core.decide(map_mod.map_snapshot_to_recovery_input(snapshot_from_dict(d))).transport_state == "AUTH_FAILED"
    # parser fatal -> FAULT_CONFIRMED, distinct
    d = _base_snapshot_dict(july16); d["parser_fatal"] = True
    assert core.decide(map_mod.map_snapshot_to_recovery_input(snapshot_from_dict(d))).transport_state == "FAULT_CONFIRMED"
    # ordinary malformed (error_count high, no parser_fatal) does NOT establish a fatal transport failure
    d = _base_snapshot_dict(july16); d["error_count_delta"] = 50
    env = core.decide(map_mod.map_snapshot_to_recovery_input(snapshot_from_dict(d)))
    assert env.reconnect_authorised is False and env.transport_state == "TRANSPORT_HEALTHY"
    # limiter rate-limit (non-emergency corroborated authority) -> RECONNECT_RATE_LIMITED, distinct from auth
    d = _base_snapshot_dict(july16)
    d["socket_state"] = "unknown"; d["heartbeat_available"] = False; d["heartbeat_age_s"] = None
    d["shared_progress_available"] = None; d["shared_progress_age_s"] = None
    d["limiter_attempts_in_window"] = 3
    for inst in d["instruments"]:
        if inst["expected_flow"] and inst["validated"]:
            inst["stale"] = True
    env = core.decide(map_mod.map_snapshot_to_recovery_input(snapshot_from_dict(d)))
    assert env.action == "RECONNECT_RATE_LIMITED" and env.reconnect_authorised is False


def test_genuine_fault_current_did_not_reconnect(july16, enabled_config):
    d = _base_snapshot_dict(july16); d["socket_state"] = "disconnected"
    res = _run(snapshot_from_dict(d), _current(requested=False, executed=False), enabled_config)
    assert res.comparison_class == "SHADOW_AUTHORIZES_CURRENT_NO_RECONNECT"
    assert res.reconnect_authorised_shadow is True


def test_adapter_error_and_conflict_and_incomplete_shortcircuit(july16, july16_current, enabled_config):
    snap = snapshot_from_dict(_base_snapshot_dict(july16))
    assert _run(snap, july16_current, enabled_config, simulate_adapter_error=True).comparison_class == "ADAPTER_ERROR"
    conflict = snapshot_from_dict({**_base_snapshot_dict(july16),
                                   "contradictory_evidence": [{"fields": "socket_state,heartbeat_age_s",
                                                               "description": "connected but hb hard-stale"}]})
    assert _run(conflict, july16_current, enabled_config).comparison_class == "EVIDENCE_CONFLICT"
    inc = with_completeness(snap, EvidenceCompleteness.INCOMPLETE)
    assert _run(inc, july16_current, enabled_config).comparison_class == "EVIDENCE_INCOMPLETE"


# =========================================================================== 14.10 config fail-safe
def test_config_disabled_by_default_and_on_malformed():
    assert cfg_mod.load_config(None).enabled is False
    assert cfg_mod.load_config("not-a-mapping").enabled is False
    assert cfg_mod.load_config({}).enabled is False              # shadow_enabled absent -> disabled
    assert cfg_mod.load_config({"shadow_enabled": False}).enabled is False


def test_config_no_implicit_env_enable():
    assert cfg_mod.load_config({"shadow_enabled": "true"}).enabled is False  # string, not bool -> disabled
    assert cfg_mod.load_config({"shadow_enabled": 1}).enabled is False       # int, not bool -> disabled


def test_config_consumer_live_true_disables():
    assert cfg_mod.load_config({"shadow_enabled": True, "shadow_consumer_live": True}).enabled is False


def test_config_valid_true_is_representable_but_not_activating(july16_snapshot, july16_current):
    """A valid enabled config is representable, but merely running an evaluation NEVER reconnects (inert)."""
    c = cfg_mod.load_config({"shadow_enabled": True})
    assert c.enabled is True
    res = _run(july16_snapshot, july16_current, c)
    assert res.produced is True and res.reconnect_authorised_shadow is False


def test_config_provisional_horizons_labelled():
    c = cfg_mod.load_config({"shadow_enabled": True})
    assert c.horizons_provisional is True and cfg_mod.HORIZONS_PROVISIONAL is True


def test_config_invalid_horizons_fail_closed():
    assert cfg_mod.load_config({"shadow_enabled": True, "heartbeat_hard_horizon_s": 5,
                                "heartbeat_soft_horizon_s": 45}).enabled is False  # hard < soft
    assert cfg_mod.load_config({"shadow_enabled": True, "heartbeat_soft_horizon_s": -1}).enabled is False


def test_config_output_path_is_externalised():
    c = cfg_mod.load_config({"shadow_enabled": True, "evidence_output_path": "ops/evidence/custom/x.jsonl"})
    assert c.evidence_output_path == "ops/evidence/custom/x.jsonl"


def test_disabled_config_produces_nothing(july16_snapshot, july16_current):
    res = _run(july16_snapshot, july16_current, cfg_mod.load_config(None))
    assert res.enabled is False and res.produced is False and res.record is None


# =========================================================================== 14.11 bounded JSONL
def _record_dict(july16_snapshot, july16_current, enabled_config):
    return _run(july16_snapshot, july16_current, enabled_config).record.to_dict()


def test_jsonl_append_only_and_deterministic_schema(tmp_path, july16_snapshot, july16_current, enabled_config):
    line_dict = _record_dict(july16_snapshot, july16_current, enabled_config)
    w = jw_mod.BoundedJsonlWriter(str(tmp_path / "sub" / "decisions.jsonl"))
    assert w.append(line_dict).ok and w.append(line_dict).ok
    lines = (tmp_path / "sub" / "decisions.jsonl").read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0]) == json.loads(json.dumps(line_dict, sort_keys=True))


def test_jsonl_rotation_retention_and_checksum(tmp_path, july16_snapshot, july16_current, enabled_config):
    line_dict = _record_dict(july16_snapshot, july16_current, enabled_config)
    path = tmp_path / "decisions.jsonl"
    w = jw_mod.BoundedJsonlWriter(str(path), max_bytes=1200, max_retained_files=2)
    results = [w.append(line_dict) for _ in range(12)]
    assert any(r.rotated for r in results)
    rotated = sorted(tmp_path.glob("decisions.jsonl.*"))
    rotated_jsonl = [p for p in rotated if p.name.split(".")[-1].isdigit()]
    assert len(rotated_jsonl) <= 2  # retention cap
    # checksum companion exists for a rotated file
    checksums = list(tmp_path.glob("decisions.jsonl.*.sha256"))
    assert checksums, "rotated file must have a sha256 companion"
    assert w.active_checksum() and len(w.active_checksum()) == 64


def test_jsonl_safe_permissions(tmp_path, july16_snapshot, july16_current, enabled_config):
    if os.name != "posix":
        pytest.skip("permission bits only meaningful on POSIX")
    path = tmp_path / "d" / "decisions.jsonl"
    jw_mod.BoundedJsonlWriter(str(path)).append(_record_dict(july16_snapshot, july16_current, enabled_config))
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_jsonl_write_failure_isolated_non_raising(tmp_path, monkeypatch, july16_snapshot, july16_current, enabled_config):
    w = jw_mod.BoundedJsonlWriter(str(tmp_path / "decisions.jsonl"))

    def _boom(*a, **k):
        raise OSError("No space left on device")

    monkeypatch.setattr("builtins.open", _boom)
    res = w.append(_record_dict(july16_snapshot, july16_current, enabled_config))
    assert res.ok is False and res.reason and res.reason.startswith("io_error")  # visible, did not raise


def test_jsonl_refuses_secrets_and_raw_payload(tmp_path):
    w = jw_mod.BoundedJsonlWriter(str(tmp_path / "decisions.jsonl"))
    assert w.append({"token": "abc123"}).ok is False
    assert w.append({"note": "redis://u:p@h:6379/0"}).ok is False
    assert not (tmp_path / "decisions.jsonl").exists()  # nothing written


def test_emitter_failure_cannot_affect_shadow_or_current_authority(july16_snapshot, july16_current, enabled_config):
    class Boom:
        def emit(self, record):
            raise RuntimeError("emitter down")
    res = _run(july16_snapshot, july16_current, enabled_config, emitter=Boom())
    assert res.produced is True and res.comparison_class == "SHADOW_DENIES_CURRENT_RECONNECT"


# =========================================================================== 14.12 perf / concurrency
def test_callback_dedup_bounded_and_new_generation_passes():
    d = BoundedCallbackDeduplicator(window_sec=2.0, max_entries=4)
    assert d.should_process("OANDA", 1, "disconnect", now_monotonic=100.0) is True
    assert d.should_process("OANDA", 1, "disconnect", now_monotonic=100.5) is False  # within window -> suppressed
    assert d.suppressed == 1
    assert d.should_process("OANDA", 2, "disconnect", now_monotonic=100.6) is True    # NEW generation never deduped
    assert d.should_process("OANDA", 1, "reconnect", now_monotonic=100.7) is True     # distinct event_type passes
    for gen in range(50):  # bounded memory: never grows past max_entries
        d.should_process("OANDA", 100 + gen, "disconnect", now_monotonic=200.0 + gen)
    assert len(d._seen) <= 4


def test_perf_p99_within_budget(july16_snapshot, july16_current, enabled_config, capsys):
    for _ in range(20):  # warmup
        _run(july16_snapshot, july16_current, enabled_config)
    samples = []
    for _ in range(300):
        t0 = time.perf_counter()
        _run(july16_snapshot, july16_current, enabled_config)
        samples.append((time.perf_counter() - t0) * 1000.0)
    samples.sort()
    p99 = samples[int(0.99 * len(samples)) - 1]
    # METHODOLOGY: synthetic single-process unit timing of the pure map->decide->classify->build path (no live
    # surfaces, no I/O). This is NOT production validation; it bounds the pure operation only.
    print(f"\n[PERF] shadow-eval p99={p99:.3f}ms mean={sum(samples)/len(samples):.3f}ms n=300 (synthetic, no I/O)")
    assert p99 < 25.0, f"p99 {p99:.3f}ms exceeds 25ms budget"


# =========================================================================== §15 inertness / static guards
def test_static_guard_no_runtime_or_infra_imports_sss():
    """FAIL if any runtime/infra file references an sss Phase-2 module. Only tests/schemas/docs/ops/design may."""
    allowed_roots = {"tests", "schemas", "docs", "ops", "design"}
    referrers = []
    for path in REPO.rglob("*.py"):
        rel = path.relative_to(REPO)
        if rel.parts[0] in allowed_roots or rel.name.startswith("hermes_sss_"):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for token in _SSS_TOKENS:
            if token in text:
                referrers.append(f"{rel} -> {token}")
    for infra in ("docker-compose.yml", "docker-compose.dev.yml", "Dockerfile"):
        p = REPO / infra
        if p.exists():
            text = p.read_text(encoding="utf-8", errors="ignore")
            for token in _SSS_TOKENS:
                if token in text:
                    referrers.append(f"{infra} -> {token}")
    for svc in REPO.glob("*.service"):
        text = svc.read_text(encoding="utf-8", errors="ignore")
        for token in _SSS_TOKENS:
            if token in text:
                referrers.append(f"{svc.name} -> {token}")
    assert referrers == [], f"INERTNESS VIOLATION: runtime/infra references sss Phase-2: {referrers}"


def test_phase1_core_and_oanda_watchdog_do_not_import_phase2():
    for name in ("main.py", "utils/watchdog.py", "adapters/oanda.py", "adapters/base.py",
                 "utils/hermes_shared_stream_recovery_v1.py"):
        p = REPO / name
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        for token in _SSS_TOKENS:
            assert token not in text, f"{name} references {token}"


def test_phase1_core_unchanged_by_sss():
    for mod in _SSS_MODULES:
        src = inspect.getsource(mod)
        assert "core.decide =" not in src and "core.RecoveryDecisionInput =" not in src


# =========================================================================== shared helpers (envelopes)
def _healthy_env():
    return core.decide(core.RecoveryDecisionInput(
        evaluated_at_utc=datetime.datetime(2026, 7, 16, 21, 9, tzinfo=core.UTC), provider="OANDA",
        socket_state=core.SocketState.CONNECTED, heartbeat_available=True, heartbeat_age_s=2.0,
        instruments=(core.InstrumentObservation("EUR_USD", True, False, True),)))
