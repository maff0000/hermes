"""HMT-2 (real-money checkpoint, MBP-1 quote resolution) — tests for
market_truth.acquisition.mbp1_quote_request.

Every record here is synthetic — never real GC session/contract data.
"""
from __future__ import annotations

import pytest

from market_truth.acquisition import mbp1_quote_request as mqr


def _session(session_id, trade_date, start, end, symbols):
    return mqr.SessionActivity(
        session_id=session_id, trade_date=trade_date, request_start_utc=start, request_end_utc=end,
        active_raw_symbols=tuple(symbols),
    )


_CALENDAR = [f"2024-01-{d:02d}" for d in range(1, 11)]  # 2024-01-01 .. 2024-01-10, no gaps


def test_two_calendar_adjacent_sessions_merge_into_one_run():
    sessions = [
        _session("S1", "2024-01-03", "2024-01-02T22:00:00+00:00", "2024-01-03T21:00:00+00:00", ["GCZ9"]),
        _session("S2", "2024-01-04", "2024-01-03T22:00:00+00:00", "2024-01-04T21:00:00+00:00", ["GCZ9", "GCF0"]),
    ]
    runs = mqr.group_sessions_into_contiguous_runs(sessions, _CALENDAR)
    assert len(runs) == 1
    run = runs[0]
    assert run.start_utc == "2024-01-02T22:00:00+00:00"
    assert run.end_utc_exclusive == "2024-01-04T21:00:00+00:00"
    assert run.session_ids == ("S1", "S2")
    assert run.raw_symbols == ("GCF0", "GCZ9")  # sorted union


def test_non_adjacent_sessions_do_not_merge():
    # 2024-01-03 and 2024-01-06 both real trading dates but not adjacent (01-04/01-05 exist
    # in between and were NOT selected) -> must NOT be folded into one call.
    sessions = [
        _session("S1", "2024-01-03", "2024-01-02T22:00:00+00:00", "2024-01-03T21:00:00+00:00", ["GCZ9"]),
        _session("S2", "2024-01-06", "2024-01-05T22:00:00+00:00", "2024-01-06T21:00:00+00:00", ["GCF0"]),
    ]
    runs = mqr.group_sessions_into_contiguous_runs(sessions, _CALENDAR)
    assert len(runs) == 2
    assert runs[0].session_ids == ("S1",)
    assert runs[1].session_ids == ("S2",)


def test_three_way_run_takes_union_of_all_three_symbol_sets():
    sessions = [
        _session("S1", "2024-01-01", "2023-12-31T22:00:00+00:00", "2024-01-01T21:00:00+00:00", ["A"]),
        _session("S2", "2024-01-02", "2024-01-01T22:00:00+00:00", "2024-01-02T21:00:00+00:00", ["B"]),
        _session("S3", "2024-01-03", "2024-01-02T22:00:00+00:00", "2024-01-03T21:00:00+00:00", ["A", "C"]),
    ]
    runs = mqr.group_sessions_into_contiguous_runs(sessions, _CALENDAR)
    assert len(runs) == 1
    assert runs[0].raw_symbols == ("A", "B", "C")
    assert runs[0].session_count == 3


def test_unsorted_input_sessions_still_produce_correctly_ordered_runs():
    sessions = [
        _session("S2", "2024-01-04", "2024-01-03T22:00:00+00:00", "2024-01-04T21:00:00+00:00", ["B"]),
        _session("S1", "2024-01-03", "2024-01-02T22:00:00+00:00", "2024-01-03T21:00:00+00:00", ["A"]),
    ]
    runs = mqr.group_sessions_into_contiguous_runs(sessions, _CALENDAR)
    assert len(runs) == 1
    assert runs[0].session_ids == ("S1", "S2")


def test_single_singleton_session_is_its_own_run():
    sessions = [_session("S1", "2024-01-05", "2024-01-04T22:00:00+00:00", "2024-01-05T21:00:00+00:00", ["GCZ9"])]
    runs = mqr.group_sessions_into_contiguous_runs(sessions, _CALENDAR)
    assert len(runs) == 1
    assert runs[0].symbol_count == 1


def test_fails_closed_on_duplicate_trade_date():
    sessions = [
        _session("S1", "2024-01-03", "2024-01-02T22:00:00+00:00", "2024-01-03T21:00:00+00:00", ["A"]),
        _session("S1DUP", "2024-01-03", "2024-01-02T22:00:00+00:00", "2024-01-03T21:00:00+00:00", ["B"]),
    ]
    with pytest.raises(mqr.Mbp1QuoteRequestError):
        mqr.group_sessions_into_contiguous_runs(sessions, _CALENDAR)


def test_fails_closed_on_trade_date_not_in_calendar():
    sessions = [_session("S1", "2099-01-01", "2099-01-01T22:00:00+00:00", "2099-01-02T21:00:00+00:00", ["A"])]
    with pytest.raises(mqr.Mbp1QuoteRequestError):
        mqr.group_sessions_into_contiguous_runs(sessions, _CALENDAR)


def test_fails_closed_on_unsorted_calendar():
    with pytest.raises(mqr.Mbp1QuoteRequestError):
        mqr.group_sessions_into_contiguous_runs([], ["2024-01-02", "2024-01-01"])


def test_fails_closed_on_duplicate_calendar_dates():
    with pytest.raises(mqr.Mbp1QuoteRequestError):
        mqr.group_sessions_into_contiguous_runs([], ["2024-01-01", "2024-01-01"])


def test_fails_closed_on_empty_calendar():
    with pytest.raises(mqr.Mbp1QuoteRequestError):
        mqr.group_sessions_into_contiguous_runs([], [])


def test_empty_sessions_yields_no_runs():
    assert mqr.group_sessions_into_contiguous_runs([], _CALENDAR) == ()
