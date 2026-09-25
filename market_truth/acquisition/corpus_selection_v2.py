"""HMT-2 (real-money checkpoint) — manifest v2 selection orchestration.

Additive to, and does NOT modify, `corpus_selection.py`'s frozen v1 algorithm (`select_corpus`,
`draw_random_development`, `draw_protected_holdout`, etc. are all reused UNCHANGED — v1's own
manifest remains byte-reproducible; see `tests/hmt2/test_corpus_selection.py` and the top-level
reproduction script). This module implements ONLY what genuinely changed for v2.

HMT-2 CORRECTION (this checkpoint, superseding an earlier same-checkpoint uncommitted draft):
an earlier version of this module's *caller* (`research/hmt2/generate_selection_manifest_v2.py`)
misused `preserve_or_backfill()` below by passing it collision sets that caused
PROTECTED_HOLDOUT/RANDOM_DEVELOPMENT sessions to be replaced merely for colliding with another
stratum's classification. `preserve_or_backfill()` ITSELF was, and remains, a correct, generic,
reason-agnostic primitive (unchanged here except for one additive, backward-compatible
`collision_reason` parameter so a caller can label a genuine collision-invalidation with an
accurate, stratum-specific reason string instead of one shared generic string) — the bug was
entirely in what the caller passed as `invalid_if_in`. See the corrected caller and the WO final
report for the full disclosed reasoning.

  1. `stratified_scheduled_event_reselection()` — deterministically reselects a smaller, broader,
     year-and-class-balanced `SCHEDULED_EVENT` sample from the now-complete v2 event snapshot,
     instead of carrying forward all of v1's 124 sessions (per the architect's brief). Gained an
     additive `exclude_dates` parameter (default empty, backward compatible) so a date already
     claimed by the higher-precedence `PROTECTED_HOLDOUT` stratum is routed around rather than
     also being picked as a `SCHEDULED_EVENT` row (which would violate the "exactly one
     primary_stratum per session" invariant) — PROTECTED_HOLDOUT still gets a descriptive
     `secondary_context` tag for that date, computed separately by the caller.
  2. `preserve_or_backfill()` — the "preserve v1's exact selections where they remain valid,
     else draw the next deterministic candidate from the SAME seeded sequence" mechanic for
     `RANDOM_DEVELOPMENT`/`PROTECTED_HOLDOUT`, built on the additive
     `corpus_selection.deterministic_shuffled_pool()` helper.
  3. `hamilton_apportionment()` / `select_final_sample_by_year()` — the year-stratified
     largest-remainder allocation and deterministic domain-separated seeded draw that reduces a
     raw per-year candidate POOL (e.g. the `HIGH_VOL_NON_EVENT`/`COMPRESSION` top/bottom-20%
     pools from `reference_series.select_high_vol_and_compression_candidates()`) down to a final
     target sample (~70), WITHOUT selecting by log-range magnitude within the pool — see each
     function's own docstring.

Final precedence order for v2 (the architect's brief, binding — NOTE this differs from v1's own
precedence order: RANDOM_DEVELOPMENT moves up above MATCHED_CONTROL/HIGH_VOL_NON_EVENT/
COMPRESSION for v2):

  1. PROTECTED_HOLDOUT   — never displaced by anything; may carry a descriptive
     `secondary_context` tag (e.g. "FOMC_DECISION") when it happens to coincide with an event
     date, but this never changes its `primary_stratum` or causes replacement.
  2. SCHEDULED_EVENT     — reselected (~80) from the full v2 event snapshot, routed around
     PROTECTED_HOLDOUT dates (never displaces them).
  3. RANDOM_DEVELOPMENT  — v1's 50 preserved wherever still valid; replaced ONLY if (a) the
     date is no longer a valid governed session, or (b) the date is now one of the FINAL
     selected SCHEDULED_EVENT sessions (an "ordinary baseline" day that turns out to secretly be
     a selected catalyst day). Never replaced for a volatility/compression classification —
     that would condition the random baseline on observed market behaviour, exactly what this
     rule exists to prevent.
  4. MATCHED_CONTROL     — one per final selected SCHEDULED_EVENT session, routed around
     PROTECTED_HOLDOUT and RANDOM_DEVELOPMENT (never displaces either).
  5. HIGH_VOL_NON_EVENT  — routes around all higher-precedence strata; final ~70 sample drawn
     from the eligible top-20%-within-year candidate pool via Hamilton allocation + seeded draw.
  6. COMPRESSION         — routes around all higher-precedence strata; same mechanic as #5
     against the bottom-20%-within-year pool.
"""
from __future__ import annotations

