"""Phase-2 shadow-adapter DESIGN tests — INERT proof + offline July-16 replay + genuine-fault scenarios.

WO-HELM-HERMES-SHARED-STREAM-RECOVERY-PHASE2-SHADOW-ADAPTER-AND-EVIDENCE-CONTRACT-DESIGN-0001.
Authority: HELM (HERMES market-data lane). Created (UTC): 2026-07-17.

Run: cd /srv/trading/hermes-worktrees/wo-sss-phase2 && python3 -m pytest tests/test_sss_phase2_shadow_design_v1.py -q

Proves:
  * the July-16 fixture snapshot -> shadow RECOVERY_PROPOSAL_ONLY (not authorised) + SHADOW_DENIES_CURRENT_RECONNECT;
  * genuine-fault fixtures -> AGREE_RECONNECT (or the appropriate divergence);
  * a static guard that FAILS if main.py / watchdog.py / adapters / compose import any Phase-2 design module;
  * schema validation of the evidence snapshot + shadow decision record;
  * MECHANICAL shadow-safety (RefusingShadowExecutor raises; shadow_only always True; no reconnect capability);
  * deterministic offline replay (same snapshot -> byte-identical record_id);
  * the full 10-class comparison taxonomy is reachable.
"""
import ast
import copy
import inspect
import json
from pathlib import Path

import pytest

import utils.hermes_shared_stream_recovery_v1 as core
import design.sss_phase2_evidence_snapshot_v1 as snap_mod
import design.sss_phase2_shadow_record_v1 as rec_mod
import design.sss_phase2_comparator_v1 as cmp_mod
import design.sss_phase2_interfaces_v1 as iface_mod
import design.sss_phase2_replay_harness_v1 as replay
import design.sss_phase2_test_doubles_v1 as doubles

from design.sss_phase2_comparator_v1 import ComparisonClass, classify
from design.sss_phase2_evidence_snapshot_v1 import (
    EvidenceCompleteness, EvidenceSnapshot, snapshot_from_dict, with_completeness,
)
from design.sss_phase2_shadow_record_v1 import CurrentAuthorityObservation, current_authority_from_dict

REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "tests" / "fixtures" / "sss_phase2"
SCHEMAS = REPO / "schemas" / "shared_stream_recovery"
DECIDED_AT = "2026-07-16T21:09:00+00:00"

_PHASE2_MODULES = (
    "sss_phase2_evidence_snapshot_v1", "sss_phase2_shadow_record_v1", "sss_phase2_comparator_v1",
    "sss_phase2_interfaces_v1", "sss_phase2_replay_harness_v1", "sss_phase2_test_doubles_v1",
)


# --------------------------------------------------------------------------- fixtures
@pytest.fixture(scope="module")
def july16():
    return json.loads((FIXTURES / "july16_snapshot.json").read_text())


@pytest.fixture(scope="module")
def july16_snapshot(july16):
    return snapshot_from_dict(july16["snapshot"])


@pytest.fixture(scope="module")
def july16_current(july16):
    return current_authority_from_dict(july16["current_authority"])


# =========================================================================== 1. July-16 offline replay
def test_july16_shadow_is_proposal_only_not_authorised(july16, july16_snapshot):
    inp = july16_snapshot.to_recovery_input()
    env = core.decide(inp)
    exp = july16["expected"]
    assert env.action == exp["shadow_action"] == "RECOVERY_PROPOSAL_ONLY"
    assert env.reconnect_authorised is False
    assert env.transport_state == exp["shadow_transport_state"] == "TRANSPORT_HEALTHY"
    assert set(env.stale_unvalidated_instruments) == set(exp["stale_unvalidated_instruments"])
    assert "UNVALIDATED_INSTRUMENT_STALE_NO_TRANSPORT_AUTHORITY" in env.reason_codes


def test_july16_comparison_denies_current_reconnect(july16, july16_snapshot, july16_current):
    record = replay.replay_snapshot(july16_snapshot, july16_current, decided_at_utc=DECIDED_AT)
    assert record.comparison_class == "SHADOW_DENIES_CURRENT_RECONNECT"
    assert record.shadow_reconnect_authorised is False
    assert record.current_authority.triggering_instrument == "SPX500_USD"
    assert record.current_authority.reconnected is True
    assert "shadow_emitted_recovery_proposal_only" in record.divergence_reasons


