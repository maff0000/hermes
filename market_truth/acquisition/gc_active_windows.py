"""HMT-2 (real-money checkpoint, MBP-1 quote resolution) — GC outright contract
activation/expiration windows and session-level active-contract determination.

Pure, deterministic, stdlib-only. No network, no provider dependency (`databento` is NEVER
imported here — mirrors `gc_definitions.py`'s own no-network discipline exactly;
`tests/hmt2/test_no_network_guard.py` scans this whole package, this module included). The
real, retained `GC.FUT` parent `definition`-schema DBN artefact is read and translated into the
plain `ActivationRecord` records this module consumes by a separate one-off script OUTSIDE this
package (`research/hmt2/generate_gc_outright_active_windows_v1.py`, which legitimately imports
`databento` — mirrors `generate_gc_definitions_v2.py`'s own placement rationale exactly).

PURPOSE (WO: "resolve the final all-outright MBP-1 quote request"): for each of the 448 governed
GC MBP-1 corpus-selection manifest v2 sessions, determine which of the 93 clean outright
contract mappings (`gc-contract-mapping-table-v2.json`) were actually live/listed on that
session's trade date — a session may involve more than one concurrently-active outright contract
(e.g. near a roll date, both the front and next-out contract are simultaneously listed and
tradeable). This module implements exactly that "is this session's date within this contract's
real activation..expiration listing window" filter — deliberately NOT a new complex algorithm,
a straightforward overlap test against the real, authoritative Databento `activation`/
`expiration` definition-schema fields (confirmed real field names against the actual decoded
`GC.FUT` definitions artefact this checkpoint — see the generator script's own docstring for the
exact column-list confirmation).

REAL-DATA DISCLOSURE (this checkpoint): a definitions-schema feed republishes a contract's
metadata row on (essentially) every session while the contract remains listed. For all but one
of the 93 clean outright raw_symbols, every republished row carries an IDENTICAL activation and
expiration timestamp (verified this checkpoint: only 1 of 93 clean symbols — `GCX1` — has more
than one distinct `expiration` value across its own republished rows). For `GCX1`, a genuine,
disclosed, real vendor correction occurred mid-life: a `security_update_action="M"` (Modify) row
moved its expiration one hour earlier (`2021-11-26T18:30:00Z` -> `2021-11-26T17:30:00Z`), with
`activation` unchanged throughout. This module resolves any such disagreement by taking the row
with the LATEST `ts_event` per raw_symbol as authoritative — the standard "last write wins"
convention for a republished reference/definition feed, and the only reading consistent with
that row's own `security_update_action="M"` (a modification of the SAME instrument, not a new
one) — never an average, never the earliest, never a silently-arbitrary pick. This one-hour
shift never changes which CALENDAR DATE a session falls on, so it has zero effect on any of this
checkpoint's session-level active/inactive determinations; it is disclosed here purely for
honesty about the real data, not because it changes any real result.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Mapping, Sequence, Tuple

GC_ACTIVE_WINDOWS_VERSION = "hmt2-gc-active-windows-v1"


class GcActiveWindowError(ValueError):
    """Raised on any structurally invalid input — fails closed, never guesses."""


@dataclass(frozen=True)
class ActivationRecord:
    """One `GC.FUT` outright (`instrument_class == "F"`) definition-schema row, translated into
    this module's own plain shape by the caller (never a vendor-typed/DataFrame-row object
    here). `ts_event` is the vendor's own event timestamp for this republished row (ISO 8601
    string, sortable lexicographically because it is a fixed-width, zero-padded, UTC ISO
    timestamp — the same representation the generator script derives via `.isoformat()` from a
    real, tz-aware `pandas.Timestamp`) — used only to pick the latest row per raw_symbol (see
    module docstring). `activation_utc`/`expiration_utc` are the real Databento definition-
    schema field values (also ISO 8601 strings), never renamed/reinterpreted.
    """

    raw_symbol: str
    ts_event: str
    activation_utc: str
    expiration_utc: str


@dataclass(frozen=True)
class ActiveWindow:
    """The resolved, single, authoritative activation..expiration window for one clean outright
    provider raw_symbol (see module docstring for how ties/republication are resolved)."""

    raw_symbol: str
    activation_utc: str
    expiration_utc: str
    source_ts_event: str


def latest_activation_window_per_symbol(
    records: Sequence[ActivationRecord],
    *,
    clean_raw_symbols: Sequence[str],
) -> Dict[str, ActiveWindow]:
    """For every raw_symbol in `clean_raw_symbols` (the 93-entry clean contract-mapping table's
    own keys — passed in explicitly so this module never has to re-derive "clean" itself, and
    never silently includes a poisoned/ambiguous raw_symbol), returns the single authoritative
    `ActiveWindow` — the record with the lexicographically-largest `ts_event` for that
    raw_symbol (see module docstring: "last write wins", and this checkpoint's real ISO-8601
    `ts_event` strings sort correctly by plain string comparison).

    Fails closed (`GcActiveWindowError`) if any `clean_raw_symbols` entry has zero matching
    records — this module never fabricates a window for a symbol it cannot observe. Extra
    records for a raw_symbol NOT in `clean_raw_symbols` (e.g. a poisoned/ambiguous symbol) are
    silently ignored — this function's whole purpose is to resolve windows for the already-
    disambiguated clean set, never to re-adjudicate ambiguity itself.
    """
    clean_set = set(clean_raw_symbols)
    if not clean_set:
        raise GcActiveWindowError("clean_raw_symbols must be non-empty")

    best_by_symbol: Dict[str, ActivationRecord] = {}
    for record in records:
        if record.raw_symbol not in clean_set:
            continue
        current_best = best_by_symbol.get(record.raw_symbol)
        if current_best is None or record.ts_event > current_best.ts_event:
            best_by_symbol[record.raw_symbol] = record

    missing = clean_set - set(best_by_symbol)
    if missing:
        raise GcActiveWindowError(f"no activation/expiration record found for clean raw_symbol(s): {sorted(missing)}")

    return {
        symbol: ActiveWindow(
            raw_symbol=symbol,
            activation_utc=rec.activation_utc,
            expiration_utc=rec.expiration_utc,
            source_ts_event=rec.ts_event,
        )
        for symbol, rec in best_by_symbol.items()
    }


def session_overlaps_active_window(
    *,
    session_start_utc: str,
    session_end_utc: str,
    activation_utc: str,
    expiration_utc: str,
) -> bool:
    """True iff a session's `[session_start_utc, session_end_utc)` trading window overlaps the
    contract's real `[activation_utc, expiration_utc]` listing window — i.e. the contract had
    already activated before the session ends, and had not yet expired before the session
    starts. Inclusive on both contract bounds: a contract that expires partway through a
    session's own window (its final, partial trading day) is still genuinely tradeable for part
    of that session, so it counts as active for it — never excluded on a same-day boundary.

    All four inputs are ISO-8601 UTC strings; this function never parses them into `datetime`
    objects — the real Databento/manifest timestamps in this checkpoint are all fixed-width,
    zero-padded, `+00:00`/`Z`-suffixed UTC strings, so plain lexicographic string comparison is
    exactly equivalent to chronological comparison. `_normalise` below defensively equalises the
    `Z`/`+00:00` suffix so the manifest's `+00:00` form and any `Z`-suffixed input compare
    correctly against each other.
    """
    a = _normalise(activation_utc)
    e = _normalise(expiration_utc)
    s = _normalise(session_start_utc)
    n = _normalise(session_end_utc)
    return a <= n and e >= s


def _normalise(ts: str) -> str:
    if ts.endswith("Z"):
        return ts[:-1] + "+00:00"
    return ts


def determine_active_contracts_for_sessions(
    sessions: Sequence[Mapping[str, str]],
    windows: Mapping[str, ActiveWindow],
) -> Dict[str, Tuple[str, ...]]:
    """For each session (a mapping with at least `session_id`/`request_start_utc`/
    `request_end_utc` keys, exactly the corpus-selection-manifest-v2 row shape), returns the
    sorted tuple of raw_symbols (from `windows`) whose real activation..expiration window
    overlaps that session — deterministic, order-independent (raw_symbols sorted
    lexicographically within each session).
    """
    result: Dict[str, Tuple[str, ...]] = {}
    for session in sessions:
        session_id = session["session_id"]
        start = session["request_start_utc"]
        end = session["request_end_utc"]
        active = tuple(
            sorted(
                symbol
                for symbol, window in windows.items()
                if session_overlaps_active_window(
                    session_start_utc=start,
                    session_end_utc=end,
                    activation_utc=window.activation_utc,
                    expiration_utc=window.expiration_utc,
                )
            )
        )
        result[session_id] = active
    return result
