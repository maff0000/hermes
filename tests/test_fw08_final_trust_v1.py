"""FW-08 PR#114 FINAL pre-build trust-boundary corrections tests (R-1 / R-2 / R-3).

WO-HELM-HERMES-PR114-FW08-FINAL-PREBUILD-TRUST-BOUNDARY-CORRECTIONS-0001.
Authority: HELM (HERMES market-data lane). Created (UTC): 2026-07-19. Contract version: 1.

These tests build NO real image, run NO real docker/SBOM/scan, publish NOTHING, deploy NOTHING, wire NO
runtime, enable NO shadow, mutate NO live state, and add NO third-party dependency (stdlib only). They close
the three residual pre-build trust-boundary corrections on the EXISTING PR#114 branch:

  R-1 §6-§8  bare-Boolean readiness REMOVED; the SOLE readiness path is a validated, producer-trusted, SEALED
             evidence chain; a checksum proves integrity NOT authority; a PUBLIC CALLER CANNOT fabricate a
             trusted evidence bundle that reaches readiness.
  R-2 §9-§11 the future Docker process consumes an IMMUTABLE, isolated, finalised snapshot (Option A) with a
             full lifecycle; EVERY adversarial mutation (write/replace/add/remove/symlink/mode/owner/second-
             runner/reuse/cleanup-fail) invalidates before success; the runner receives ONLY the finalised
             identity.
  R-3 §12-§14 active-import evidence binds to the ACTUAL candidate image (image_id + source_sha + fs digest)
             via a trusted producer with checksummed import graph + module inventory; a fixture that merely
             DECLARES inertness is rejected; every import/mismatch/tamper/stale/test-only case rejects.

Run: cd <worktree> && python3 -m pytest tests/test_fw08_final_trust_v1.py -q
"""
from __future__ import annotations

import dataclasses
import os
import stat
from pathlib import Path

import pytest

import tools.hermes_stage_b_build_v1 as w
import design.hermes_fw08_readiness_evidence_v1 as ee
import design.hermes_fw08_readiness_evaluator_v1 as evtomb
import design.hermes_fw08_producer_trust_v1 as pt
import design.hermes_fw08_evidence_chain_v1 as ec
import design.hermes_fw08_immutable_context_v1 as imc
import design.hermes_fw08_active_import_evidence_v1 as ai

REPO = Path(__file__).resolve().parents[1]
SRC40 = "a" * 40
IMAGE_ID = "sha256:" + "b" * 64
FS_DIGEST = "sha256:" + "c" * 64
CANDIDATE_ID = f"fw08cand@{SRC40}"
NOW = "2026-07-19T11:00:00+00:00"
PRODUCER = w.GOVERNED_PRODUCER_ID
P2_PREFIX = "utils/hermes_sss_"
P2_SUFFIX = "_v1.py"


# ============================================================ helpers: governed sealed evidence + trust
def _gate_records():
    order = ee.GATE_ORDER
    img_bearing = {"G-BUILD", "G-OCI-LABELS", "G-IMAGE-CONTENT", "G-SBOM", "G-VULN-SCAN"}
    recs = []
    for i, g in enumerate(order):
        recs.append(ee.make_gate_evidence(
            gate_id=g, result=True, reason_code="OK", source_sha=SRC40, producer=PRODUCER,
            tool_version="1", at_utc=f"2026-07-19T10:0{i}:00+00:00",
            image_id=(IMAGE_ID if g in img_bearing else None)))
    return recs


def _trust_contract(**over):
    base = dict(
        contract_version=pt.CONTRACT_VERSION, producer_id=PRODUCER,
        producer_type="IN_PROCESS_GOVERNED_WRAPPER", trust_contract_version="1",
        allowed_gate_ids=ee.GATE_ORDER, application="hermes", module_identity=PRODUCER,
        content_digest="d" * 64, source_version=SRC40, trust_policy_version="1",
        sealing_method=pt.SEALING_METHOD_IN_PROCESS, candidate_binding=CANDIDATE_ID,
        source_binding=SRC40, created_utc="2026-07-19T09:00:00+00:00",
        expiry_utc="2026-07-20T09:00:00+00:00", audit_reference="FW08-TEST",
    )
    base.update(over)
    return pt.ProducerTrustContract(**base)


