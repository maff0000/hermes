"""HMT-2 (real-money checkpoint) — tests for market_truth.acquisition.reference_series.

Every bar/session fixture here is synthetic (clearly-labelled small numbers), never real GC
price data — see market_truth/acquisition/reference_series.py's own module docstring for why
(this module is pure/deterministic and never touches the real retained artefact directly; a
separate one-off script does that translation).
"""
from __future__ import annotations

import datetime as _dt

import pytest

from market_truth.acquisition import reference_series as rs
from market_truth.acquisition.session_calendar import (
    CALENDAR_VERSION,
    SpecialSessionFlag,
    build_session_universe,
)

_UTC = _dt.timezone.utc


def _session(date_str: str, start: str, end: str) -> "rs.SessionRecord":
    from market_truth.acquisition.session_calendar import SessionRecord

    return SessionRecord(
        session_date=_dt.date.fromisoformat(date_str),
        window_start_utc=_dt.datetime.fromisoformat(start).replace(tzinfo=_UTC),
        window_end_utc=_dt.datetime.fromisoformat(end).replace(tzinfo=_UTC),
        calendar_version=CALENDAR_VERSION,
    )


def _bar(ts: str, *, high: float, low: float, instrument_id: int = 100) -> rs.HourlyBar:
    return rs.HourlyBar(
        ts_event_utc=_dt.datetime.fromisoformat(ts).replace(tzinfo=_UTC), high=high, low=low,
        instrument_id=instrument_id,
    )


# ----------------------------------------------------------------------------------------------
# HourlyBar
# ----------------------------------------------------------------------------------------------

def test_hourly_bar_requires_tz_aware_timestamp():
    with pytest.raises(ValueError):
        rs.HourlyBar(ts_event_utc=_dt.datetime(2026, 1, 1), high=1.0, low=1.0, instrument_id=1)


# ----------------------------------------------------------------------------------------------
# assign_bars_to_sessions
# ----------------------------------------------------------------------------------------------

def test_assign_bars_to_sessions_places_each_bar_in_its_session():
    session_a = _session("2026-01-05", "2026-01-04T22:00:00", "2026-01-05T21:00:00")
    session_b = _session("2026-01-06", "2026-01-05T22:00:00", "2026-01-06T21:00:00")
    bars = [
        _bar("2026-01-04T23:00:00", high=10, low=9),
        _bar("2026-01-05T22:30:00", high=11, low=10),
    ]
    by_session, orphans = rs.assign_bars_to_sessions(bars, [session_a, session_b])
    assert orphans == []
    assert len(by_session["GC-2026-01-05"]) == 1
    assert len(by_session["GC-2026-01-06"]) == 1


def test_assign_bars_to_sessions_detects_orphans_outside_any_window():
    session_a = _session("2026-01-05", "2026-01-04T22:00:00", "2026-01-05T21:00:00")
    orphan_bar = _bar("2026-01-05T23:00:00", high=10, low=9)  # after window_end
    by_session, orphans = rs.assign_bars_to_sessions([orphan_bar], [session_a])
    assert orphans == [orphan_bar]
    assert by_session["GC-2026-01-05"] == []


def test_assign_bars_to_sessions_window_end_is_exclusive():
    session_a = _session("2026-01-05", "2026-01-04T22:00:00", "2026-01-05T21:00:00")
    session_b = _session("2026-01-06", "2026-01-05T22:00:00", "2026-01-06T21:00:00")
    boundary_bar = _bar("2026-01-05T21:00:00", high=10, low=9)  # exactly window_end of A
    by_session, orphans = rs.assign_bars_to_sessions([boundary_bar], [session_a, session_b])
    assert orphans == [boundary_bar]  # falls in the maintenance-halt gap, not in either session
    assert by_session["GC-2026-01-05"] == []
    assert by_session["GC-2026-01-06"] == []


# ----------------------------------------------------------------------------------------------
# classify_session_reference_quality
# ----------------------------------------------------------------------------------------------

def test_classify_missing_when_zero_bars():
    session_a = _session("2026-01-05", "2026-01-04T22:00:00", "2026-01-05T21:00:00")
    result = rs.classify_session_reference_quality(session_a, [])
    assert result.quality_tag == rs.ReferenceQualityTag.REFERENCE_MISSING
    assert result.session_log_range is None
    assert result.bar_count == 0