def test_july16_granular_is_proposal_current_reconnect(july16_snapshot, july16_current):
    env = core.decide(july16_snapshot.to_recovery_input())
    klass, _ = classify(env, july16_current, granular=True)
    assert klass == ComparisonClass.SHADOW_PROPOSAL_CURRENT_RECONNECT


def test_july16_offline_replay_is_deterministic(july16_snapshot, july16_current):
    r1 = replay.replay_snapshot(july16_snapshot, july16_current, decided_at_utc=DECIDED_AT)
    r2 = replay.replay_snapshot(july16_snapshot, july16_current, decided_at_utc=DECIDED_AT)
    assert r1.record_id == r2.record_id
    assert r1.to_json() == r2.to_json()
    assert july16_snapshot.snapshot_id == snapshot_from_dict(july16_snapshot.to_dict()).snapshot_id


def test_july16_honesty_heartbeat_is_inferred_not_measured(july16_snapshot):
    prov = {p.field: p for p in july16_snapshot.field_provenance}
    assert prov["socket_state"].observation == snap_mod.Observation.DIRECTLY_OBSERVED
    assert prov["heartbeat_available"].observation == snap_mod.Observation.JUSTIFIED_INFERENCE
    assert prov["heartbeat_age_s"].observation == snap_mod.Observation.JUSTIFIED_INFERENCE
    # provider maintenance is never inferred from the metals break
    assert prov["provider_maintenance_indication"].availability == snap_mod.AvailabilityClass.NOT_CURRENTLY_AVAILABLE


# =========================================================================== 2. genuine-fault scenarios
def _base_healthy_snapshot(july16) -> dict:
    """A coherent socket-connected + heartbeat-fresh + FX-flowing base to mutate into genuine faults."""
    d = copy.deepcopy(july16["snapshot"])
    d["provider_maintenance_indication"] = False
    d["contradictory_evidence"] = []
    d["unavailable_fields"] = []
    d["evidence_completeness"] = "COMPLETE"
    return d


def _current(evaluated=True, requested=True, executed=True, instrument=None):
    return CurrentAuthorityObservation(
        evaluated=evaluated, triggering_instrument=instrument,
        recovery_requested=requested, reconnect_executed=executed,
    )


GENUINE_FAULTS = [
    ("provider_disconnect", {"provider_disconnect_event": True}, _current(), "AGREE_RECONNECT"),
    ("socket_disconnect", {"socket_state": "disconnected"}, _current(), "AGREE_RECONNECT"),
    ("auth_failure", {"auth_failure": True}, _current(), "AGREE_RECONNECT"),
    ("shared_stream_silent", {"shared_stream_silent": True}, _current(), "AGREE_RECONNECT"),
    ("parser_fatal", {"parser_fatal": True}, _current(), "AGREE_RECONNECT"),
    ("limiter_exhausted_plus_provider_disconnect",
     {"provider_disconnect_event": True, "limiter_attempts_in_window": 3}, _current(), "AGREE_RECONNECT"),
]


@pytest.mark.parametrize("name,mut,cur,expected", GENUINE_FAULTS, ids=[g[0] for g in GENUINE_FAULTS])
def test_genuine_fault_shadow_authorises_reconnect(july16, name, mut, cur, expected):
    d = _base_healthy_snapshot(july16)
    d.update(mut)
    snap = snapshot_from_dict(d)
    record = replay.replay_snapshot(snap, cur, decided_at_utc=DECIDED_AT)
    assert record.shadow_reconnect_authorised is True, name
    assert record.comparison_class == expected, (name, record.comparison_class)
    assert record.shadow_action == "RECONNECT_AUTHORISED", name


