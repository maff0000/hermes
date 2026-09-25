"""HMT-2 (real-money checkpoint, MBP-1 quote resolution) — tests for
market_truth.acquisition.gc_active_windows.

Every record here is synthetic — never real GC definitions data (mirrors
tests/hmt2/test_gc_definitions.py's own synthetic-only discipline).
"""
from __future__ import annotations

import pytest

from market_truth.acquisition import gc_active_windows as gaw


def _rec(raw_symbol, ts_event, activation_utc, expiration_utc):
    return gaw.ActivationRecord(
        raw_symbol=raw_symbol, ts_event=ts_event, activation_utc=activation_utc, expiration_utc=expiration_utc,
    )


# ----------------------------------------------------------------------------------------------
# latest_activation_window_per_symbol
# ----------------------------------------------------------------------------------------------

def test_latest_activation_window_picks_max_ts_event_per_symbol():
    records = [
        _rec("GCZ9", "2019-01-01T00:00:00+00:00", "2018-01-01T00:00:00+00:00", "2019-12-01T00:00:00+00:00"),
        _rec("GCZ9", "2019-06-01T00:00:00+00:00", "2018-01-01T00:00:00+00:00", "2019-11-30T23:00:00+00:00"),
        _rec("GCZ9", "2019-03-01T00:00:00+00:00", "2018-01-01T00:00:00+00:00", "2019-11-30T00:00:00+00:00"),
    ]
    windows = gaw.latest_activation_window_per_symbol(records, clean_raw_symbols=["GCZ9"])
    assert windows["GCZ9"].expiration_utc == "2019-11-30T23:00:00+00:00"
    assert windows["GCZ9"].source_ts_event == "2019-06-01T00:00:00+00:00"


def test_latest_activation_window_ignores_symbols_not_in_clean_set():
    records = [
        _rec("GCZ9", "2019-01-01T00:00:00+00:00", "2018-01-01T00:00:00+00:00", "2019-12-01T00:00:00+00:00"),
        _rec("GCPOISONED", "2019-01-01T00:00:00+00:00", "2018-01-01T00:00:00+00:00", "2099-12-01T00:00:00+00:00"),
    ]
    windows = gaw.latest_activation_window_per_symbol(records, clean_raw_symbols=["GCZ9"])
    assert set(windows) == {"GCZ9"}


def test_latest_activation_window_fails_closed_on_missing_symbol():
    records = [_rec("GCZ9", "2019-01-01T00:00:00+00:00", "2018-01-01T00:00:00+00:00", "2019-12-01T00:00:00+00:00")]
    with pytest.raises(gaw.GcActiveWindowError):
        gaw.latest_activation_window_per_symbol(records, clean_raw_symbols=["GCZ9", "GCMISSING"])


def test_latest_activation_window_requires_non_empty_clean_set():
    with pytest.raises(gaw.GcActiveWindowError):
        gaw.latest_activation_window_per_symbol([], clean_raw_symbols=[])


# ----------------------------------------------------------------------------------------------
# session_overlaps_active_window
# ----------------------------------------------------------------------------------------------

def test_session_strictly_inside_window_overlaps():
    assert gaw.session_overlaps_active_window(
        session_start_utc="2019-06-01T22:00:00+00:00",
        session_end_utc="2019-06-02T21:00:00+00:00",
        activation_utc="2019-01-01T00:00:00+00:00",
        expiration_utc="2019-12-01T00:00:00+00:00",
    )


def test_session_entirely_before_activation_does_not_overlap():
    assert not gaw.session_overlaps_active_window(
        session_start_utc="2018-06-01T22:00:00+00:00",
        session_end_utc="2018-06-02T21:00:00+00:00",
        activation_utc="2019-01-01T00:00:00+00:00",
        expiration_utc="2019-12-01T00:00:00+00:00",
    )


def test_session_entirely_after_expiration_does_not_overlap():
    assert not gaw.session_overlaps_active_window(
        session_start_utc="2020-01-01T22:00:00+00:00",
        session_end_utc="2020-01-02T21:00:00+00:00",
        activation_utc="2019-01-01T00:00:00+00:00",
        expiration_utc="2019-12-01T00:00:00+00:00",
    )


def test_session_spanning_expiration_instant_still_overlaps_inclusive():
    # Contract expires partway through the session's own window — still tradeable for part of
    # it, so it counts as active (see module docstring: inclusive on both contract bounds).
    assert gaw.session_overlaps_active_window(
        session_start_utc="2019-11-26T22:00:00+00:00",
        session_end_utc="2019-11-27T21:00:00+00:00",
        activation_utc="2019-01-01T00:00:00+00:00",
        expiration_utc="2019-11-26T23:30:00+00:00",
    )


def test_session_z_suffix_and_plus00_00_suffix_compare_equal():
    assert gaw.session_overlaps_active_window(
        session_start_utc="2019-06-01T22:00:00Z",
        session_end_utc="2019-06-02T21:00:00Z",
        activation_utc="2019-01-01T00:00:00+00:00",
        expiration_utc="2019-12-01T00:00:00+00:00",
    )


# ----------------------------------------------------------------------------------------------
# determine_active_contracts_for_sessions
# ----------------------------------------------------------------------------------------------

def test_determine_active_contracts_for_sessions_multiple_overlapping():
    windows = {
        "GCZ9": gaw.ActiveWindow("GCZ9", "2018-06-01T00:00:00+00:00", "2019-12-01T00:00:00+00:00", "ts"),
        "GCF0": gaw.ActiveWindow("GCF0", "2019-06-01T00:00:00+00:00", "2020-01-29T00:00:00+00:00", "ts"),
        "GCG0": gaw.ActiveWindow("GCG0", "2020-02-01T00:00:00+00:00", "2020-02-26T00:00:00+00:00", "ts"),
    }
    sessions = [
        {"session_id": "S1", "request_start_utc": "2019-07-01T22:00:00+00:00", "request_end_utc": "2019-07-02T21:00:00+00:00"},
        {"session_id": "S2", "request_start_utc": "2020-02-10T22:00:00+00:00", "request_end_utc": "2020-02-11T21:00:00+00:00"},
    ]
    result = gaw.determine_active_contracts_for_sessions(sessions, windows)
    assert result["S1"] == ("GCF0", "GCZ9")
    assert result["S2"] == ("GCG0",)


def test_determine_active_contracts_for_sessions_deterministic_sorted_output():
    windows = {
        "GCZ9": gaw.ActiveWindow("GCZ9", "2018-06-01T00:00:00+00:00", "2019-12-01T00:00:00+00:00", "ts"),
        "GCF0": gaw.ActiveWindow("GCF0", "2018-06-01T00:00:00+00:00", "2019-12-01T00:00:00+00:00", "ts"),
    }
    sessions = [
        {"session_id": "S1", "request_start_utc": "2019-07-01T22:00:00+00:00", "request_end_utc": "2019-07-02T21:00:00+00:00"},
    ]
    result = gaw.determine_active_contracts_for_sessions(sessions, windows)
    assert result["S1"] == ("GCF0", "GCZ9")
