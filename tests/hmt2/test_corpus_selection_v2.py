"""HMT-2 (real-money checkpoint) — tests for market_truth.acquisition.corpus_selection_v2."""
from __future__ import annotations

import datetime as _dt

from market_truth.acquisition.corpus_selection import (
    EventRecord,
    SEED_DOMAIN_DEVELOPMENT_RANDOM,
    deterministic_shuffled_pool,
    draw_random_development,
)
from market_truth.acquisition.corpus_selection_v2 import (
    REASON_NOT_A_VALID_GOVERNED_SESSION,
    REASON_NOW_A_SELECTED_SCHEDULED_EVENT_SESSION,
    build_reselected_event_records,
    group_session_ids_by_year,
    hamilton_apportionment,
    preserve_or_backfill,
    select_final_sample_by_year,
    stratified_scheduled_event_reselection,
)
from market_truth.acquisition.reference_series import ReferenceQualityTag, SessionReferenceResult
from market_truth.acquisition.session_calendar import build_session_universe


def _universe(start, end):
    return build_session_universe(_dt.date.fromisoformat(start), _dt.date.fromisoformat(end))


# ----------------------------------------------------------------------------------------------
# stratified_scheduled_event_reselection
# ----------------------------------------------------------------------------------------------

def test_reselection_picks_target_per_class_when_enough_years_and_dates_available():
    universe = _universe("2020-01-01", "2029-12-31")
    sessions_by_date = {s.session_date: s for s in universe}
    records = []
    for year in range(2020, 2030):
        for month in (1, 4, 7, 10):
            records.append(EventRecord("FOMC_DECISION", _dt.date(year, month, 6), "FED", "ref", None))
    result = stratified_scheduled_event_reselection(records, sessions_by_date, target_per_class=10)
    fomc_dates = [d for cls, d in result.chosen_class_date_pairs if cls == "FOMC_DECISION"]
    assert len(fomc_dates) == 10


def test_reselection_spreads_quota_evenly_across_years_with_remainder_to_earliest_years():
    # 3 years, target 10 -> base=3, remainder=1 -> years[0] gets 4, others get 3.
    universe = _universe("2020-01-01", "2022-12-31")
    sessions_by_date = {s.session_date: s for s in universe}
    records = [EventRecord("FOMC_DECISION", _dt.date(y, 1, 6), "FED", "ref", None) for y in (2020, 2021, 2022)
               for _ in range(1)]
    # Give each year several candidate dates so quotas > 1 are satisfiable.
    # Use the first 4 genuine governed session dates of January each year (guaranteed valid
    # weekdays, no holiday) rather than hand-picked day numbers (which can silently collide
    # with a holiday like MLK Day in some years).
    records = []
    for year in (2020, 2021, 2022):
        january_sessions = sorted(d for d in sessions_by_date if d.year == year and d.month == 1)[:4]
        for d in january_sessions:
            records.append(EventRecord("FOMC_DECISION", d, "FED", "ref", None))
    result = stratified_scheduled_event_reselection(records, sessions_by_date, target_per_class=10)
    quota = result.per_class_per_year_quota["FOMC_DECISION"]
    assert quota == {2020: 4, 2021: 3, 2022: 3}
    fomc_dates_by_year = {}
    for cls, d in result.chosen_class_date_pairs:
        fomc_dates_by_year.setdefault(d.year, []).append(d)
    assert len(fomc_dates_by_year[2020]) == 4
    assert len(fomc_dates_by_year[2021]) == 3
    assert len(fomc_dates_by_year[2022]) == 3


def test_reselection_caps_at_available_when_fewer_dates_than_quota():
    universe = _universe("2020-01-01", "2020-12-31")
    sessions_by_date = {s.session_date: s for s in universe}
    records = [EventRecord("FOMC_DECISION", _dt.date(2020, 1, 6), "FED", "ref", None)]  # only 1 available
    result = stratified_scheduled_event_reselection(records, sessions_by_date, target_per_class=20)
    fomc_dates = [d for cls, d in result.chosen_class_date_pairs if cls == "FOMC_DECISION"]
    assert fomc_dates == [_dt.date(2020, 1, 6)]