def test_heartbeat_stale_corroborated_authorises(july16):
    """hb hard-stale + ALL validated expected-flow instruments stale -> shadow authorises (corroborated)."""
    d = _base_healthy_snapshot(july16)
    d["heartbeat_available"] = True
    d["heartbeat_age_s"] = 200.0
    d["shared_progress_age_s"] = 200.0
    for inst in d["instruments"]:
        if inst["expected_flow"] and inst["validated"]:
            inst["stale"] = True
    snap = snapshot_from_dict(d)
    record = replay.replay_snapshot(snap, _current(), decided_at_utc=DECIDED_AT)
    assert record.shadow_reconnect_authorised is True
    assert record.comparison_class == "AGREE_RECONNECT"


def test_genuine_fault_current_did_not_reconnect_is_shadow_authorizes(july16):
    """Socket disconnected (genuine fault) but current authority did NOT request a reconnect."""
    d = _base_healthy_snapshot(july16)
    d["socket_state"] = "disconnected"
    snap = snapshot_from_dict(d)
    record = replay.replay_snapshot(snap, _current(requested=False, executed=False), decided_at_utc=DECIDED_AT)
    assert record.shadow_reconnect_authorised is True
    assert record.comparison_class == "SHADOW_AUTHORIZES_CURRENT_NO_RECONNECT"


def test_evidence_incomplete_short_circuits(july16, july16_current):
    d = _base_healthy_snapshot(july16)
    snap = with_completeness(snapshot_from_dict(d), EvidenceCompleteness.INCOMPLETE)
    record = replay.replay_snapshot(snap, july16_current, decided_at_utc=DECIDED_AT)
    assert record.comparison_class == "EVIDENCE_INCOMPLETE"
    assert record.shadow_reconnect_authorised is False


def test_conflicting_evidence_short_circuits(july16, july16_current):
    d = _base_healthy_snapshot(july16)
    d["contradictory_evidence"] = [{"fields": "socket_state,heartbeat_age_s",
                                    "description": "socket connected but heartbeat hard-stale with no corroboration"}]
    snap = snapshot_from_dict(d)
    record = replay.replay_snapshot(snap, july16_current, decided_at_utc=DECIDED_AT)
    assert record.comparison_class == "EVIDENCE_CONFLICT"
    assert record.shadow_reconnect_authorised is False


def test_adapter_error_short_circuits(july16, july16_current):
    snap = snapshot_from_dict(_base_healthy_snapshot(july16))
    record = replay.replay_snapshot(snap, july16_current, decided_at_utc=DECIDED_AT, simulate_adapter_error=True)
    assert record.comparison_class == "ADAPTER_ERROR"
    assert record.shadow_reconnect_authorised is False


def test_current_not_evaluated(july16):
    snap = snapshot_from_dict(_base_healthy_snapshot(july16))
    record = replay.replay_snapshot(snap, _current(evaluated=False, requested=False, executed=False),
                                    decided_at_utc=DECIDED_AT)
    assert record.comparison_class == "CURRENT_AUTHORITY_NOT_EVALUATED"


