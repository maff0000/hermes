"""HMT-2A — session_calendar.py behavioural tests.

Spot-checks known weekends/holidays/early-close days against the standard, documented CME
rule-set (see the module's evidence-tier docstring), and proves the UTC window derivation is
DST-correct (America/Chicago), not a hardcoded fixed offset.
"""
import datetime as dt

import pytest

from market_truth.acquisition.session_calendar import (
    CALENDAR_VERSION,
    SpecialSessionFlag,
    build_session_record,
    build_session_universe,
    easter_sunday,
    good_friday,
    is_gc_full_closure,
)


def test_known_weekends_excluded():
    # Saturday and Sunday, 2024-06-01 / 2024-06-02.
    assert build_session_record(dt.date(2024, 6, 1)) is None
    assert build_session_record(dt.date(2024, 6, 2)) is None
    closed, reason = is_gc_full_closure(dt.date(2024, 6, 1))
    assert closed and reason == "WEEKEND"


@pytest.mark.parametrize(
    "date_,expected_reason",
    [
        (dt.date(2024, 1, 1), "NEW_YEARS_DAY"),
        (dt.date(2024, 1, 15), "MLK_DAY"),
        (dt.date(2024, 2, 19), "PRESIDENTS_DAY"),
        (dt.date(2024, 5, 27), "MEMORIAL_DAY"),
        (dt.date(2024, 6, 19), "JUNETEENTH"),
        (dt.date(2024, 7, 4), "INDEPENDENCE_DAY"),
        (dt.date(2024, 9, 2), "LABOR_DAY"),
        (dt.date(2024, 11, 28), "THANKSGIVING"),
        (dt.date(2024, 12, 25), "CHRISTMAS"),
        (dt.date(2024, 3, 29), "GOOD_FRIDAY"),  # Good Friday 2024 == Easter(2024-03-31) - 2 days
    ],
)
def test_known_holidays_excluded(date_, expected_reason):
    assert build_session_record(date_) is None
    closed, reason = is_gc_full_closure(date_)
    assert closed
    assert reason == expected_reason


def test_juneteenth_not_applied_before_2021():
    # Juneteenth became a federal holiday in 2021 — must NOT be retroactively applied.
    assert build_session_record(dt.date(2020, 6, 19)) is not None
    closed, _ = is_gc_full_closure(dt.date(2020, 6, 19))
    assert closed is False


def test_known_regular_trading_days_included():
    for date_ in (dt.date(2024, 6, 3), dt.date(2024, 6, 4), dt.date(2024, 6, 5)):
        record = build_session_record(date_)
        assert record is not None
        assert record.special_session_flag == SpecialSessionFlag.NONE
        assert record.calendar_version == CALENDAR_VERSION


def test_early_close_days_flagged_distinctly_from_holidays():
    day_before_july4 = build_session_record(dt.date(2024, 7, 3))
    assert day_before_july4 is not None  # early close is NOT a non-trading day
    assert day_before_july4.special_session_flag == SpecialSessionFlag.EARLY_CLOSE
    assert day_before_july4.reason == "PRE_INDEPENDENCE_DAY"

    day_after_thanksgiving = build_session_record(dt.date(2024, 11, 29))
    assert day_after_thanksgiving is not None
    assert day_after_thanksgiving.special_session_flag == SpecialSessionFlag.EARLY_CLOSE
    assert day_after_thanksgiving.reason == "POST_THANKSGIVING"

    christmas_eve = build_session_record(dt.date(2024, 12, 24))
    assert christmas_eve is not None
    assert christmas_eve.special_session_flag == SpecialSessionFlag.EARLY_CLOSE
    assert christmas_eve.reason == "CHRISTMAS_EVE"

    # Early close must produce an earlier window_end than a regular session on the same
    # weekday, proving the flag actually changes the acquisition window, not just a label.
    regular_tuesday = build_session_record(dt.date(2024, 7, 2))
    assert day_before_july4.window_end_utc.time() < regular_tuesday.window_end_utc.time()


def test_easter_and_good_friday_known_dates():
    # Independently known, widely-published Easter Sunday dates (not derived from this
    # module) used to sanity-check the Anonymous Gregorian algorithm implementation.
    known_easters = {
        2017: dt.date(2017, 4, 16),
        2018: dt.date(2018, 4, 1),
        2019: dt.date(2019, 4, 21),
        2020: dt.date(2020, 4, 12),
        2021: dt.date(2021, 4, 4),
        2022: dt.date(2022, 4, 17),
        2023: dt.date(2023, 4, 9),
        2024: dt.date(2024, 3, 31),
        2025: dt.date(2025, 4, 20),
        2026: dt.date(2026, 4, 5),
    }
    for year, expected in known_easters.items():
        assert easter_sunday(year) == expected
        assert good_friday(year) == expected - dt.timedelta(days=2)


