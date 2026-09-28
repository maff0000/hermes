"""HMT-2 (real-money checkpoint, MBP-1 quote resolution) — deterministic construction of the
exact MBP-1 metadata quote request for the 448-session governed GC corpus-selection manifest.

Pure, deterministic, stdlib-only. No network, no provider dependency (mirrors every other pure
module in this package; `tests/hmt2/test_no_network_guard.py` scans this whole package, this
module included).

PROBLEM (WO): Databento's free `metadata.get_cost` / `get_record_count` / `get_billable_size`
estimate endpoints each take exactly ONE contiguous `[start, end)` date/time range per call (see
`databento.historical.api.metadata.MetadataHttpAPI.get_cost/get_record_count/get_billable_size`
— each accepts a single `start`/`end` pair, no list of discontiguous ranges; independently
confirmed this checkpoint by reading the installed SDK's own source, not assumed). The 448
selected sessions are, by construction, a discontiguous SAMPLE of specific trade dates spread
across 2017-2026 — not a single contiguous range, and (a REAL, checkpoint-discovered fact) COMEX
lists GC outright contracts many months — in several cases multiple YEARS — before their own
delivery month, so a typical session in this corpus has ~15-19 simultaneously-listed outright
contracts overlapping it (real range across the 448 sessions: 2..25), not just "the front month
and, near a roll, the next month" a naive reading of "actively-traded" might suggest. See
`market_truth.acquisition.gc_active_windows` module docstring and the WO final report for the
full disclosure of this real, checkpoint-discovered finding.

CHOSEN APPROACH (explicit, per WO's own stated architect intent — approach (b), NOT (a)): quote
EXACTLY what would be purchased for the sampled research corpus — never each contract's own
full real tradable-life date range (which would be far more expensive and is NOT what this
checkpoint's real-money MBP-1 pilot decision needs to see).

BATCHING STRATEGY (this is the "efficient" half of the WO's own instruction: "Databento's
metadata endpoints may support multiple discontiguous date ranges in one call, or may require
one call per contiguous range — check the real API and use whichever is correct and efficient"):
the real, installed Databento SDK's `symbols` parameter accepts UP TO 2,000 symbols in a SINGLE
call (confirmed against the installed SDK's own docstring) — so rather than issue one call per
(contract, contiguous-date-run) pair (which this checkpoint measured at 5,375 groups for the
real 448-session manifest — clearly impractical for a "free quote" step), this module groups by
CONTIGUOUS SESSION-DATE RUN FIRST (irrespective of contract), then issues ONE call per run with
the UNION of every contract active in ANY session within that run as its `symbols` list (a real
maximum of 25 symbols per run in this corpus, well under the 2,000 limit). This reduces the real
448-session manifest to 359 total groups (measured this checkpoint) while remaining EXACTLY as
honest as the naive per-contract grouping: a symbol genuinely not listed on some day inside its
own run's date range simply has zero real MBP-1 records for that day (nothing to bill — a
contract that has expired or not yet activated produces no real ticks), so batching a
contract's request across the whole run's date span never fabricates or inflates a real
record/byte/cost figure. It can only ever ask for MORE of the exact real per-symbol-per-day
truth that already exists; it never asks Databento to charge for days a real session wasn't
selected (that is what the RUN-boundary itself, defined only over selected+calendar-adjacent
dates, already prevents — see the MERGE RULE below).

MERGE RULE (why grouping session-date-runs is still exact, not an approximation): two selected
session dates are merged into the SAME run only when they are IMMEDIATELY ADJACENT in the full,
real GC trading-session calendar (`market_truth.acquisition.session_calendar.
build_session_universe`) — i.e. there is no OTHER real GC trading session date between them
that this manifest did NOT select. This guarantees a merged multi-session run can never sweep in
a real trading day's worth of extra, non-sampled MBP-1 records for ANY symbol: if the calendar
has no session between two selected dates, there is nothing extra to include no matter how wide
the vendor's own per-symbol billing window is; if it does, this rule refuses to merge across it
and starts a new run instead. A short daily/weekend maintenance-halt gap between two
immediately-adjacent trading sessions' own UTC windows is harmless (no data exists in it).

Each `metadata.*` call also takes `end` as EXCLUSIVE (confirmed against the installed SDK's own
docstrings, same as `research/hmt2/hmt2b1_reference_and_definition_quotes.py` already documents
for the day-granularity case) — here, since every window bound is a session's own exact UTC
instant (not a bare calendar date), the manifest's own `request_end_utc` value is used directly
as the exclusive upper bound with no `+1 day` adjustment: it already IS the precise instant the
desired session data should stop at.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Sequence, Tuple

MBP1_QUOTE_REQUEST_VERSION = "hmt2-mbp1-quote-request-v2"


class Mbp1QuoteRequestError(ValueError):
    """Raised on any structurally invalid input — fails closed, never guesses."""


@dataclass(frozen=True)
class SessionActivity:
    """One selected session plus its own already-resolved active-contract set — the minimal
    shape this module needs from `gc-session-contract-activity-v1.json`."""

    session_id: str
    trade_date: str  # ISO 8601 date, e.g. "2017-05-22"
    request_start_utc: str
    request_end_utc: str
    active_raw_symbols: Tuple[str, ...]


@dataclass(frozen=True)
class SessionRun:
    """One maximal, trading-calendar-contiguous run of selected sessions — exactly one free
    metadata-estimate call per instance of this class, `raw_symbols` being the deterministic,
    sorted UNION of every session's own active-contract set within the run."""

    start_utc: str
    end_utc_exclusive: str
    session_ids: Tuple[str, ...]
    raw_symbols: Tuple[str, ...]

    @property
    def session_count(self) -> int:
        return len(self.session_ids)

    @property
    def symbol_count(self) -> int:
        return len(self.raw_symbols)


