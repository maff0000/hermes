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