def _governed(sealer=None):
    sealer = sealer or pt.GovernedEvidenceSealer(producer_id=PRODUCER)
    reg = pt.ProducerTrustRegistry()
    contract = _trust_contract()
    reg.register(contract)
    sealed = [sealer.seal(r) for r in _gate_records()]
    return sealer, reg, contract, sealed


# =========================================================================== R-1 §6 bare-Boolean removed
def test_r1_bare_boolean_api_removed_and_fail_closed():
    with pytest.raises(evtomb.BareBooleanReadinessRemovedError):
        _ = evtomb.ReadinessInputs
    with pytest.raises(evtomb.BareBooleanReadinessRemovedError):
        evtomb.evaluate({"build_succeeded": True}, True)


def test_r1_no_compatibility_constructor_recreates_readiness_from_bools():
    # There is no public symbol anywhere that turns caller-supplied bools into a ready verdict.
    for name in ("ReadinessInputs", "evaluate_bools", "from_bools", "readiness_from_flags"):
        assert not hasattr(w, name)


# =========================================================================== R-1 §7 producer-trust contract
def test_r1_valid_producer_trust_contract_ok():
    pt.validate_producer_trust(_trust_contract())  # does not raise


@pytest.mark.parametrize("over,label", [
    ({"contract_version": "9"}, "bad-version"),
    ({"producer_type": "SOMETHING"}, "bad-type"),
    ({"sealing_method": "PLAINTEXT"}, "bad-method"),
    ({"allowed_gate_ids": ()}, "empty-gates"),
    ({"allowed_gate_ids": ("G-NOPE",)}, "unknown-gate"),
    ({"producer_id": "*"}, "wildcard-producer"),
    ({"source_binding": ""}, "blank-source"),
    ({"expiry_utc": "not-a-date"}, "bad-expiry"),
])
def test_r1_malformed_producer_trust_rejected(over, label):
    with pytest.raises(pt.ProducerTrustError):
        pt.validate_producer_trust(_trust_contract(**over))


def test_r1_registry_rejects_expired_and_wrong_binding():
    reg = pt.ProducerTrustRegistry()
    reg.register(_trust_contract())
    ok, _ = reg.is_trusted(PRODUCER, "G-BUILD", now_utc=NOW, candidate_id=CANDIDATE_ID, source_sha=SRC40)
    assert ok
    # expired
    reg2 = pt.ProducerTrustRegistry()
    reg2.register(_trust_contract(expiry_utc="2026-07-19T10:00:00+00:00"))
    bad, reason = reg2.is_trusted(PRODUCER, "G-BUILD", now_utc=NOW, candidate_id=CANDIDATE_ID, source_sha=SRC40)
    assert not bad and reason == "PT-EXPIRED"
    # wrong candidate / source
    b2, r2 = reg.is_trusted(PRODUCER, "G-BUILD", now_utc=NOW, candidate_id="other@x", source_sha=SRC40)
    assert not b2 and r2 == "PT-CANDIDATE-MISMATCH"
    b3, r3 = reg.is_trusted(PRODUCER, "G-BUILD", now_utc=NOW, candidate_id=CANDIDATE_ID, source_sha="e" * 40)
    assert not b3 and r3 == "PT-SOURCE-MISMATCH"
    # unknown producer
    b4, r4 = reg.is_trusted("nobody", "G-BUILD", now_utc=NOW, candidate_id=CANDIDATE_ID, source_sha=SRC40)
    assert not b4 and r4 == "PT-UNKNOWN-PRODUCER"


def test_r1_sealer_key_is_not_exposed():
    s = pt.GovernedEvidenceSealer(producer_id=PRODUCER)
    # the ephemeral key is name-mangled (private) — no PUBLIC attribute exposes it, no getter, no repr leak.
    assert getattr(s, "key", None) is None
    public = [a for a in vars(s) if not a.startswith("_")]
    assert all(not isinstance(getattr(s, a), (bytes, bytearray)) for a in public)
    assert "GovernedEvidenceSealer" in type(s).__name__


# =============================================================== R-1 §7 CRITICAL: caller cannot fabricate
def test_r1_governed_sealed_chain_reaches_readiness():
    sealer, reg, _c, sealed = _governed()
    v = pt.evaluate_trusted_readiness(
        sealed, verifier=sealer, registry=reg, expected_source_sha=SRC40, expected_image_id=IMAGE_ID,
        expected_candidate_id=CANDIDATE_ID, now_utc=NOW)
    assert v.ready, v.reason_codes