def test_reselection_excludes_event_dates_that_are_not_valid_sessions():
    universe = _universe("2020-01-01", "2020-12-31")
    sessions_by_date = {s.session_date: s for s in universe}
    weekend_date = _dt.date(2020, 1, 4)  # a Saturday, never a session
    assert weekend_date not in sessions_by_date
    records = [EventRecord("FOMC_DECISION", weekend_date, "FED", "ref", None)]
    result = stratified_scheduled_event_reselection(records, sessions_by_date, target_per_class=20)
    assert result.chosen_class_date_pairs == ()


def test_reselection_is_fully_deterministic():
    universe = _universe("2020-01-01", "2024-12-31")
    sessions_by_date = {s.session_date: s for s in universe}
    records = []
    for year in range(2020, 2025):
        for month in range(1, 13):
            records.append(EventRecord("CPI_RELEASE", _dt.date(year, month, 10), "BLS", "ref", None))
    r1 = stratified_scheduled_event_reselection(records, sessions_by_date, target_per_class=20)
    r2 = stratified_scheduled_event_reselection(records, sessions_by_date, target_per_class=20)
    assert r1.chosen_class_date_pairs == r2.chosen_class_date_pairs


def test_build_reselected_event_records_filters_to_chosen_pairs_only():
    records = [
        EventRecord("FOMC_DECISION", _dt.date(2026, 1, 6), "FED", "ref", None),
        EventRecord("CPI_RELEASE", _dt.date(2026, 1, 6), "BLS", "ref", None),  # same date, different class
    ]
    chosen = [("FOMC_DECISION", _dt.date(2026, 1, 6))]
    filtered = build_reselected_event_records(records, chosen)
    assert len(filtered) == 1
    assert filtered[0].event_class == "FOMC_DECISION"


# ----------------------------------------------------------------------------------------------
# preserve_or_backfill
# ----------------------------------------------------------------------------------------------

def test_preserve_or_backfill_keeps_everything_when_nothing_is_invalid():
    universe = _universe("2024-01-01", "2024-12-31")
    sessions_by_date = {s.session_date: s for s in universe}
    eligible = sorted(sessions_by_date.keys())
    original_rows = draw_random_development(eligible, sessions_by_date, set(), count=20)
    original_dates = sorted(row.session.session_date for row in original_rows)
    full_pool = deterministic_shuffled_pool(eligible, set(), SEED_DOMAIN_DEVELOPMENT_RANDOM)

    result = preserve_or_backfill(
        original_v1_dates=original_dates,
        full_v1_shuffled_pool=full_pool,
        invalid_if_in=set(),
        already_used_for_backfill=set(),
        valid_session_dates=set(sessions_by_date.keys()),
        target_count=20,
    )
    assert result.final_dates == tuple(original_dates)
    assert result.invalidated_dates == ()
    assert result.backfilled_dates == ()


def test_preserve_or_backfill_replaces_invalidated_dates_from_the_pool_continuation():
    universe = _universe("2024-01-01", "2024-12-31")
    sessions_by_date = {s.session_date: s for s in universe}
    eligible = sorted(sessions_by_date.keys())
    original_rows = draw_random_development(eligible, sessions_by_date, set(), count=20)
    original_dates = sorted(row.session.session_date for row in original_rows)
    full_pool = deterministic_shuffled_pool(eligible, set(), SEED_DOMAIN_DEVELOPMENT_RANDOM)

    invalidate_one = {original_dates[0]}
    result = preserve_or_backfill(
        original_v1_dates=original_dates,
        full_v1_shuffled_pool=full_pool,
        invalid_if_in=invalidate_one,
        already_used_for_backfill=set(),
        valid_session_dates=set(sessions_by_date.keys()),
        target_count=20,
    )
    assert len(result.final_dates) == 20
    assert original_dates[0] not in result.final_dates
    assert original_dates[0] in result.invalidated_dates
    assert len(result.backfilled_dates) == 1
    # The backfill must come from the pool continuation, not be one of the other 19 originals.
    assert result.backfilled_dates[0] not in set(original_dates)


