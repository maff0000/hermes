"""market_truth.acquisition.orchestration.chunk_allowlist -- ledger-truth-derived session allowlists.

Bounded chunking doctrine (Trinity handoff, `reports/incident_retry_mechanisms.md`): canonical
work is always processed in bounded chunks, each derived FRESH from ledger truth (acquisition-
COMPLETE minus canonical-COMPLETE) at the moment the chunk is built -- NEVER from a hardcoded
ordinal position in a previously-written allowlist file. This is what makes a resumed/retried
chunk stay correct even after an intervening guard-fire or ledger repair changed which sessions
are actually still pending: re-running `derive_pending_sessions()`/`build_chunk()` against the
CURRENT ledger state always reflects reality, with no stale position to fall out of sync.

Every session id is validated against the fixed `GC-YYYY-MM-DD` grammar AND against ledger truth
(must be acquisition-COMPLETE) before it is ever considered eligible -- never trusted purely
because it appears in an operator-supplied allowlist file.
"""
from __future__ import annotations

import json
import re
from typing import Dict, Iterable, List, Mapping, Set, Tuple

SESSION_ID_RE = re.compile(r"^GC-\d{4}-\d{2}-\d{2}$")


class AllowlistError(ValueError):
    """Raised when a session id fails grammar or ledger-truth eligibility validation."""


def validate_session_id(sid: str, acquisition_complete_set: Set[str]) -> str:
    """Ledger truth for canonicalisation eligibility: a session id is only ever a valid
    candidate if (a) it matches the fixed `GC-YYYY-MM-DD` grammar, AND (b) the acquisition
    ledger itself recorded it COMPLETE. Never validated by ordinal position or file order."""
    if not SESSION_ID_RE.match(sid):
        raise AllowlistError(f"session id fails grammar check: {sid!r}")
    if sid not in acquisition_complete_set:
        raise AllowlistError(f"session id not COMPLETE in acquisition ledger truth: {sid!r}")
    return sid


def acquisition_complete_set(acquisition_ledger: Mapping[str, dict], *, source_state_complete: str) -> Set[str]:
    return {sid for sid, rec in acquisition_ledger.items() if rec.get("state") == source_state_complete}


def canonical_complete_set(canonical_ledger: Mapping[str, dict], *, canonical_state_complete: str) -> Set[str]:
    return {sid for sid, rec in canonical_ledger.items() if rec.get("state") == canonical_state_complete}


def derive_pending_sessions(
    *,
    acquisition_ledger: Mapping[str, dict],
    canonical_ledger: Mapping[str, dict],
    source_state_complete: str,
    canonical_state_complete: str,
) -> List[str]:
    """Fresh, ledger-truth-derived pending set: acquisition-COMPLETE minus canonical-COMPLETE,
    sorted deterministically (ascending session id, which is also chronological for the
    `GC-YYYY-MM-DD` grammar). Re-running this against an updated ledger after a guard-fire or a
    retry-repair always reflects the CURRENT state -- there is no cached/stale position anywhere
    in this function."""
    complete_source = acquisition_complete_set(acquisition_ledger, source_state_complete=source_state_complete)
    already_canonical = canonical_complete_set(canonical_ledger, canonical_state_complete=canonical_state_complete)
    return sorted(complete_source - already_canonical)


def build_chunk(
    *,
    acquisition_ledger: Mapping[str, dict],
    canonical_ledger: Mapping[str, dict],
    max_chunk_size: int,
    source_state_complete: str,
    canonical_state_complete: str,
) -> List[str]:
    """The next bounded (`<= max_chunk_size`) chunk of pending sessions, freshly derived. Safe
    to call repeatedly across resumed/retried batches -- see module docstring."""
    if max_chunk_size <= 0:
        raise AllowlistError(f"max_chunk_size must be positive, got {max_chunk_size!r}")
    pending = derive_pending_sessions(
        acquisition_ledger=acquisition_ledger,
        canonical_ledger=canonical_ledger,
        source_state_complete=source_state_complete,
        canonical_state_complete=canonical_state_complete,
    )
    return pending[:max_chunk_size]


def load_requested_session_ids(allowlist_path: str) -> List[str]:
    """Reads an operator-supplied allowlist file (`{"session_ids": [...]}`). This is the ONLY
    thing trusted verbatim from the file -- every id in it is still fully re-validated against
    live ledger truth by `validate_and_partition_allowlist()` before use."""
    with open(allowlist_path, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    session_ids = doc.get("session_ids")
    if not isinstance(session_ids, list) or not session_ids:
        raise AllowlistError(f"{allowlist_path}: 'session_ids' must be a non-empty list")
    return list(session_ids)


def validate_and_partition_allowlist(
    requested: Iterable[str],
    *,
    canonical_ledger: Mapping[str, dict],
    acquisition_ledger: Mapping[str, dict],
    source_state_complete: str,
    canonical_state_complete: str,
) -> Tuple[List[str], List[str]]:
    """Partitions `requested` into `(validated, already_complete)`:

      * `already_complete` -- sessions already `canonical_state_complete` in the canonical
        ledger; these are skipped (never re-validated against the grammar/acquisition check,
        since they are already known-good and re-processing them is a caller decision, not this
        function's).
      * `validated` -- every remaining session, individually checked against
        `validate_session_id()` (grammar + acquisition-COMPLETE membership). Raises
        `AllowlistError` on the FIRST invalid id (fail closed -- an orchestrator must never
        silently drop an invalid id and proceed with a partial batch).
    """
    complete_set = acquisition_complete_set(acquisition_ledger, source_state_complete=source_state_complete)
    validated: List[str] = []
    already_complete: List[str] = []
    for sid in requested:
        rec = canonical_ledger.get(sid)
        state = rec.get("state") if rec else None
        if state == canonical_state_complete:
            already_complete.append(sid)
            continue
        validate_session_id(sid, complete_set)
        validated.append(sid)
    return validated, already_complete