def test_r1_public_caller_cannot_fabricate_trusted_readiness():
    """CRITICAL. A public caller who lacks the wrapper's ephemeral seal key CANNOT construct a sealed bundle
    that the governed verifier accepts — three distinct attacks all fail closed."""
    gov_sealer, reg, _c, _sealed = _governed()

    # Attack A: fabricate seals with a random 64-hex value (no key at all).
    forged = [pt.SealedGateEvidence(evidence=r, producer_id=PRODUCER,
                                    sealing_method=pt.SEALING_METHOD_IN_PROCESS, seal="f" * 64)
              for r in _gate_records()]
    va = pt.evaluate_trusted_readiness(forged, verifier=gov_sealer, registry=reg, expected_source_sha=SRC40,
                                       expected_image_id=IMAGE_ID, expected_candidate_id=CANDIDATE_ID,
                                       now_utc=NOW)
    assert not va.ready and any(c.startswith("PT-SEAL-INVALID") for c in va.reason_codes)

    # Attack B: attacker mints their OWN sealer (own key) and claims the trusted producer id.
    attacker = pt.GovernedEvidenceSealer(producer_id=PRODUCER)
    forged_b = [attacker.seal(r) for r in _gate_records()]
    vb = pt.evaluate_trusted_readiness(forged_b, verifier=gov_sealer, registry=reg, expected_source_sha=SRC40,
                                       expected_image_id=IMAGE_ID, expected_candidate_id=CANDIDATE_ID,
                                       now_utc=NOW)
    assert not vb.ready and any(c.startswith("PT-SEAL-INVALID") for c in vb.reason_codes)

    # Attack C: attacker uses a producer id that is not in the governed registry.
    outsider = pt.GovernedEvidenceSealer(producer_id="attacker/tool.py")
    forged_c = [outsider.seal(r) for r in _gate_records()]
    vc = pt.evaluate_trusted_readiness(forged_c, verifier=outsider, registry=reg, expected_source_sha=SRC40,
                                       expected_image_id=IMAGE_ID, expected_candidate_id=CANDIDATE_ID,
                                       now_utc=NOW)
    assert not vc.ready and any("PT-UNKNOWN-PRODUCER" in c for c in vc.reason_codes)


def test_r1_checksum_only_fabrication_rejected():
    # A record with a self-consistent checksum but NO valid seal cannot reach readiness (checksum != authority).
    _sealer, reg, _c, _sealed = _governed()
    fresh_verifier = pt.GovernedEvidenceSealer(producer_id=PRODUCER)
    forged = [pt.SealedGateEvidence(evidence=r, producer_id=PRODUCER,
                                    sealing_method=pt.SEALING_METHOD_IN_PROCESS, seal=r.evidence_checksum)
              for r in _gate_records()]
    v = pt.evaluate_trusted_readiness(forged, verifier=fresh_verifier, registry=reg, expected_source_sha=SRC40,
                                      expected_image_id=IMAGE_ID, expected_candidate_id=CANDIDATE_ID,
                                      now_utc=NOW)
    assert not v.ready and any(c.startswith("PT-SEAL-INVALID") for c in v.reason_codes)


def test_r1_wrapper_exposes_no_evidence_injection_parameter():
    # The wrapper builds its own evidence; there is NO parameter by which a caller injects gate evidence.
    import inspect
    params = set(inspect.signature(w.run_stage_b_candidate_build).parameters)
    for forbidden in ("evidence", "gate_evidence", "sealed_records", "readiness", "ready_inputs",
                      "chain", "verdict"):
        assert forbidden not in params


# =========================================================================== R-1 §8 evidence chain
def _chain(sealed, contract, **over):
    kw = dict(candidate_id=CANDIDATE_ID, source_sha=SRC40, image_id=IMAGE_ID,
              producer_trust_ref=contract.reference(), created_utc=NOW)
    kw.update(over)
    return ec.build_evidence_chain(sealed, **kw)


def test_r1_chain_root_and_valid_chain_ready():
    sealer, reg, contract, sealed = _governed()
    manifest = _chain(sealed, contract)
    assert len(manifest.chain_root) == 64
    v = ec.validate_evidence_chain(
        manifest, sealed, verifier=sealer, registry=reg, expected_candidate_id=CANDIDATE_ID,
        expected_source_sha=SRC40, expected_image_id=IMAGE_ID,
        expected_producer_trust_ref=contract.reference(), now_utc=NOW)
    assert v.ready, v.reason_codes


