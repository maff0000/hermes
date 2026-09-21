"""HMT-2A — Deterministic corpus-selection algorithm (WO brief §5-§7, §19).

Selects a stratified sample of governed GC sessions (built by session_calendar.py) for the
HMT-2 historical research corpus, using ONLY calendar/event-metadata inputs available in
this checkpoint. Zero MBP-1/TBBO/trade data of any kind is used, examined, or referenced —
there is none in this checkpoint. Zero network access; zero provider dependency.

SELECTION_ALGORITHM_VERSION identifies the exact algorithm below; bump it on any behavioural
change, since the manifest records the version a selection run was produced under.

--------------------------------------------------------------------------------------------
Precedence / collision-handling — an explicit, disclosed implementation choice
--------------------------------------------------------------------------------------------
The WO brief's dedup precedence is:
    PROTECTED_HOLDOUT > SCHEDULED_EVENT > MATCHED_CONTROL > HIGH_VOL_NON_EVENT
        > COMPRESSION > RANDOM_DEVELOPMENT
and states explicitly, for one concrete case: "A holdout candidate colliding with an
already-assigned development session should be replaced by drawing the next deterministic
holdout candidate (not by stealing/reassigning the other stratum's session)."

SCHEDULED_EVENT and MATCHED_CONTROL are fixed real-world facts (a historical FOMC/CPI/NFP/
PCE date is what it is; a matched control is a deterministic function of an event date) —
they cannot be "redrawn". RANDOM_DEVELOPMENT and PROTECTED_HOLDOUT are the only two strata
assigned by seeded random draw, so this implementation achieves the brief's stated
non-destructive, no-stealing behaviour by construction, generalised uniformly rather than
special-cased for one pair:

    1. SCHEDULED_EVENT is assigned first (fixed real dates).
    2. MATCHED_CONTROL is assigned second, excluding every SCHEDULED_EVENT date.
    3. HIGH_VOL_NON_EVENT and COMPRESSION are attempted third; both are PENDING in this
       checkpoint (see research/hmt2/volatility-compression-reference-series-v1.json) and so
       contribute zero sessions — they never collide with anything.
    4. RANDOM_DEVELOPMENT is drawn fourth (seeded), excluding every session already assigned
       above. A collision is impossible by construction (the exclusion set is applied before
       drawing, so there is nothing to "steal" or "redraw away from" — the pool offered to
       the draw already has no overlap).
    5. PROTECTED_HOLDOUT is drawn LAST (seeded), excluding every session already assigned to
       ANY of the preceding strata. Exactly as the brief specifies for the development case:
       on encountering an already-assigned candidate, the deterministic draw simply continues
       to its next candidate — the already-assigned session is left untouched. Drawing holdout
       last with a full exclusion set is operationally identical to "holdout always wins any
       collision, non-destructively" (nothing lower in the list can ever have already claimed
       a session AFTER holdout has drawn, since holdout is drawn after everyone else), which is
       what the brief's stated precedence order requires, and it trivially guarantees the
       explicit test requirement "holdout sessions never appear as a non-holdout stratum".

This ordering is a disclosed engineering judgment call resolving an apparent tension in the
brief (SCHEDULED_EVENT/MATCHED_CONTROL are fixed facts, yet are listed as LOWER precedence
than PROTECTED_HOLDOUT, which is a random draw) — see the WO final report for this exact
callout.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import random
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from market_truth.acquisition.session_calendar import SessionRecord

SELECTION_ALGORITHM_VERSION = "hmt2-corpus-selection-v1"
CORPUS_VERSION = "hmt2-corpus-v1"

BASE_SHA = "03d700a463ada61d472c64ad52265d09d82c60ea"
SEED_DOMAIN_DEVELOPMENT_RANDOM = "DEVELOPMENT_RANDOM"
SEED_DOMAIN_PROTECTED_HOLDOUT = "PROTECTED_HOLDOUT"

# Indicative planning targets (WO brief §6) — NOT mandatory quotas. Actual counts from a real
# run are reported by the caller/CLI; deviation from these targets is expected and documented
# (see docs/research/hmt2-corpus-selection-methodology-v1.md) given this checkpoint's honestly
# partial event-snapshot and fully-pending reference-series coverage.
TARGET_SCHEDULED_EVENT = 80
TARGET_HIGH_VOL_NON_EVENT = 70
TARGET_COMPRESSION = 70
TARGET_RANDOM_DEVELOPMENT = 50
TARGET_PROTECTED_HOLDOUT = 100

# Matched-control search radius: same-weekday candidates up to this many weeks away are
# considered "within the same broad period" (WO brief §6). Documented, fixed constant.
MATCHED_CONTROL_MAX_WEEKS = 8


class Stratum(str, Enum):
    PROTECTED_HOLDOUT = "PROTECTED_HOLDOUT"
    SCHEDULED_EVENT = "SCHEDULED_EVENT"
    MATCHED_CONTROL = "MATCHED_CONTROL"
    HIGH_VOL_NON_EVENT = "HIGH_VOL_NON_EVENT"
    COMPRESSION = "COMPRESSION"
    RANDOM_DEVELOPMENT = "RANDOM_DEVELOPMENT"


# Precedence order exactly as given in the WO brief §6 (highest precedence first). See module
# docstring for how this is honoured non-destructively given SCHEDULED_EVENT/MATCHED_CONTROL
# are fixed facts rather than redrawable candidates.
STRATUM_PRECEDENCE = (
    Stratum.PROTECTED_HOLDOUT,
    Stratum.SCHEDULED_EVENT,
    Stratum.MATCHED_CONTROL,
    Stratum.HIGH_VOL_NON_EVENT,
    Stratum.COMPRESSION,
    Stratum.RANDOM_DEVELOPMENT,
)

PENDING_MARKER = "PENDING_REFERENCE_SERIES_DATA"


class ReferenceSeriesUnavailableError(Exception):
    """Raised by the volatility/compression compute path — see WO brief §4. This checkpoint
    has no live/real market-data source, so this is raised rather than any function silently
    returning a fabricated number. The main selection pipeline does NOT call the raising
    path; it uses classify_high_vol_and_compression_sessions(), which returns an explicit
    PENDING result instead of raising, so a full corpus-selection run can still complete."""


def compute_coarse_range_series(daily_ohlc: list[dict]) -> list[float]:
    """Structurally defined per the frozen methodology (research/hmt2/volatility-compression-
    reference-series-v1.json) but genuinely unimplemented in this checkpoint: there is no real
    daily OHLC reference series available to compute over. Always fails closed."""
    raise ReferenceSeriesUnavailableError(
        "no real GC/gold daily reference-price series is available in HMT-2A; see "
        "research/hmt2/volatility-compression-reference-series-v1.json"
    )


def classify_high_vol_and_compression_sessions(
    eligible_dates: list[_dt.date], excluded_dates: set[_dt.date]
) -> dict:
    """Always returns the PENDING result for both strata in this checkpoint (see WO brief §4).
    Deliberately does NOT raise, so the overall selection pipeline can complete and honestly
    report zero sessions in these two strata rather than aborting the whole corpus selection."""
    return {
        Stratum.HIGH_VOL_NON_EVENT: [],
        Stratum.COMPRESSION: [],
        "status": PENDING_MARKER,
    }


def domain_seed_string(domain: str) -> str:
    return f"HERMES|HMT-2|GC-MBP1|corpus-selection-v1|{BASE_SHA}|{domain}"


def domain_seed_hex(domain: str) -> str:
    return hashlib.sha256(domain_seed_string(domain).encode("utf-8")).hexdigest()


def seeded_random(domain: str) -> random.Random:
    digest = domain_seed_hex(domain)
    return random.Random(int(digest, 16))


@dataclass(frozen=True)
class EventRecord:
    event_class: str
    date: _dt.date
    authority: str
    source_ref: str
    coverage_note: Optional[str]


def load_event_records(snapshot_document: dict) -> list[EventRecord]:
    records = []
    for r in snapshot_document["records"]:
        records.append(
            EventRecord(
                event_class=r["event_class"],
                date=_dt.date.fromisoformat(r["date"]),
                authority=r["authority"],
                source_ref=r["source_ref"],
                coverage_note=r.get("coverage_note"),
            )
        )
    return records


@dataclass(frozen=True)
class SelectedSession:
    session: SessionRecord
    primary_stratum: Stratum
    inclusion_reason: str
    event_class: Optional[str] = None
    event_source_ref: Optional[str] = None
    matched_parent_session: Optional[str] = None
    volatility_compression_metric: Optional[str] = None
    random_seed_domain: Optional[str] = None
    random_seed_version: Optional[str] = None
    exclusion_or_replacement_lineage: Optional[str] = None


def _group_events_by_date(events: list[EventRecord]) -> dict[_dt.date, list[EventRecord]]:
    grouped: dict[_dt.date, list[EventRecord]] = {}
    for e in events:
        grouped.setdefault(e.date, []).append(e)
    return grouped


def select_scheduled_events(
    events: list[EventRecord], sessions_by_date: dict[_dt.date, SessionRecord]
) -> list[SelectedSession]:
    """WO brief §6: scheduled-event sample. Deduplicates multiple event classes landing on
    the same session date into ONE row (deterministic: classes joined in sorted order),
    never inspecting MBP-1 (none exists). Any event date that is not itself a valid governed
    GC session (should not occur for real US-market-data-release days, but checked and
    excluded defensively rather than assumed) is dropped, with the drop itself being a
    silent, documented exclusion — not an error — since it cannot happen for genuine event
    days without also indicating a genuine full market closure that this checkpoint's
    calendar computed independently."""
    grouped = _group_events_by_date(events)
    selected: list[SelectedSession] = []
    for date_ in sorted(grouped.keys()):
        session = sessions_by_date.get(date_)
        if session is None:
            continue  # not a valid governed GC session — defensively excluded, not guessed.
        classes = sorted({e.event_class for e in grouped[date_]})
        source_refs = sorted({e.source_ref for e in grouped[date_]})
        selected.append(
            SelectedSession(
                session=session,
                primary_stratum=Stratum.SCHEDULED_EVENT,
                inclusion_reason=f"scheduled_macro_event:{'+'.join(classes)}",
                event_class="+".join(classes),
                event_source_ref=" ; ".join(source_refs),
            )
        )
    return selected


def _same_weekday_candidates(event_date: _dt.date, max_weeks: int):
    """Yield candidate dates in deterministic search order: distance 1 week, 2 weeks, ...;
    within a distance, the earlier (prior) date is tried before the later (subsequent) one —
    a fixed, documented tie-break rule (WO brief §6 point 4)."""
    for k in range(1, max_weeks + 1):
        yield event_date - _dt.timedelta(weeks=k)
        yield event_date + _dt.timedelta(weeks=k)


def match_controls(
    scheduled_events: list[SelectedSession],
    sessions_by_date: dict[_dt.date, SessionRecord],
    event_dates: set[_dt.date],
) -> tuple[list[SelectedSession], list[dict]]:
    """WO brief §6: matched quiet controls, ~1 per event session, via a pre-declared,
    calendar/event-metadata-only deterministic scoring hierarchy: (1) same weekday — enforced
    structurally by only ever considering +/-7*k day offsets; (2) nearest calendar distance
    within the same broad period — enforced by searching k=1,2,...,MATCHED_CONTROL_MAX_WEEKS
    in ascending order and taking the first hit; (3) no selected catalyst present — enforced
    by excluding event_dates and every date already claimed by a prior match; (4)
    deterministic tie-break by session date — enforced by always trying the prior week before
    the subsequent week at the same distance. Never inspects price/volatility behaviour.

    Returns (matched_rows, unmatched_log) — an event that cannot find any qualifying control
    within the search radius is logged, not silently dropped or fabricated.
    """
    claimed: set[_dt.date] = set()
    matches: list[SelectedSession] = []
    unmatched: list[dict] = []
    for event_row in scheduled_events:
        event_date = event_row.session.session_date
        found: Optional[_dt.date] = None
        for candidate in _same_weekday_candidates(event_date, MATCHED_CONTROL_MAX_WEEKS):
            if candidate in event_dates:
                continue
            if candidate in claimed:
                continue
            if candidate not in sessions_by_date:
                continue
            found = candidate
            break
        if found is None:
            unmatched.append(
                {
                    "event_session_id": event_row.session.session_id,
                    "reason": "NO_QUALIFYING_CONTROL_WITHIN_SEARCH_RADIUS",
                }
            )
            continue
        claimed.add(found)
        control_session = sessions_by_date[found]
        matches.append(
            SelectedSession(
                session=control_session,
                primary_stratum=Stratum.MATCHED_CONTROL,
                inclusion_reason=(
                    f"matched_control_for:{event_row.session.session_id};"
                    f"weekday_distance_days:{abs((found - event_date).days)}"
                ),
                matched_parent_session=event_row.session.session_id,
            )
        )
    return matches, unmatched


def draw_random_development(
    eligible_dates_sorted: list[_dt.date],
    sessions_by_date: dict[_dt.date, SessionRecord],
    excluded_dates: set[_dt.date],
    count: int,
) -> list[SelectedSession]:
    """WO brief §5/§6: deterministic seeded draw. The pool is built in a fixed, deterministic
    order (ascending date) BEFORE shuffling, so the only source of randomness is the seeded
    Random instance itself — re-running with the same inputs reproduces the same draw exactly."""
    rng = seeded_random(SEED_DOMAIN_DEVELOPMENT_RANDOM)
    pool = [d for d in eligible_dates_sorted if d not in excluded_dates]
    rng.shuffle(pool)
    chosen = sorted(pool[:count])  # sort for stable manifest ordering; draw itself already happened
    return [
        SelectedSession(
            session=sessions_by_date[d],
            primary_stratum=Stratum.RANDOM_DEVELOPMENT,
            inclusion_reason="deterministic_random_draw",
            random_seed_domain=SEED_DOMAIN_DEVELOPMENT_RANDOM,
            random_seed_version=SELECTION_ALGORITHM_VERSION,
        )
        for d in chosen
    ]


def draw_protected_holdout(
    eligible_dates_sorted: list[_dt.date],
    sessions_by_date: dict[_dt.date, SessionRecord],
    excluded_dates: set[_dt.date],
    count: int,
) -> list[SelectedSession]:
    """WO brief §5/§6: protected holdout, drawn LAST (see module docstring for why), excluding
    every session already assigned to any other stratum. On a collision the draw simply moves
    to its next deterministic candidate — never stealing/reassigning another stratum's session
    (WO brief §6 point, generalised here to every preceding stratum, not only RANDOM_DEVELOPMENT)."""
    rng = seeded_random(SEED_DOMAIN_PROTECTED_HOLDOUT)
    pool = [d for d in eligible_dates_sorted if d not in excluded_dates]
    rng.shuffle(pool)
    chosen = sorted(pool[:count])
    return [
        SelectedSession(
            session=sessions_by_date[d],
            primary_stratum=Stratum.PROTECTED_HOLDOUT,
            inclusion_reason="deterministic_protected_holdout_draw",
            random_seed_domain=SEED_DOMAIN_PROTECTED_HOLDOUT,
            random_seed_version=SELECTION_ALGORITHM_VERSION,
        )
        for d in chosen
    ]


@dataclass(frozen=True)
class CorpusSelectionResult:
    scheduled_events: list[SelectedSession]
    matched_controls: list[SelectedSession]
    unmatched_events: list[dict]
    high_vol_non_event: list[SelectedSession]
    compression: list[SelectedSession]
    random_development: list[SelectedSession]
    protected_holdout: list[SelectedSession]
    reference_series_status: str

    def all_rows(self) -> list[SelectedSession]:
        return (
            list(self.scheduled_events)
            + list(self.matched_controls)
            + list(self.high_vol_non_event)
            + list(self.compression)
            + list(self.random_development)
            + list(self.protected_holdout)
        )

    def counts(self) -> dict:
        return {
            "SCHEDULED_EVENT": len(self.scheduled_events),
            "MATCHED_CONTROL": len(self.matched_controls),
            "UNMATCHED_EVENTS": len(self.unmatched_events),
            "HIGH_VOL_NON_EVENT": len(self.high_vol_non_event),
            "COMPRESSION": len(self.compression),
            "RANDOM_DEVELOPMENT": len(self.random_development),
            "PROTECTED_HOLDOUT": len(self.protected_holdout),
            "TOTAL_UNIQUE_SESSIONS": len(self.all_rows()),
        }


def select_corpus(
    session_universe: list[SessionRecord],
    event_records: list[EventRecord],
    random_development_target: int = TARGET_RANDOM_DEVELOPMENT,
    protected_holdout_target: int = TARGET_PROTECTED_HOLDOUT,
) -> CorpusSelectionResult:
    """The single deterministic entry point (WO brief §5: "implement the selection as a
    single deterministic function of the frozen inputs, run once, and use whatever it
    produces"). Calling this twice with the same inputs MUST produce byte-identical results —
    exercised directly by tests/hmt2/test_corpus_selection.py."""
    sessions_by_date = {s.session_date: s for s in session_universe}
    eligible_dates_sorted = sorted(sessions_by_date.keys())

    scheduled_events = select_scheduled_events(event_records, sessions_by_date)
    event_dates = {row.session.session_date for row in scheduled_events}

    matched_controls, unmatched = match_controls(scheduled_events, sessions_by_date, event_dates)
    control_dates = {row.session.session_date for row in matched_controls}

    excluded_for_vol = event_dates | control_dates
    vol_result = classify_high_vol_and_compression_sessions(eligible_dates_sorted, excluded_for_vol)
    high_vol_rows: list[SelectedSession] = []
    compression_rows: list[SelectedSession] = []

    excluded_for_random = event_dates | control_dates
    random_rows = draw_random_development(
        eligible_dates_sorted, sessions_by_date, excluded_for_random, random_development_target
    )
    random_dates = {row.session.session_date for row in random_rows}

    excluded_for_holdout = event_dates | control_dates | random_dates
    holdout_rows = draw_protected_holdout(
        eligible_dates_sorted, sessions_by_date, excluded_for_holdout, protected_holdout_target
    )

    return CorpusSelectionResult(
        scheduled_events=scheduled_events,
        matched_controls=matched_controls,
        unmatched_events=unmatched,
        high_vol_non_event=high_vol_rows,
        compression=compression_rows,
        random_development=random_rows,
        protected_holdout=holdout_rows,
        reference_series_status=vol_result["status"],
    )