def test_classify_complete_when_full_coverage_single_instrument():
    session_a = _session("2026-01-05", "2026-01-04T22:00:00", "2026-01-05T00:00:00")  # 2 hours
    bars = [
        _bar("2026-01-04T22:00:00", high=100, low=90, instrument_id=1),
        _bar("2026-01-04T23:00:00", high=110, low=95, instrument_id=1),
    ]
    result = rs.classify_session_reference_quality(session_a, bars)
    assert result.quality_tag == rs.ReferenceQualityTag.REFERENCE_COMPLETE
    assert result.expected_hour_count == 2
    assert result.coverage_ratio == 1.0
    import math
    assert result.session_log_range == pytest.approx(math.log(110 / 90))


def test_classify_partial_when_coverage_below_threshold():
    session_a = _session("2026-01-05", "2026-01-04T00:00:00", "2026-01-05T00:00:00")  # 24 hours
    bars = [_bar("2026-01-04T00:00:00", high=100, low=90, instrument_id=1)]  # 1/24 coverage
    result = rs.classify_session_reference_quality(session_a, bars)
    assert result.quality_tag == rs.ReferenceQualityTag.REFERENCE_PARTIAL
    assert result.coverage_ratio == pytest.approx(1 / 24)


def test_classify_roll_ambiguous_when_multiple_instrument_ids_even_with_full_coverage():
    session_a = _session("2026-01-05", "2026-01-04T22:00:00", "2026-01-05T00:00:00")  # 2 hours
    bars = [
        _bar("2026-01-04T22:00:00", high=100, low=90, instrument_id=1),
        _bar("2026-01-04T23:00:00", high=110, low=95, instrument_id=2),  # different instrument
    ]
    result = rs.classify_session_reference_quality(session_a, bars)
    assert result.quality_tag == rs.ReferenceQualityTag.REFERENCE_ROLL_AMBIGUOUS
    assert result.distinct_instrument_ids == (1, 2)
    # Still computes the metric for transparency, even though excluded from candidacy.
    assert result.session_log_range is not None


def test_min_complete_coverage_ratio_boundary_is_ge_not_gt():
    """Pins the exact frozen threshold semantics: coverage_ratio == MIN_COMPLETE_COVERAGE_RATIO
    counts as COMPLETE (>=, not strictly >)."""
    session_a = _session("2026-01-05", "2026-01-04T00:00:00", "2026-01-04T10:00:00")  # 10 hours
    # 9 of 10 hours present -> coverage_ratio == 0.9 == MIN_COMPLETE_COVERAGE_RATIO exactly.
    assert rs.MIN_COMPLETE_COVERAGE_RATIO == 0.90
    bars = [
        _bar(f"2026-01-04T{h:02d}:00:00", high=100, low=90, instrument_id=1) for h in range(9)
    ]
    result = rs.classify_session_reference_quality(session_a, bars)
    assert result.coverage_ratio == pytest.approx(0.9)
    assert result.quality_tag == rs.ReferenceQualityTag.REFERENCE_COMPLETE


# ----------------------------------------------------------------------------------------------
# classify_all_sessions — end-to-end wrapper
# ----------------------------------------------------------------------------------------------

