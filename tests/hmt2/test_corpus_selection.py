"""HMT-2A — corpus_selection.py determinism, protection, and dedup tests.

Uses a small synthetic session universe + synthetic event records for fast, focused unit
tests, and also exercises the full real 2017-05-21..2025-12-31 universe with the real,
committed scheduled-macro-event snapshot for an end-to-end integration check (mirrors what
research/hmt2/generate_selection_manifest.py does, without touching the committed manifest
file itself).
"""
import datetime as dt
import json
from pathlib import Path

from market_truth.acquisition.corpus_selection import (
    EventRecord,
    Stratum,
    domain_seed_hex,
    load_event_records,
    select_corpus,
)
from market_truth.acquisition.session_calendar import build_session_universe

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SNAPSHOT_PATH = REPO_ROOT / "research" / "hmt2" / "scheduled-macro-event-snapshot-v1.json"


def _synthetic_events():
    # A small, hand-built synthetic event set over a short, real trading-day range —
    # deliberately NOT the real committed snapshot, to keep this unit test fast and self-
    # contained. Dates chosen are genuine GC trading days (weekdays, no holiday in range).
    return [
        EventRecord("FOMC_DECISION", dt.date(2024, 6, 12), "FEDERAL_RESERVE", "test-ref", None),
        EventRecord("CPI_RELEASE", dt.date(2024, 6, 12), "BLS", "test-ref", None),  # same-day collision
        EventRecord("EMPLOYMENT_SITUATION_NFP", dt.date(2024, 6, 7), "BLS", "test-ref", None),
        EventRecord("PCE_RELEASE", dt.date(2024, 6, 27), "BEA", "test-ref", None),
    ]


def _synthetic_universe():
    return build_session_universe(dt.date(2024, 1, 1), dt.date(2024, 12, 31))


def test_same_day_multi_class_events_dedup_into_one_row():
    result = select_corpus(_synthetic_universe(), _synthetic_events(), random_development_target=5, protected_holdout_target=5)
    matching = [r for r in result.scheduled_events if r.session.session_date == dt.date(2024, 6, 12)]
    assert len(matching) == 1
    assert matching[0].event_class == "CPI_RELEASE+FOMC_DECISION"


def test_selection_is_byte_for_byte_reproducible():
    universe = _synthetic_universe()
    events = _synthetic_events()
    r1 = select_corpus(universe, events, random_development_target=10, protected_holdout_target=10)
    r2 = select_corpus(universe, events, random_development_target=10, protected_holdout_target=10)

    def ids(rows):
        return [row.session.session_id for row in rows]

    assert ids(r1.scheduled_events) == ids(r2.scheduled_events)
    assert ids(r1.matched_controls) == ids(r2.matched_controls)
    assert ids(r1.random_development) == ids(r2.random_development)
    assert ids(r1.protected_holdout) == ids(r2.protected_holdout)


def test_holdout_sessions_never_appear_as_a_non_holdout_stratum():
    universe = _synthetic_universe()
    events = _synthetic_events()
    result = select_corpus(universe, events, random_development_target=40, protected_holdout_target=40)
    holdout_ids = {row.session.session_id for row in result.protected_holdout}
    non_holdout_ids = {
        row.session.session_id
        for row in (result.scheduled_events + result.matched_controls + result.random_development)
    }
    assert holdout_ids.isdisjoint(non_holdout_ids)


def test_no_duplicate_session_assignment_across_any_stratum():
    universe = _synthetic_universe()
    events = _synthetic_events()
    result = select_corpus(universe, events, random_development_target=40, protected_holdout_target=40)
    all_ids = [row.session.session_id for row in result.all_rows()]
    assert len(all_ids) == len(set(all_ids))


