"""HMT-2 (real-money checkpoint) — reference-series session mapping, log-range computation, and
quality tagging for the real, retained `GC.v.0` continuous `ohlcv-1h` reference series.

Pure, deterministic, stdlib-only. No network. No provider dependency (`databento` is NEVER
imported here — mirrors the HMT-2A/2B/2B.1 no-network-guard discipline exactly;
`tests/hmt2/test_no_network_guard.py` scans this whole package, this module included). The
real DBN file is read, decoded, and translated into the plain `HourlyBar` records this module
consumes by a separate one-off script OUTSIDE this package
(`research/hmt2/generate_reference_series_v2.py`, which legitimately imports `databento` — that
script lives in `research/hmt2/`, not `market_truth/acquisition/`, so it is not subject to, and
does not need an exception from, this package's no-network guard).

PURPOSE BOUNDARY (repeated because it matters): every session-level metric this module computes
is corpus-selection metadata ONLY. It is permanently NOT canonical GC Market Truth v2, NOT the
P0 corpus, NOT a derived HERMES microstructure fact, NOT a DARWIN input, NOT a trading
feature/signal.

Frozen methodology (implemented once, run once on the real data — see WO final report; this
module's functions are never re-tuned after seeing real output):

  - `session_log_range = ln(max(hourly highs) / min(hourly lows))` over every hourly bar whose
    `ts_event` (bar-open UTC timestamp) falls within a session's governed UTC window
    (`market_truth.acquisition.session_calendar.SessionRecord.window_start_utc` /
    `.window_end_utc`, `[start, end)` half-open, reused unmodified — this module never redefines
    session boundaries). NOT close-to-close return. NOT computed from, or referencing, any
    MBP-1/TBBO data (none has been acquired anywhere in this repository).
  - Reference-quality tagging, per session:
      * `REFERENCE_MISSING` — zero hourly bars assigned to the session.
      * `REFERENCE_ROLL_AMBIGUOUS` — the session's hourly bars carry more than one distinct
        `instrument_id` (the continuous `GC.v.0` series rolled to a different underlying
        contract mid-session). Never silently spliced into one continuous price series;
        excluded from `HIGH_VOL_NON_EVENT`/`COMPRESSION` candidacy (checked BEFORE the
        completeness threshold below, since a roll-ambiguous session's "coverage" is not a
        meaningful single-instrument concept).
      * `REFERENCE_COMPLETE` — at least one bar, single instrument_id, and hourly-bar coverage
        of the session's nominal window at or above `MIN_COMPLETE_COVERAGE_RATIO`.
      * `REFERENCE_PARTIAL` — at least one bar, single instrument_id, coverage below that
        threshold. Gaps are NEVER interpolated to fill.
  - Only `REFERENCE_COMPLETE` sessions may enter `HIGH_VOL_NON_EVENT`/`COMPRESSION` candidacy.
  - Within each calendar year separately (never globally), eligible sessions (REFERENCE_COMPLETE,
    not already claimed by a higher-precedence stratum) are ranked by `session_log_range`.
    `HIGH_VOL_NON_EVENT` candidates = the top `CANDIDATE_FRACTION` (20%) within that year;
    `COMPRESSION` candidates = the bottom `CANDIDATE_FRACTION` within that year. Sizing uses
    `floor(eligible_count_in_year * CANDIDATE_FRACTION)` — a year with too few eligible sessions
    to produce even one candidate at that stratum simply contributes zero, disclosed, not
    forced. Deterministic tie-break at a percentile boundary: sessions are sorted by
    `(session_log_range, session_date)` ascending; the bottom slice is the first K entries of
    that sort (ties broken toward the EARLIER session_date), the top slice is the last K entries
    of that same sort, reversed for reporting (ties broken toward the EARLIER session_date
    entering the boundary from the top, i.e. the LATER-dated tied session is excluded first —
    concretely: among sessions sharing the exact boundary log_range value, the ascending-date
    order decides which side of the cut each one lands on, applied identically and only once).
"""
from __future__ import annotations

import datetime as _dt
import math
from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Optional, Sequence

from market_truth.acquisition.session_calendar import SessionRecord

REFERENCE_SERIES_METHODOLOGY_VERSION = "hmt2-reference-series-v2"

# Frozen constants — fixed BEFORE this module was ever run against the real data; never
# adjusted after seeing real output (see WO final report / methodology addendum).
MIN_COMPLETE_COVERAGE_RATIO = 0.90
CANDIDATE_FRACTION = 0.20


class ReferenceQualityTag(str, Enum):
    REFERENCE_COMPLETE = "REFERENCE_COMPLETE"
    REFERENCE_PARTIAL = "REFERENCE_PARTIAL"
    REFERENCE_MISSING = "REFERENCE_MISSING"
    REFERENCE_ROLL_AMBIGUOUS = "REFERENCE_ROLL_AMBIGUOUS"


@dataclass(frozen=True)
class HourlyBar:
    """One `GC.v.0` continuous `ohlcv-1h` bar, translated from the retained native DBN artefact
    into this module's own plain shape by the caller (never a vendor-typed object here)."""

    ts_event_utc: _dt.datetime  # bar-open UTC timestamp, tz-aware
    high: float
    low: float
    instrument_id: int

    def __post_init__(self) -> None:
        if self.ts_event_utc.tzinfo is None:
            raise ValueError("ts_event_utc must be tz-aware (UTC)")


@dataclass(frozen=True)
class SessionReferenceResult:
    session_id: str
    session_date: _dt.date
    quality_tag: ReferenceQualityTag
    session_log_range: Optional[float]
    bar_count: int
    expected_hour_count: int
    coverage_ratio: Optional[float]
    distinct_instrument_ids: tuple  # tuple[int, ...]
    methodology_version: str = REFERENCE_SERIES_METHODOLOGY_VERSION


