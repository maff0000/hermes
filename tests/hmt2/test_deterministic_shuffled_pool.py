"""HMT-2 (real-money checkpoint) — tests for corpus_selection.deterministic_shuffled_pool(),
the additive helper manifest v2's holdout/random-development preservation logic depends on.

Proves it reproduces exactly what draw_random_development()/draw_protected_holdout() already
select (when truncated to the same count), given the identical inputs — this is the load-
bearing property the v2 preservation logic relies on to "continue" the original v1 draw
sequence rather than redraw from scratch.
"""
from __future__ import annotations

import datetime as dt

from market_truth.acquisition.corpus_selection import (
    SEED_DOMAIN_DEVELOPMENT_RANDOM,
    SEED_DOMAIN_PROTECTED_HOLDOUT,
    deterministic_shuffled_pool,
    draw_protected_holdout,
    draw_random_development,
)
from market_truth.acquisition.session_calendar import build_session_universe


def _universe():
    return build_session_universe(dt.date(2024, 1, 1), dt.date(2024, 12, 31))


def test_truncated_shuffled_pool_matches_draw_random_development_exactly():
    universe = _universe()
    sessions_by_date = {s.session_date: s for s in universe}
    eligible = sorted(sessions_by_date.keys())
    excluded = set()

    rows = draw_random_development(eligible, sessions_by_date, excluded, count=30)
    expected_dates = {row.session.session_date for row in rows}

    full_pool = deterministic_shuffled_pool(eligible, excluded, SEED_DOMAIN_DEVELOPMENT_RANDOM)
    truncated_dates = set(full_pool[:30])

    assert truncated_dates == expected_dates


def test_truncated_shuffled_pool_matches_draw_protected_holdout_exactly():
    universe = _universe()
    sessions_by_date = {s.session_date: s for s in universe}
    eligible = sorted(sessions_by_date.keys())
    excluded = set()

    rows = draw_protected_holdout(eligible, sessions_by_date, excluded, count=40)
    expected_dates = {row.session.session_date for row in rows}

    full_pool = deterministic_shuffled_pool(eligible, excluded, SEED_DOMAIN_PROTECTED_HOLDOUT)
    truncated_dates = set(full_pool[:40])

    assert truncated_dates == expected_dates


def test_deterministic_shuffled_pool_is_reproducible():
    universe = _universe()
    eligible = sorted(s.session_date for s in universe)
    p1 = deterministic_shuffled_pool(eligible, set(), SEED_DOMAIN_PROTECTED_HOLDOUT)
    p2 = deterministic_shuffled_pool(eligible, set(), SEED_DOMAIN_PROTECTED_HOLDOUT)
    assert p1 == p2


def test_deterministic_shuffled_pool_excludes_given_dates():
    universe = _universe()
    eligible = sorted(s.session_date for s in universe)
    excluded = set(eligible[:10])
    pool = deterministic_shuffled_pool(eligible, excluded, SEED_DOMAIN_DEVELOPMENT_RANDOM)
    assert excluded.isdisjoint(set(pool))
    assert len(pool) == len(eligible) - len(excluded)


def test_continuation_past_original_count_yields_new_undrawn_candidates():
    """The whole point of the helper: entries beyond the original `count` are genuinely new,
    not-yet-selected candidates from the SAME deterministic sequence."""
    universe = _universe()
    eligible = sorted(s.session_date for s in universe)
    pool = deterministic_shuffled_pool(eligible, set(), SEED_DOMAIN_PROTECTED_HOLDOUT)
    first_100 = set(pool[:100])
    next_20 = pool[100:120]
    assert first_100.isdisjoint(set(next_20))