def _trading_date_index(full_calendar_dates: Sequence[str]) -> Dict[str, int]:
    if not full_calendar_dates:
        raise Mbp1QuoteRequestError("full_calendar_dates must be non-empty")
    if list(full_calendar_dates) != sorted(full_calendar_dates):
        raise Mbp1QuoteRequestError("full_calendar_dates must be sorted ascending, no duplicates")
    if len(set(full_calendar_dates)) != len(full_calendar_dates):
        raise Mbp1QuoteRequestError("full_calendar_dates must not contain duplicate dates")
    return {date_str: idx for idx, date_str in enumerate(full_calendar_dates)}


def group_sessions_into_contiguous_runs(
    sessions: Sequence[SessionActivity],
    full_calendar_dates: Sequence[str],
) -> Tuple[SessionRun, ...]:
    """Groups the given sessions (sorted by `trade_date`) into maximal runs of real-trading-
    calendar-adjacent dates (see module docstring MERGE RULE), each run carrying the sorted
    union of every member session's own `active_raw_symbols`. `full_calendar_dates` must be the
    complete, sorted, deduplicated list of every real GC trading-session date in the governed
    range (ISO 8601 date strings) — e.g. from `[r.session_date.isoformat() for r in
    session_calendar.build_session_universe(start, end)]`.

    Deterministic: sessions are sorted by `trade_date` (a manifest invariant — one session per
    trade date — but this function does not trust that and sorts explicitly); the returned runs
    are in ascending date order, each with a sorted `raw_symbols` tuple. Fails closed
    (`Mbp1QuoteRequestError`) on a duplicate `trade_date` (would indicate a manifest-integrity
    defect upstream) or if any session's `trade_date` is not found in `full_calendar_dates`.
    """
    date_index = _trading_date_index(full_calendar_dates)

    ordered = sorted(sessions, key=lambda s: s.trade_date)
    seen_dates: set = set()
    runs: list[SessionRun] = []
    current_group: list[SessionActivity] = []
    prev_index = None
    for session in ordered:
        if session.trade_date in seen_dates:
            raise Mbp1QuoteRequestError(f"duplicate trade_date in input sessions: {session.trade_date!r}")
        seen_dates.add(session.trade_date)
        if session.trade_date not in date_index:
            raise Mbp1QuoteRequestError(
                f"session {session.session_id!r} trade_date {session.trade_date!r} is not a "
                f"real trading-calendar date in full_calendar_dates"
            )
        idx = date_index[session.trade_date]
        if current_group and prev_index is not None and idx == prev_index + 1:
            current_group.append(session)
        else:
            if current_group:
                runs.append(_close_group(current_group))
            current_group = [session]
        prev_index = idx
    if current_group:
        runs.append(_close_group(current_group))
    return tuple(runs)


def _close_group(group: Sequence[SessionActivity]) -> SessionRun:
    symbol_union: set = set()
    for session in group:
        symbol_union.update(session.active_raw_symbols)
    return SessionRun(
        start_utc=group[0].request_start_utc,
        end_utc_exclusive=group[-1].request_end_utc,
        session_ids=tuple(s.session_id for s in group),
        raw_symbols=tuple(sorted(symbol_union)),
    )
