#!/usr/bin/env python3
"""HERMES FW-08 Phase-2 active-import evidence contract v1 (PURE, fixture-backed).

WO-HELM-HERMES-PR114-FW08-EXACT-AUDIT-CORRECTIONS-AND-CANONICAL-PREFLIGHT-CLOSURE-0001 (§13).
Owner: HERMES (Helm). Created (UTC): 2026-07-19. Contract version: 1.

WHY THIS EXISTS. A governed Stage-B candidate image must ship the Phase-2 modules PRESENT-BUT-INERT — present
on the image filesystem, but NOT imported by the startup entrypoint, NOT registered as a runner/callback,
NOT reachable via a dynamic import, NOT enabled by an activation env default, and NOT surfaced through a
plugin-discovery mechanism. This module defines the machine-readable EVIDENCE a future REAL image must
provide (a static import graph derived from the image filesystem / an entrypoint transitive-import scan / a
module-loader trace produced by NON-RUNNING inspection) and validates that the graph proves inertness.

This WO ships NO image; the validator is exercised against FIXTURE evidence records only. A record that
declares itself merely `TEST_ONLY_DECLARATION` is NOT accepted as proof of inertness.

PURE: no I/O, no subprocess, no network, no secrets, stdlib only, deterministic. NOT imported by runtime.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Sequence, Set, Tuple

CONTRACT_VERSION = "1"
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_UTC_ISO_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?(\+00:00|Z)$")

# Inspection methods that constitute admissible proof (derived from the actual candidate image, off-line).
ADMISSIBLE_METHODS = frozenset({
    "STATIC_IMPORT_GRAPH",              # parsed import graph from the image filesystem
    "ENTRYPOINT_TRANSITIVE_IMPORT_SCAN",  # transitive import closure from the startup entrypoint
    "MODULE_LOADER_TRACE_NON_RUNNING",    # import-loader trace captured without starting the service
})
# Explicitly INADMISSIBLE: a self-declaration with no inspected graph.
INADMISSIBLE_METHODS = frozenset({"TEST_ONLY_DECLARATION", "ASSERTION_ONLY", ""})


@dataclass(frozen=True)
class ActiveImportVerdict:
    inert: bool
    reason_codes: Tuple[str, ...]
    present_phase2: Tuple[str, ...]
    reachable_phase2: Tuple[str, ...]

    def to_dict(self) -> Dict[str, object]:
        return {
            "contract_version": CONTRACT_VERSION,
            "inert": self.inert,
            "reason_codes": list(self.reason_codes),
            "present_phase2": list(self.present_phase2),
            "reachable_phase2": list(self.reachable_phase2),
        }


def _transitive_closure(entrypoint: str, edges: Mapping[str, Sequence[str]]) -> Set[str]:
    """All modules reachable from `entrypoint` following import edges (BFS, cycle-safe, bounded)."""
    seen: Set[str] = set()
    frontier: List[str] = [entrypoint]
    while frontier:
        cur = frontier.pop()
        for nxt in edges.get(cur, ()):  # missing node -> no outgoing edges
            if nxt not in seen:
                seen.add(nxt)
                frontier.append(nxt)
    return seen


def _is_phase2(module: str, prefix: str, suffix: str) -> bool:
    return module.startswith(prefix) and module.endswith(suffix)


def validate_phase2_inert(
    evidence: Mapping[str, object],
    *,
    phase2_prefix: str,
    phase2_suffix: str,
    required_phase2_count: int,
) -> ActiveImportVerdict:
    """Validate a Phase-2 active-import evidence record from a candidate image. Returns inert=True ONLY if
    every expected Phase-2 module is present AND none is imported by the entrypoint (directly OR
    transitively), registered as a runner/callback, dynamically imported, enabled by an activation env
    default, or exposed by plugin discovery — and the evidence method is admissible (not a self-declaration)."""
    reasons: List[str] = []

    if str(evidence.get("contract_version")) != CONTRACT_VERSION:
        reasons.append("AI-CONTRACT-VERSION")
    method = str(evidence.get("evidence_method", ""))
    if method in INADMISSIBLE_METHODS or method not in ADMISSIBLE_METHODS:
        reasons.append("AI-INADMISSIBLE-METHOD")   # test-only / assertion-only declarations rejected

    modules = [str(m) for m in _seq(evidence.get("modules"))]
    module_set = set(modules)
    entrypoint = str(evidence.get("entrypoint_module", ""))
    edges: Dict[str, List[str]] = {}
    raw_edges = evidence.get("import_edges")
    if isinstance(raw_edges, Mapping):
        for k, v in raw_edges.items():
            edges[str(k)] = [str(x) for x in _seq(v)]

    present_phase2 = tuple(sorted(m for m in module_set if _is_phase2(m, phase2_prefix, phase2_suffix)))
    if len(present_phase2) < required_phase2_count:
        reasons.append("AI-PHASE2-MODULES-MISSING")

    if not entrypoint:
        reasons.append("AI-ENTRYPOINT-MISSING")

    # Direct + transitive import from the startup entrypoint.
    reachable = _transitive_closure(entrypoint, edges) if entrypoint else set()
    reachable_phase2 = tuple(sorted(m for m in reachable if _is_phase2(m, phase2_prefix, phase2_suffix)))
    if reachable_phase2:
        reasons.append("AI-PHASE2-IMPORTED-BY-ENTRYPOINT")

    # Runner / callback registrations.
    registered = [str(x) for x in _seq(evidence.get("runner_registrations"))]
    registered += [str(x) for x in _seq(evidence.get("callback_registrations"))]
    if any(_is_phase2(m, phase2_prefix, phase2_suffix) for m in registered):
        reasons.append("AI-PHASE2-REGISTERED")

    # Dynamic imports (importlib / __import__ / config-driven).
    dynamic = [str(x) for x in _seq(evidence.get("dynamic_imports"))]
    if any(_is_phase2(m, phase2_prefix, phase2_suffix) for m in dynamic):
        reasons.append("AI-PHASE2-DYNAMIC-IMPORT")

    # Activation env defaults — any default that would enable Phase-2 at startup.
    env_defaults = evidence.get("activation_env_defaults")
    if isinstance(env_defaults, Mapping):
        for k, v in env_defaults.items():
            if _enables(v):
                reasons.append("AI-PHASE2-ACTIVATION-ENV-DEFAULT")
                break

    # Hidden plugin discovery.
    plugins = [str(x) for x in _seq(evidence.get("plugin_discovery"))]
    if plugins:
        # ANY plugin-discovery mechanism that can surface Phase-2 is rejected (present-but-inert means no
        # discovery path at all).
        if any(_is_phase2(m, phase2_prefix, phase2_suffix) for m in plugins) or evidence.get(
                "plugin_discovery_enabled"):
            reasons.append("AI-PHASE2-PLUGIN-DISCOVERY")

    return ActiveImportVerdict(
        inert=(len(reasons) == 0),
        reason_codes=tuple(sorted(set(reasons))),
        present_phase2=present_phase2,
        reachable_phase2=reachable_phase2,
    )


def _seq(v: object) -> Tuple[object, ...]:
    return tuple(v) if isinstance(v, (list, tuple)) else ()


def _enables(v: object) -> bool:
    """A default that would ACTIVATE Phase-2 (truthy / '1' / 'true' / 'on' / 'enabled')."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "on", "yes", "enabled", "enable", "active")
    return False