import datetime as _dt
import math
from dataclasses import dataclass
from typing import Mapping, Sequence

from market_truth.acquisition.corpus_selection import (
    EventRecord,
    SelectedSession,
    Stratum,
    deterministic_shuffled_pool,
    match_controls,
    seeded_random,
    select_scheduled_events,
)
from market_truth.acquisition.reference_series import (
    SessionReferenceResult,
    select_high_vol_and_compression_candidates,
)
from market_truth.acquisition.session_calendar import SessionRecord

CORPUS_SELECTION_V2_VERSION = "hmt2-corpus-selection-v2"

TARGET_SCHEDULED_EVENT_PER_CLASS_V2 = 20
SCHEDULED_EVENT_CLASSES_V2 = (
    "FOMC_DECISION",
    "CPI_RELEASE",
    "EMPLOYMENT_SITUATION_NFP",
    "PCE_RELEASE",
)

# HMT-2 CORRECTION — new, final-sample targets for the two formula-driven strata. These are NOT
# the raw per-year top/bottom-20% candidate POOL sizes (those are computed by
# `reference_series.select_high_vol_and_compression_candidates()` and can be much larger — the
# WO final report records the real pool sizes); they are the target FINAL paid-corpus sample
# size, year-stratified via `hamilton_apportionment()` below.
TARGET_HIGH_VOL_NON_EVENT_V2 = 70
TARGET_COMPRESSION_V2 = 70

# New, domain-separated seed constants for the two formula-driven strata' final sampling draw —
# same SHA256("HERMES|HMT-2|GC-MBP1|corpus-selection-v1|{BASE_SHA}|{domain}") construction as
# every other seed domain in this checkpoint (see corpus_selection.domain_seed_string), reusing
# the SAME BASE_SHA constant already established there — never inventing a new one.
SEED_DOMAIN_HIGH_VOL_NON_EVENT_V2 = "HIGH_VOL_NON_EVENT_V2"
SEED_DOMAIN_COMPRESSION_V2 = "COMPRESSION_V2"

# Default, generic collision-invalidation reason string — kept for backward compatibility with
# existing callers/tests of `preserve_or_backfill()` that do not pass an explicit
# `collision_reason`. A v2-checkpoint-correct caller MUST pass an accurate, stratum- and
# category-specific reason (see generate_selection_manifest_v2.py) rather than relying on this
# default, which deliberately does NOT claim any specific invalidity category.
DEFAULT_COLLISION_REASON = "COLLIDES_WITH_CALLER_SUPPLIED_EXCLUSION_SET"

# The two, and only two, reasons `generate_selection_manifest_v2.py` may ever use to explain a
# genuine RANDOM_DEVELOPMENT replacement (never a volatility/compression-outcome reason — see
# module docstring point 3 above and the WO final report).
REASON_NOT_A_VALID_GOVERNED_SESSION = "NOT_A_VALID_GOVERNED_SESSION_DATE"
REASON_NOW_A_SELECTED_SCHEDULED_EVENT_SESSION = "NOW_A_FINAL_SELECTED_SCHEDULED_EVENT_SESSION"


@dataclass(frozen=True)
class StratifiedReselectionResult:
    chosen_class_date_pairs: tuple  # tuple[tuple[str, date], ...], sorted
    per_class_per_year_quota: dict  # {class: {year: quota}}
    per_class_per_year_available: dict  # {class: {year: available_count}}


def _year_quota_with_earliest_years_getting_the_remainder(
    years_sorted: Sequence[int], target_total: int
) -> dict:
    """Deterministic quota split: `base = target_total // N` for every year, and the
    remainder (`target_total % N`) goes one-each to the EARLIEST `remainder` years
    (documented, fixed tie-break — mirrors this whole checkpoint's general convention of
    breaking ties toward the earlier date/year rather than an arbitrary or random choice)."""
    n = len(years_sorted)
    if n == 0:
        return {}
    base = target_total // n
    remainder = target_total % n
    quota = {year: base for year in years_sorted}
    for year in years_sorted[:remainder]:
        quota[year] += 1
    return quota


