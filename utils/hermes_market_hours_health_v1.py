"""HERMES market-hours-aware stream-health & recovery-suppression decision core v1.
WO-HELM-HERMES-MARKET-HOURS-AWARE-STREAM-HEALTH-AND-RECOVERY-SUPPRESSION-0001. Created (UTC): 2026-07-16. Owner: HERMES (Helm).

PURE, side-effect-free decision functions. NO Redis/SQL/network/file-write/env-read/thread/singleton/logging at import.
Addresses R2D2 findings F-1 (stale-candle fires during the expected daily market close because the schedule was fixed-UTC,
not DST-aware) and F-2 (a closed/after-hours instrument must not drive full-stream reconnect churn).

Doctrine: EXPECTED INACTIVITY and UNEXPECTED FAILURE are different states. This module classifies an instrument into an
explicit, testable market-hours health state and decides incident / per-instrument-recovery / full-stream-recovery
ELIGIBILITY. It NEVER suppresses genuine connection/session faults, NEVER fabricates data, NEVER refreshes keys, and FAILS
CLOSED (conservative = behave as OPEN so normal detection/recovery still fires) when market-hours info is unavailable /
malformed / unknown / ambiguous. Schedules are expressed in the MARKET timezone and converted to UTC per-date (DST-aware)
via the standard-library zoneinfo — no fixed-UTC break, no added heavy dependency.

The runtime watchdog consumes these decisions; this module writes nothing.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

UTC = datetime.timezone.utc
DECISION_VERSION = "1"

# --------------------------------------------------------------------------- instrument market-hours health states (§ArchPrinciple)
MARKET_OPEN_FLOWING = "MARKET_OPEN_FLOWING"                     # open + data fresh
MARKET_OPEN_STALE = "MARKET_OPEN_STALE"                        # open + data stale past threshold -> genuine fault
MARKET_CLOSED_EXPECTED = "MARKET_CLOSED_EXPECTED"             # governed closed interval -> expected silence, suppress
MARKET_REOPENING_GRACE = "MARKET_REOPENING_GRACE"            # just reopened, within bounded grace -> tolerate absence
MARKET_OPEN_MISSING_AFTER_GRACE = "MARKET_OPEN_MISSING_AFTER_GRACE"  # reopened, grace expired, still no data -> escalate
ALL_STATES = frozenset({MARKET_OPEN_FLOWING, MARKET_OPEN_STALE, MARKET_CLOSED_EXPECTED,
                        MARKET_REOPENING_GRACE, MARKET_OPEN_MISSING_AFTER_GRACE})

# market phase (schedule-only, before data is considered)
PHASE_OPEN = "OPEN"
PHASE_CLOSED = "CLOSED"

# reason codes (observability §15)
REASON_FLOWING = "MARKET_OPEN_FLOWING"
REASON_EXPECTED_CLOSED = "EXPECTED_MARKET_CLOSED_NO_FLOW"      # the governed suppression reason (§5)
REASON_REOPENING_GRACE = "MARKET_REOPENING_GRACE"
REASON_STALE_OPEN = "STREAM_STALE_DURING_MARKET_HOURS"
REASON_MISSING_AFTER_GRACE = "NO_DATA_AFTER_REOPEN_GRACE"
REASON_CONNECTION_FAULT = "EXPLICIT_CONNECTION_FAULT"          # genuine fault, never suppressed
REASON_SCHEDULE_UNKNOWN_FAILCLOSED = "MARKET_HOURS_UNKNOWN_FAILCLOSED"  # fail-closed conservative (behave as open)
REASON_SCHEDULE_MALFORMED_FAILCLOSED = "MARKET_HOURS_MALFORMED_FAILCLOSED"


class ScheduleError(Exception):
    """Malformed schedule at construction. Callers should catch and fail closed (treat as open)."""


# --------------------------------------------------------------------------- governed schedule model (from config/market_hours_schedule.v1.json)
@dataclass(frozen=True)
class InstrumentSchedule:
    instrument: str
    tz: ZoneInfo
    open_weekday: int            # 0=Mon .. 6=Sun (market local)
    open_local: datetime.time
    close_weekday: int
    close_local: datetime.time
    daily_breaks: Tuple[Tuple[datetime.time, datetime.time], ...]  # (start_local, end_local) pairs
    reopening_grace_seconds: int


def _parse_hhmm(s: str) -> datetime.time:
    h, m = str(s).split(":")
    return datetime.time(int(h), int(m))


def load_schedule(cfg: Mapping, instrument: str) -> Optional[InstrumentSchedule]:
    """Resolve an instrument to a governed InstrumentSchedule. HARDENED (readiness WO): NO silent _default_fx. Resolution:
      1. instrument listed in `fail_closed_unvalidated` -> None (fail loud: behave as open, normal detection).
      2. v2: `instrument_map`[instrument] -> `named_schedules`[name]. UNMAPPED instrument -> None (fail loud).
      3. v1 back-compat: explicit `instruments`[instrument] ONLY (still NO _default_fx). Unknown -> None.
    Returns None when the instrument has no governed schedule (caller treats as open = conservative). Raises ScheduleError
    on a malformed/timezone-invalid entry (caller fails closed = open)."""
    try:
        tz = ZoneInfo(cfg["market_timezone"])
        grace = int(cfg.get("reopening_grace_seconds", 300))
        if instrument in cfg.get("fail_closed_unvalidated", {}):
            return None                                    # explicitly unvalidated -> fail loud (never suppress)
        entry = None
        imap, named = cfg.get("instrument_map"), cfg.get("named_schedules")
        if imap is not None and named is not None:
            sched_name = imap.get(instrument)
            if sched_name is None:
                return None                                # UNMAPPED configured/unknown instrument -> fail loud
            entry = named.get(sched_name)
            if entry is None:
                raise ScheduleError(f"instrument_map points {instrument!r} at missing named schedule {sched_name!r}")
        else:
            entry = cfg.get("instruments", {}).get(instrument)   # v1 back-compat, explicit only (no _default_fx)
            if entry is None:
                return None
        breaks = tuple((_parse_hhmm(b["local_start"]), _parse_hhmm(b["local_end"])) for b in entry.get("daily_breaks", []))
        return InstrumentSchedule(
            instrument=instrument, tz=tz,
            open_weekday=int(entry["weekly_open"]["weekday"]), open_local=_parse_hhmm(entry["weekly_open"]["local_time"]),
            close_weekday=int(entry["weekly_close"]["weekday"]), close_local=_parse_hhmm(entry["weekly_close"]["local_time"]),
            daily_breaks=breaks, reopening_grace_seconds=grace)
    except ScheduleError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ScheduleError(f"malformed market-hours schedule for {instrument!r}: {exc!r}")


def validate_config_completeness(cfg: Mapping, configured_instruments: Sequence[str]) -> Tuple[bool, dict]:
    """Deterministic startup/readiness validator (§7). Pure: no I/O, no secrets, no live query. Proves every configured
    instrument resolves to exactly one schedule OR is explicitly fail-closed-unvalidated (which resolves to None ->
    fail loud, never suppresses). Returns (ok, report). ok=False if any instrument is silently ungoverned, a named
    schedule is missing, a timezone/grace is invalid, or an alias would double-resolve."""
    report = {"resolved": {}, "fail_closed": [], "unmapped": [], "errors": [], "config_version": cfg.get("config_version"),
              "provenance": cfg.get("provenance"), "holiday_support": cfg.get("holiday_support")}
    try:
        ZoneInfo(cfg["market_timezone"])
    except Exception as exc:  # noqa: BLE001
        report["errors"].append(f"invalid market_timezone: {exc!r}")
    g = cfg.get("reopening_grace_seconds", 300)
    if not isinstance(g, int) or isinstance(g, bool) or not (0 < g <= 86400):
        report["errors"].append("reopening_grace_seconds must be a bounded positive int (<=86400)")
    fcu = cfg.get("fail_closed_unvalidated", {})
    seen = set()
    for inst in configured_instruments:
        if inst in seen:
            report["errors"].append(f"duplicate configured instrument {inst!r} (double-voting risk)")
        seen.add(inst)
        if inst in fcu:
            report["fail_closed"].append(inst)
            continue
        try:
            sched = load_schedule(cfg, inst)
        except ScheduleError as e:
            report["errors"].append(f"{inst}: {e}")
            continue
        if sched is None:
            report["unmapped"].append(inst)     # ungoverned -> fail loud (acceptable ONLY if intentional)
        else:
            report["resolved"][inst] = f"open_wd{sched.open_weekday}@{sched.open_local} close_wd{sched.close_weekday}@{sched.close_local} breaks={len(sched.daily_breaks)}"
    # completeness: every configured instrument must be resolved OR explicitly fail-closed. An UNMAPPED instrument is a
    # governance gap (it fails loud so it cannot suppress, but the operator must have declared it) -> not OK.
    ok = not report["errors"] and not report["unmapped"]
    report["ok"] = ok
    return ok, report


# --------------------------------------------------------------------------- phase classification (DST-aware, UTC in/out)
def _require_utc(now_utc: datetime.datetime) -> datetime.datetime:
    if now_utc.tzinfo is None or now_utc.utcoffset() != datetime.timedelta(0):
        raise ScheduleError("now_utc must be timezone-aware UTC")
    return now_utc


@dataclass(frozen=True)
class PhaseResult:
    phase: str                                   # PHASE_OPEN | PHASE_CLOSED
    reason: str
    last_scheduled_open_utc: Optional[datetime.datetime]   # when the current open session began (for grace)


def classify_market_phase(sched: InstrumentSchedule, now_utc: datetime.datetime) -> PhaseResult:
    """DST-aware. Convert now to market-local time, apply weekly window (Sun-open .. Fri-close) + daily breaks. Returns
    OPEN/CLOSED + the UTC instant the current open session began (used for reopening grace). Deterministic; no I/O."""
    _require_utc(now_utc)
    local = now_utc.astimezone(sched.tz)
    wd = local.weekday()
    t = local.time()

    # weekly closed window: from close_weekday close_local .. open_weekday open_local (wraps the weekend)
    def _in_weekly_open(wd, t):
        # Open interval is [open_weekday open_local, close_weekday close_local) walking forward Sun->Fri.
        # Represent position as (weekday, time). Compare using minutes-of-week.
        def mow(day, tm): return day * 1440 + tm.hour * 60 + tm.minute
        cur = mow(wd, t)
        o = mow(sched.open_weekday, sched.open_local)
        c = mow(sched.close_weekday, sched.close_local)
        if o <= c:
            return o <= cur < c
        return cur >= o or cur < c            # wraps week boundary

    if not _in_weekly_open(wd, t):
        return PhaseResult(PHASE_CLOSED, "WEEKLY_CLOSED", None)

    # daily break (market-local times, applied every open day)
    for (bs, be) in sched.daily_breaks:
        if bs <= be:
            in_break = bs <= t < be
        else:
            in_break = t >= bs or t < be      # wraps midnight
        if in_break:
            return PhaseResult(PHASE_CLOSED, "DAILY_BREAK", None)

    # OPEN: compute when this open session began (for grace) = the most recent boundary among weekly-open and each daily
    # break end that is <= now, in market-local, converted back to UTC (DST-correct).
    candidates = []
    # start of today's session after the latest preceding daily-break end (if any today), else weekly open
    for (bs, be) in sched.daily_breaks:
        be_dt_local = local.replace(hour=be.hour, minute=be.minute, second=0, microsecond=0)
        if be_dt_local <= local:
            candidates.append(be_dt_local)
    # weekly open boundary at/just before now
    days_since_open = (wd - sched.open_weekday) % 7
    open_date_local = (local - datetime.timedelta(days=days_since_open)).replace(
        hour=sched.open_local.hour, minute=sched.open_local.minute, second=0, microsecond=0)
    if open_date_local <= local:
        candidates.append(open_date_local)
    last_open_local = max(candidates) if candidates else open_date_local
    return PhaseResult(PHASE_OPEN, "MARKET_OPEN", last_open_local.astimezone(UTC))


# --------------------------------------------------------------------------- instrument health decision
@dataclass(frozen=True)
class InstrumentHealthDecision:
    instrument: str
    state: str
    reason_code: str
    incident_eligible: bool
    recovery_eligible: bool          # per-instrument recovery eligibility
    contributes_to_full_stream: bool  # may this instrument count toward full-stream quorum?
    expected_reopen_utc: Optional[datetime.datetime]
    grace_expiry_utc: Optional[datetime.datetime]
    evaluated_at_utc: datetime.datetime
    fail_closed: bool = False


def classify_instrument_health(
    *, instrument: str, now_utc: datetime.datetime,
    sched: Optional[InstrumentSchedule],
    last_tick_utc: Optional[datetime.datetime], last_candle_utc: Optional[datetime.datetime],
    tick_threshold_s: int, candle_threshold_s: int,
    connection_fault: bool = False, schedule_error: bool = False,
) -> InstrumentHealthDecision:
    """The core decision. connection_fault=True (genuine socket/session fault) is NEVER suppressed. When the schedule is
    unknown (sched is None) or malformed (schedule_error), FAIL CLOSED -> behave as OPEN so normal detection/recovery fires."""
    now = _require_utc(now_utc)

    # ---- genuine explicit fault: never suppressed, always eligible ----
    if connection_fault:
        return _decision(instrument, MARKET_OPEN_STALE, REASON_CONNECTION_FAULT, now,
                         incident=True, recovery=True, contributes=True)

    # ---- fail-closed: unknown/malformed schedule -> treat as OPEN (conservative) ----
    if sched is None or schedule_error:
        reason = REASON_SCHEDULE_MALFORMED_FAILCLOSED if schedule_error else REASON_SCHEDULE_UNKNOWN_FAILCLOSED
        stale = _is_stale(now, last_tick_utc, last_candle_utc, tick_threshold_s, candle_threshold_s)
        st = MARKET_OPEN_STALE if stale else MARKET_OPEN_FLOWING
        return _decision(instrument, st, reason, now, incident=stale, recovery=stale, contributes=stale, fail_closed=True)

    phase = classify_market_phase(sched, now)

    # ---- CLOSED: expected silence -> suppress incident + per-instrument recovery; never contribute to full-stream ----
    if phase.phase == PHASE_CLOSED:
        return _decision(instrument, MARKET_CLOSED_EXPECTED, REASON_EXPECTED_CLOSED, now,
                         incident=False, recovery=False, contributes=False,
                         reopen=_next_reopen_utc(sched, now))

    # ---- OPEN: reopening grace tolerates absence for a bounded window ----
    grace_expiry = None
    if phase.last_scheduled_open_utc is not None:
        grace_expiry = phase.last_scheduled_open_utc + datetime.timedelta(seconds=sched.reopening_grace_seconds)
        if now < grace_expiry:
            # within grace: absence tolerated; only escalate on an explicit connection fault (handled above)
            return _decision(instrument, MARKET_REOPENING_GRACE, REASON_REOPENING_GRACE, now,
                             incident=False, recovery=False, contributes=False, grace_expiry=grace_expiry)

    # ---- OPEN + past grace: require freshness ----
    stale = _is_stale(now, last_tick_utc, last_candle_utc, tick_threshold_s, candle_threshold_s)
    if not stale:
        return _decision(instrument, MARKET_OPEN_FLOWING, REASON_FLOWING, now,
                         incident=False, recovery=False, contributes=False, grace_expiry=grace_expiry)
    # stale while open past grace -> genuine missing data
    reason = REASON_MISSING_AFTER_GRACE if grace_expiry is not None else REASON_STALE_OPEN
    st = MARKET_OPEN_MISSING_AFTER_GRACE if grace_expiry is not None else MARKET_OPEN_STALE
    return _decision(instrument, st, reason, now, incident=True, recovery=True, contributes=True, grace_expiry=grace_expiry)


def _is_stale(now, last_tick, last_candle, tick_thr, candle_thr) -> bool:
    def age_ok(ts, thr):
        if ts is None:
            return False                      # no data at all while open+past-grace = stale
        t = ts if ts.tzinfo else ts.replace(tzinfo=UTC)
        return (now - t).total_seconds() <= thr
    return not (age_ok(last_tick, tick_thr) and age_ok(last_candle, candle_thr))


def _next_reopen_utc(sched: InstrumentSchedule, now_utc: datetime.datetime) -> Optional[datetime.datetime]:
    """Smallest future market-local boundary (weekly open or daily-break end) converted to UTC. Bounded search (<= 8 days)."""
    local = now_utc.astimezone(sched.tz)
    best = None
    for day in range(0, 9):
        d = (local + datetime.timedelta(days=day)).date()
        bounds = [datetime.datetime.combine(d, be, tzinfo=sched.tz) for (_bs, be) in sched.daily_breaks]
        wd = datetime.datetime.combine(d, sched.open_local, tzinfo=sched.tz)
        if wd.weekday() == sched.open_weekday:
            bounds.append(wd)
        for b in bounds:
            bu = b.astimezone(UTC)
            if bu > now_utc and (best is None or bu < best):
                # only a boundary that actually opens the market (phase OPEN just after)
                if classify_market_phase(sched, bu + datetime.timedelta(seconds=1)).phase == PHASE_OPEN:
                    best = bu
    return best


def _decision(instrument, state, reason, now, *, incident, recovery, contributes,
              reopen=None, grace_expiry=None, fail_closed=False) -> InstrumentHealthDecision:
    return InstrumentHealthDecision(
        instrument=instrument, state=state, reason_code=reason, incident_eligible=incident,
        recovery_eligible=recovery, contributes_to_full_stream=contributes, expected_reopen_utc=reopen,
        grace_expiry_utc=grace_expiry, evaluated_at_utc=now, fail_closed=fail_closed)


# --------------------------------------------------------------------------- full-stream reconnect eligibility (F-2, §7/§8)
def full_stream_recovery_eligible(
    *, instrument_decisions: Sequence[InstrumentHealthDecision],
    shared_stream_fault: bool = False, min_open_stale_quorum: int = 2,
) -> Tuple[bool, str]:
    """A closed/after-hours instrument NEVER triggers full-stream reconnect. Full-stream reconnect is eligible ONLY on a
    genuine SHARED fault (connection/heartbeat/session/upstream — passed as shared_stream_fault) OR when >= quorum
    instruments that are EXPECTED OPEN are stale (contributes_to_full_stream). Returns (eligible, reason)."""
    if shared_stream_fault:
        return True, "SHARED_STREAM_FAULT"
    open_stale = [d for d in instrument_decisions if d.contributes_to_full_stream]
    if len(open_stale) >= min_open_stale_quorum:
        return True, f"OPEN_INSTRUMENTS_STALE_QUORUM({len(open_stale)}>={min_open_stale_quorum})"
    if len(open_stale) == 1:
        # a single genuinely-missing open instrument -> per-instrument recovery handles it, not a full-stream reconnect
        return False, "SINGLE_OPEN_INSTRUMENT_STALE_NO_FULL_STREAM"
    return False, "NO_ELIGIBLE_FULL_STREAM_TRIGGER"


# --------------------------------------------------------------------------- gap-contract classification (§9, non-mutating)
GAP_EXPECTED_CLOSED = "EXPECTED_CLOSED_INTERVAL"        # scheduled absence, non-recoverable
GAP_RECOVERABLE = "RECOVERABLE_GAP"
GAP_UNKNOWN = "UNKNOWN_UNCLASSIFIED_ABSENCE"


def classify_absence_for_gap(*, instrument: str, interval_start_utc: datetime.datetime,
                             interval_end_utc: datetime.datetime, sched: Optional[InstrumentSchedule]) -> str:
    """Classify a data-absence interval WITHOUT mutating/hiding it. Fully inside a governed closed window ->
    EXPECTED_CLOSED_INTERVAL (non-recoverable scheduled absence). Fully inside open hours -> RECOVERABLE_GAP. Unknown
    schedule or straddling -> UNKNOWN_UNCLASSIFIED_ABSENCE (fail closed; never silently 'expected')."""
    if sched is None:
        return GAP_UNKNOWN
    try:
        start_closed = classify_market_phase(sched, interval_start_utc).phase == PHASE_CLOSED
        # sample end just-before to avoid the reopening instant
        end_closed = classify_market_phase(sched, interval_end_utc - datetime.timedelta(seconds=1)).phase == PHASE_CLOSED
    except ScheduleError:
        return GAP_UNKNOWN
    if start_closed and end_closed:
        return GAP_EXPECTED_CLOSED
    if not start_closed and not end_closed:
        return GAP_RECOVERABLE
    return GAP_UNKNOWN


# --------------------------------------------------------------------------- drop-in adapter for the existing watchdog gates
class DstAwareMarketHours:
    """Governed DST-aware replacement for the legacy fixed-UTC checkers. Built from config/market_hours_schedule.v1.json.
    Provides BOTH existing interfaces so it drops into the watchdog with no signature change:
      - is_market_open(timestamp) -> (bool, reason)          [stream-level; primary_instrument]
      - is_truth_expected(instrument, utc_now) -> (bool, reason)  [per-instrument]
    Fails closed (returns open=True) on any unknown/malformed schedule so genuine detection/recovery is NEVER suppressed."""

    def __init__(self, cfg: Mapping, primary_instrument: str = "XAU_USD"):
        self._cfg = cfg
        self._primary = primary_instrument
        self._cache: dict = {}

    def _sched(self, instrument: str) -> Optional[InstrumentSchedule]:
        if instrument not in self._cache:
            try:
                self._cache[instrument] = load_schedule(self._cfg, instrument)
            except ScheduleError:
                self._cache[instrument] = None
        return self._cache[instrument]

    def _now_utc(self, ts: Optional[datetime.datetime]) -> datetime.datetime:
        if ts is None:
            return datetime.datetime.now(UTC)
        return ts if ts.tzinfo else ts.replace(tzinfo=UTC)   # legacy callers may pass naive-UTC

    def is_market_open(self, timestamp: Optional[datetime.datetime] = None) -> Tuple[bool, str]:
        now = self._now_utc(timestamp)
        sched = self._sched(self._primary)
        if sched is None:
            return True, REASON_SCHEDULE_UNKNOWN_FAILCLOSED         # fail closed = open (conservative)
        try:
            p = classify_market_phase(sched, now)
        except ScheduleError:
            return True, REASON_SCHEDULE_MALFORMED_FAILCLOSED
        return (p.phase == PHASE_OPEN), p.reason

    def is_truth_expected(self, instrument: str, utc_now: Optional[datetime.datetime] = None) -> Tuple[bool, str]:
        now = self._now_utc(utc_now)
        sched = self._sched(instrument)
        if sched is None:
            return True, "NO_POLICY"                                # unknown -> conservative (expect data => normal detection)
        try:
            p = classify_market_phase(sched, now)
        except ScheduleError:
            return True, REASON_SCHEDULE_MALFORMED_FAILCLOSED
        return (p.phase == PHASE_OPEN), ("MARKET_OPEN" if p.phase == PHASE_OPEN else REASON_EXPECTED_CLOSED)
