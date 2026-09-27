"""market_truth.acquisition.orchestration.retry -- controlled, explicitly-authorised retry.

Ports the GC-2022-09-13 one-time ledger repair procedure (Trinity handoff,
`reports/incident_retry_mechanisms.md`) as a real, tested mechanism.

Hard constraints, preserved exactly, deliberately NOT relaxed by this dispatch (no ledger schema
change is authorised here):

  * Uses ONLY the existing, unmodified `load_ledger()` / `save_ledger_atomic()` /
    `build_ledger_entry()` functions from the already-in-Git `research/hmt2/hmt2h_gc_corpus_ledger.py`
    -- this module never hand-edits ledger JSON, and never imports that module eagerly at import
    time (it is passed in by the caller as `ledger_module`, so this file stays independently
    unit-testable with a lightweight test double that offers the same three-function surface,
    without needing the real, already-governed acquisition module tree or its own dependencies
    on-path).
  * Requires an explicit, narrowly-scoped `RetryAuthorization` for exactly one `session_id` --
    never a batch, never "retry everything FAILED_AMBIGUOUS".
  * Only ever transitions a row that is currently `FAILED_AMBIGUOUS` -- refuses any other
    starting state.
  * A MANDATORY post-mutation diff proves that only the target row changed; if anything else
    changed, this raises rather than accepting the write.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Protocol


SESSION_ID_RE = re.compile(r"^GC-\d{4}-\d{2}-\d{2}$")


class RetryError(RuntimeError):
    """Raised when a retry authorization, precondition, or post-mutation proof fails."""


@dataclass(frozen=True)
class RetryAuthorization:
    session_id: str
    authorised_by: str
    authorised_utc: str
    reason: str


class LedgerModule(Protocol):
    """The exact, already-in-Git surface this module depends on -- documented here as a
    `Protocol` purely so tests can supply a lightweight double with the same shape, never so
    this module can diverge from the real module's real signatures."""

    def load_ledger(self, state_path: str) -> Dict[str, dict]: ...

    def save_ledger_atomic(self, state_path: str, ledger: Dict[str, dict]) -> None: ...

    def ledger_content_sha256(self, ledger: Dict[str, dict]) -> str: ...


def diff_ledger_rows(before: Mapping[str, dict], after: Mapping[str, dict]) -> List[str]:
    """Sorted list of every session_id whose row differs (added, removed, or changed value)
    between `before` and `after`."""
    changed = []
    all_ids = set(before.keys()) | set(after.keys())
    for sid in all_ids:
        if before.get(sid) != after.get(sid):
            changed.append(sid)
    return sorted(changed)


def validate_authorization(auth: RetryAuthorization) -> None:
    if not SESSION_ID_RE.match(auth.session_id):
        raise RetryError(f"authorization session_id fails grammar check: {auth.session_id!r}")
    if not auth.authorised_by:
        raise RetryError("authorization must name who authorised it (authorised_by)")
    if not auth.authorised_utc:
        raise RetryError("authorization must record when it was authorised (authorised_utc)")


def reset_session_to_planned(
    *,
    ledger_state_path: str,
    authorization: RetryAuthorization,
    ledger_module: LedgerModule,
    fresh_entry_state: str,
    failed_ambiguous_state: str,
    build_fresh_entry_fn: Callable[[dict], dict],
) -> Dict[str, Any]:
    """Resets exactly one `failed_ambiguous_state` row to a fresh `fresh_entry_state` shape.

    `build_fresh_entry_fn(existing_entry) -> new_entry` is caller-supplied and must itself call
    the real, unmodified `hmt2h_gc_corpus_ledger.build_ledger_entry()` (or an equivalent),
    preserving every deterministic request-identity field from `existing_entry` unchanged while
    resetting quote/artefact/actual_cost_usd/failure_reason/ledger_updated_utc -- this function
    does not itself know or enforce the entry schema; it enforces the SAFETY properties around
    the mutation (single row, correct starting state, atomic write via the injected module,
    mandatory post-mutation diff).

    Returns a dict with `before_sha256`, `after_sha256`, and `changed_rows` (always exactly
    `[authorization.session_id]` on success -- `RetryError` is raised otherwise, before any
    write is even attempted where detectable, and immediately after the write if the diff still
    proves a wider mutation).
    """
    validate_authorization(authorization)

    before = ledger_module.load_ledger(ledger_state_path)
    session_id = authorization.session_id
    if session_id not in before:
        raise RetryError(f"unknown session_id, not present in ledger: {session_id!r}")

    existing_entry = before[session_id]
    if existing_entry.get("state") != failed_ambiguous_state:
        raise RetryError(
            f"retry is only ever authorised for a {failed_ambiguous_state!r} row; "
            f"{session_id!r} is currently {existing_entry.get('state')!r}"
        )

    fresh_entry = build_fresh_entry_fn(existing_entry)
    if fresh_entry.get("state") != fresh_entry_state:
        raise RetryError(
            f"build_fresh_entry_fn returned state={fresh_entry.get('state')!r}, expected "
            f"{fresh_entry_state!r} -- refusing to write an unexpected shape"
        )

    before_sha256 = ledger_module.ledger_content_sha256(before)

    after = dict(before)
    after[session_id] = fresh_entry
    ledger_module.save_ledger_atomic(ledger_state_path, after)

    reloaded = ledger_module.load_ledger(ledger_state_path)
    changed_rows = diff_ledger_rows(before, reloaded)
    if changed_rows != [session_id]:
        raise RetryError(
            f"post-mutation diff proves more than the target row changed (or the target row did "
            f"not change): changed_rows={changed_rows!r}, expected=[{session_id!r}]. This ledger "
            f"file may now be in an inconsistent state and must be treated as a STOP-severity "
            f"incident, not silently retried."
        )

    after_sha256 = ledger_module.ledger_content_sha256(reloaded)
    return {
        "session_id": session_id,
        "before_sha256": before_sha256,
        "after_sha256": after_sha256,
        "changed_rows": changed_rows,
        "authorised_by": authorization.authorised_by,
        "authorised_utc": authorization.authorised_utc,
        "reason": authorization.reason,
    }