def _evenly_spaced_indices(available_count: int, quota: int) -> list:
    """Deterministic, RNG-free even spacing: index i (0-indexed, i in [0, quota)) picks the
    element at `(i * available_count) // quota` from an ascending-sorted candidate list. Never
    clusters at one end of the year; fully reproducible from plain integer arithmetic."""
    if quota <= 0:
        return []
    if quota >= available_count:
        return list(range(available_count))
    return [(i * available_count) // quota for i in range(quota)]


def stratified_scheduled_event_reselection(
    event_records: Sequence[EventRecord],
    sessions_by_date: Mapping[_dt.date, SessionRecord],
    *,
    target_per_class: int = TARGET_SCHEDULED_EVENT_PER_CLASS_V2,
    exclude_dates: frozenset = frozenset(),
) -> StratifiedReselectionResult:
    """Deterministically reselects `target_per_class` dates per event class, spread as evenly
    as possible across the calendar years that class has genuine eligible coverage for within
    `sessions_by_date`'s governed universe. See module docstring for the exact quota/spacing
    rules — no randomness anywhere in this function.

    `exclude_dates` (HMT-2 CORRECTION, additive/backward-compatible, default empty): dates
    already claimed by a strictly higher-precedence stratum (in practice, PROTECTED_HOLDOUT) —
    removed from candidacy BEFORE quota computation, so SCHEDULED_EVENT never displaces or
    duplicates a holdout session. A caller wanting v1's original (holdout-unaware) behaviour may
    simply omit this argument.
    """
    by_class_year: dict = {}
    for e in event_records:
        if e.date not in sessions_by_date:
            continue  # not a valid governed session date — defensively excluded, not guessed.
        if e.date in exclude_dates:
            continue  # already claimed by a higher-precedence stratum — routed around.
        by_class_year.setdefault(e.event_class, {}).setdefault(e.date.year, set()).add(e.date)

    chosen: set = set()
    quotas: dict = {}
    available: dict = {}

    for event_class in SCHEDULED_EVENT_CLASSES_V2:
        years_map = by_class_year.get(event_class, {})
        years_sorted = sorted(years_map.keys())
        quota_by_year = _year_quota_with_earliest_years_getting_the_remainder(years_sorted, target_per_class)
        quotas[event_class] = quota_by_year
        available[event_class] = {y: len(years_map[y]) for y in years_sorted}

        for year in years_sorted:
            dates_sorted = sorted(years_map[year])
            year_quota = min(quota_by_year[year], len(dates_sorted))
            indices = _evenly_spaced_indices(len(dates_sorted), year_quota)
            for idx in indices:
                chosen.add((event_class, dates_sorted[idx]))

    return StratifiedReselectionResult(
        chosen_class_date_pairs=tuple(sorted(chosen, key=lambda p: (p[0], p[1]))),
        per_class_per_year_quota=quotas,
        per_class_per_year_available=available,
    )


def build_reselected_event_records(
    event_records: Sequence[EventRecord], chosen_class_date_pairs: Sequence[tuple]
) -> list:
    chosen_set = set(chosen_class_date_pairs)
    return [e for e in event_records if (e.event_class, e.date) in chosen_set]


@dataclass(frozen=True)
class PreserveOrBackfillResult:
    final_dates: tuple  # tuple[date, ...], sorted
    kept_dates: tuple
    invalidated_dates: tuple
    backfilled_dates: tuple
    invalidation_reasons: dict  # {date: reason string}


def preserve_or_backfill(
    *,
    original_v1_dates: Sequence[_dt.date],
    full_v1_shuffled_pool: Sequence[_dt.date],
    invalid_if_in: set,
    already_used_for_backfill: set,
    valid_session_dates: set,
    target_count: int,
    collision_reason: str = DEFAULT_COLLISION_REASON,
) -> PreserveOrBackfillResult:
    """The general preserve-or-backfill mechanic shared by RANDOM_DEVELOPMENT and
    PROTECTED_HOLDOUT for v2. `full_v1_shuffled_pool` MUST be
    `deterministic_shuffled_pool(...)` called with the EXACT SAME `(eligible_dates_sorted,
    excluded_dates, domain)` v1's own draw used for this stratum — so that
    `full_v1_shuffled_pool[:len(original_v1_dates)] == original_v1_dates` (as sets), and
    continuing past that same index yields the deterministic "next candidate" sequence.

    A v1 date is invalidated if it appears in `invalid_if_in` (the reason is labelled
    `collision_reason` — HMT-2 CORRECTION: the caller is responsible for choosing an accurate,
    honest set for `invalid_if_in` — this function itself has no opinion on WHAT counts as a
    genuine invalidity reason for a given stratum; see generate_selection_manifest_v2.py for the
    corrected per-stratum rules: PROTECTED_HOLDOUT must pass an EMPTY `invalid_if_in` (holdout is
    never displaced by a collision, only by genuine session-invalidity, handled below via
    `valid_session_dates`); RANDOM_DEVELOPMENT must pass ONLY the final selected SCHEDULED_EVENT
    date set, never a volatility/compression outcome set).

    A v1 date not in `valid_session_dates` is invalidated as `NOT_A_VALID_GOVERNED_SESSION_DATE`
    regardless of `invalid_if_in` — this is the one genuine-invalidity reason always available in
    this checkpoint (no other independent proof of "acquisition impossible" exists here).

    Backfill candidates are drawn, in order, from `full_v1_shuffled_pool` starting immediately
    after the original count, skipping anything in `invalid_if_in`, `already_used_for_backfill`,
    not in `valid_session_dates`, or already picked as a backfill this call.
    """
    kept = []
    invalidated = []
    reasons: dict = {}
    for d in original_v1_dates:
        if d not in valid_session_dates:
            invalidated.append(d)
            reasons[d] = REASON_NOT_A_VALID_GOVERNED_SESSION
        elif d in invalid_if_in:
            invalidated.append(d)
            reasons[d] = collision_reason
        else:
            kept.append(d)

    needed = target_count - len(kept)
    backfilled: list = []
    if needed > 0:
        start_idx = len(original_v1_dates)
        used = set(kept) | set(already_used_for_backfill)
        for candidate in full_v1_shuffled_pool[start_idx:]:
            if len(backfilled) >= needed:
                break
            if candidate in invalid_if_in:
                continue
            if candidate not in valid_session_dates:
                continue
            if candidate in used or candidate in backfilled:
                continue
            backfilled.append(candidate)

    final_dates = tuple(sorted(kept + backfilled))
    return PreserveOrBackfillResult(
        final_dates=final_dates,
        kept_dates=tuple(sorted(kept)),
        invalidated_dates=tuple(sorted(invalidated)),
        backfilled_dates=tuple(sorted(backfilled)),
        invalidation_reasons=reasons,
    )


# ----------------------------------------------------------------------------------------------
# HMT-2 CORRECTION — Hamilton (largest-remainder) year-stratified apportionment + deterministic
# domain-separated seeded draw, for HIGH_VOL_NON_EVENT / COMPRESSION final sampling.
# ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class YearAllocation:
    year: int
    candidate_count: int
    raw_allocation: float
    floor_allocation: int
    remainder: float
    final_allocation: int


@dataclass(frozen=True)
class HamiltonApportionmentResult:
    target_total: int
    total_candidates: int
    shortfall: bool
    per_year: tuple  # tuple[YearAllocation, ...], sorted ascending by year
    final_total: int


def hamilton_apportionment(
    candidate_counts_by_year: Mapping[int, int], target_total: int
) -> HamiltonApportionmentResult:
    """Largest-remainder (Hamilton) apportionment of `target_total` seats across years,
    weighted by each year's `candidate_counts_by_year[year]` (= C_y).

    For each year: `raw_allocation = target_total * C_y / total_candidates`;
    `floor_allocation = floor(raw_allocation)`; the leftover seats
    (`target_total - sum(floor_allocation)`) go one-at-a-time to the years with the largest
    `remainder = raw_allocation - floor_allocation`, largest first — deterministic tie-break by
    ascending year on an exact remainder tie.

    If `total_candidates <= target_total` (genuinely fewer eligible candidates than the target
    across ALL years combined), every eligible candidate is allocated a seat
    (`final_allocation == candidate_count` for every year) and `shortfall=True` is reported
    honestly — this function never fabricates seats that have no eligible candidate behind them.
    """
    years_sorted = sorted(candidate_counts_by_year.keys())
    total_candidates = sum(candidate_counts_by_year.values())

    if total_candidates <= target_total:
        per_year = tuple(
            YearAllocation(
                year=y,
                candidate_count=candidate_counts_by_year[y],
                raw_allocation=float(candidate_counts_by_year[y]),
                floor_allocation=candidate_counts_by_year[y],
                remainder=0.0,
                final_allocation=candidate_counts_by_year[y],
            )
            for y in years_sorted
        )
        return HamiltonApportionmentResult(
            target_total=target_total,
            total_candidates=total_candidates,
            shortfall=(total_candidates < target_total),
            per_year=per_year,
            final_total=total_candidates,
        )

    raw = {y: target_total * candidate_counts_by_year[y] / total_candidates for y in years_sorted}
    floor_alloc = {y: math.floor(raw[y]) for y in years_sorted}
    remainder = {y: raw[y] - floor_alloc[y] for y in years_sorted}
    allocated = sum(floor_alloc.values())
    remaining_seats = target_total - allocated

    # Largest remainder first; ascending year is the deterministic tie-break on an exact tie.
    order = sorted(years_sorted, key=lambda y: (-remainder[y], y))
    bonus_years = set(order[:remaining_seats])

    per_year = tuple(
        YearAllocation(
            year=y,
            candidate_count=candidate_counts_by_year[y],
            raw_allocation=raw[y],
            floor_allocation=floor_alloc[y],
            remainder=remainder[y],
            final_allocation=floor_alloc[y] + (1 if y in bonus_years else 0),
        )
        for y in years_sorted
    )
    final_total = sum(a.final_allocation for a in per_year)
    return HamiltonApportionmentResult(
        target_total=target_total,
        total_candidates=total_candidates,
        shortfall=False,
        per_year=per_year,
        final_total=final_total,
    )


def group_session_ids_by_year(
    session_ids: Sequence[str], reference_results: Mapping[str, SessionReferenceResult]
) -> dict:
    """Groups a pool of session ids (e.g. a HIGH_VOL_NON_EVENT/COMPRESSION candidate pool) by
    calendar year, using each session's real `session_date` from `reference_results`."""
    by_year: dict = {}
    for sid in session_ids:
        year = reference_results[sid].session_date.year
        by_year.setdefault(year, []).append(sid)
    return by_year


def select_final_sample_by_year(
    pool_ids_by_year: Mapping[int, Sequence[str]],
    allocation: HamiltonApportionmentResult,
    domain: str,
) -> tuple:
    """Deterministic, domain-separated seeded draw that reduces a raw per-year candidate pool
    down to `allocation`'s final per-year seat counts — deliberately NOT a selection by
    log-range/magnitude ranking within the pool (that would just re-select the most extreme
    members and defeat the purpose of sampling the classified regime broadly).

    Constructs exactly ONE seeded `random.Random` for `domain` (via
    `corpus_selection.seeded_random`, i.e. the same
    `SHA256("HERMES|HMT-2|GC-MBP1|corpus-selection-v1|{BASE_SHA}|{domain}")` construction used by
    every other seed domain in this checkpoint), then visits years in a FIXED, deterministic
    order (ascending), and for each year takes a fresh, deterministic shuffle of that year's
    (sorted) candidate ids and keeps the first `final_allocation` of them. Running this twice
    with the same inputs reproduces byte-identical output — the only randomness source is the
    single seeded RNG, advanced in a fixed, reproducible sequence.
    """
    rng = seeded_random(domain)
    final_by_year = {a.year: a.final_allocation for a in allocation.per_year}
    chosen: list = []
    for year in sorted(pool_ids_by_year.keys()):
        candidates = sorted(pool_ids_by_year[year])
        rng.shuffle(candidates)
        k = final_by_year.get(year, 0)
        chosen.extend(candidates[:k])
    return tuple(sorted(chosen))