def test_preserve_or_backfill_skips_already_used_candidates_during_backfill():
    universe = _universe("2024-01-01", "2024-12-31")
    sessions_by_date = {s.session_date: s for s in universe}
    eligible = sorted(sessions_by_date.keys())
    original_rows = draw_random_development(eligible, sessions_by_date, set(), count=10)
    original_dates = sorted(row.session.session_date for row in original_rows)
    full_pool = deterministic_shuffled_pool(eligible, set(), SEED_DOMAIN_DEVELOPMENT_RANDOM)

    # Force the very next candidate in the pool to be "already used elsewhere" so the function
    # must skip past it to the one after.
    next_candidate = full_pool[10]
    result = preserve_or_backfill(
        original_v1_dates=original_dates,
        full_v1_shuffled_pool=full_pool,
        invalid_if_in={original_dates[0]},
        already_used_for_backfill={next_candidate},
        valid_session_dates=set(sessions_by_date.keys()),
        target_count=10,
    )
    assert next_candidate not in result.final_dates
    assert len(result.final_dates) == 10


def test_preserve_or_backfill_is_deterministic_across_repeated_calls():
    universe = _universe("2024-01-01", "2024-12-31")
    sessions_by_date = {s.session_date: s for s in universe}
    eligible = sorted(sessions_by_date.keys())
    original_rows = draw_random_development(eligible, sessions_by_date, set(), count=15)
    original_dates = sorted(row.session.session_date for row in original_rows)
    full_pool = deterministic_shuffled_pool(eligible, set(), SEED_DOMAIN_DEVELOPMENT_RANDOM)
    invalid = set(original_dates[:3])

    r1 = preserve_or_backfill(
        original_v1_dates=original_dates, full_v1_shuffled_pool=full_pool, invalid_if_in=invalid,
        already_used_for_backfill=set(), valid_session_dates=set(sessions_by_date.keys()), target_count=15,
    )
    r2 = preserve_or_backfill(
        original_v1_dates=original_dates, full_v1_shuffled_pool=full_pool, invalid_if_in=invalid,
        already_used_for_backfill=set(), valid_session_dates=set(sessions_by_date.keys()), target_count=15,
    )
    assert r1 == r2


def test_preserve_or_backfill_reports_zero_when_pool_exhausted_rather_than_crashing():
    universe = _universe("2024-01-06", "2024-01-10")  # tiny universe, 5 sessions
    sessions_by_date = {s.session_date: s for s in universe}
    eligible = sorted(sessions_by_date.keys())
    full_pool = deterministic_shuffled_pool(eligible, set(), SEED_DOMAIN_DEVELOPMENT_RANDOM)
    original_dates = sorted(full_pool[:3])

    result = preserve_or_backfill(
        original_v1_dates=original_dates,
        full_v1_shuffled_pool=full_pool,
        invalid_if_in=set(original_dates),  # invalidate everything
        already_used_for_backfill=set(),
        valid_session_dates=set(sessions_by_date.keys()),
        target_count=3,
    )
    # Pool has only 5 entries total; 3 were invalidated, at most 2 remain for backfill.
    assert len(result.final_dates) <= 2
    assert len(result.final_dates) == len(result.backfilled_dates)


# ----------------------------------------------------------------------------------------------
# HMT-2 CORRECTION — stratified_scheduled_event_reselection(exclude_dates=...)
# ----------------------------------------------------------------------------------------------

def test_reselection_routes_around_excluded_dates_never_picking_them():
    universe = _universe("2020-01-01", "2020-12-31")
    sessions_by_date = {s.session_date: s for s in universe}
    january_sessions = sorted(d for d in sessions_by_date if d.month == 1)[:5]
    records = [EventRecord("FOMC_DECISION", d, "FED", "ref", None) for d in january_sessions]
    excluded = frozenset({january_sessions[0], january_sessions[1]})

    result = stratified_scheduled_event_reselection(
        records, sessions_by_date, target_per_class=20, exclude_dates=excluded
    )
    chosen_dates = {d for cls, d in result.chosen_class_date_pairs}
    assert chosen_dates.isdisjoint(excluded)
    assert chosen_dates == set(january_sessions[2:])


