#!/usr/bin/env python3
"""HERMES FW-08 F2-R3 legacy comparator containment + verification v1 (PURE, fixture-backed).

WO-HELM-HERMES-FW08-F2-R3-EXECUTION-CONTENT-INDEPENDENCE-AND-REAL-IMAGE-PROOF-IMPLEMENTATION-0001 (§9).
Owner: HERMES (Helm). Created (UTC): 2026-07-27. Contract version: 1.

WHY THIS EXISTS (F2-R3 §9). `validate_image_bound_active_import` is the LEGACY comparator: a PURE image/source/
fs/candidate/producer comparator. 14 tests call it DIRECTLY for FIXTURE comparison, so it MUST stay byte-
identical — §9 closure is CONTAINMENT + VERIFICATION, NOT mutation. The risk is not the comparator's logic; it
is a PRODUCTION READINESS path (a wrapper / CLI / dynamic route) calling the raw comparator and treating its
fixture-level verdict as READINESS AUTHORITY. This module contains the comparator: it is declared NOT a
readiness authority, and it statically verifies that the real production wrapper routes readiness ONLY through
the GOVERNED validators, never the raw comparator.

FIXTURE-LEVEL COMPARISON is PRESERVED (the comparator is unchanged and still callable directly). AUTHORITY is
NOT PRESERVED (the comparator is not readiness authority; the real-image-proof assembly refuses a verdict whose
provenance is the raw comparator).

PURE: no I/O, no subprocess, no network, no secrets, stdlib only, deterministic (static AST/source analysis
over a source STRING passed in). NOT imported by runtime; does NOT import the active-import module.
"""
from __future__ import annotations

import ast
from typing import List, Sequence, Tuple

CONTRACT_VERSION = "1"

LEGACY_COMPARATOR_NAME = "validate_image_bound_active_import"
LEGACY_COMPARATOR_STATUS = "CONTAINED_FIXTURE_COMPARISON_ONLY_NOT_READINESS_AUTHORITY"

# The GOVERNED validators the production readiness path is PERMITTED to call.
GOVERNED_READINESS_VALIDATORS = frozenset({
    "validate_active_import_against_bundle",
    "validate_active_import_with_activated_handle",
    "validate_active_import_externally_rooted",
    "_with_activated_handle",
    "_externally_rooted",
})


def comparator_is_readiness_authority() -> bool:
    """The legacy comparator is NOT a readiness authority — it is a fixture-level comparator only. Always
    False."""
    return False


def _called_names(tree: ast.AST) -> List[str]:
    """Every callable NAME/attribute invoked in the tree (both `foo(...)` and `mod.foo(...)`)."""
    names: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name):
                names.append(fn.id)
            elif isinstance(fn, ast.Attribute):
                names.append(fn.attr)
    return names


def _dynamic_dispatch_present(tree: ast.AST) -> bool:
    """Detect a dynamic route that could reach a validator/comparator by a COMPUTED name at runtime — a static
    allow-list cannot vouch for that. Precise, not blunt:
      * eval / exec / __import__ / import_module  -> always a dynamic route (arbitrary code / module by name);
      * getattr(...)                              -> a route ONLY when the attribute name is NOT a constant
        string literal. `getattr(runner, "oci_inspection_evidence", None)` (a fixed attribute fetch) is benign
        and does NOT count; `getattr(mod, computed_name)` (a computed dispatch) does.
    This lets the real Stage-B wrapper — which uses many constant-attribute `getattr(runner, "...")` calls to
    read runner artifacts — pass, while still catching a genuine name-computed dispatch."""
    always = {"__import__", "import_module", "eval", "exec"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        nm = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else "")
        if nm in always:
            return True
        if nm == "getattr":
            # benign iff the attribute-name argument is a constant string literal.
            attr_arg = node.args[1] if len(node.args) >= 2 else None
            if not (isinstance(attr_arg, ast.Constant) and isinstance(attr_arg.value, str)):
                return True
    return False


def assert_no_readiness_bypass(
    *,
    active_import_module: object,
    wrapper_source: str,
) -> Tuple[str, ...]:
    """§9 statically verify the production readiness path does NOT bypass the governed validators. Returns ()
    iff clean, else a sorted tuple of LC-* codes.

    Checks (over the `wrapper_source` string via AST):
      * the wrapper does NOT call the legacy comparator directly     -> LC-WRAPPER-DIRECT-COMPARATOR
      * the wrapper DOES call at least one governed validator        -> LC-CLI-BYPASS (none present = bypass)
      * the wrapper has no dynamic dispatch that could route by name  -> LC-DYNAMIC-ROUTE
    And structurally, the active_import module still EXPOSES the governed validators (else the readiness path
    could not be routing through them) -> LC-CLI-BYPASS."""
    reasons: List[str] = []

    # the governed validators must still exist on the module (containment did not remove them).
    for v in ("validate_active_import_against_bundle", LEGACY_COMPARATOR_NAME):
        if not hasattr(active_import_module, v):
            reasons.append("LC-CLI-BYPASS")
            break

    try:
        tree = ast.parse(wrapper_source)
    except SyntaxError:
        return ("LC-DYNAMIC-ROUTE",)

    called = _called_names(tree)

    if LEGACY_COMPARATOR_NAME in called:
        reasons.append("LC-WRAPPER-DIRECT-COMPARATOR")

    if not any(v in called for v in GOVERNED_READINESS_VALIDATORS):
        reasons.append("LC-CLI-BYPASS")

    if _dynamic_dispatch_present(tree):
        reasons.append("LC-DYNAMIC-ROUTE")

    return tuple(sorted(set(reasons)))


def guard_not_proof(verdict_source: str) -> Tuple[str, ...]:
    """The real-image-proof assembly MUST refuse a verdict whose provenance is the RAW legacy comparator.
    Returns ('LC-COMPARATOR-NOT-PROOF-AUTHORITY',) iff `verdict_source` names the legacy comparator, else ()."""
    if LEGACY_COMPARATOR_NAME in str(verdict_source):
        return ("LC-COMPARATOR-NOT-PROOF-AUTHORITY",)
    return tuple()