def test_all_ten_comparison_classes_reachable():
    """Every taxonomy member must be produced by classify() (no dead class)."""
    seen = set()
    healthy = core.decide(core.RecoveryDecisionInput(
        evaluated_at_utc=core.datetime.datetime(2026, 7, 16, 21, 9, tzinfo=core.UTC), provider="OANDA",
        socket_state=core.SocketState.CONNECTED, heartbeat_available=True, heartbeat_age_s=2.0,
        instruments=(core.InstrumentObservation("EUR_USD", True, False, True),)))
    disc = core.decide(core.RecoveryDecisionInput(
        evaluated_at_utc=core.datetime.datetime(2026, 7, 16, 21, 9, tzinfo=core.UTC), provider="OANDA",
        socket_state=core.SocketState.DISCONNECTED))
    proposal = core.decide(core.RecoveryDecisionInput(
        evaluated_at_utc=core.datetime.datetime(2026, 7, 16, 21, 9, tzinfo=core.UTC), provider="OANDA",
        socket_state=core.SocketState.CONNECTED, heartbeat_available=True, heartbeat_age_s=2.0,
        instruments=(core.InstrumentObservation("SPX500_USD", True, True, False),)))
    escalate = core.decide(core.RecoveryDecisionInput(
        evaluated_at_utc=core.datetime.datetime(2026, 7, 16, 21, 9, tzinfo=core.UTC), provider="OANDA",
        socket_state=core.SocketState.UNKNOWN, heartbeat_available=False,
        instruments=(core.InstrumentObservation("SPX500_USD", True, True, False),)))
    seen.add(classify(healthy, _current(requested=False, executed=False))[0])                 # AGREE_NO_ACTION
    seen.add(classify(disc, _current())[0])                                                    # AGREE_RECONNECT
    seen.add(classify(proposal, _current())[0])                                                # SHADOW_DENIES_CURRENT_RECONNECT
    seen.add(classify(proposal, _current(), granular=True)[0])                                 # SHADOW_PROPOSAL_CURRENT_RECONNECT
    seen.add(classify(disc, _current(requested=False, executed=False))[0])                     # SHADOW_AUTHORIZES_CURRENT_NO_RECONNECT
    seen.add(classify(healthy, _current(evaluated=False))[0])                                   # CURRENT_AUTHORITY_NOT_EVALUATED
    seen.add(classify(escalate, _current(requested=False, executed=False))[0])                 # SHADOW_INDETERMINATE
    seen.add(classify(healthy, _current(), evidence_completeness="INCOMPLETE")[0])             # EVIDENCE_INCOMPLETE
    seen.add(classify(healthy, _current(), contradictory_evidence=True)[0])                    # EVIDENCE_CONFLICT
    seen.add(classify(None, _current(), adapter_error=True)[0])                                 # ADAPTER_ERROR
    assert {c.value for c in seen} == cmp_mod.ALL_COMPARISON_CLASSES


# =========================================================================== 3. MECHANICAL shadow-safety
def test_shadow_record_is_always_shadow_only(july16_snapshot, july16_current):
    record = replay.replay_snapshot(july16_snapshot, july16_current, decided_at_utc=DECIDED_AT)
    assert record.shadow_only is True
    assert record.consumer_live is False
    # Constructing a record with shadow_only False (or consumer_live True) is MECHANICALLY impossible:
    with pytest.raises(rec_mod.ShadowRecordError):
        rec_mod.ShadowDecisionRecord(
            snapshot_id="x", decided_at_utc=DECIDED_AT, adapter_version="v", source_sha="s",
            config_version="3", contract_version="1", runtime_identity={}, shadow_action="NO_ACTION",
            shadow_authority_status="not_authorised", shadow_transport_state="TRANSPORT_HEALTHY",
            shadow_reason_codes=(), shadow_reconnect_authorised=False, shadow_emergency_bypass_eligible=False,
            shadow_decision_id="x", shadow_limiter_state={}, current_authority=july16_current,
            comparison_class="AGREE_NO_ACTION", divergence_reasons=(), evidence_completeness="COMPLETE",
            envelope={}, shadow_only=False)
    with pytest.raises(rec_mod.ShadowRecordError):
        rec_mod.ShadowDecisionRecord(
            snapshot_id="x", decided_at_utc=DECIDED_AT, adapter_version="v", source_sha="s",
            config_version="3", contract_version="1", runtime_identity={}, shadow_action="NO_ACTION",
            shadow_authority_status="not_authorised", shadow_transport_state="TRANSPORT_HEALTHY",
            shadow_reason_codes=(), shadow_reconnect_authorised=False, shadow_emergency_bypass_eligible=False,
            shadow_decision_id="x", shadow_limiter_state={}, current_authority=july16_current,
            comparison_class="AGREE_NO_ACTION", divergence_reasons=(), evidence_completeness="COMPLETE",
            envelope={}, consumer_live=True)


def test_refusing_executor_cannot_reconnect(july16_snapshot, july16_current):
    record = replay.replay_snapshot(july16_snapshot, july16_current, decided_at_utc=DECIDED_AT)
    executor = doubles.RefusingShadowExecutor()
    with pytest.raises(iface_mod.ShadowExecutionForbidden):
        executor.refuse(record)
    # the boundary has NO reconnect capability at all:
    for forbidden in ("connect", "disconnect", "recovery_request", "reconnect", "execute"):
        assert not hasattr(executor, forbidden), f"shadow executor must not expose {forbidden}"


