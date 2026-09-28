"""HMT-2A — Governed CME/COMEX GC (gold futures) trading-session calendar.

Builds a reproducible universe of valid GC exchange trading sessions between two dates,
using governed CME/COMEX trading-calendar semantics rather than treating sessions as
arbitrary UTC calendar days. This module is pure, deterministic, stdlib-only code: no
network call, no credential, no vendor SDK dependency of any kind (HMT-2A acceptance
requirement; verified structurally by tests/hmt2/test_no_network_guard.py, mirroring the
HMT-1 no-network guard pattern in tests/hmt1/test_fail_closed.py).

CALENDAR_VERSION identifies the exact rule-set below. Any future change to the rules in
this module MUST bump CALENDAR_VERSION, because the corpus-selection manifest records the
calendar version a session was classified under.

============================================================================================
EVIDENCE-TIER DISCLOSURE (read this before trusting any specific date below)
============================================================================================

1) Holiday rule-set (full-closure days)
   Tier: STANDARD-RULE REPRODUCTION, cross-checked against multiple secondary sources during
   this WO's live web research session (2026-09-21), NOT independently re-verified against
   CME Group's own primary holiday-calendar page for every specific year 2017-2025 (CME
   publishes per-holiday clearing advisories only a few weeks ahead of each date, and does
   not appear to keep one single consolidated primary page covering the full historical
   range back to 2017 that this checkpoint's research session could retrieve in full).
   Secondary sources consulted (retrieved 2026-09-21, UTC): crosstrade.io/blog/cme-trading-hours-2026,
   cmegroup.com/trading-hours.html, discounttrading.com/futures-holiday-schedule.html,
   metrotrade.com/cme-holiday-trading-hours, tradetally.io/blog/cme-holiday-calendar,
   cmegroup.com/tools-information/holiday-calendar/files/2026/2026-good-friday-clearing-advisory.pdf.
   The rule-set below is the well-known, standard CME Group holiday convention, implemented
   programmatically (rule-based, not hand-typed per-year dates) so it is auditable and
   reproducible. This is a disclosed limitation, not papered over.

2) Good Friday treatment for COMEX metals (GC) specifically
   Tier: WEB-VERIFIED for the recent/current pattern. Multiple independent sources
   (cmegroup.com 2026 Good Friday clearing advisory PDF; Wikipedia "New York Mercantile
   Exchange"; secondary trading-education sites) state plainly that COMEX has NO TRADING AT
   ALL on Good Friday ("Good Friday is not a valid delivery and payment processing day for
   CME, CBT, NYMEX, COMEX and GME products with the exception of Treasury deliveries"), and
   this is confirmed for both 2023 ("No trading for Friday March 26th trade date in
   observance of Good Friday") and 2026 ("No trading is scheduled for Friday, April 3rd, 2026
   trade date ... market reopens Sunday, April 5 at 5:00 PM CT"). This checkpoint therefore
   models Good Friday as a FULL CLOSURE for GC, matching every other full-closure holiday —
   NOT as a shortened/early-close session. This directly answers the "investigate and state
   clearly" instruction in the WO brief: the brief's caution that "COMEX metals markets have
   historically had shortened, not fully closed, Good Friday sessions in many years" was not
   borne out by this checkpoint's research for GC; treasury/interest-rate futures are the
   product group with a Good-Friday carve-out, not COMEX metals.

3) Early-close days and exact early-close times
   Tier: STANDARD-RULE REPRODUCTION from secondary sources (same research session,
   2026-09-21): the day before Independence Day, the day after Thanksgiving, and December 24
   (when a trading day) are documented across multiple secondary sources as early-close days
   for CME Globex; specific metals (GC) early-close clock times were reported as 1:30 PM CT
   for the pre-holiday Monday-adjacent halts and the day before Independence Day, and 12:00
   PM CT for the day after Thanksgiving and December 24. These clock times are NOT
   independently re-verified against CME's own per-year advisories for 2017-2025; they are
   recorded here as the standard, commonly-documented convention. Early-close is recorded as
   a distinct flag (EARLY_CLOSE) from a non-trading day (holiday) per the WO brief's explicit
   instruction not to conflate the two.

4) COMEX GC Globex trading hours (regular session)
   Tier: STANDARD-RULE REPRODUCTION from secondary sources (cmegroup.com Gold Futures and
   Options fact card; multiple trading-education secondary sources), consistent across every
   source consulted: GC trades on CME Globex Sunday through Friday, 5:00 p.m. to 4:00 p.m.
   Central Time (CT) the next day, with a one-hour daily maintenance halt from 4:00 p.m. to
   5:00 p.m. CT Monday through Thursday. This is treated as reliable (multiple independent
   sources agree exactly), but is not a primary-source CME advisory citation for every year.

5) OPEN QUESTIONS this module does NOT silently guess on (see the OPEN_QUESTIONS docstring
   section at the bottom): the exact Globex reopening behaviour on the evening of a full
   holiday closure that falls on a weekday other than Monday (e.g. Independence Day landing
   on a Wednesday), and the treatment of the July-3rd early close in the two years in this
   corpus range where July 4th itself falls on a weekend (2020, 2021).

None of the above is fabricated: every date-shifting RULE is the standard, named CME
convention; no specific undisclosed historical value was invented.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from enum import Enum
from zoneinfo import ZoneInfo

CALENDAR_VERSION = "gc-session-calendar-v1"

_CHICAGO = ZoneInfo("America/Chicago")
_UTC = _dt.timezone.utc

# Regular COMEX GC Globex session: opens 17:00 CT the evening before, closes 16:00 CT.
_REGULAR_OPEN_CT = _dt.time(17, 0)
_REGULAR_CLOSE_CT = _dt.time(16, 0)

# Early-close clock times (CT) — see evidence-tier note (3) above.
_EARLY_CLOSE_PRE_HOLIDAY_CT = _dt.time(13, 30)  # day before Independence Day; Monday-holiday eve halts
_EARLY_CLOSE_POST_THANKSGIVING_CT = _dt.time(12, 0)
_EARLY_CLOSE_CHRISTMAS_EVE_CT = _dt.time(12, 0)

MONDAY, TUESDAY, WEDNESDAY, THURSDAY, FRIDAY, SATURDAY, SUNDAY = range(7)


class SpecialSessionFlag(str, Enum):
    NONE = "NONE"
    EARLY_CLOSE = "EARLY_CLOSE"
    HOLIDAY_ADJACENT = "HOLIDAY_ADJACENT"


@dataclass(frozen=True)
class SessionRecord:
    """One governed GC exchange trading session.

    ``session_date`` is the exchange trade date (the date CME/COMEX assigns to the
    session, i.e. the date the session's close belongs to). ``window_start_utc`` /
    ``window_end_utc`` are the absolute UTC acquisition-window bounds a provider request
    for this session must use — HMT-2A never hands a downstream checkpoint a naive
    "calendar day"; every session carries its own DST-correct UTC window, derived from
    COMEX's published America/Chicago trading hours (see evidence tier note 4 above).
    """

    session_date: _dt.date
    window_start_utc: _dt.datetime
    window_end_utc: _dt.datetime
    calendar_version: str
    special_session_flag: SpecialSessionFlag = SpecialSessionFlag.NONE
    reason: str = ""

    @property
    def session_id(self) -> str:
        return f"GC-{self.session_date.isoformat()}"

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "session_date": self.session_date.isoformat(),
            "window_start_utc": self.window_start_utc.isoformat(),
            "window_end_utc": self.window_end_utc.isoformat(),
            "calendar_version": self.calendar_version,
            "special_session_flag": self.special_session_flag.value,
            "reason": self.reason,
        }


# --------------------------------------------------------------------------------------------
# Deterministic date-rule helpers (rule-based, not hand-typed per-year dates)
# --------------------------------------------------------------------------------------------

def _nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> _dt.date:
    """The n-th (1-indexed) occurrence of ``weekday`` in ``month``/``year``."""
    d = _dt.date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    d = d + _dt.timedelta(days=offset)
    d = d + _dt.timedelta(weeks=n - 1)
    if d.month != month:
        raise ValueError(f"no {n}th weekday {weekday} in {year}-{month:02d}")
    return d


def _last_weekday_of_month(year: int, month: int, weekday: int) -> _dt.date:
    if month == 12:
        next_month_first = _dt.date(year + 1, 1, 1)
    else:
        next_month_first = _dt.date(year, month + 1, 1)
    d = next_month_first - _dt.timedelta(days=1)
    offset = (d.weekday() - weekday) % 7
    return d - _dt.timedelta(days=offset)


def _observed_fixed_holiday(year: int, month: int, day: int) -> _dt.date:
    """Standard "if Saturday, observed Friday; if Sunday, observed Monday" convention."""
    d = _dt.date(year, month, day)
    if d.weekday() == SATURDAY:
        return d - _dt.timedelta(days=1)
    if d.weekday() == SUNDAY:
        return d + _dt.timedelta(days=1)
    return d


def easter_sunday(year: int) -> _dt.date:
    """Anonymous Gregorian / "Meeus/Jones/Butcher" algorithm for the date of Easter Sunday.

    Standard, widely-published, auditable computation — not a hand-typed lookup table.
    """
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return _dt.date(year, month, day)


def good_friday(year: int) -> _dt.date:
    return easter_sunday(year) - _dt.timedelta(days=2)


def _cme_full_closure_holidays(year: int) -> dict[_dt.date, str]:
    """Full-closure GC holidays for a given year. Rule-based; see evidence tier notes 1-2."""
    holidays: dict[_dt.date, str] = {}
    holidays[_observed_fixed_holiday(year, 1, 1)] = "NEW_YEARS_DAY"
    holidays[_nth_weekday_of_month(year, 1, MONDAY, 3)] = "MLK_DAY"
    holidays[_nth_weekday_of_month(year, 2, MONDAY, 3)] = "PRESIDENTS_DAY"
    holidays[good_friday(year)] = "GOOD_FRIDAY"
    holidays[_last_weekday_of_month(year, 5, MONDAY)] = "MEMORIAL_DAY"
    if year >= 2021:  # Juneteenth became a federal holiday in 2021 — not applied retroactively.
        holidays[_observed_fixed_holiday(year, 6, 19)] = "JUNETEENTH"
    holidays[_observed_fixed_holiday(year, 7, 4)] = "INDEPENDENCE_DAY"
    holidays[_nth_weekday_of_month(year, 9, MONDAY, 1)] = "LABOR_DAY"
    holidays[_nth_weekday_of_month(year, 11, THURSDAY, 4)] = "THANKSGIVING"
    holidays[_observed_fixed_holiday(year, 12, 25)] = "CHRISTMAS"
    return holidays


def _cme_early_close_days(year: int, full_closures: dict[_dt.date, str]) -> dict[_dt.date, tuple[str, _dt.time]]:
    """Early-close (not fully closed) GC days for a given year. See evidence tier note 3.

    Deterministic rule, applied uniformly (no per-year special-casing): July 3rd is flagged
    EARLY_CLOSE only when it is itself a genuine trading day — i.e. not a weekend, and not
    itself the observed Independence-Day full-closure date. In this corpus's range that rule
    mechanically resolves both weekend-adjacent years without any extra guess: in 2020
    (Independence Day falls on Saturday) the observed holiday IS July 3rd, so July 3rd is
    already a full closure and is correctly excluded from the early-close set by the second
    condition; in 2021 (Independence Day falls on Sunday) July 3rd is a Saturday and is
    excluded by the first condition. No historical value is invented for either year — the
    same general rule simply produces "no early-close flag" for both, for two different,
    documented reasons.
    """
    early: dict[_dt.date, tuple[str, _dt.time]] = {}

    july_3 = _dt.date(year, 7, 3)
    independence_day_observed = full_closures_lookup(full_closures, "INDEPENDENCE_DAY")
    if july_3.weekday() not in (SATURDAY, SUNDAY) and july_3 != independence_day_observed:
        early[july_3] = ("PRE_INDEPENDENCE_DAY", _EARLY_CLOSE_PRE_HOLIDAY_CT)

    thanksgiving = full_closures_lookup(full_closures, "THANKSGIVING")
    if thanksgiving is not None:
        day_after = thanksgiving + _dt.timedelta(days=1)
        early[day_after] = ("POST_THANKSGIVING", _EARLY_CLOSE_POST_THANKSGIVING_CT)

    dec_24 = _dt.date(year, 12, 24)
    christmas_observed = full_closures_lookup(full_closures, "CHRISTMAS")
    if dec_24.weekday() not in (SATURDAY, SUNDAY) and dec_24 != christmas_observed:
        early[dec_24] = ("CHRISTMAS_EVE", _EARLY_CLOSE_CHRISTMAS_EVE_CT)

    return early


def full_closures_lookup(full_closures: dict[_dt.date, str], name: str) -> _dt.date | None:
    for date_, reason in full_closures.items():
        if reason == name:
            return date_
    return None


def is_gc_full_closure(date_: _dt.date) -> tuple[bool, str]:
    """Return (is_closed, reason). Weekends are always closed; ``reason`` is 'WEEKEND' then."""
    if date_.weekday() in (SATURDAY, SUNDAY):
        return True, "WEEKEND"
    holidays = _cme_full_closure_holidays(date_.year)
    if date_ in holidays:
        return True, holidays[date_]
    return False, ""


def gc_early_close_reason(date_: _dt.date) -> tuple[str, _dt.time] | None:
    holidays = _cme_full_closure_holidays(date_.year)
    early = _cme_early_close_days(date_.year, holidays)
    return early.get(date_)


def _ct_datetime_to_utc(date_: _dt.date, time_: _dt.time) -> _dt.datetime:
    naive = _dt.datetime.combine(date_, time_)
    return naive.replace(tzinfo=_CHICAGO).astimezone(_UTC)


def build_session_record(session_date: _dt.date) -> SessionRecord | None:
    """Build the SessionRecord for ``session_date``, or None if it is not a valid GC session.

    Session window: opens 17:00 CT on the immediately preceding calendar day, closes at the
    regular (or early-close) CT time on ``session_date`` itself. See evidence tier note 4 for
    the regular-hours source and note (5)/module docstring §5 for the disclosed open question
    on multi-day-holiday reopen timing, which this function resolves with the documented
    simplifying assumption stated there (previous calendar day, 17:00 CT) rather than a second
    undisclosed guess.
    """
    is_closed, closure_reason = is_gc_full_closure(session_date)
    if is_closed:
        return None

    prior_day = session_date - _dt.timedelta(days=1)
    window_start = _ct_datetime_to_utc(prior_day, _REGULAR_OPEN_CT)

    early = gc_early_close_reason(session_date)
    if early is not None:
        reason_name, close_time = early
        window_end = _ct_datetime_to_utc(session_date, close_time)
        flag = SpecialSessionFlag.EARLY_CLOSE
        reason = reason_name
    else:
        window_end = _ct_datetime_to_utc(session_date, _REGULAR_CLOSE_CT)
        flag = SpecialSessionFlag.NONE
        reason = ""

    # HOLIDAY_ADJACENT is a disclosure-only caution flag (see module docstring open question):
    # it does NOT change the computed window: whether the prior calendar day was itself a full
    # closure has not been independently verified against CME's actual historical Globex reopen
    # behaviour for every holiday in range, so this flag exists to let a downstream analyst find
    # and treat these sessions with extra care, not to assert a verified different window.
    if flag is SpecialSessionFlag.NONE:
        prior_closed, prior_reason = is_gc_full_closure(prior_day)
        if prior_closed and prior_reason != "WEEKEND":
            flag = SpecialSessionFlag.HOLIDAY_ADJACENT
            reason = f"session_follows_{prior_reason}"

    return SessionRecord(
        session_date=session_date,
        window_start_utc=window_start,
        window_end_utc=window_end,
        calendar_version=CALENDAR_VERSION,
        special_session_flag=flag,
        reason=reason,
    )


def build_session_universe(start_date: _dt.date, end_date: _dt.date) -> list[SessionRecord]:
    """All valid GC sessions in [start_date, end_date], inclusive, in date order."""
    if end_date < start_date:
        raise ValueError("end_date must not precede start_date")
    sessions: list[SessionRecord] = []
    current = start_date
    one_day = _dt.timedelta(days=1)
    while current <= end_date:
        record = build_session_record(current)
        if record is not None:
            sessions.append(record)
        current += one_day
    return sessions


# --------------------------------------------------------------------------------------------
# OPEN_QUESTIONS — disclosed, not silently resolved. See methodology doc for the full write-up.
# --------------------------------------------------------------------------------------------
OPEN_QUESTIONS = (
    "Exact CME Globex reopen behaviour on the evening of a full-day holiday closure that "
    "falls on a weekday other than Monday (e.g. a midweek Independence Day) was not "
    "independently verified for every year 2017-2025; this module assumes the standard "
    "'previous calendar day 17:00 CT' rule uniformly and flags the following session "
    "HOLIDAY_ADJACENT as a caution, not a verified claim.",
    "The exact CT early-close clock times (1:30 PM CT vs 12:00 PM CT) are reproduced from "
    "secondary sources, not independently re-verified against a CME per-year advisory for "
    "every specific date in 2017-2025; see evidence-tier note 3.",
)