def test_matched_control_reproducibility_and_no_self_match():
    universe = _synthetic_universe()
    events = _synthetic_events()
    r1 = select_corpus(universe, events, random_development_target=5, protected_holdout_target=5)
    r2 = select_corpus(universe, events, random_development_target=5, protected_holdout_target=5)
    pairs1 = sorted((row.matched_parent_session, row.session.session_id) for row in r1.matched_controls)
    pairs2 = sorted((row.matched_parent_session, row.session.session_id) for row in r2.matched_controls)
    assert pairs1 == pairs2
    event_ids = {row.session.session_id for row in r1.scheduled_events}
    control_ids = {row.session.session_id for row in r1.matched_controls}
    assert event_ids.isdisjoint(control_ids)


def test_matched_controls_are_same_weekday_as_their_event():
    universe = _synthetic_universe()
    events = _synthetic_events()
    result = select_corpus(universe, events, random_development_target=5, protected_holdout_target=5)
    events_by_id = {row.session.session_id: row.session.session_date for row in result.scheduled_events}
    for control in result.matched_controls:
        parent_date = events_by_id[control.matched_parent_session]
        assert control.session.session_date.weekday() == parent_date.weekday()


def test_pending_strata_contribute_zero_sessions():
    universe = _synthetic_universe()
    events = _synthetic_events()
    result = select_corpus(universe, events, random_development_target=5, protected_holdout_target=5)
    assert result.high_vol_non_event == []
    assert result.compression == []
    assert result.reference_series_status == "PENDING_REFERENCE_SERIES_DATA"


def test_deterministic_seed_digests_are_stable_and_documented():
    # These exact values MUST match what is recorded in docs/research/
    # hmt2-corpus-selection-methodology-v1.md and in the committed manifest's metadata.
    dev = domain_seed_hex("DEVELOPMENT_RANDOM")
    holdout = domain_seed_hex("PROTECTED_HOLDOUT")
    assert dev == "f0a08806a405bacd052059a3129548741a563149ea5e56bf72dac71b582021a3"
    assert holdout == "df554685c2c9d063a0e1f4a783fad07657847ca2f47059571befde71fc8b61a8"
    assert len(dev) == 64 and len(holdout) == 64
    assert dev != holdout


# --------------------------------------------------------------------------------------------
# Integration check against the real, committed scheduled-macro-event snapshot.
# --------------------------------------------------------------------------------------------

def test_real_snapshot_full_corpus_selection_has_no_duplicates_and_protects_holdout():
    with open(SNAPSHOT_PATH, encoding="utf-8") as f:
        snapshot = json.load(f)
    event_records = load_event_records(snapshot)
    universe = build_session_universe(dt.date(2017, 5, 21), dt.date(2025, 12, 31))

    result = select_corpus(universe, event_records)

    all_ids = [row.session.session_id for row in result.all_rows()]
    assert len(all_ids) == len(set(all_ids))

    holdout_ids = {row.session.session_id for row in result.protected_holdout}
    non_holdout_ids = set(all_ids) - holdout_ids
    assert holdout_ids.isdisjoint(non_holdout_ids)

    # Every scheduled event must be a real event class from the taxonomy.
    known_classes = {"FOMC_DECISION", "CPI_RELEASE", "EMPLOYMENT_SITUATION_NFP", "PCE_RELEASE"}
    for row in result.scheduled_events:
        for cls in row.event_class.split("+"):
            assert cls in known_classes

    counts = result.counts()
    assert counts["SCHEDULED_EVENT"] > 0
    assert counts["MATCHED_CONTROL"] > 0
    assert counts["RANDOM_DEVELOPMENT"] > 0
    assert counts["PROTECTED_HOLDOUT"] > 0
    assert counts["HIGH_VOL_NON_EVENT"] == 0
    assert counts["COMPRESSION"] == 0


def test_real_snapshot_selection_is_reproducible_end_to_end():
    with open(SNAPSHOT_PATH, encoding="utf-8") as f:
        snapshot = json.load(f)
    event_records = load_event_records(snapshot)
    universe = build_session_universe(dt.date(2017, 5, 21), dt.date(2025, 12, 31))

    r1 = select_corpus(universe, event_records)
    r2 = select_corpus(universe, event_records)

    ids1 = [row.session.session_id for row in r1.all_rows()]
    ids2 = [row.session.session_id for row in r2.all_rows()]
    assert ids1 == ids2
