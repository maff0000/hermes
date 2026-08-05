"""HERMES reusable, metadata-driven MARKET-HOURS POLICY authority v1.
WO-HELM-HERMES-ADVANCED-V1-EIGHT-INSTRUMENT-SHADOW-0001.

The Advanced-v1 gap-classification path selects market-hours behaviour by the registry field
`InstrumentRecord.market_hours_policy` (a governed policy KEY — DATA), NEVER by ticker symbol or asset category.
One reusable SessionPolicy implementation is parameterised by a governed schedule; the resolver maps a policy key to
a single shared instance. Adding an instrument to an existing policy is data-only; there is no per-ticker branch, no
ticker->policy dictionary, and no silent uniform default when metadata is missing (fail-closed).

Governed policy keys (must match the registry `market_hours_policy` domain):
  * fx_24x5    — FOREX continuous week. Source: config/market_hours_schedule.v1.json named_schedule 'fx'
                 (config_version 3): weekly open Sun 17:00 America/New_York, weekly close Fri 17:00; no daily break.
  * metals     — METAL continuous week. Source: named_schedule 'metals': weekly open Sun 18:00 NY, close Fri 17:00,
                 daily rollover break 17:00-18:00 NY (OANDA_METAL_DAILY_ROLLOVER).
  * index_cash — EQUITY-INDEX CFD near-24x5. GOVERNED ASSUMPTION (no named_schedule exists in-repo for this key):
                 weekly open Sun 18:00 NY, close Fri 17:00, daily maintenance halt 17:00-18:00 NY. Documented here
                 and in tests; correct against the exchange/broker calendar before any production activation.
  * energy     — CRUDE-OIL CFD near-24x5. GOVERNED ASSUMPTION (no named_schedule exists in-repo for this key):
                 weekly open Sun 18:00 NY, close Fri 17:00, daily maintenance halt 17:00-18:00 NY. Documented here
                 and in tests; correct against the exchange/broker calendar before any production activation.

UTC is the sole internal time authority. All local reasoning uses an explicit IANA timezone (America/New_York) and is
DST-correct via zoneinfo; every result is returned UTC-normalised. No host-local timezone assumption is made.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, time, timezone
from typing import Tuple
from zoneinfo import ZoneInfo

UTC = timezone.utc
NY = ZoneInfo("America/New_York")               # governed market timezone (config_version 3: America/New_York)

# Market phases (superset of the legacy gaps vocab; CLOSED_SESSION is the new metadata-driven scheduled-closure phase).
PHASE_OPEN = "OPEN"
PHASE_CLOSED_WEEKEND = "CLOSED_WEEKEND"
PHASE_CLOSED_SESSION = "CLOSED_SESSION"          # expected intra-week scheduled closure (daily break / maintenance halt)
MARKET_PHASES = (PHASE_OPEN, PHASE_CLOSED_WEEKEND, PHASE_CLOSED_SESSION)


class PolicyError(ValueError):
    """Fail-closed market-hours policy fault (unknown key, missing metadata, invalid configuration)."""


@dataclass(frozen=True)
class _Break:
    start: time            # NY-local inclusive start
    end: time              # NY-local exclusive end
    label: str


@dataclass(frozen=True)
class SessionPolicy:
    """Reusable governed session schedule. Weekdays: Mon=0 .. Sun=6 (Python weekday()). Times are NY-local; DST is
    resolved per instant via zoneinfo. The market is OPEN from `open_weekday`@`open_time` through `close_weekday`@
    `close_time` each week, minus `daily_breaks` (intra-week scheduled closures, applied every open weekday)."""
    key: str
    market_type: str
    open_weekday: int
    open_time: time
    close_weekday: int
    close_time: time
    daily_breaks: Tuple[_Break, ...] = ()
    source: str = ""

    def _ny(self, dt_utc: datetime) -> datetime:
        if dt_utc.tzinfo is None:
            dt_utc = dt_utc.replace(tzinfo=UTC)
        return dt_utc.astimezone(NY)

    def _in_weekly_open(self, ny: datetime) -> bool:
        """True iff ny is within the weekly open span [Sun open .. Fri close] (handles the week-wrapping window)."""
        wd, t = ny.weekday(), ny.time()
        o_wd, o_t, c_wd, c_t = self.open_weekday, self.open_time, self.close_weekday, self.close_time
        # Normalise each instant to "minutes since Monday 00:00" for a linear weekly comparison.
        cur = wd * 1440 + t.hour * 60 + t.minute
        opn = o_wd * 1440 + o_t.hour * 60 + o_t.minute
        cls = c_wd * 1440 + c_t.hour * 60 + c_t.minute
        if opn <= cls:
            return opn <= cur < cls
        # wrapping window (e.g. open Sun 17:00 .. close Fri 17:00 is NOT wrapping; but open Sun 18:00..Fri 17:00 isn't
        # either — the wrap form covers schedules that cross Monday midnight). Kept general/fail-safe.
        return cur >= opn or cur < cls

    def _in_daily_break(self, ny: datetime) -> bool:
        t = ny.time()
        for b in self.daily_breaks:
            if b.start <= t < b.end:
                return True
        return False

    def phase(self, dt_utc: datetime) -> str:
        """Governed market phase for a UTC instant: OPEN, CLOSED_WEEKEND (outside the weekly span), or CLOSED_SESSION
        (inside the weekly span but within a scheduled daily break)."""
        ny = self._ny(dt_utc)
        if not self._in_weekly_open(ny):
            return PHASE_CLOSED_WEEKEND
        if self._in_daily_break(ny):
            return PHASE_CLOSED_SESSION
        return PHASE_OPEN

    def is_open(self, dt_utc: datetime) -> bool:
        return self.phase(dt_utc) == PHASE_OPEN

    def is_expected_closure(self, dt_utc: datetime) -> bool:
        """True iff the market is closed for a GOVERNED/scheduled reason (weekend or daily break) at this instant —
        i.e. a missing candle here is EXPECTED, not an outage."""
        return self.phase(dt_utc) != PHASE_OPEN


_T = time  # alias for compactness
_ROLLOVER = (_Break(start=_T(17, 0), end=_T(18, 0), label="DAILY_MAINTENANCE_NY_1700_1800"),)

# The four governed policies as reusable DATA instances (no per-policy code branch; the resolver is a dict lookup).
_POLICIES = {
    "fx_24x5": SessionPolicy(key="fx_24x5", market_type="FOREX", open_weekday=6, open_time=_T(17, 0),
                             close_weekday=4, close_time=_T(17, 0), daily_breaks=(),
                             source="config/market_hours_schedule.v1.json named_schedule 'fx' (config_version 3)"),
    "metals": SessionPolicy(key="metals", market_type="METAL", open_weekday=6, open_time=_T(18, 0),
                            close_weekday=4, close_time=_T(17, 0), daily_breaks=_ROLLOVER,
                            source="config/market_hours_schedule.v1.json named_schedule 'metals' (config_version 3)"),
    "index_cash": SessionPolicy(key="index_cash", market_type="INDEX_CFD", open_weekday=6, open_time=_T(18, 0),
                                close_weekday=4, close_time=_T(17, 0), daily_breaks=_ROLLOVER,
                                source="GOVERNED ASSUMPTION (no in-repo named_schedule): near-24x5 index CFD, daily 17:00-18:00 NY halt"),
    "energy": SessionPolicy(key="energy", market_type="ENERGY_CFD", open_weekday=6, open_time=_T(18, 0),
                            close_weekday=4, close_time=_T(17, 0), daily_breaks=_ROLLOVER,
                            source="GOVERNED ASSUMPTION (no in-repo named_schedule): near-24x5 energy CFD, daily 17:00-18:00 NY halt"),
}

GOVERNED_POLICY_KEYS = tuple(_POLICIES)


def resolve_policy(policy_key) -> SessionPolicy:
    """Resolve a governed market-hours policy by its registry key. Fail-closed: missing/empty metadata -> PolicyError
    (NO silent uniform default); unknown key -> PolicyError. Never performs a ticker lookup."""
    if policy_key is None or not str(policy_key).strip():
        raise PolicyError("GOV-HERMES-MHP-001: market_hours_policy metadata is missing/empty (fail-closed; no default)")
    key = str(policy_key).strip()
    if key not in _POLICIES:
        raise PolicyError(f"GOV-HERMES-MHP-002: unknown market_hours_policy {key!r} (governed keys: {GOVERNED_POLICY_KEYS})")
    return _POLICIES[key]