# =========================================================== R-3 §12/§13 IMAGE-BOUND active-import evidence
# WO-HELM-HERMES-PR114-FW08-FINAL-PREBUILD-TRUST-BOUNDARY-CORRECTIONS-0001.
# R2D2 R-3: active-import evidence must bind to the ACTUAL candidate image (image_id + source_sha +
# filesystem digest) and to a TRUSTED producer, and must carry a checksummed import graph + module inventory
# derived by a NON-RUNNING analysis of the exported image filesystem. A fixture that merely DECLARES inertness
# is NOT accepted as proof.

# Required string fields for an image-bound record. `tool_digest` is recorded "where practical" and is
# therefore optional; a MISSING tool IDENTITY (name/version) is fatal.
_IMG_REQUIRED = (
    "contract_version", "application", "candidate_id", "image_id", "source_sha",
    "entrypoint", "command", "analysis_method", "analysis_tool", "analysis_tool_version",
    "import_graph_checksum", "module_inventory_checksum", "generated_utc",
    "producer_trust_reference", "result",
)


def import_graph_checksum(import_edges: Mapping[str, Sequence[str]]) -> str:
    """Deterministic checksum over the import graph (canonicalised: sorted nodes + sorted edge targets)."""
    canon = {str(k): sorted(str(x) for x in _seq(v)) for k, v in dict(import_edges).items()}
    blob = json.dumps(canon, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def module_inventory_checksum(modules: Sequence[str]) -> str:
    """Deterministic checksum over the sorted, de-duplicated module inventory."""
    blob = json.dumps(sorted(set(str(m) for m in modules)), separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _parse_utc(value: str) -> Optional[datetime.datetime]:
    if not isinstance(value, str) or not _UTC_ISO_RE.match(value):
        return None
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass(frozen=True)
class ImageBoundImportVerdict:
    accepted: bool
    inert: bool
    reason_codes: Tuple[str, ...]
    present_phase2: Tuple[str, ...]
    reachable_phase2: Tuple[str, ...]

    def to_dict(self) -> Dict[str, object]:
        return {
            "contract_version": CONTRACT_VERSION,
            "accepted": self.accepted,
            "inert": self.inert,
            "reason_codes": list(self.reason_codes),
            "present_phase2": list(self.present_phase2),
            "reachable_phase2": list(self.reachable_phase2),
        }


def validate_image_bound_active_import(
    evidence: Mapping[str, object],
    *,
    expected_image_id: str,
    expected_source_sha: str,
    expected_fs_digest: str,
    expected_candidate_id: str,
    trusted_producer_refs: Sequence[str],
    now_utc: str,
    phase2_prefix: str,
    phase2_suffix: str,
    required_phase2_count: int,
    max_age_hours: int = 24,
    application: str = "hermes",
) -> ImageBoundImportVerdict:
    """R-3 §12/§13. Validate active-import evidence that is BOUND to the actual candidate image. `accepted` is
    True ONLY if EVERY binding matches (image_id / source_sha / filesystem digest / candidate / trusted
    producer), the analysis method is admissible (not a self-declaration), the analysis TOOL identity is
    present, the import-graph AND module-inventory checksums are present and recompute EXACTLY (rejects
    tamper), and the evidence is fresh. `inert` additionally requires the graph to prove Phase-2 is
    present-but-inert (delegated to validate_phase2_inert)."""
    reasons: List[str] = []
    g = evidence  # alias

    missing = [k for k in _IMG_REQUIRED if not str(g.get(k, "")).strip()]
    if missing:
        reasons.append("IMG-MISSING-FIELDS:" + ",".join(sorted(missing)))

    if str(g.get("contract_version")) != CONTRACT_VERSION:
        reasons.append("IMG-CONTRACT-VERSION")
    if str(g.get("application")) != application:
        reasons.append("IMG-APPLICATION-MISMATCH")

    # candidate / image / source / filesystem-digest binding to the ACTUAL image.
    if str(g.get("candidate_id")) != expected_candidate_id:
        reasons.append("IMG-CANDIDATE-MISMATCH")
    img = str(g.get("image_id", ""))
    if not img:
        reasons.append("IMG-IMAGE-ID-MISSING")
    elif img != expected_image_id:
        reasons.append("IMG-IMAGE-ID-MISMATCH")
    if str(g.get("source_sha")) != expected_source_sha:
        reasons.append("IMG-SOURCE-SHA-MISMATCH")

    fs_digest = str(g.get("image_filesystem_digest") or g.get("manifest_digest") or "")
    if not fs_digest:
        reasons.append("IMG-FS-DIGEST-MISSING")
    elif fs_digest != expected_fs_digest:
        reasons.append("IMG-FS-DIGEST-MISMATCH")

    # trusted producer.
    if str(g.get("producer_trust_reference")) not in set(str(x) for x in trusted_producer_refs):
        reasons.append("IMG-UNTRUSTED-PRODUCER")

    # analysis method admissible + not self-attested; tool identity present.
    method = str(g.get("analysis_method", ""))
    if method in INADMISSIBLE_METHODS or method not in ADMISSIBLE_METHODS:
        reasons.append("IMG-INADMISSIBLE-METHOD")
    if not str(g.get("analysis_tool", "")).strip() or not str(g.get("analysis_tool_version", "")).strip():
        reasons.append("IMG-TOOL-IDENTITY-MISSING")

    # entrypoint / command must be present.
    if not str(g.get("entrypoint", "")).strip():
        reasons.append("IMG-ENTRYPOINT-MISSING")
    if not str(g.get("command", "")).strip():
        reasons.append("IMG-COMMAND-MISSING")

    # graph + inventory checksums present AND recompute exactly (rejects tamper).
    edges_raw = g.get("import_edges")
    edges: Dict[str, List[str]] = {}
    if isinstance(edges_raw, Mapping):
        for k, v in edges_raw.items():
            edges[str(k)] = [str(x) for x in _seq(v)]
    modules = [str(m) for m in _seq(g.get("modules"))]

    declared_graph = str(g.get("import_graph_checksum", ""))
    if not _HEX64_RE.match(declared_graph):
        reasons.append("IMG-GRAPH-CHECKSUM-ABSENT")
    elif declared_graph != import_graph_checksum(edges):
        reasons.append("IMG-GRAPH-CHECKSUM-TAMPER")

    declared_inv = str(g.get("module_inventory_checksum", ""))
    if not _HEX64_RE.match(declared_inv):
        reasons.append("IMG-INVENTORY-CHECKSUM-ABSENT")
    elif declared_inv != module_inventory_checksum(modules):
        reasons.append("IMG-INVENTORY-CHECKSUM-TAMPER")

    # freshness.
    gen = _parse_utc(str(g.get("generated_utc", "")))
    now = _parse_utc(now_utc)
    if gen is None or now is None:
        reasons.append("IMG-UTC-INVALID")
    else:
        if (now - gen) > datetime.timedelta(hours=max_age_hours):
            reasons.append("IMG-STALE")
        if gen > now:
            reasons.append("IMG-FUTURE-DATED")

    # Inertness proof over the same graph (present-but-inert).
    inert_verdict = validate_phase2_inert(
        g, phase2_prefix=phase2_prefix, phase2_suffix=phase2_suffix,
        required_phase2_count=required_phase2_count,
    )
    if not inert_verdict.inert:
        reasons.extend(inert_verdict.reason_codes)

    accepted = (len(reasons) == 0)
    return ImageBoundImportVerdict(
        accepted=accepted,
        inert=(accepted and inert_verdict.inert),
        reason_codes=tuple(sorted(set(reasons))),
        present_phase2=inert_verdict.present_phase2,
        reachable_phase2=inert_verdict.reachable_phase2,
    )


# =========================================================== F-2 §11 INDEPENDENT ANCHOR-BOUND active import
# WO-HELM-HERMES-FW08-F2-INDEPENDENT-ACTIVE-IMPORT-ANCHORS-IMPLEMENTATION-0001.
# F-2 defect: the Stage-B wrapper sourced the EXPECTED filesystem digest + trusted producer FROM THE
# ACTIVE-IMPORT EVIDENCE RECORD ITSELF and fed them straight back into `validate_image_bound_active_import`
# — a tautology (a record proves its own truth by repeating a value). The correction here derives the
# expected anchors from an INDEPENDENT `IndependentAnchorBundle` (governed build-result + OCI inspection +
# producer registry), never from the evidence. `validate_image_bound_active_import` (above) is unchanged and
# remains a PURE comparator; this wrapper structurally guarantees its `expected_*` arguments come from an
# independent governed source.
#
# STRUCTURAL GUARDS (each rejects; none can be bypassed by mutating the evidence):
#   * the bundle must be a real IndependentAnchorBundle (passing the evidence Mapping is rejected);
#   * the registry must be a real ProducerRegistry (an inline dict is rejected);
#   * the bundle must NOT be derived from active-import evidence;
#   * the evidence must NOT carry any expected-authority field;
#   * the evidence must not be the same object as the bundle or either anchor;
#   * the active-import producer must resolve through the registry AND differ from the build/oci producers.
# The new anchor/bundle/registry modules do NOT import this module (import them lazily to avoid any cycle).

_EXPECTED_AUTHORITY_KEYS = frozenset({
    "expected_image_id", "expected_fs_digest", "expected_manifest_digest", "expected_producer",
    "expected_candidate_id", "trusted_producer_refs",
})


def validate_active_import_against_bundle(
    evidence: Mapping[str, object],
    *,
    anchor_bundle: object,
    producer_registry: object,
    now_utc: str,
    phase2_prefix: str,
    phase2_suffix: str,
    required_phase2_count: int,
    max_age_hours: int = 24,
    application: str = "hermes",
) -> ImageBoundImportVerdict:
    """F-2 §11. Validate active-import evidence against an INDEPENDENT anchor bundle. `accepted` is True ONLY
    if every structural guard passes AND the delegated pure comparator
    (`validate_image_bound_active_import`, with EXPECTED values derived from the bundle's anchors) accepts.
    The expected image id / source / filesystem digest / candidate / trusted producer are derived FROM THE
    BUNDLE, never from `evidence`."""
    import design.hermes_fw08_anchor_bundle_v1 as _ab
    import design.hermes_fw08_producer_registry_v1 as _pr

    # STRUCTURAL guard 1: the bundle must be the governed independent bundle type.
    if not isinstance(anchor_bundle, _ab.IndependentAnchorBundle):
        return ImageBoundImportVerdict(False, False, ("AI2-BUNDLE-NOT-INDEPENDENT",), (), ())
    # STRUCTURAL guard 2: the registry must be the governed registry type (an inline dict is rejected).
    if not isinstance(producer_registry, _pr.ProducerRegistry):
        return ImageBoundImportVerdict(False, False, ("AI2-REGISTRY-NOT-GOVERNED",), (), ())

    reasons: List[str] = []

    # STRUCTURAL guard 3: the bundle must not be self-derived from active-import evidence.
    if anchor_bundle.provenance.derived_from_active_import is not False:
        reasons.append("AI2-BUNDLE-SELF-DERIVED")

    # STRUCTURAL guard 4: the evidence must not carry expected-authority fields (no tautological injection).
    if isinstance(evidence, Mapping):
        if _EXPECTED_AUTHORITY_KEYS & set(evidence.keys()):
            reasons.append("AI2-EVIDENCE-SUPPLIES-EXPECTED")
    else:
        reasons.append("AI2-EVIDENCE-NOT-MAPPING")

    # STRUCTURAL guard 5: the evidence must not BE the bundle or an anchor (identity aliasing).
    if evidence is anchor_bundle or evidence is anchor_bundle.image_identity_anchor \
            or evidence is anchor_bundle.image_filesystem_anchor:
        reasons.append("AI2-EVIDENCE-IS-ANCHOR")

    # Producer authority: resolve the active-import producer through the registry (never via the evidence's
    # repeated value), and require independence from the build + oci producers.
    producer_id = str(evidence.get("producer_trust_reference", "")) if isinstance(evidence, Mapping) else ""
    resolved, _rr = producer_registry.resolve(
        producer_id=producer_id, evidence_type="ACTIVE_IMPORT", gate_id="STAGE_B_ACTIVE_IMPORT",
        application=application, now_utc=now_utc,
    )
    resolved_pid: Optional[str] = None
    if resolved is None:
        reasons.append("AI2-PRODUCER-UNAUTHORISED")
    else:
        resolved_pid = resolved.producer_id
        prov = anchor_bundle.provenance
        if resolved_pid in (prov.build_producer_id, prov.oci_producer_id):
            reasons.append("AI2-PRODUCER-NOT-INDEPENDENT")

    if reasons:
        return ImageBoundImportVerdict(False, False, tuple(sorted(set(reasons))), (), ())

    # Expected anchors derived FROM THE BUNDLE ONLY, then delegated to the unchanged pure comparator.
    a_ii = anchor_bundle.image_identity_anchor
    a_fs = anchor_bundle.image_filesystem_anchor
    verdict = validate_image_bound_active_import(
        evidence,
        expected_image_id=a_ii.image_id,
        expected_source_sha=a_ii.source_sha,
        expected_fs_digest=a_fs.filesystem_digest,
        expected_candidate_id=a_ii.candidate_id,
        trusted_producer_refs=(resolved_pid,) if resolved_pid else (),
        now_utc=now_utc,
        phase2_prefix=phase2_prefix,
        phase2_suffix=phase2_suffix,
        required_phase2_count=required_phase2_count,
        max_age_hours=max_age_hours,
        application=application,
    )
    return ImageBoundImportVerdict(
        accepted=verdict.accepted,
        inert=verdict.inert,
        reason_codes=verdict.reason_codes,
        present_phase2=verdict.present_phase2,
        reachable_phase2=verdict.reachable_phase2,
    )


# =========================================================== F2-R1 §16 ACTIVATED-HANDLE candidate readiness
# WO-HELM-HERMES-FW08-F2-R1-EXTERNAL-PRODUCER-REGISTRY-ROOT-OF-TRUST-IMPLEMENTATION-0001.
# F2-R1 defect: `validate_active_import_against_bundle` (above) accepts a RAW ProducerRegistry — but a
# registry's checksum proves internal INTEGRITY, not external AUTHENTICITY. A caller can mint a fresh
# registry, invent an approver, register real-authority producers, freeze it, recompute every digest, and hand
# it in. This entry point accepts a registry ONLY via an ACTIVATED HANDLE minted (elsewhere) by resolution
# through a governed external authority boundary and sealed by an ActivationAuthority key the caller does not
# hold. A caller-built lookalike handle fails the seal check (AH-SEAL-INVALID). The handle-module is imported
# LAZILY to avoid any import cycle; this function does NOT alter `validate_active_import_against_bundle`.


def validate_active_import_with_activated_handle(
    evidence: Mapping[str, object],
    *,
    anchor_bundle: object,
    activated_handle: object,
    activation_authority: object,
    now_utc: str,
    phase2_prefix: str,
    phase2_suffix: str,
    required_phase2_count: int,
    real_candidate_mode: bool = False,
    max_age_hours: int = 24,
    application: str = "hermes",
) -> ImageBoundImportVerdict:
    """F2-R1 §16. Accept a producer registry ONLY via a trusted `ActivatedRegistryHandle`, then DELEGATE to
    the unchanged `validate_active_import_against_bundle`. Rejects (fail-closed):
      * a raw ProducerRegistry passed as the handle → AI3-RAW-REGISTRY-NOT-SUFFICIENT;
      * an untrusted handle → AI3-HANDLE-<reason> (e.g. AI3-HANDLE-AH-SEAL-INVALID);
      * a TEST_ONLY handle used in real_candidate_mode → AI3-SYNTHETIC-HANDLE-IN-REAL-MODE;
      * an application mismatch → AI3-HANDLE-APPLICATION-MISMATCH;
      * a handle whose bound registry's digest diverges → AI3-HANDLE-REGISTRY-DIGEST-MISMATCH."""
    import design.hermes_fw08_activated_registry_handle_v1 as _arh
    import design.hermes_fw08_producer_registry_v1 as _pr

    if isinstance(activated_handle, _pr.ProducerRegistry):
        return ImageBoundImportVerdict(False, False, ("AI3-RAW-REGISTRY-NOT-SUFFICIENT",), (), ())

    trusted, reasons = _arh.is_trusted(
        activated_handle, activation_authority=activation_authority, now_utc=now_utc)
    if not trusted:
        return ImageBoundImportVerdict(
            False, False, tuple(sorted("AI3-HANDLE-" + r for r in reasons)), (), ())

    if real_candidate_mode and activated_handle.candidate_use_policy == "TEST_ONLY":
        return ImageBoundImportVerdict(False, False, ("AI3-SYNTHETIC-HANDLE-IN-REAL-MODE",), (), ())

    if activated_handle.application != application:
        return ImageBoundImportVerdict(False, False, ("AI3-HANDLE-APPLICATION-MISMATCH",), (), ())

    bound_registry, br_reasons = _arh.get_bound_registry(
        activated_handle, activation_authority=activation_authority, now_utc=now_utc)
    if bound_registry is None:
        return ImageBoundImportVerdict(
            False, False, tuple(sorted("AI3-HANDLE-" + r for r in br_reasons)), (), ())

    if bound_registry.canonical_digest() != activated_handle.registry_digest:
        return ImageBoundImportVerdict(False, False, ("AI3-HANDLE-REGISTRY-DIGEST-MISMATCH",), (), ())

    # DELEGATE to the unchanged pure comparator with the AUTHENTICATED bound registry.
    return validate_active_import_against_bundle(
        evidence,
        anchor_bundle=anchor_bundle,
        producer_registry=bound_registry,
        now_utc=now_utc,
        phase2_prefix=phase2_prefix,
        phase2_suffix=phase2_suffix,
        required_phase2_count=required_phase2_count,
        max_age_hours=max_age_hours,
        application=application,
    )
