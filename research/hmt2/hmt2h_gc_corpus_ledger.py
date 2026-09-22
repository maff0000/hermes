#!/usr/bin/env python3
"""HMT-2 (real-money checkpoint) — governed GC MBP-1 corpus acquisition ledger (HMT-2 Part 5+).

Pure, deterministic, stdlib-only module. No network, no provider dependency, no vendor SDK
import of any kind — mirrors `market_truth/acquisition/mbp1_quote_request.py`'s own purity
discipline exactly, so this module can be fully unit-tested with zero network access.

PURPOSE — this checkpoint extends the governed MBP-1 pilot (`hmt2e_mbp1_pilot_acquire.py`,
already CLOSED for its 2 pilot sessions `GC-2019-03-22`/`GC-2019-03-29`) to the REMAINING 446
sessions of the frozen 448-session corpus-selection manifest v2. It does NOT change, weaken, or
duplicate any already-governed acquisition logic:

  - The one, single, per-session real-acquisition call site remains
    `DatabentoHistoricalProvider.acquire_mbp1_pilot_session_data()` — UNCHANGED, not touched by
    this checkpoint at all (it is already fully generic over `session_id`/`symbols`/`start`/
    `end`/`path`; only its *docstring name* still says "pilot" — renaming it was considered and
    REJECTED: it is exercised by call-site-hardcoding AST guards
    (`tests/hmt2/test_hardcoded_acquisition_call_sites.py`,
    `tests/hmt2/test_no_network_guard.py`) that pin its exact name; renaming it would be a
    disproportionately risky change to a already-audited, already-tested real-money code path
    for a purely cosmetic gain — see this checkpoint's own final report for the explicit
    disclosure of this judgment call).
  - The one, single, per-session orchestration function that calls it —
    `hmt2e_mbp1_pilot_acquire.acquire_one_session()` — is likewise UNCHANGED and imported
    (never copied/re-implemented) by the new driver script (`hmt2h_gc_corpus_acquire.py`) that
    consumes this ledger. It was ALREADY fully generic (session_id/trade_date/
    active_raw_symbols/start/end/provider/store/catalogue — nothing pilot-specific in its own
    logic, only in the file/module NAME it happens to live in), so "generalizing" it meant
    reusing it as-is against a wider session set, not modifying it.
  - `compute_request_identity()` (`market_truth/acquisition/source_store.py`) — UNCHANGED,
    reused exactly, including its symbol-order-independence and its role as the sole
    duplicate-request-reuse key. This ledger computes the SAME identity, over the SAME fields,
    for every one of the 448 governed sessions (the 2 already-acquired pilot sessions included)
    — so the pilot's own two already-catalogued artefacts are found and reused automatically,
    with no special-casing beyond "look them up like anything else".
  - The single shared on-disk catalogue
    (`research-source/hmt2-gc-mbp1-v1/manifest/mbp1_pilot_acquisition_catalogue.json`,
    `market_truth.acquisition.source_store.manifest_relative_path("mbp1_pilot_acquisition_
    catalogue.json")`) is reused UNCHANGED and is the SAME catalogue `acquire_one_session()`
    already reads/writes for the pilot — this is what "the ledger/reuse-guard correctly skips
    [the 2 pilot sessions]" means at the file level: they are already present in that catalogue,
    keyed by their own already-computed `request_identity`, so a lookup by this ledger's own
    (identically-computed) identity for those 2 sessions finds them immediately.

WHAT THIS MODULE ADDS (genuinely new, HMT-2 Part 5+) — the LEDGER itself: a durable, per-session
tracking row for every one of the 448 governed sessions (`gc-session-contract-activity-v1.json`)
covering state (`PLANNED`/`IN_PROGRESS`/`COMPLETE`/`FAILED_AMBIGUOUS`), the free per-session
quote (obtained fresh, per-session, immediately before that session's real acquisition — never
skipped just because an aggregate corpus-wide quote already exists, per WO), and — once
acquired — the native artefact's own identity (relative path/byte size/sha256/record count/
acquisition UTC). See `hmt2h_gc_corpus_acquire.py` (the network-capable driver script, NOT this
module) for how these rows are actually populated and advanced.

STORAGE — structure/schema (this file) is committed to git normally. The actual PER-REQUEST
LEDGER STATE (which sessions are PLANNED/COMPLETE/etc, their quotes, their artefact identities)
lives in a local, gitignored JSON file under `research-source/` (`.gitignore`'s own blanket
`research-source/` rule already covers it — see that file's comment: "Evidence of what was
retained ... is committed separately in tracked JSON under research/hmt2/"), at
`LEDGER_STATE_RELATIVE_PATH` below, alongside the native `.dbn.zst` artefacts and the existing
pilot catalogue it reuses. A resumable, git-committed SNAPSHOT of the ledger's state (counts,
running spend, which sessions are still PLANNED) is written separately, as ordinary tracked
JSON evidence under `research/hmt2/` — see `hmt2h_gc_corpus_acquire.py`'s own snapshot-writing
step — exactly mirroring the existing `hmt2c-mbp1-quote-evidence-v1.json` /
`hmt2d-mbp1-pilot-selection-and-quote-evidence-v1.json` evidence-file convention.

RESUMABILITY (a follow-up dispatch) — reads `LEDGER_STATE_RELATIVE_PATH` (via `load_ledger()`)
if it already exists (never rebuilds from scratch over an existing ledger — `build_initial_
ledger()` is only ever used to seed a BRAND NEW ledger file, and the driver script calls it only
when no ledger file exists yet), finds every row still `PLANNED`, and continues from there —
sequential, one session at a time, exactly as this checkpoint's first dispatch did. Nothing about
resuming requires re-reading this module's own docstring or any human memory: the ledger file
itself is the single source of truth for "what has and has not been done".
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

# ------------------------------------------------------------------------------------------
# Governed request-shape constants — deliberately redefined here (not imported from
# `hmt2e_mbp1_pilot_acquire.py`) so this module stays a pure, network-free, zero-vendor-import
# leaf module (mirrors `mbp1_quote_request.py`'s own purity discipline) — importing hmt2e here
# would pull `DatabentoHistoricalProvider`/`databento` transitively into what must remain a
# fully offline-testable module. Values are IDENTICAL to hmt2e's own module-level constants of
# the same name, and to the literal constants hardcoded at
# `DatabentoHistoricalProvider.acquire_mbp1_pilot_session_data()`'s own `.get_range(...)` call
# site — see `tests/hmt2/test_ledger_constants_match_acquisition_call_site.py` for a direct,
# automated proof these never drift apart.
# ------------------------------------------------------------------------------------------
DATASET = "GLBX.MDP3"
SCHEMA = "mbp-1"
STYPE_IN = "raw_symbol"

LEDGER_SCHEMA_VERSION = "hmt2h-gc-corpus-acquisition-ledger-v1"

# Local, gitignored (via the blanket `research-source/` .gitignore rule) ledger STATE file —
# relative to the `research-source/` root, exactly like `manifest_relative_path(...)` builds
# for the existing pilot catalogue.
LEDGER_STATE_RELATIVE_PATH = "hmt2-gc-mbp1-v1/manifest/gc_corpus_acquisition_ledger.json"

# Governed $100 real-money ceiling for the whole HMT-2 checkpoint (WO Part 2, binding) — the
# SAME ceiling every prior real acquisition in this checkpoint has been checked against
# (see `research/hmt2/hmt2c-mbp1-quote-evidence-v1.json`'s own `hmt2_running_total_spend.
# ceiling_usd`).
GOVERNED_SPEND_CEILING_USD = 100.0

STATE_PLANNED = "PLANNED"
STATE_IN_PROGRESS = "IN_PROGRESS"
STATE_COMPLETE = "COMPLETE"
STATE_FAILED_AMBIGUOUS = "FAILED_AMBIGUOUS"
VALID_STATES = frozenset({STATE_PLANNED, STATE_IN_PROGRESS, STATE_COMPLETE, STATE_FAILED_AMBIGUOUS})


class LedgerError(ValueError):
    """Fails closed on any structurally invalid ledger operation."""


class CostCeilingExceededError(LedgerError):
    """Raised when a specific request's quoted cost, added to the running actual spend, would
    exceed `GOVERNED_SPEND_CEILING_USD`. Callers MUST stop before the specific request that
    trips this and report it — never silently skip it and continue past it (WO Part 2,
    binding)."""


def build_ledger_entry(
    *,
    session_id: str,
    trade_date: str,
    active_raw_symbols: Tuple[str, ...],
    start_utc: str,
    end_utc: str,
    request_identity: str,
) -> dict:
    """One freshly-seeded `PLANNED` row — no quote, no artefact yet."""
    return {
        "session_id": session_id,
        "trade_date": trade_date,
        "run_session_ids": [session_id],  # request-group membership; 1 session == 1 group in
        # this ledger's design (see hmt2h_gc_corpus_acquire.py module docstring for why this is
        # a deliberate, disclosed departure from hmt2c's 359-run CONTIGUOUS-DATE quote-batching
        # strategy, which existed only to minimise FREE metadata-call count for the aggregate
        # go/no-go quote, never as the acquisition's own request-grouping).
        "dataset": DATASET,
        "schema": SCHEMA,
        "stype_in": STYPE_IN,
        "symbols": list(active_raw_symbols),
        "start_utc": start_utc,
        "end_utc": end_utc,
        "request_identity": request_identity,
        "state": STATE_PLANNED,
        "pilot_reused": False,
        "quote": None,
        "artefact": None,
        "actual_cost_usd": None,
        "failure_reason": None,
        "ledger_updated_utc": None,
    }


def build_initial_ledger(
    *,
    activity_sessions: list,
    pilot_session_ids: frozenset,
    pilot_catalogue: dict,
    compute_request_identity_fn,
) -> Dict[str, dict]:
    """Seed a BRAND NEW ledger (keyed by `session_id`) from
    `gc-session-contract-activity-v1.json`'s own `sessions` list (all 448). The 2 pilot session
    ids are marked `COMPLETE` immediately, populated from their ALREADY-EXISTING catalogue
    entries (found by the SAME `compute_request_identity()` this ledger uses for every other
    session — never a hardcoded pilot session_id -> artefact mapping). Every other session is
    seeded `PLANNED`.

    `compute_request_identity_fn` is injected (never imported directly here) to keep this
    module import-free of anything beyond the stdlib — the real caller passes
    `market_truth.acquisition.source_store.compute_request_identity`.

    Fails closed (`LedgerError`) if a declared pilot session_id has no matching catalogue entry
    — this checkpoint's whole premise is that the pilot is ALREADY durably acquired; silently
    seeding it as `PLANNED` would risk a real duplicate-billing re-request.
    """
    ledger: Dict[str, dict] = {}
    for s in activity_sessions:
        session_id = s["session_id"]
        active_raw_symbols = tuple(s["active_raw_symbols"])
        start_utc = s["request_start_utc"]
        end_utc = s["request_end_utc"]
        request_identity = compute_request_identity_fn(
            dataset=DATASET, schema=SCHEMA, symbols=active_raw_symbols, stype_in=STYPE_IN,
            start=start_utc, end=end_utc,
        )
        entry = build_ledger_entry(
            session_id=session_id, trade_date=s["trade_date"], active_raw_symbols=active_raw_symbols,
            start_utc=start_utc, end_utc=end_utc, request_identity=request_identity,
        )
        if session_id in pilot_session_ids:
            existing = pilot_catalogue.get(request_identity)
            if existing is None:
                raise LedgerError(
                    f"pilot session {session_id!r} (request_identity={request_identity}) has no "
                    f"matching entry in the existing pilot acquisition catalogue — refusing to "
                    f"seed it as anything other than already-complete; investigate before "
                    f"building the ledger"
                )
            entry["state"] = STATE_COMPLETE
            entry["pilot_reused"] = True
            entry["artefact"] = {
                "object_relative_path": existing["object_relative_path"],
                "byte_size": existing["byte_size"],
                "sha256": existing["sha256"],
                "record_count": existing["reported_record_count"],
                "acquisition_utc": existing["acquisition_utc"],
            }
            # Cost is intentionally NOT populated here (left None) — the pilot's real spend
            # ($0.175759524107) is already counted once, in the checkpoint's PRIOR baseline
            # spend (see hmt2h_gc_corpus_acquire.compute_prior_actual_spend_usd()); summing it
            # again from this ledger row would double-count it. See
            # `sum_completed_actual_cost_usd(..., exclude_pilot_reused=True)` below.
        ledger[session_id] = entry
    return ledger


def load_ledger(state_path: str) -> Dict[str, dict]:
    if not os.path.exists(state_path):
        return {}
    with open(state_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_ledger_atomic(state_path: str, ledger: Dict[str, dict]) -> None:
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    tmp_path = state_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(ledger, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp_path, state_path)  # atomic rename on POSIX


def summarize(ledger: Dict[str, dict]) -> dict:
    counts = {state: 0 for state in VALID_STATES}
    for entry in ledger.values():
        state = entry["state"]
        if state not in VALID_STATES:
            raise LedgerError(f"session {entry.get('session_id')!r}: invalid state {state!r}")
        counts[state] += 1
    return {
        "total_sessions": len(ledger),
        "by_state": counts,
    }


def sum_completed_actual_cost_usd(ledger: Dict[str, dict], *, exclude_pilot_reused: bool = True) -> float:
    total = 0.0
    for entry in ledger.values():
        if entry["state"] != STATE_COMPLETE:
            continue
        if exclude_pilot_reused and entry.get("pilot_reused"):
            continue
        cost = entry.get("actual_cost_usd")
        if cost is not None:
            total += float(cost)
    return total


def check_cost_ceiling(*, spend_so_far: float, quoted_cost: float, ceiling: float = GOVERNED_SPEND_CEILING_USD) -> None:
    """Fails closed (`CostCeilingExceededError`) if `spend_so_far + quoted_cost` would exceed
    `ceiling`. Pure — never itself makes a network call or mutates any state; the caller decides
    what to do (stop, report) on the raised error."""
    projected = spend_so_far + quoted_cost
    if projected > ceiling:
        raise CostCeilingExceededError(
            f"projected spend ${projected!r} (spend_so_far=${spend_so_far!r} + this request's "
            f"quoted_cost=${quoted_cost!r}) would exceed the governed ${ceiling!r} ceiling — "
            f"STOP before this specific request"
        )


def ledger_content_sha256(ledger: Dict[str, dict]) -> str:
    """Deterministic hash over the ledger's own current content — used only for the committed
    snapshot evidence file's own integrity self-check, never as a request/artefact identity."""
    canonical = json.dumps(ledger, indent=2, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