def test_r1_chain_reorder_rejected():
    sealer, reg, contract, sealed = _governed()
    manifest = _chain(sealed, contract)
    permuted = manifest.ordered_gate_ids[1:] + manifest.ordered_gate_ids[:1]
    tampered = dataclasses.replace(manifest, ordered_gate_ids=permuted)
    v = ec.validate_evidence_chain(tampered, sealed, verifier=sealer, registry=reg,
                                   expected_candidate_id=CANDIDATE_ID, expected_source_sha=SRC40,
                                   expected_image_id=IMAGE_ID,
                                   expected_producer_trust_ref=contract.reference(), now_utc=NOW)
    assert not v.ready and "CH-ORDER-OR-SET-INVALID" in v.reason_codes


def test_r1_chain_missing_removed_rejected():
    sealer, reg, contract, sealed = _governed()
    manifest = _chain(sealed, contract)
    v = ec.validate_evidence_chain(manifest, sealed[:-1], verifier=sealer, registry=reg,
                                   expected_candidate_id=CANDIDATE_ID, expected_source_sha=SRC40,
                                   expected_image_id=IMAGE_ID,
                                   expected_producer_trust_ref=contract.reference(), now_utc=NOW)
    assert not v.ready and ("CH-SUPPLIED-ORDER-OR-SET-INVALID" in v.reason_codes
                            or "CH-ROOT-MISMATCH" in v.reason_codes)


def test_r1_chain_appended_untrusted_rejected():
    sealer, reg, contract, sealed = _governed()
    manifest = _chain(sealed, contract)
    extra = sealer.seal(ee.make_gate_evidence(
        gate_id="G-SBOM", result=True, reason_code="EXTRA", source_sha=SRC40, producer=PRODUCER,
        tool_version="1", at_utc="2026-07-19T10:07:00+00:00", image_id=IMAGE_ID))
    v = ec.validate_evidence_chain(manifest, list(sealed) + [extra], verifier=sealer, registry=reg,
                                   expected_candidate_id=CANDIDATE_ID, expected_source_sha=SRC40,
                                   expected_image_id=IMAGE_ID,
                                   expected_producer_trust_ref=contract.reference(), now_utc=NOW)
    assert not v.ready


def test_r1_chain_duplicate_rejected():
    sealer, reg, contract, sealed = _governed()
    manifest = _chain(sealed, contract)
    dup = list(sealed) + [sealed[3]]
    v = ec.validate_evidence_chain(manifest, dup, verifier=sealer, registry=reg,
                                   expected_candidate_id=CANDIDATE_ID, expected_source_sha=SRC40,
                                   expected_image_id=IMAGE_ID,
                                   expected_producer_trust_ref=contract.reference(), now_utc=NOW)
    assert not v.ready


def test_r1_chain_leaf_tamper_rejected():
    sealer, reg, contract, sealed = _governed()
    manifest = _chain(sealed, contract)
    # tamper a leaf checksum in the manifest -> root recompute mismatches supplied records.
    bad_leaves = ("0" * 64,) + manifest.leaf_checksums[1:]
    tampered = dataclasses.replace(manifest, leaf_checksums=bad_leaves)
    v = ec.validate_evidence_chain(tampered, sealed, verifier=sealer, registry=reg,
                                   expected_candidate_id=CANDIDATE_ID, expected_source_sha=SRC40,
                                   expected_image_id=IMAGE_ID,
                                   expected_producer_trust_ref=contract.reference(), now_utc=NOW)
    assert not v.ready and ("CH-LEAF-CHECKSUM-MISMATCH" in v.reason_codes
                            or "CH-ROOT-MISMATCH" in v.reason_codes)


@pytest.mark.parametrize("over,code", [
    ({"expected_candidate_id": "wrong@x"}, "CH-CANDIDATE-MISMATCH"),
    ({"expected_source_sha": "e" * 40}, "CH-SOURCE-MISMATCH"),
    ({"expected_image_id": "sha256:" + "9" * 64}, "CH-IMAGE-MISMATCH"),
    ({"expected_producer_trust_ref": "0" * 64}, "CH-PRODUCER-TRUST-REF-MISMATCH"),
])
def test_r1_chain_binding_mismatch_rejected(over, code):
    sealer, reg, contract, sealed = _governed()
    manifest = _chain(sealed, contract)
    kw = dict(expected_candidate_id=CANDIDATE_ID, expected_source_sha=SRC40, expected_image_id=IMAGE_ID,
              expected_producer_trust_ref=contract.reference())
    kw.update(over)
    v = ec.validate_evidence_chain(manifest, sealed, verifier=sealer, registry=reg, now_utc=NOW, **kw)
    assert not v.ready and code in v.reason_codes


