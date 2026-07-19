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

from dataclasses import dataclass
from typing import Dict, List, Mapping, Sequence, Set, Tuple

CONTRACT_VERSION = "1"

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