def test_reselection_with_no_exclude_dates_is_unchanged_from_v1_behaviour():
    """Backward compatibility: omitting `exclude_dates` (or passing empty) reproduces the exact
    same result as before this checkpoint's correction."""
    universe = _universe("2020-01-01", "2020-12-31")
    sessions_by_date = {s.session_date: s for s in universe}
    january_sessions = sorted(d for d in sessions_by_date if d.month == 1)[:5]
    records = [EventRecord("FOMC_DECISION", d, "FED", "ref", None) for d in january_sessions]

    default_result = stratified_scheduled_event_reselection(records, sessions_by_date, target_per_class=20)
    explicit_empty_result = stratified_scheduled_event_reselection(
        records, sessions_by_date, target_per_class=20, exclude_dates=frozenset()
    )
    assert default_result.chosen_class_date_pairs == explicit_empty_result.chosen_class_date_pairs


# ----------------------------------------------------------------------------------------------
# HMT-2 CORRECTION — preserve_or_backfill: collision_reason labelling + the two permitted
# RANDOM_DEVELOPMENT reasons / the "holdout never invalidated by collision" contract.
# ----------------------------------------------------------------------------------------------

def test_preserve_or_backfill_holdout_style_call_with_empty_invalid_if_in_never_invalidates_for_collision():
    """Mirrors exactly how generate_selection_manifest_v2.py calls this for PROTECTED_HOLDOUT:
    `invalid_if_in=set()`. No matter what the caller's OWN broader context looks like (e.g. a
    full event snapshot that happens to cover every one of these dates), this function cannot
    invalidate a single kept date for a collision reason when `invalid_if_in` is empty — the
    only invalidation category reachable is genuine session-invalidity."""
    universe = _universe("2024-01-01", "2024-12-31")
    sessions_by_date = {s.session_date: s for s in universe}
    eligible = sorted(sessions_by_date.keys())
    original_rows = draw_random_development(eligible, sessions_by_date, set(), count=20)
    original_dates = sorted(row.session.session_date for row in original_rows)
    full_pool = deterministic_shuffled_pool(eligible, set(), SEED_DOMAIN_DEVELOPMENT_RANDOM)

    result = preserve_or_backfill(
        original_v1_dates=original_dates,
        full_v1_shuffled_pool=full_pool,
        invalid_if_in=set(),  # the holdout contract: never a collision-based invalidation
        already_used_for_backfill=set(),
        valid_session_dates=set(sessions_by_date.keys()),
        target_count=20,
    )
    assert result.invalidated_dates == ()
    assert set(result.invalidation_reasons.values()) <= {REASON_NOT_A_VALID_GOVERNED_SESSION}


def test_preserve_or_backfill_genuine_session_invalidity_still_works_with_empty_invalid_if_in():
    universe = _universe("2024-01-01", "2024-12-31")
    sessions_by_date = {s.session_date: s for s in universe}
    eligible = sorted(sessions_by_date.keys())
    original_rows = draw_random_development(eligible, sessions_by_date, set(), count=10)
    original_dates = sorted(row.session.session_date for row in original_rows)
    full_pool = deterministic_shuffled_pool(eligible, set(), SEED_DOMAIN_DEVELOPMENT_RANDOM)

    valid_minus_one = set(sessions_by_date.keys()) - {original_dates[0]}
    result = preserve_or_backfill(
        original_v1_dates=original_dates,
        full_v1_shuffled_pool=full_pool,
        invalid_if_in=set(),
        already_used_for_backfill=set(),
        valid_session_dates=valid_minus_one,
        target_count=10,
    )
    assert result.invalidation_reasons[original_dates[0]] == REASON_NOT_A_VALID_GOVERNED_SESSION
    assert len(result.final_dates) == 10