def test_r1_chain_foreign_key_seal_rejected():
    sealer, reg, contract, sealed = _governed()
    manifest = _chain(sealed, contract)
    foreign = pt.GovernedEvidenceSealer(producer_id=PRODUCER)  # different key
    v = ec.validate_evidence_chain(manifest, sealed, verifier=foreign, registry=reg,
                                   expected_candidate_id=CANDIDATE_ID, expected_source_sha=SRC40,
                                   expected_image_id=IMAGE_ID,
                                   expected_producer_trust_ref=contract.reference(), now_utc=NOW)
    assert not v.ready and any(c.startswith("PT-SEAL-INVALID") for c in v.reason_codes)


def test_r1_chain_stale_rejected():
    sealer, reg, contract, sealed = _governed()
    manifest = _chain(sealed, contract)
    v = ec.validate_evidence_chain(manifest, sealed, verifier=sealer, registry=reg,
                                   expected_candidate_id=CANDIDATE_ID, expected_source_sha=SRC40,
                                   expected_image_id=IMAGE_ID,
                                   expected_producer_trust_ref=contract.reference(),
                                   now_utc="2026-07-25T11:00:00+00:00")
    # a stale bundle is refused either at the producer-trust expiry or at the evidence freshness check.
    assert not v.ready and any(c.startswith("EV-STALE") or "PT-EXPIRED" in c for c in v.reason_codes)