def test_emitter_write_failure_is_visible_and_nonraising(july16_snapshot, july16_current):
    record = replay.replay_snapshot(july16_snapshot, july16_current, decided_at_utc=DECIDED_AT)
    ok_emitter = doubles.InMemoryShadowEmitter()
    assert ok_emitter.emit(record) is True and len(ok_emitter.records) == 1
    failing = doubles.InMemoryShadowEmitter(fail_writes=True)
    assert failing.emit(record) is False and failing.write_failures == 1  # visible, did not raise


def test_no_phase2_module_performs_transport_io():
    """Static scan: no Phase-2 design module may import a transport/IO capability (httpx/socket/redis/pymysql/...)."""
    import design.sss_phase2_evidence_snapshot_v1 as m1
    forbidden = {"httpx", "requests", "socket", "redis", "pymysql", "sqlalchemy", "subprocess", "aiohttp"}
    for mod in (m1, rec_mod, cmp_mod, iface_mod, replay, doubles):
        tree = ast.parse(inspect.getsource(mod))
        names = {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        names |= {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        assert not (names & forbidden), f"{mod.__name__} imports forbidden IO: {names & forbidden}"


# =========================================================================== 4. static inertness guard
def test_static_guard_no_runtime_imports_phase2_design():
    """FAIL if any runtime/infra file references a Phase-2 design module. Only tests/schemas/docs/ops/design may."""
    allowed_roots = {"tests", "schemas", "docs", "ops", "design"}
    referrers = []
    for path in REPO.rglob("*.py"):
        rel = path.relative_to(REPO)
        if rel.parts[0] in allowed_roots:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for token in _PHASE2_MODULES:
            if token in text:
                referrers.append(f"{rel} -> {token}")
    for infra in ("docker-compose.yml", "docker-compose.dev.yml", "Dockerfile"):
        p = REPO / infra
        if p.exists():
            text = p.read_text(encoding="utf-8", errors="ignore")
            for token in _PHASE2_MODULES:
                if token in text:
                    referrers.append(f"{infra} -> {token}")
    for svc in REPO.glob("*.service"):
        text = svc.read_text(encoding="utf-8", errors="ignore")
        for token in _PHASE2_MODULES:
            if token in text:
                referrers.append(f"{svc.name} -> {token}")
    assert referrers == [], f"INERTNESS VIOLATION: runtime/infra references Phase-2 design: {referrers}"


def test_core_is_unchanged_by_phase2():
    """The Phase-1 core must be imported, never mutated: no Phase-2 module defines/reassigns core symbols."""
    for mod in (replay, cmp_mod, snap_mod, rec_mod):
        src = inspect.getsource(mod)
        assert "core.decide =" not in src and "core.RecoveryDecisionInput =" not in src


# =========================================================================== 5. schema validation
def _validate(instance, schema_name):
    import jsonschema
    schema = json.loads((SCHEMAS / schema_name).read_text())
    jsonschema.validate(instance=instance, schema=schema)


def test_snapshot_validates_against_schema(july16_snapshot):
    _validate(july16_snapshot.to_dict(), "evidence_snapshot.v1.schema.json")


def test_shadow_record_validates_against_schema(july16_snapshot, july16_current):
    record = replay.replay_snapshot(july16_snapshot, july16_current, decided_at_utc=DECIDED_AT)
    _validate(record.to_dict(), "shadow_decision_record.v1.schema.json")


def test_config_schema_defaults_disabled():
    schema = json.loads((SCHEMAS / "shadow_adapter_config.v1.schema.json").read_text())
    assert schema["properties"]["shadow_enabled"]["default"] is False
    assert schema["properties"]["shadow_consumer_live"]["const"] is False


def test_embedded_envelope_validates_against_decision_envelope_schema(july16_snapshot, july16_current):
    record = replay.replay_snapshot(july16_snapshot, july16_current, decided_at_utc=DECIDED_AT)
    _validate(record.envelope, "decision_envelope.v1.schema.json")
