#!/usr/bin/env python3
"""HERMES FW-08 build-context + image-content contract constants v1 (PURE data).

WO-HELM-HERMES-FW08-GOVERNED-STAGE-B-IMAGE-BUILD-ENFORCEMENT-IMPLEMENTATION-0001.
Owner: HERMES (Helm). Created (UTC): 2026-07-19. Contract version: 1.

The declarative contract for what a governed Stage-B candidate image MUST and MUST NOT contain: required
effective-context inclusions (files that must survive .dockerignore and enter the image), required
context-present files (needed to run the build but deliberately COPY-excluded), prohibited effective
patterns (must never enter the image), and the canonical image-content identity (entrypoint/cmd/user/
workdir/port). These constants name the Phase-1 core + Phase-2 modules as INERT REQUIRED FILES ONLY — this
module is inert design data under design/ (an allowed inert referrer), it imports nothing from them and
NO runtime path imports this module.

PURE: no I/O, no subprocess, no network, no secrets, stdlib only, deterministic. NOT imported by runtime.
"""
from __future__ import annotations

import re
from typing import Pattern, Tuple

CONTRACT_VERSION = "1"

# §14 canonical image-content identity (from the canonical Dockerfile).
EXPECTED_ENTRYPOINT: Tuple[str, ...] = ("/app/docker/entrypoint.sh",)
EXPECTED_CMD: Tuple[str, ...] = ("python", "main.py")
EXPECTED_USER = "hermes"
EXPECTED_WORKDIR = "/app"
EXPECTED_PORT = "8210"

# §10 / §14 required effective-context inclusions (must survive .dockerignore and enter the image).
REQUIRED_EFFECTIVE_INCLUSIONS: Tuple[str, ...] = (
    "main.py",
    "signal_builder.py",
    "config.py",
    "env_config.py",
    "requirements.txt",
    "docker/entrypoint.sh",
    "utils/hermes_shared_stream_recovery_v1.py",          # Phase-1 core (required file only)
    "utils/hermes_market_hours_health_v1.py",
    "config/market_hours_schedule.v1.json",
    "adapters/oanda.py",
    "adapters/base.py",
)
# All 10 Phase-2 modules must also survive (matched by prefix/suffix, not named here).
PHASE2_MODULE_PREFIX = "utils/hermes_sss_"
PHASE2_MODULE_SUFFIX = "_v1.py"
REQUIRED_PHASE2_MODULE_COUNT = 10
# Files needed to run the build but deliberately COPY-excluded from the image.
REQUIRED_CONTEXT_PRESENT: Tuple[str, ...] = ("Dockerfile", "requirements.txt", "docker/entrypoint.sh")

# §10 / §14 required effective-context EXCLUSIONS (must NOT enter the image). Regex probes over posix paths.
PROHIBITED_EFFECTIVE_PATTERNS: Tuple[Tuple[str, Pattern], ...] = (
    ("EX-GIT", re.compile(r"(^|/)\.git(/|$)")),
    ("EX-CLAUDE", re.compile(r"(^|/)\.claude(/|$)")),
    ("EX-TESTS", re.compile(r"(^|/)tests(/|$)")),
    ("EX-OPS-EVIDENCE", re.compile(r"(^|/)ops/evidence(/|$)")),
    ("EX-JSONL", re.compile(r"\.jsonl$")),
    ("EX-KEY", re.compile(r"\.(pem|key|crt|p12|pfx)$")),
    ("EX-DB", re.compile(r"\.(sqlite3?|db)$")),
    ("EX-WORKTREES", re.compile(r"(^|/)worktrees(/|$)")),
    ("EX-PID-SOCK", re.compile(r"\.(pid|sock)$")),
    ("EX-ENV", re.compile(r"(^|/)\.env(?!\.example)(\.|$)")),
    ("EX-DOCKERFILE", re.compile(r"^Dockerfile$")),
    ("EX-DOCKERIGNORE", re.compile(r"^\.dockerignore$")),
)