def assign_bars_to_sessions(
    bars: Sequence[HourlyBar], sessions: Sequence[SessionRecord]
) -> tuple[dict, list]:
    """Deterministically assigns each bar to the (at most one) session whose governed
    `[window_start_utc, window_end_utc)` UTC window contains its `ts_event_utc`. Returns
    `(bars_by_session_id, orphan_bars)` — an orphan is a real bar that fell in no session's
    window at all (should not occur for genuine traded bars; recorded, never silently dropped
    or fabricated into a session).

    O((B + S) log S) via a sort + binary search over session start times, not O(B*S).
    """
    import bisect

    sessions_sorted = sorted(sessions, key=lambda s: s.window_start_utc)
    starts = [s.window_start_utc for s in sessions_sorted]
    bars_by_session: dict = {s.session_id: [] for s in sessions_sorted}
    orphans: list = []

    for bar in bars:
        idx = bisect.bisect_right(starts, bar.ts_event_utc) - 1
        if idx < 0:
            orphans.append(bar)
            continue
        session = sessions_sorted[idx]
        if session.window_start_utc <= bar.ts_event_utc < session.window_end_utc:
            bars_by_session[session.session_id].append(bar)
        else:
            orphans.append(bar)

    return bars_by_session, orphans


def _expected_hour_count(session: SessionRecord) -> int:
    delta = session.window_end_utc - session.window_start_utc
    return max(1, int(delta.total_seconds() // 3600))


def classify_session_reference_quality(
    session: SessionRecord, bars: Sequence[HourlyBar]
) -> SessionReferenceResult:
    """Applies the frozen methodology (module docstring) to exactly one session's assigned
    bars. Pure function — no I/O, no randomness, no reference to any other session."""
    expected_hours = _expected_hour_count(session)
    distinct_instrument_ids = tuple(sorted({b.instrument_id for b in bars}))

    if not bars:
        return SessionReferenceResult(
            session_id=session.session_id,
            session_date=session.session_date,
            quality_tag=ReferenceQualityTag.REFERENCE_MISSING,
            session_log_range=None,
            bar_count=0,
            expected_hour_count=expected_hours,
            coverage_ratio=None,
            distinct_instrument_ids=distinct_instrument_ids,
        )

    high = max(b.high for b in bars)
    low = min(b.low for b in bars)
    session_log_range = math.log(high / low) if high > 0 and low > 0 else None

    distinct_hours_present = len({b.ts_event_utc for b in bars})
    coverage_ratio = distinct_hours_present / expected_hours

    if len(distinct_instrument_ids) > 1:
        quality_tag = ReferenceQualityTag.REFERENCE_ROLL_AMBIGUOUS
    elif coverage_ratio >= MIN_COMPLETE_COVERAGE_RATIO:
        quality_tag = ReferenceQualityTag.REFERENCE_COMPLETE
    else:
        quality_tag = ReferenceQualityTag.REFERENCE_PARTIAL

    return SessionReferenceResult(
        session_id=session.session_id,
        session_date=session.session_date,
        quality_tag=quality_tag,
        session_log_range=session_log_range,
        bar_count=len(bars),
        expected_hour_count=expected_hours,
        coverage_ratio=coverage_ratio,
        distinct_instrument_ids=distinct_instrument_ids,
    )


def classify_all_sessions(
    sessions: Sequence[SessionRecord], bars: Sequence[HourlyBar]
) -> tuple[dict, list]:
    """Convenience wrapper: assigns bars then classifies every session. Returns
    `(results_by_session_id, orphan_bars)`."""
    bars_by_session, orphans = assign_bars_to_sessions(bars, sessions)
    results = {
        session.session_id: classify_session_reference_quality(session, bars_by_session[session.session_id])
        for session in sessions
    }
    return results, orphans


def select_high_vol_and_compression_candidates(
    results_by_session_id: Mapping[str, SessionReferenceResult],
    already_claimed_session_ids: set,
) -> tuple[list, list]:
    """Frozen per-year top/bottom `CANDIDATE_FRACTION` selection (module docstring). Returns
    `(high_vol_non_event_session_ids, compression_session_ids)`, each a sorted tuple of session
    ids, deterministic given the same inputs. Never re-examines anything outside
    `REFERENCE_COMPLETE`-tagged, not-already-claimed sessions."""
    eligible = [
        r
        for r in results_by_session_id.values()
        if r.quality_tag == ReferenceQualityTag.REFERENCE_COMPLETE
        and r.session_id not in already_claimed_session_ids
        and r.session_log_range is not None
    ]

    by_year: dict = {}
    for r in eligible:
        by_year.setdefault(r.session_date.year, []).append(r)

    high_vol_ids: list = []
    compression_ids: list = []
    for year in sorted(by_year):
        year_rows = by_year[year]
        # Deterministic tie-break: ascending (log_range, session_date).
        year_rows_sorted = sorted(year_rows, key=lambda r: (r.session_log_range, r.session_date))
        n = len(year_rows_sorted)
        k = int(n * CANDIDATE_FRACTION)  # floor; a too-small year contributes zero, disclosed
        if k <= 0:
            continue
        compression_slice = year_rows_sorted[:k]
        high_vol_slice = year_rows_sorted[n - k :]
        compression_ids.extend(r.session_id for r in compression_slice)
        high_vol_ids.extend(r.session_id for r in high_vol_slice)

    return sorted(set(high_vol_ids)), sorted(set(compression_ids))
