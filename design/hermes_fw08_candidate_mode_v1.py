#!/usr/bin/env python3
"""HERMES FW-08 F2-R1-C typed candidate-mode contract v1 (PURE, fixture-backed).

WO-HELM-HERMES-FW08-F2-R1-EXTERNAL-PRODUCER-REGISTRY-ROOT-OF-TRUST-IMPLEMENTATION-0001 (§13, F2-R1-C).
Owner: HERMES (Helm). Created (UTC): 2026-07-21. Contract version: 1.

WHY THIS EXISTS (F2-R1-C / AMBER C5). The candidate mode governs whether a build path is TEST-only, an inert
simulation, or a REAL candidate. AUDIT C5 found the wrapper's raw-registry else-branch was not mechanically
excluded from REAL_CANDIDATE mode. The fix requires a TYPED mode, never a bare bool:

  * mode MUST be an exact typed string from CANDIDATE_MODES (never a bare `True`/`False`);
  * mode NEVER defaults to REAL_CANDIDATE — the absence of a mode is treated as inert, not real;
  * `is_real` is the ONLY sanctioned way to ask "is this the real candidate path?" — it returns True ONLY for
    the exact string "REAL_CANDIDATE", so a truthy non-string can never be mistaken for real.

PURE: no I/O, no subprocess, no network, no secrets, stdlib only, deterministic. NOT imported by runtime.
"""
from __future__ import annotations

from typing import Tuple

CONTRACT_VERSION = "1"

# The ONLY valid candidate modes. A bare bool is NOT a valid mode.
CANDIDATE_MODES = frozenset({"TEST_ONLY", "INERT_SIMULATION", "REAL_CANDIDATE"})

# The single mode that denotes a real external candidate. Absence/None/any other value is NOT real.
REAL_CANDIDATE_MODE = "REAL_CANDIDATE"


def is_real(mode: object) -> bool:
    """True ONLY for the exact typed string 'REAL_CANDIDATE'. A bare bool, None, an int, or any other string
    is NOT real. This is the sole sanctioned real-mode test — realness never defaults on."""
    return isinstance(mode, str) and mode == REAL_CANDIDATE_MODE


def require_mode(mode: object) -> Tuple[str, ...]:
    """Fail-closed typed-mode gate. Returns () iff `mode` is an exact string in CANDIDATE_MODES, else
    ('CM-INVALID-MODE',). A bare bool (even True) is REJECTED — the mode must be a typed string, never a
    bool, and never defaults to real."""
    if isinstance(mode, bool):
        return ("CM-INVALID-MODE",)
    if not isinstance(mode, str) or mode not in CANDIDATE_MODES:
        return ("CM-INVALID-MODE",)
    return tuple()
