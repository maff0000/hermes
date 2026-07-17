"""HERMES shared-stream recovery Phase-2 — SECURITY REDACTION / FIELD-REJECTION LAYER (PRODUCTION-OWNED, INERT).

WO-HELM-HERMES-SHARED-STREAM-RECOVERY-PHASE2-SHADOW-ADAPTER-IMPLEMENTATION-0001.
Authority: HELM (HERMES market-data lane). Created (UTC): 2026-07-17. Owner: HERMES (Helm).
Contract: docs/design/shared_stream_recovery/architecture_phase2_v1.md §16/§32 (binding). Contract version "1".

STATUS: INERT / NOT WIRED. Imported by NO live runtime path; only tests + sibling utils/hermes_sss_* modules.

A MECHANICAL (not comment-only) validation layer that rejects prohibited fields before any evidence record is
persisted. It scans a payload's KEYS and STRING VALUES for secrets / credentials / raw provider payloads / shell
commands / full account ids / db+redis credential URLs, returning a list of concrete violations. The JSONL writer
refuses to write any payload with a non-empty violation list, so a secret can never reach disk. PURE, deterministic,
standard-library only. No wall-clock, no randomness, no eval.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Mapping, Sequence, Tuple

# Prohibited KEY substrings (case-insensitive). A payload key containing any of these is rejected outright — the
# shadow record schema has no legitimate need for them.
_PROHIBITED_KEY_SUBSTRINGS: Tuple[str, ...] = (
    "token", "password", "passwd", "secret", "api_key", "apikey", "credential", "private_key",
    "access_key", "auth_header", "authorization", "bearer", "account_id", "accountid", "raw_payload",
    "raw_message", "db_password", "redis_password", "connection_string", "conn_string", "dsn",
)

# Prohibited VALUE patterns (credential-bearing connection URLs, bearer tokens, obvious secret prefixes, shell
# command injection markers). Bounded, anchored where possible; no catastrophic backtracking.
_PROHIBITED_VALUE_PATTERNS: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("redis_url_with_credentials", re.compile(r"rediss?://[^/\s:@]+:[^/\s@]+@", re.IGNORECASE)),
    ("sql_url_with_credentials", re.compile(r"(?:mysql|postgres(?:ql)?|mariadb)://[^/\s:@]+:[^/\s@]+@", re.IGNORECASE)),
    ("amqp_url_with_credentials", re.compile(r"amqps?://[^/\s:@]+:[^/\s@]+@", re.IGNORECASE)),
    ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{12,}", re.IGNORECASE)),
    ("openai_style_secret", re.compile(r"\bsk-[A-Za-z0-9]{16,}")),
    ("shell_command_injection", re.compile(r"(?:\$\([^)]+\)|;\s*rm\s+-rf|&&\s*curl\s|\|\s*sh\b)")),
)

# How deep / wide the scan will recurse. Bounded so a malicious oversized payload cannot cause unbounded work.
_MAX_DEPTH = 12
_MAX_NODES = 20000
_MAX_STR_LEN = 100000


class RedactionError(Exception):
    """Raised only by the strict entrypoint when a prohibited field is present."""


@dataclass(frozen=True)
class Violation:
    path: str
    kind: str
    detail: str

    def to_dict(self) -> dict:
        return {"path": self.path, "kind": self.kind, "detail": self.detail}


def scan_for_prohibited(payload: object) -> Tuple[Violation, ...]:
    """Recursively scan a JSON-serialisable payload for prohibited keys/values. Returns a tuple of Violations
    (empty when clean). Bounded depth/nodes/string length. Never raises on ordinary input."""
    violations: List[Violation] = []
    nodes = [0]

    def _key_violation(key: str, path: str) -> None:
        low = key.lower()
        for sub in _PROHIBITED_KEY_SUBSTRINGS:
            if sub in low:
                violations.append(Violation(path=path, kind="prohibited_key", detail=f"key contains {sub!r}"))
                return

    def _value_violation(value: str, path: str) -> None:
        sample = value[:_MAX_STR_LEN]
        for name, pattern in _PROHIBITED_VALUE_PATTERNS:
            if pattern.search(sample):
                violations.append(Violation(path=path, kind=name, detail="value matches a prohibited secret pattern"))

    def _walk(node: object, path: str, depth: int) -> None:
        nodes[0] += 1
        if depth > _MAX_DEPTH or nodes[0] > _MAX_NODES:
            violations.append(Violation(path=path, kind="payload_too_large", detail="scan bound exceeded -> reject"))
            return
        if isinstance(node, Mapping):
            for k, v in node.items():
                child = f"{path}.{k}" if path else str(k)
                if isinstance(k, str):
                    _key_violation(k, child)
                _walk(v, child, depth + 1)
        elif isinstance(node, (list, tuple)):
            for idx, item in enumerate(node):
                _walk(item, f"{path}[{idx}]", depth + 1)
        elif isinstance(node, str):
            _value_violation(node, path)
        # numbers / bools / None carry no secret surface.

    _walk(payload, "", 0)
    return tuple(violations)


def is_clean(payload: object) -> bool:
    """True when the payload contains no prohibited field."""
    return len(scan_for_prohibited(payload)) == 0


def assert_clean(payload: object) -> None:
    """Strict entrypoint: raise RedactionError if any prohibited field is present. Used where a violation must be
    loud rather than silently dropped."""
    violations = scan_for_prohibited(payload)
    if violations:
        summary = "; ".join(f"{v.path}:{v.kind}" for v in violations[:8])
        raise RedactionError(f"prohibited field(s) present: {summary}")