def test_classify_all_sessions_end_to_end_over_a_real_governed_session_universe():
    sessions = build_session_universe(_dt.date(2026, 1, 5), _dt.date(2026, 1, 9))
    assert len(sessions) == 5  # Mon-Fri, no holiday in that week
    bars = []
    for s in sessions:
        # Fill each session fully so every one is REFERENCE_COMPLETE, single instrument.
        hours = int((s.window_end_utc - s.window_start_utc).total_seconds() // 3600)
        for i in range(hours):
            ts = s.window_start_utc + _dt.timedelta(hours=i)
            bars.append(rs.HourlyBar(ts_event_utc=ts, high=100 + i, low=90, instrument_id=1))
    results, orphans = rs.classify_all_sessions(sessions, bars)
    assert orphans == []
    assert all(r.quality_tag == rs.ReferenceQualityTag.REFERENCE_COMPLETE for r in results.values())


# ----------------------------------------------------------------------------------------------
# select_high_vol_and_compression_candidates — per-year top/bottom 20%, deterministic tie-break
# ----------------------------------------------------------------------------------------------

def _complete_result(session_id, date_str, log_range):
    return rs.SessionReferenceResult(
        session_id=session_id,
        session_date=_dt.date.fromisoformat(date_str),
        quality_tag=rs.ReferenceQualityTag.REFERENCE_COMPLETE,
        session_log_range=log_range,
        bar_count=10,
        expected_hour_count=10,
        coverage_ratio=1.0,
        distinct_instrument_ids=(1,),
    )


def test_select_candidates_top_and_bottom_20_percent_within_year():
    # 10 sessions in 2026, log_range = 0..9 (session i has value i). 20% of 10 = 2.
    results = {
        f"GC-2026-01-{i+1:02d}": _complete_result(f"GC-2026-01-{i+1:02d}", f"2026-01-{i+1:02d}", float(i))
        for i in range(10)
    }
    high_vol, compression = rs.select_high_vol_and_compression_candidates(results, set())
    assert compression == ["GC-2026-01-01", "GC-2026-01-02"]  # lowest 2 values (0, 1)
    assert high_vol == ["GC-2026-01-09", "GC-2026-01-10"]  # highest 2 values (8, 9)


def test_select_candidates_excludes_already_claimed_sessions():
    results = {
        f"GC-2026-01-{i+1:02d}": _complete_result(f"GC-2026-01-{i+1:02d}", f"2026-01-{i+1:02d}", float(i))
        for i in range(10)
    }
    claimed = {"GC-2026-01-01"}  # would otherwise be the sole bottom candidate at k=2 boundary
    high_vol, compression = rs.select_high_vol_and_compression_candidates(results, claimed)
    assert "GC-2026-01-01" not in compression
    assert "GC-2026-01-01" not in high_vol


def test_select_candidates_separates_years_independently():
    results = {}
    for i in range(10):
        results[f"GC-2025-01-{i+1:02d}"] = _complete_result(f"GC-2025-01-{i+1:02d}", f"2025-01-{i+1:02d}", float(i))
    for i in range(10):
        # 2026 values inverted relative to 2025, to prove ranking is per-year not global.
        results[f"GC-2026-01-{i+1:02d}"] = _complete_result(f"GC-2026-01-{i+1:02d}", f"2026-01-{i+1:02d}", float(9 - i))
    high_vol, compression = rs.select_high_vol_and_compression_candidates(results, set())
    assert "GC-2025-01-09" in high_vol and "GC-2025-01-10" in high_vol
    assert "GC-2026-01-01" in high_vol and "GC-2026-01-02" in high_vol  # inverted year's top-2


def test_select_candidates_excludes_non_complete_and_none_log_range():
    results = {
        "GC-2026-01-01": _complete_result("GC-2026-01-01", "2026-01-01", 1.0),
        "GC-2026-01-02": rs.SessionReferenceResult(
            session_id="GC-2026-01-02", session_date=_dt.date(2026, 1, 2),
            quality_tag=rs.ReferenceQualityTag.REFERENCE_ROLL_AMBIGUOUS, session_log_range=5.0,
            bar_count=10, expected_hour_count=10, coverage_ratio=1.0, distinct_instrument_ids=(1, 2),
        ),
        "GC-2026-01-03": rs.SessionReferenceResult(
            session_id="GC-2026-01-03", session_date=_dt.date(2026, 1, 3),
            quality_tag=rs.ReferenceQualityTag.REFERENCE_MISSING, session_log_range=None,
            bar_count=0, expected_hour_count=10, coverage_ratio=None, distinct_instrument_ids=(),
        ),
    }
    high_vol, compression = rs.select_high_vol_and_compression_candidates(results, set())
    # Only 1 eligible session in the whole year -> floor(1*0.2) == 0 -> zero candidates.
    assert high_vol == []
    assert compression == []


def test_select_candidates_tiny_year_produces_zero_candidates_disclosed_not_forced():
    results = {
        "GC-2026-01-01": _complete_result("GC-2026-01-01", "2026-01-01", 1.0),
        "GC-2026-01-02": _complete_result("GC-2026-01-02", "2026-01-02", 2.0),
    }
    # floor(2 * 0.2) == 0
    high_vol, compression = rs.select_high_vol_and_compression_candidates(results, set())
    assert high_vol == []
    assert compression == []


def test_select_candidates_is_deterministic_across_repeated_calls():
    results = {
        f"GC-2026-01-{i+1:02d}": _complete_result(f"GC-2026-01-{i+1:02d}", f"2026-01-{i+1:02d}", float(i % 4))
        for i in range(20)
    }
    first = rs.select_high_vol_and_compression_candidates(results, set())
    second = rs.select_high_vol_and_compression_candidates(results, set())
    assert first == second
