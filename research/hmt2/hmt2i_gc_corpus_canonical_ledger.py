#!/usr/bin/env python3
"""HMT-2 — governed GC MBP-1 CANONICAL-PROCESSING ledger (canonicalisation checkpoint).

Pure, deterministic, stdlib-only module — mirrors `hmt2h_gc_corpus_ledger.py`'s own purity
discipline exactly (no network, no vendor SDK import of any kind), so this module can be fully
unit-tested with zero network access.

DESIGN DECISION (disclosed, as the dispatch requires) — a SEPARATE, DEDICATED ledger, not an
extension of `hmt2h_gc_corpus_ledger.py`'s acquisition ledger
------------------------------------------------------------------------------------------------
The acquisition ledger's `COMPLETE` state means exactly one thing: native MBP-1 bytes for that
session are durably retained on disk. Overloading that SAME state to also mean "canonicalised"
would make `COMPLETE` ambiguous between two genuinely different durable facts — and, worse, would
require either (a) a backward-incompatible schema change to `hmt2h_gc_corpus_ledger.py`'s already
heavily-tested row shape (`tests/hmt2/test_hmt2h_gc_corpus_ledger.py`,
`tests/hmt2/test_ledger_constants_match_acquisition_call_site.py`), or (b) inventing a THIRD
pseudo-state ("COMPLETE-but-not-really") to paper over the ambiguity. Neither is acceptable.

A dedicated ledger, keyed by the SAME `session_id` the acquisition ledger already uses (never a
new, second identity scheme — see `build_initial_canonical_ledger()` below), keeps the two facts
cleanly separable while still trivially joinable: `SOURCE_STATE_COMPLETE` (acquisition-side) and
`CANONICAL_PENDING` (this ledger) can — and, for 100 of the corpus's 102 already-acquired
sessions, currently DO — coexist for the same session_id. That is the expected, correct state,
not a bug: acquisition and canonicalisation are two separate durable processes with two separate
completion facts.

States (canonicalisation checkpoint, WO-specified names, used verbatim):
    CANONICAL_PENDING     -- native bytes retained (acquisition SOURCE_STATE_COMPLETE); nothing
                             canonicalised yet.
    CANONICAL_IN_PROGRESS -- reserved for symmetry with the acquisition ledger's own
                             `STATE_IN_PROGRESS` (see `hmt2h_gc_corpus_ledger.py`) — like that
                             ledger, THIS checkpoint's own driver
                             (`hmt2i_gc_corpus_canonicalise.py`) never durably persists a row in
                             this state either: a row goes PENDING -> (work happens in memory,
                             including every staging/promotion/reload step) -> COMPLETE or
                             FAILED, with exactly one atomic ledger save at the terminal outcome.
                             This guarantees the WO's own crash-safety requirement by
                             construction: since IN_PROGRESS is never durably written, a crash
                             mid-session can only ever leave the ledger at its last durably-saved
                             state, which is always PENDING.
    CANONICAL_COMPLETE    -- a real (`synthetic=False`) lineage record exists, verified, plus a
                             quality record and an evidence manifest — see
                             `market_truth.acquisition.canonical_worker.canonicalise_mbp1_session()`
                             and `.verify_existing_completion()`.
    CANONICAL_FAILED      -- an ambiguous failure (including a failed re-verification of a
                             previously-claimed-complete session) — never auto-retried.

VALID-EMPTY ARCHITECTURE RULING (additive, v2 schema) — NO NEW LIFECYCLE STATE is introduced by
this ruling: a session that fully, honestly processes to zero canonical events is STILL
`CANONICAL_COMPLETE` at this ledger's level, exactly like any other complete session. What is
new is two additive, optional fields on the row — `canonical_result_kind` ("NONEMPTY" /
"EMPTY_VALID") and `empty_reason` (only meaningful for "EMPTY_VALID") — recorded by
`hmt2i_gc_corpus_canonicalise.py`'s `process_sessions()` alongside the existing lineage/evidence/
quality references, straight from `market_truth.acquisition.canonical_worker.
SessionCanonicalisationResult`. A legacy row that predates this ruling simply carries
`canonical_result_kind=None` — indistinguishable, for every existing consumer, from the row
shape this module always had.
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Dict

STATE_CANONICAL_PENDING = "CANONICAL_PENDING"
STATE_CANONICAL_IN_PROGRESS = "CANONICAL_IN_PROGRESS"
STATE_CANONICAL_COMPLETE = "CANONICAL_COMPLETE"
STATE_CANONICAL_FAILED = "CANONICAL_FAILED"
VALID_CANONICAL_STATES = frozenset(
    {STATE_CANONICAL_PENDING, STATE_CANONICAL_IN_PROGRESS, STATE_CANONICAL_COMPLETE, STATE_CANONICAL_FAILED}
)

CANONICAL_LEDGER_SCHEMA_VERSION = "hmt2i-gc-corpus-canonical-ledger-v2"

# Relative to the DURABLE CANONICAL research root's corpus subdirectory
# (`market_truth.acquisition.canonical_worker.corpus_canonical_store_root()`) — NOT
# research-source/. This ledger's own state lives alongside the canonical/evidence/lineage/
# quality output it governs, never mixed into the acquisition-side research-source/ tree.
CANONICAL_LEDGER_STATE_RELATIVE_PATH = "manifest/gc_corpus_canonical_ledger.json"

# The acquisition ledger's own COMPLETE state name (`hmt2h_gc_corpus_ledger.STATE_COMPLETE`),
# re-declared here as a literal (never imported) to keep this module's only dependency this
# checkpoint needs it to have: none beyond the stdlib — exactly `hmt2h_gc_corpus_ledger.py`'s own
# "deliberately redefined here, not imported" discipline for its own DATASET/SCHEMA/STYPE_IN
# constants. `tests/hmt2/test_hmt2i_gc_corpus_canonical_ledger.py` proves this never drifts from
# the real acquisition ledger's own constant.
SOURCE_STATE_COMPLETE = "COMPLETE"


class CanonicalLedgerError(ValueError):
    """Fails closed on any structurally invalid canonical-ledger operation."""


def build_canonical_ledger_entry(
    *, session_id: str, trade_date: str, acquisition_request_identity: str, native_artefact_sha256: str,
) -> dict:
    """One freshly-seeded `CANONICAL_PENDING` row — no canonical output yet."""
    return {
        "session_id": session_id,
        "trade_date": trade_date,
        "acquisition_request_identity": acquisition_request_identity,
        "native_artefact_sha256_at_seed_time": native_artefact_sha256,
        "state": STATE_CANONICAL_PENDING,
        "lineage_record_relative_path": None,
        "canonical_event_set_hash": None,
        "canonical_partition_relative_paths": None,
        "evidence_manifest_relative_path": None,
        "quality_record_relative_path": None,
        "failure_reason": None,
        "canonical_updated_utc": None,
        # ---- valid-empty architecture ruling additions (additive, v2 schema) ----
        "canonical_result_kind": None,  # "NONEMPTY" | "EMPTY_VALID", set once CANONICAL_COMPLETE
        "empty_reason": None,  # only meaningful when canonical_result_kind == "EMPTY_VALID"
        # ---- governed canonical storage-layout-defect remediation additions (additive, still v2 schema — same no-version-bump-for-purely-additive-optional-fields discipline lineage.py itself already established) ----
        # Set once CANONICAL_COMPLETE, from `canonical_worker.CANONICAL_STORAGE_LAYOUT_VERSION` —
        # `None` means this row's canonical output (if any) predates the namespace-collision fix
        # (the old, unnamespaced `canonical/` layout). See
        # `hmt2i_gc_corpus_canonicalise.process_sessions(force_rebuild_v2=True)`.
        "canonical_storage_layout_version": None,
        # Only ever populated by a `force_rebuild_v2=True` run: the row's own
        # `canonical_event_set_hash` immediately BEFORE this rebuild overwrote it, and whether the
        # freshly-rebuilt value matched it exactly (the full-corpus rebuild's binding regression
        # check).
        "pre_rebuild_v1_event_set_hash": None,
        "v2_rebuild_matches_v1_hash": None,
    }


def build_initial_canonical_ledger(*, acquisition_ledger: Dict[str, dict]) -> Dict[str, dict]:
    """Seed a BRAND NEW canonical ledger: one `CANONICAL_PENDING` row per session whose
    ACQUISITION state is already `SOURCE_STATE_COMPLETE` (native bytes durably retained). A
    session with no retained native bytes yet gets no row at all — it simply has nothing to
    canonicalise (added later, via `merge_new_source_complete_sessions()`, once its own
    acquisition completes)."""
    ledger: Dict[str, dict] = {}
    for session_id, entry in acquisition_ledger.items():
        if entry.get("state") != SOURCE_STATE_COMPLETE:
            continue
        artefact = entry.get("artefact") or {}
        ledger[session_id] = build_canonical_ledger_entry(
            session_id=session_id,
            trade_date=entry["trade_date"],
            acquisition_request_identity=entry["request_identity"],
            native_artefact_sha256=artefact.get("sha256", ""),
        )
    return ledger


def merge_new_source_complete_sessions(*, canonical_ledger: Dict[str, dict], acquisition_ledger: Dict[str, dict]) -> int:
    """Add a fresh `CANONICAL_PENDING` row for any newly-`SOURCE_STATE_COMPLETE` session not
    already present in the canonical ledger — NEVER overwrites an existing row (resumability,
    mirrors `hmt2h_gc_corpus_ledger.build_initial_ledger`'s own "never rebuilds from scratch over
    an existing ledger" discipline). Returns how many rows were added."""
    added = 0
    for session_id, entry in acquisition_ledger.items():
        if entry.get("state") != SOURCE_STATE_COMPLETE:
            continue
        if session_id in canonical_ledger:
            continue
        artefact = entry.get("artefact") or {}
        canonical_ledger[session_id] = build_canonical_ledger_entry(
            session_id=session_id, trade_date=entry["trade_date"],
            acquisition_request_identity=entry["request_identity"], native_artefact_sha256=artefact.get("sha256", ""),
        )
        added += 1
    return added


def load_canonical_ledger(state_path: str) -> Dict[str, dict]:
    if not os.path.exists(state_path):
        return {}
    with open(state_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_canonical_ledger_atomic(state_path: str, ledger: Dict[str, dict]) -> None:
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    tmp_path = state_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(ledger, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp_path, state_path)  # atomic rename on POSIX


def summarize_canonical(ledger: Dict[str, dict]) -> dict:
    counts = {state: 0 for state in VALID_CANONICAL_STATES}
    for entry in ledger.values():
        state = entry["state"]
        if state not in VALID_CANONICAL_STATES:
            raise CanonicalLedgerError(f"session {entry.get('session_id')!r}: invalid canonical state {state!r}")
        counts[state] += 1
    return {"total_sessions": len(ledger), "by_state": counts}


def canonical_ledger_content_sha256(ledger: Dict[str, dict]) -> str:
    """Deterministic hash over the ledger's own current content — used only for the committed
    snapshot evidence file's own integrity self-check, never as a request/artefact identity."""
    canonical = json.dumps(ledger, indent=2, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