def test_preserve_or_backfill_random_development_style_call_only_uses_the_two_permitted_reasons():
    """Mirrors exactly how generate_selection_manifest_v2.py calls this for RANDOM_DEVELOPMENT:
    `invalid_if_in=<final selected SCHEDULED_EVENT dates>`,
    `collision_reason=REASON_NOW_A_SELECTED_SCHEDULED_EVENT_SESSION`. The replacement log must
    contain ONLY that reason and/or genuine session-invalidity — never anything volatility- or
    compression-shaped, because this function was never even given such a set to check against."""
    universe = _universe("2024-01-01", "2024-12-31")
    sessions_by_date = {s.session_date: s for s in universe}
    eligible = sorted(sessions_by_date.keys())
    original_rows = draw_random_development(eligible, sessions_by_date, set(), count=20)
    original_dates = sorted(row.session.session_date for row in original_rows)
    full_pool = deterministic_shuffled_pool(eligible, set(), SEED_DOMAIN_DEVELOPMENT_RANDOM)

    final_selected_event_dates = {original_dates[0], original_dates[1]}
    result = preserve_or_backfill(
        original_v1_dates=original_dates,
        full_v1_shuffled_pool=full_pool,
        invalid_if_in=final_selected_event_dates,
        already_used_for_backfill=final_selected_event_dates,
        valid_session_dates=set(sessions_by_date.keys()),
        target_count=20,
        collision_reason=REASON_NOW_A_SELECTED_SCHEDULED_EVENT_SESSION,
    )
    permitted = {REASON_NOW_A_SELECTED_SCHEDULED_EVENT_SESSION, REASON_NOT_A_VALID_GOVERNED_SESSION}
    assert set(result.invalidation_reasons.values()) <= permitted
    assert len(result.invalidated_dates) == 2


# ----------------------------------------------------------------------------------------------
# hamilton_apportionment — hand-verified synthetic cases
# ----------------------------------------------------------------------------------------------

def test_hamilton_apportionment_exact_division_no_remainder():
    # 4 years, 10/10/10/10 candidates, target 40 -> raw is exact, no remainder seats needed.
    result = hamilton_apportionment({2020: 10, 2021: 10, 2022: 10, 2023: 10}, target_total=40)
    assert result.final_total == 40
    assert not result.shortfall
    for a in result.per_year:
        assert a.final_allocation == 10
        assert a.remainder == 0.0


def test_hamilton_apportionment_hand_verified_remainder_distribution():
    # C_y = {2020: 1, 2021: 2, 2022: 7}, total=10, target=6.
    # raw: 2020 = 6*1/10=0.6, 2021=6*2/10=1.2, 2022=6*7/10=4.2
    # floor: 2020=0, 2021=1, 2022=4 -> sum floor = 5, remaining seats = 1
    # remainders: 2020=0.6, 2021=0.2, 2022=0.2 -> largest is 2020 (0.6) -> gets the 1 bonus seat.
    # final: 2020=1, 2021=1, 2022=4 -> sum = 6.
    result = hamilton_apportionment({2020: 1, 2021: 2, 2022: 7}, target_total=6)
    by_year = {a.year: a for a in result.per_year}
    assert by_year[2020].final_allocation == 1
    assert by_year[2021].final_allocation == 1
    assert by_year[2022].final_allocation == 4
    assert result.final_total == 6
    assert not result.shortfall


def test_hamilton_apportionment_exact_tie_break_by_ascending_year():
    # C_y = {2022: 5, 2021: 5, 2020: 5}, total=15, target=2.
    # raw = 2/15*5 = 0.6667 for every year -> floor=0 for all -> remaining seats=2.
    # All three remainders are EXACTLY equal (0.6667) -> tie-break by ascending year:
    # 2020 and 2021 get the two bonus seats (ascending order), 2022 gets none.
    result = hamilton_apportionment({2022: 5, 2021: 5, 2020: 5}, target_total=2)
    by_year = {a.year: a for a in result.per_year}
    assert by_year[2020].final_allocation == 1
    assert by_year[2021].final_allocation == 1
    assert by_year[2022].final_allocation == 0
    assert result.final_total == 2


def test_hamilton_apportionment_shortfall_when_fewer_candidates_than_target():
    # Only 4 total candidates across all years, target asks for 10 -> take everyone, disclose.
    result = hamilton_apportionment({2020: 1, 2021: 3}, target_total=10)
    assert result.shortfall is True
    assert result.final_total == 4
    by_year = {a.year: a for a in result.per_year}
    assert by_year[2020].final_allocation == 1
    assert by_year[2021].final_allocation == 3


def test_hamilton_apportionment_exact_number_of_candidates_as_target_is_not_a_shortfall():
    result = hamilton_apportionment({2020: 3, 2021: 3}, target_total=6)
    assert result.shortfall is False
    assert result.final_total == 6