def test_utc_window_is_dst_aware_not_a_fixed_offset():
    # Winter (CST, UTC-6): Monday 2024-01-08.
    winter = build_session_record(dt.date(2024, 1, 8))
    # Summer (CDT, UTC-5): Monday 2024-07-08.
    summer = build_session_record(dt.date(2024, 7, 8))
    assert winter.window_end_utc.hour == 22  # 16:00 CST -> 22:00 UTC
    assert summer.window_end_utc.hour == 21  # 16:00 CDT -> 21:00 UTC


def test_session_id_and_dict_shape():
    record = build_session_record(dt.date(2024, 6, 3))
    assert record.session_id == "GC-2024-06-03"
    d = record.to_dict()
    assert d["session_date"] == "2024-06-03"
    assert d["calendar_version"] == CALENDAR_VERSION
    assert "window_start_utc" in d and "window_end_utc" in d


def test_build_session_universe_excludes_weekends_and_holidays_in_a_short_range():
    # 2024-12-23 (Mon, early close) .. 2024-12-26 (Thu) — 12/24 early close, 12/25 holiday.
    sessions = build_session_universe(dt.date(2024, 12, 23), dt.date(2024, 12, 26))
    dates = [s.session_date for s in sessions]
    assert dates == [dt.date(2024, 12, 23), dt.date(2024, 12, 24), dt.date(2024, 12, 26)]
    christmas_eve = sessions[1]
    assert christmas_eve.special_session_flag == SpecialSessionFlag.EARLY_CLOSE


def test_build_session_universe_rejects_reversed_range():
    with pytest.raises(ValueError):
        build_session_universe(dt.date(2024, 1, 2), dt.date(2024, 1, 1))


# --------------------------------------------------------------------------------------------
# HMT-2B.1 Part 2 — resolved final eligible-universe cutoff (2026 coverage check)
# --------------------------------------------------------------------------------------------
# This module needed NO modification to support the HMT-2B.1 resolution: every holiday/
# early-close rule is a function of `year` (nth_weekday_of_month, easter_sunday/good_friday,
# _observed_fixed_holiday), not a hardcoded/bounded lookup table, so it already computes
# correctly for 2026 (and any later year) unmodified. These tests pin that fact and the exact
# resolved cutoff session recorded in market_truth/acquisition/__init__.py
# (HMT2B1_RESOLVED_FINAL_CUTOFF_DATE / HMT2B1_CUTOFF_FREEZE_UTC) and in
# docs/research/hmt2-corpus-selection-methodology-v1.md.

_HMT2B1_FREEZE_UTC = dt.datetime(2026, 9, 21, 15, 11, 9, tzinfo=dt.timezone.utc)


def test_session_calendar_computes_2026_sessions_without_any_code_change():
    """2026 is fully computable by the existing rule-based functions: no bounded table, no
    KeyError/lookup miss for any month of 2026."""
    sessions = build_session_universe(dt.date(2026, 1, 1), dt.date(2026, 12, 31))
    assert len(sessions) > 0
    # Spot-check a real 2026 rule-based holiday: Good Friday 2026 is 2026-04-03 (Easter Sunday
    # 2026-04-05, see test_easter_and_good_friday_known_dates), and must be a full closure.
    is_closed, reason = is_gc_full_closure(dt.date(2026, 4, 3))
    assert is_closed and reason == "GOOD_FRIDAY"


def test_hmt2b1_resolved_cutoff_session_is_the_last_fully_completed_session_at_freeze_time():
    """Pins the exact HMT-2B.1 Part 2 resolution: at freeze time 2026-09-21T15:11:09Z, the GC
    session dated 2026-09-18 (a Friday) is the last one whose UTC window had already fully
    closed, and the next governed session (2026-09-21, the freeze day itself) had NOT yet
    closed — i.e. was correctly excluded as in-progress/partial, not counted as complete."""
    last_completed = build_session_record(dt.date(2026, 9, 18))
    assert last_completed is not None
    assert last_completed.window_end_utc < _HMT2B1_FREEZE_UTC

    in_progress_at_freeze = build_session_record(dt.date(2026, 9, 21))
    assert in_progress_at_freeze is not None
    assert in_progress_at_freeze.window_end_utc > _HMT2B1_FREEZE_UTC
    assert in_progress_at_freeze.window_start_utc < _HMT2B1_FREEZE_UTC

    # And no valid session strictly between 2026-09-18 and 2026-09-21 was skipped (weekend).
    universe = build_session_universe(dt.date(2026, 9, 18), dt.date(2026, 9, 21))
    assert [s.session_date for s in universe] == [dt.date(2026, 9, 18), dt.date(2026, 9, 21)]