# =========================================================================== R-2 §9-§11 immutable context
def _source_context(base):
    src = Path(base) / "verified_ctx"
    (src / "docker").mkdir(parents=True, exist_ok=True)
    (src / "main.py").write_text("import config\n", encoding="utf-8")
    (src / "config.py").write_text("x = 1\n", encoding="utf-8")
    (src / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    (src / "docker" / "entrypoint.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    return src


def _finalised(tmp_path, name="c1"):
    src = _source_context(tmp_path / name)
    ctx = imc.ImmutableBuildContext(source_sha=SRC40, candidate_id=CANDIDATE_ID)
    ctx.create(src, tmp_path / name / "snap")
    ctx.verify(effective_context_checksum="e" * 64)
    ident = ctx.finalise()
    return ctx, ident


def test_r2_lifecycle_happy_path(tmp_path):
    ctx, ident = _finalised(tmp_path)
    assert ctx.state is imc.ContextLifecycleState.FINALISED
    assert ident.mode_verified and ident.file_count == 4
    got = ctx.acquire("runner-1", expected_candidate_id=CANDIDATE_ID, expected_source_sha=SRC40)
    assert ctx.state is imc.ContextLifecycleState.BUILD_IN_USE
    assert got.snapshot_checksum == ident.snapshot_checksum   # runner receives ONLY finalised identity
    ctx.release()
    ctx.destroy()
    assert ctx.state is imc.ContextLifecycleState.DESTROYED
    assert not Path(ident.root_path).exists()


def test_r2_finalise_revokes_write_bits(tmp_path):
    _ctx, ident = _finalised(tmp_path)
    for p in Path(ident.root_path).rglob("*"):
        if p.is_file():
            assert not (p.stat().st_mode & stat.S_IWUSR), f"still writable: {p}"


def test_r2_acquire_before_finalise_rejected(tmp_path):
    src = _source_context(tmp_path)
    ctx = imc.ImmutableBuildContext(source_sha=SRC40, candidate_id=CANDIDATE_ID)
    ctx.create(src, tmp_path / "c")
    with pytest.raises(imc.ImmutableContextError):
        ctx.acquire("r")  # race: not yet FINALISED


def test_r2_second_runner_rejected(tmp_path):
    ctx, _ident = _finalised(tmp_path)
    ctx.acquire("runner-1")
    with pytest.raises(imc.ImmutableContextError):
        ctx.acquire("runner-2")  # single-consumer


def test_r2_reuse_across_candidate_or_source_rejected(tmp_path):
    ctx, _ident = _finalised(tmp_path)
    with pytest.raises(imc.ImmutableContextError):
        ctx.acquire("r", expected_candidate_id="other@x")
    ctx2, _i2 = _finalised(tmp_path, name="c2")
    with pytest.raises(imc.ImmutableContextError):
        ctx2.acquire("r", expected_source_sha="e" * 40)


@pytest.mark.parametrize("mutate", ["write", "replace", "add", "remove", "mode", "symlink"])
def test_r2_post_finalise_mutation_invalidates(tmp_path, mutate):
    ctx, ident = _finalised(tmp_path, name=f"c_{mutate}")
    root = Path(ident.root_path)
    target = root / "main.py"
    os.chmod(target, 0o600)  # root can re-enable writes; detection must still catch the change
    if mutate == "write":
        target.write_text("import config\nimport evil\n", encoding="utf-8")
    elif mutate == "replace":
        target.unlink(); target.write_text("import config\n# replaced\n", encoding="utf-8")
    elif mutate == "add":
        (root / "sneaky.py").write_text("evil=1\n", encoding="utf-8")
    elif mutate == "remove":
        target.unlink()
    elif mutate == "mode":
        pass  # the chmod above IS the mutation
    elif mutate == "symlink":
        target.unlink(); target.symlink_to("/etc/hostname")
    with pytest.raises(imc.ImmutableContextError):
        ctx.assert_intact()
    assert ctx.state is imc.ContextLifecycleState.REJECTED
    # a rejected/mutated snapshot can never be acquired for a build.
    with pytest.raises(imc.ImmutableContextError):
        ctx.acquire("runner")


def test_r2_owner_mismatch_detected(tmp_path):
    ctx, _ident = _finalised(tmp_path)
    with pytest.raises(imc.ImmutableContextError):
        ctx.assert_intact(expected_uid=os.getuid() + 999999)


def test_r2_reuse_after_success_or_failure_rejected(tmp_path):
    ctx, _ident = _finalised(tmp_path)
    ctx.acquire("r"); ctx.release(); ctx.destroy()
    with pytest.raises(imc.ImmutableContextError):
        ctx.acquire("again")  # DESTROYED is terminal


def test_r2_symlink_in_source_rejected(tmp_path):
    src = _source_context(tmp_path)
    (src / "evil_link").symlink_to("/etc/hostname")
    ctx = imc.ImmutableBuildContext(source_sha=SRC40, candidate_id=CANDIDATE_ID)
    with pytest.raises(imc.ImmutableContextError):
        ctx.create(src, tmp_path / "c")
    assert ctx.state is imc.ContextLifecycleState.REJECTED


def test_r2_cleanup_failure_fails_closed(tmp_path):
    ctx, ident = _finalised(tmp_path)
    ctx.acquire("r"); ctx.release()
    with pytest.raises(imc.ImmutableContextError):
        ctx.destroy(remover=lambda _root: None)  # remover that does NOT remove -> root still present
    assert ctx.state is imc.ContextLifecycleState.REJECTED
    assert Path(ident.root_path).exists()
    # real cleanup still possible to avoid leaking the fixture dir
    for p in sorted(Path(ident.root_path).rglob("*"), reverse=True):
        os.chmod(p, 0o700)
    os.chmod(ident.root_path, 0o700)


def test_r2_prohibited_snapshot_root_refused(tmp_path):
    ctx = imc.ImmutableBuildContext(source_sha=SRC40, candidate_id=CANDIDATE_ID)
    with pytest.raises(imc.ImmutableContextError):
        ctx.create(_source_context(tmp_path), Path("/etc"))


def test_r2_writable_alias_is_not_the_finalised_identity(tmp_path):
    # A copy of the snapshot to a writable alias is NOT the finalised identity; the runner only ever receives
    # the finalised root_path, and the alias's files are writable (demonstrating why it is unsafe).
    ctx, ident = _finalised(tmp_path)
    alias = tmp_path / "alias"
    import shutil
    shutil.copytree(ident.root_path, alias)
    assert str(alias) != ident.root_path
    got = ctx.acquire("runner")
    assert got.root_path == ident.root_path  # only the finalised dir is handed over


# =========================================================================== R-3 §12-§14 image-bound import
def _p2_modules():
    return [f"{P2_PREFIX}{n}{P2_SUFFIX}" for n in range(10)]


def _img_evidence(**over):
    edges = {"main.py": ["config.py"], "config.py": []}
    modules = ["main.py", "config.py"] + _p2_modules()
    e = {
        "contract_version": "1", "application": "hermes", "candidate_id": CANDIDATE_ID,
        "image_id": IMAGE_ID, "source_sha": SRC40, "image_filesystem_digest": FS_DIGEST,
        "entrypoint": "/app/docker/entrypoint.sh", "command": "python main.py",
        "analysis_method": "STATIC_IMPORT_GRAPH", "analysis_tool": "hermes_img_import_scan",
        "analysis_tool_version": "1", "tool_digest": "t" * 64,
        "evidence_method": "STATIC_IMPORT_GRAPH", "entrypoint_module": "main.py",
        "modules": modules, "import_edges": edges,
        "runner_registrations": [], "callback_registrations": [], "dynamic_imports": [],
        "activation_env_defaults": {"HERMES_SSS_SHADOW_ENABLED": "0"}, "plugin_discovery": [],
        "producer_trust_reference": "ptref-1", "result": "INERT", "generated_utc": "2026-07-19T10:30:00+00:00",
    }
    e.update(over)
    # recompute checksums unless the test overrode them explicitly
    if "import_graph_checksum" not in over:
        e["import_graph_checksum"] = ai.import_graph_checksum(e["import_edges"])
    if "module_inventory_checksum" not in over:
        e["module_inventory_checksum"] = ai.module_inventory_checksum(e["modules"])
    return e


def _validate_img(e, **over):
    kw = dict(expected_image_id=IMAGE_ID, expected_source_sha=SRC40, expected_fs_digest=FS_DIGEST,
              expected_candidate_id=CANDIDATE_ID, trusted_producer_refs=("ptref-1",), now_utc=NOW,
              phase2_prefix=P2_PREFIX, phase2_suffix=P2_SUFFIX, required_phase2_count=10)
    kw.update(over)
    return ai.validate_image_bound_active_import(e, **kw)


def test_r3_valid_image_bound_evidence_accepted():
    v = _validate_img(_img_evidence())
    assert v.accepted and v.inert, v.reason_codes


@pytest.mark.parametrize("over,code", [
    ({"image_id": ""}, "IMG-IMAGE-ID-MISSING"),
    ({"image_id": "sha256:" + "9" * 64}, "IMG-IMAGE-ID-MISMATCH"),
    ({"source_sha": "e" * 40}, "IMG-SOURCE-SHA-MISMATCH"),
    ({"image_filesystem_digest": "sha256:" + "9" * 64}, "IMG-FS-DIGEST-MISMATCH"),
    ({"candidate_id": "other@x"}, "IMG-CANDIDATE-MISMATCH"),
    ({"analysis_tool": ""}, "IMG-TOOL-IDENTITY-MISSING"),
    ({"analysis_method": "TEST_ONLY_DECLARATION", "evidence_method": "TEST_ONLY_DECLARATION"},
     "IMG-INADMISSIBLE-METHOD"),
    ({"import_graph_checksum": ""}, "IMG-GRAPH-CHECKSUM-ABSENT"),
    ({"module_inventory_checksum": ""}, "IMG-INVENTORY-CHECKSUM-ABSENT"),
    ({"generated_utc": "2026-07-10T00:00:00+00:00"}, "IMG-STALE"),
    ({"producer_trust_reference": "not-trusted"}, "IMG-UNTRUSTED-PRODUCER"),
])
def test_r3_binding_and_method_rejects(over, code):
    v = _validate_img(_img_evidence(**over))
    assert not v.accepted and code in v.reason_codes, v.reason_codes


def test_r3_graph_checksum_tamper_rejected():
    e = _img_evidence()
    e["import_graph_checksum"] = "0" * 64  # tamper without touching the graph
    v = _validate_img(e)
    assert not v.accepted and "IMG-GRAPH-CHECKSUM-TAMPER" in v.reason_codes


def test_r3_inventory_checksum_tamper_rejected():
    e = _img_evidence()
    e["module_inventory_checksum"] = "0" * 64
    v = _validate_img(e)
    assert not v.accepted and "IMG-INVENTORY-CHECKSUM-TAMPER" in v.reason_codes


@pytest.mark.parametrize("over,check", [
    ({"import_edges": {"main.py": [f"{P2_PREFIX}0{P2_SUFFIX}"]}}, "AI-PHASE2-IMPORTED-BY-ENTRYPOINT"),
    ({"import_edges": {"main.py": ["config.py"], "config.py": [f"{P2_PREFIX}3{P2_SUFFIX}"]}},
     "AI-PHASE2-IMPORTED-BY-ENTRYPOINT"),
    ({"dynamic_imports": [f"{P2_PREFIX}1{P2_SUFFIX}"]}, "AI-PHASE2-DYNAMIC-IMPORT"),
    ({"runner_registrations": [f"{P2_PREFIX}2{P2_SUFFIX}"]}, "AI-PHASE2-REGISTERED"),
    ({"callback_registrations": [f"{P2_PREFIX}4{P2_SUFFIX}"]}, "AI-PHASE2-REGISTERED"),
    ({"activation_env_defaults": {"HERMES_SSS_SHADOW_ENABLED": "1"}}, "AI-PHASE2-ACTIVATION-ENV-DEFAULT"),
    ({"plugin_discovery": [f"{P2_PREFIX}5{P2_SUFFIX}"]}, "AI-PHASE2-PLUGIN-DISCOVERY"),
])
def test_r3_active_import_variants_rejected(over, check):
    # each recomputes graph/inventory checksums so ONLY the inertness violation is under test.
    v = _validate_img(_img_evidence(**over))
    assert not v.accepted and check in v.reason_codes, v.reason_codes


def test_r3_test_only_declaration_is_not_candidate_proof():
    v = _validate_img(_img_evidence(analysis_method="TEST_ONLY_DECLARATION",
                                    evidence_method="TEST_ONLY_DECLARATION"))
    assert not v.accepted and not v.inert


# =========================================================================== §15 wrapper single readiness path
def test_s15_wrapper_has_single_readiness_path_no_alternate():
    src = (REPO / "tools" / "hermes_stage_b_build_v1.py").read_text(encoding="utf-8")
    # exactly one place advances to CANDIDATE_READY, and it is gated by the trusted evidence chain.
    assert src.count("sm.State.CANDIDATE_READY") == 1
    assert "_build_trusted_chain(" in src
    assert "chain_verdict.ready" in src
    # the immutable context is finalised + acquired before the (fake) build, with no alternate build call.
    assert "imm.finalise()" in src and "imm.acquire(" in src
    assert src.count("runner.build(command)") == 1
    # bare-Boolean evaluator is gone from the wrapper.
    assert "ev.ReadinessInputs" not in src and "ev.evaluate(" not in src
    assert "import design.hermes_fw08_readiness_evaluator_v1 as ev" not in src


def test_s15_full_flow_stages_in_order():
    # the 15-step future flow ordering is encoded by the state machine's non-skippable order.
    import design.hermes_fw08_candidate_state_v1 as sm
    order = sm.state_order()
    assert order.index("SOURCE_VERIFIED") < order.index("CONTEXT_VERIFIED") < order.index("BUILD_READY")
    assert order.index("BUILD_READY") < order.index("BUILD_COMPLETED") < order.index("CANDIDATE_READY")


# =========================================================================== §17 canonical preflight USABLE
def _immutable():
    base = "fe2037c5b4b4836d1038b4c0f734426ac9b3fa02"
    return {"authorised_sha": base, "binding_id": "WO-PR114-FINAL", "canonical_state_sha": base}


def test_s17_canonical_preflight_usable_and_no_docker(tmp_path):
    base = "fe2037c5b4b4836d1038b4c0f734426ac9b3fa02"
    rep = w.run_canonical_preflight(
        source_sha=base, repo_dir=REPO, quarantine_dir=tmp_path / "q",
        now_utc="2026-07-19T00:00:00+00:00", build_utc="2026-07-19T12:00:00+00:00",
        candidate_name="fw08final", dispositions=w.load_governed_dispositions(),
        authorised_sha=base, immutable_source_binding=_immutable(),
    )
    assert rep.terminal_state == "USABLE", rep.reason_codes
    assert rep.runner_constructed is False and rep.docker_invoked is False
    assert rep.image_id is None and rep.tag is None and rep.sbom is None and rep.vuln_scan is None
    assert rep.published is False and rep.deployed is False
    assert len(rep.dispositioned) == 1


def test_s21_default_runner_refuses_no_docker():
    r = w.RefusingDockerRunner()
    with pytest.raises(w.RealDockerInvocationForbidden):
        r.build(("docker", "build"))
    with pytest.raises(w.RealDockerInvocationForbidden):
        r.image_content("sha256:x")