def test_hamilton_apportionment_zero_candidates_in_a_year_gets_zero_seats():
    result = hamilton_apportionment({2020: 0, 2021: 10}, target_total=5)
    by_year = {a.year: a for a in result.per_year}
    assert by_year[2020].final_allocation == 0
    assert by_year[2021].final_allocation == 5
    assert result.final_total == 5


def test_hamilton_apportionment_per_year_allocation_never_exceeds_candidate_count():
    result = hamilton_apportionment({2020: 2, 2021: 2, 2022: 2, 2023: 100}, target_total=50)
    for a in result.per_year:
        assert a.final_allocation <= a.candidate_count


# ----------------------------------------------------------------------------------------------
# select_final_sample_by_year — determinism + never selects by magnitude
# ----------------------------------------------------------------------------------------------

def _synthetic_reference_results(session_ids_by_year: dict) -> dict:
    """Builds synthetic SessionReferenceResult objects (never real data) — session_log_range
    is set to a value CORRELATED with the session id ordinal, so a test can check the draw is
    NOT simply picking the highest-magnitude members."""
    results = {}
    for year, ids in session_ids_by_year.items():
        for i, sid in enumerate(ids):
            results[sid] = SessionReferenceResult(
                session_id=sid,
                session_date=_dt.date(year, 1, 1) + _dt.timedelta(days=i),
                quality_tag=ReferenceQualityTag.REFERENCE_COMPLETE,
                session_log_range=float(i),  # ascending with index — deliberately monotonic
                bar_count=24,
                expected_hour_count=24,
                coverage_ratio=1.0,
                distinct_instrument_ids=(1,),
            )
    return results


def test_select_final_sample_by_year_is_deterministic_across_repeated_calls():
    pool_by_year = {2020: [f"GC-2020-{i:02d}" for i in range(10)], 2021: [f"GC-2021-{i:02d}" for i in range(10)]}
    allocation = hamilton_apportionment({2020: 10, 2021: 10}, target_total=6)

    r1 = select_final_sample_by_year(pool_by_year, allocation, "HIGH_VOL_NON_EVENT_V2")
    r2 = select_final_sample_by_year(pool_by_year, allocation, "HIGH_VOL_NON_EVENT_V2")
    assert r1 == r2


def test_select_final_sample_by_year_respects_final_allocation_counts():
    pool_by_year = {2020: [f"GC-2020-{i:02d}" for i in range(10)], 2021: [f"GC-2021-{i:02d}" for i in range(10)]}
    allocation = hamilton_apportionment({2020: 10, 2021: 10}, target_total=6)
    chosen = select_final_sample_by_year(pool_by_year, allocation, "HIGH_VOL_NON_EVENT_V2")

    by_year = {a.year: a.final_allocation for a in allocation.per_year}
    chosen_2020 = [sid for sid in chosen if sid.startswith("GC-2020")]
    chosen_2021 = [sid for sid in chosen if sid.startswith("GC-2021")]
    assert len(chosen_2020) == by_year[2020]
    assert len(chosen_2021) == by_year[2021]


def test_select_final_sample_by_year_does_not_always_pick_the_highest_magnitude_members():
    """This is the whole point of the seeded draw over magnitude-ranking: given a large pool
    where log-range is monotonic in id order, the drawn sample must NOT simply be the top-K
    ids by construction. Uses a large pool/small K so a magnitude-ranking implementation would
    deterministically differ from a genuine random draw with overwhelming probability."""
    year_ids = [f"GC-2099-{i:03d}" for i in range(200)]
    pool_by_year = {2099: year_ids}
    allocation = hamilton_apportionment({2099: 200}, target_total=5)
    chosen = select_final_sample_by_year(pool_by_year, allocation, "COMPRESSION_V2")
    top_5_by_magnitude = set(year_ids[-5:])  # highest log_range (index-correlated) members
    assert set(chosen) != top_5_by_magnitude


def test_group_session_ids_by_year_groups_correctly():
    results = _synthetic_reference_results({2020: ["A", "B"], 2021: ["C"]})
    grouped = group_session_ids_by_year(["A", "B", "C"], results)
    assert grouped == {2020: ["A", "B"], 2021: ["C"]}
