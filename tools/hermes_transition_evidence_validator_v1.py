#!/usr/bin/env python3
"""
HERMES XAU Market-Transition Evidence Validator (v1)
WO-HELM-HERMES-MARKET-TRANSITION-EVIDENCE-VALIDATOR-0001

PURPOSE
-------
Read-only, deterministic parser + validator for the XAU market-transition
observer log produced by ``xau_transition_observer.sh``. It turns a raw
observation log into a stable, machine-readable audit verdict so the
weekly-break / daily-rollover transition can be REPRODUCIBLY signed off.

DOCTRINE (non-negotiable)
-------------------------
* This tool is INERT. It NEVER writes to the observer, the runtime, Redis, SQL,
  or the network. It only reads: a log file, a schedule config JSON, and an
  optional runtime-identity JSON. Pure stdlib.
* It NEVER infers missing evidence as success. Every acceptance condition
  resolves to exactly one of: PASS / FAIL / INDETERMINATE / NOT_OBSERVED.
* The governed closure window is computed here, DST-aware, from the schedule
  config (America/New_York) + grace. Values inside the log are NOT trusted for
  window boundaries -- the log is audited AGAINST the independently computed
  governed window.
* UTC only. Project-relative paths. No secrets.

CANONICAL OBSERVER-LOG LINE GRAMMAR (v1)
----------------------------------------
Every non-blank line begins with an outer UTC bracket ``[<UTC>]``. ``<UTC>`` is
either a full ISO-8601 instant (``2026-07-16T21:00:00Z``) or a bare
``HH:MM:SSZ`` wall-clock which is combined with ``--date``. The full-instant
form is authoritative and preferred.

Marker lines (fixed prefixes after the bracket):
  [<UTC>] OBSERVER START (pid <N>). <free text>
  [<UTC>] === ENTERING OBSERVATION WINDOW ===
  [<UTC>] === WINDOW COMPLETE ===
  [<UTC>] D1 latest after seal: <ts> <FRESHNESS>
  [<UTC>] OBSERVER END (pid <N>).
  [<UTC>] HEALTH RESTORED <free text>                     (optional)
  [<UTC>] FAIL-LOUD <INSTR>: schedule=<S> state=<STATE> reason=<REASON>   (optional)

Sample lines (five pipe-delimited groups):
  [<UTC>] <HH:MM:SSZ> | phase=<STATE> truth_expected=<bool> reason=<REASON>
        | M1=<ts> <FRESHNESS>
        | quote=<STATUS>/<FRESHNESS> age=<float>
        | feed=<STATUS>/<FRESHNESS> conn=<CONN> fault=<STATE> M1age=<int>
        | incidents=<int> reconnect=<int> recovery=<int> false_close_log=<int>

Tokens:
  FRESHNESS  := FRESH | STALE | NONE
  quote STATUS := OK | ERROR | MISSING
  feed  STATUS := OK | STALE | DOWN
  CONN   := UP | DOWN
  fault  := NONE | STALE | DOWN
  counters (incidents/reconnect/recovery/false_close_log) are cumulative ints.

The parser is tolerant of extra trailing text on marker lines and of unknown
phase / reason tokens (they are preserved verbatim), but a sample line that
does not satisfy the five-group shape is reported as a malformed line rather
than silently dropped.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - stdlib on 3.9+
    ZoneInfo = None  # type: ignore

TOOL_ID = "hermes_transition_evidence_validator_v1"
TOOL_VERSION = "1.0.0"

# Verdict states
PASS = "PASS"
FAIL = "FAIL"
INDETERMINATE = "INDETERMINATE"
NOT_OBSERVED = "NOT_OBSERVED"

# Freshness advance tolerance (seconds) for quote "tick" fabrication detection.
# A frozen (genuinely closed) feed keeps effective tick ts constant; a
# fabricated refresh advances it materially. 5s absorbs float jitter only.
TICK_FABRICATION_TOLERANCE_S = 5.0

# Phase tokens the observer emits for a genuinely-healthy, flowing market.
HEALTHY_PHASES = {"MARKET_OPEN", "MARKET_OPEN_HEALTHY", "HEALTHY", "STREAM_RESTORED", "GREEN"}
# Phase tokens indicating a raised staleness/fault escalation.
ESCALATION_PHASES = {"MARKET_OPEN_STALE", "STREAM_STALL", "STALE", "FAULT"}


# --------------------------------------------------------------------------- #
# Timestamp helpers                                                           #
# --------------------------------------------------------------------------- #

_FULL_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
_CLOCK_TS_RE = re.compile(r"^(\d{2}):(\d{2}):(\d{2})Z?$")


def parse_utc(raw: str, date: Optional[str] = None) -> Optional[datetime]:
    """Parse a bracket/marker timestamp into an aware UTC datetime.

    Accepts a full ISO-8601 instant (with optional fractional seconds and a
    trailing ``Z``) or a bare ``HH:MM:SSZ`` clock which is combined with the
    supplied ``date`` (YYYY-MM-DD). Returns None if unparseable.
    """
    raw = raw.strip()
    if _FULL_TS_RE.match(raw):
        s = raw.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            # Trim fractional seconds beyond microseconds if present.
            try:
                head, _, tail = raw.partition(".")
                frac = tail.rstrip("Z")[:6]
                dt = datetime.fromisoformat(f"{head}.{frac}+00:00")
            except ValueError:
                return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    m = _CLOCK_TS_RE.match(raw)
    if m and date:
        try:
            d = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            return None
        return datetime(d.year, d.month, d.day, int(m.group(1)), int(m.group(2)),
                        int(m.group(3)), tzinfo=timezone.utc)
    return None


def iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# Parsing                                                                     #
# --------------------------------------------------------------------------- #

_LINE_RE = re.compile(r"^\[(?P<outer>[^\]]+)\]\s*(?P<rest>.*)$")

_SAMPLE_RE = re.compile(
    r"^(?P<clock>\d{2}:\d{2}:\d{2}Z?)\s*\|\s*"
    r"phase=(?P<phase>\S+)\s+truth_expected=(?P<truth>\S+)\s+reason=(?P<reason>\S+)\s*\|\s*"
    r"M1=(?P<m1ts>\S+)\s+(?P<m1fresh>\S+)\s*\|\s*"
    r"quote=(?P<qstatus>[^/\s]+)/(?P<qfresh>\S+)\s+age=(?P<qage>[^\s|]+)\s*\|\s*"
    r"feed=(?P<fstatus>[^/\s]+)/(?P<ffresh>\S+)\s+conn=(?P<conn>\S+)\s+fault=(?P<fault>\S+)\s+M1age=(?P<m1age>\S+)\s*\|\s*"
    r"incidents=(?P<incidents>\d+)\s+reconnect=(?P<reconnect>\d+)\s+recovery=(?P<recovery>\d+)\s+false_close_log=(?P<false_close>\d+)\s*$"
)

_FAILLOUD_RE = re.compile(
    r"^FAIL-LOUD\s+(?P<instr>\S+):\s*schedule=(?P<schedule>\S+)\s+state=(?P<state>\S+)\s+reason=(?P<reason>\S+)"
)


def _to_bool(tok: str) -> Optional[bool]:
    t = tok.strip().lower()
    if t in ("true", "1", "yes"):
        return True
    if t in ("false", "0", "no"):
        return False
    return None


def _to_float(tok: str) -> Optional[float]:
    try:
        return float(tok)
    except (TypeError, ValueError):
        return None


@dataclass
class Sample:
    line_no: int
    outer_ts: datetime
    clock: str
    phase: str
    truth_expected: Optional[bool]
    reason: str
    m1_ts: Optional[datetime]
    m1_fresh: str
    quote_status: str
    quote_fresh: str
    quote_age_s: Optional[float]
    feed_status: str
    feed_fresh: str
    conn: str
    fault: str
    m1age: Optional[int]
    incidents: int
    reconnect: int
    recovery: int
    false_close_log: int

    @property
    def effective_quote_ts(self) -> Optional[datetime]:
        """Reconstruct the last-tick instant the quote reflects: outer - age.

        A genuinely frozen feed keeps this constant (age grows with wall clock);
        a fabricated refresh (age reset small) advances it.
        """
        if self.quote_age_s is None:
            return None
        return self.outer_ts - timedelta(seconds=self.quote_age_s)


@dataclass
class ParsedLog:
    observer_start: Optional[datetime] = None
    observer_start_pid: Optional[int] = None
    observer_end: Optional[datetime] = None
    observer_end_pid: Optional[int] = None
    entering_window_ts: Optional[datetime] = None
    window_complete_ts: Optional[datetime] = None
    d1_after_seal: Optional[Dict[str, str]] = None
    health_restored_markers: List[datetime] = field(default_factory=list)
    fail_loud: Dict[str, Dict[str, str]] = field(default_factory=dict)
    samples: List[Sample] = field(default_factory=list)
    malformed_lines: List[Dict[str, Any]] = field(default_factory=list)


def parse_log(text: str, date: Optional[str]) -> ParsedLog:
    out = ParsedLog()
    for idx, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.rstrip("\n")
        if not line.strip():
            continue
        m = _LINE_RE.match(line.strip())
        if not m:
            out.malformed_lines.append({"line_no": idx, "text": line, "why": "no_outer_bracket"})
            continue
        outer = parse_utc(m.group("outer"), date)
        rest = m.group("rest").strip()

        # Marker lines -------------------------------------------------------
        if rest.startswith("OBSERVER START"):
            out.observer_start = outer
            pid = re.search(r"pid\s+(\d+)", rest)
            out.observer_start_pid = int(pid.group(1)) if pid else None
            continue
        if rest.startswith("OBSERVER END"):
            out.observer_end = outer
            pid = re.search(r"pid\s+(\d+)", rest)
            out.observer_end_pid = int(pid.group(1)) if pid else None
            continue
        if "ENTERING OBSERVATION WINDOW" in rest:
            out.entering_window_ts = outer
            continue
        if "WINDOW COMPLETE" in rest:
            out.window_complete_ts = outer
            continue
        if rest.startswith("D1 latest after seal:"):
            payload = rest.split(":", 1)[1].strip()
            parts = payload.split()
            out.d1_after_seal = {
                "ts": parts[0] if parts else "",
                "freshness": parts[1] if len(parts) > 1 else "",
            }
            continue
        if rest.startswith("HEALTH RESTORED"):
            if outer is not None:
                out.health_restored_markers.append(outer)
            continue
        fl = _FAILLOUD_RE.match(rest)
        if fl:
            out.fail_loud[fl.group("instr")] = {
                "schedule": fl.group("schedule"),
                "state": fl.group("state"),
                "reason": fl.group("reason"),
            }
            continue

        # Sample line --------------------------------------------------------
        sm = _SAMPLE_RE.match(rest)
        if sm and outer is not None:
            m1_raw = sm.group("m1ts")
            m1_ts = None if m1_raw in ("-", "NONE", "None", "null") else parse_utc(m1_raw, date)
            try:
                m1age = int(sm.group("m1age"))
            except ValueError:
                m1age = None
            out.samples.append(Sample(
                line_no=idx,
                outer_ts=outer,
                clock=sm.group("clock"),
                phase=sm.group("phase"),
                truth_expected=_to_bool(sm.group("truth")),
                reason=sm.group("reason"),
                m1_ts=m1_ts,
                m1_fresh=sm.group("m1fresh"),
                quote_status=sm.group("qstatus"),
                quote_fresh=sm.group("qfresh"),
                quote_age_s=_to_float(sm.group("qage")),
                feed_status=sm.group("fstatus"),
                feed_fresh=sm.group("ffresh"),
                conn=sm.group("conn"),
                fault=sm.group("fault"),
                m1age=m1age,
                incidents=int(sm.group("incidents")),
                reconnect=int(sm.group("reconnect")),
                recovery=int(sm.group("recovery")),
                false_close_log=int(sm.group("false_close")),
            ))
            continue

        out.malformed_lines.append({"line_no": idx, "text": line, "why": "unrecognised_body"})
    return out


# --------------------------------------------------------------------------- #
# Governed window computation (DST-aware, independent of the log)             #
# --------------------------------------------------------------------------- #

@dataclass
class GovernedWindow:
    market_timezone: str
    named_schedule: Optional[str]
    schedule_resolution: str          # RESOLVED | FAIL_CLOSED_NONE | NO_DAILY_BREAK | UNMAPPED
    closure_start: Optional[datetime]
    closure_end: Optional[datetime]   # == reopening
    reopening: Optional[datetime]
    grace_seconds: int
    grace_end: Optional[datetime]
    break_label: Optional[str]
    detail: str


def _local_time(s: str) -> dt_time:
    hh, mm = s.split(":")
    return dt_time(int(hh), int(mm))


def compute_governed_window(config: dict, instrument: str, date: str,
                            grace_seconds: Optional[int]) -> GovernedWindow:
    tzname = config.get("market_timezone", "America/New_York")
    if ZoneInfo is None:
        raise RuntimeError("zoneinfo unavailable; cannot compute DST-aware window")
    tz = ZoneInfo(tzname)
    cfg_grace = int(config.get("reopening_grace_seconds", 0) or 0)
    grace = int(grace_seconds) if grace_seconds is not None else cfg_grace

    inst_map = config.get("instrument_map", {})
    fail_closed = config.get("fail_closed_unvalidated", {})
    named = inst_map.get(instrument)

    if named is None:
        resolution = "FAIL_CLOSED_NONE" if instrument in fail_closed else "UNMAPPED"
        return GovernedWindow(tzname, None, resolution, None, None, None, grace, None,
                              None, f"instrument {instrument} resolves to no governed schedule "
                                    f"({resolution}) -> fail loud, no closure window computed")

    sched = config.get("named_schedules", {}).get(named, {})
    breaks = sched.get("daily_breaks", [])
    if not breaks:
        return GovernedWindow(tzname, named, "NO_DAILY_BREAK", None, None, None, grace, None,
                              None, f"schedule {named} has no daily break window")

    br = breaks[0]
    ls = _local_time(br["local_start"])
    le = _local_time(br["local_end"])
    d = datetime.strptime(date, "%Y-%m-%d").date()
    closure_start_local = datetime.combine(d, ls, tz)
    closure_end_local = datetime.combine(d, le, tz)
    closure_start = closure_start_local.astimezone(timezone.utc)
    closure_end = closure_end_local.astimezone(timezone.utc)
    grace_end = closure_end + timedelta(seconds=grace)
    return GovernedWindow(
        market_timezone=tzname, named_schedule=named, schedule_resolution="RESOLVED",
        closure_start=closure_start, closure_end=closure_end, reopening=closure_end,
        grace_seconds=grace, grace_end=grace_end, break_label=br.get("label"),
        detail=(f"{named} daily break {br['local_start']}-{br['local_end']} {tzname} "
                f"-> closure {iso(closure_start)}..{iso(closure_end)} UTC, grace {grace}s "
                f"-> grace_end {iso(grace_end)}"),
    )


# --------------------------------------------------------------------------- #
# Validation                                                                  #
# --------------------------------------------------------------------------- #

def _cond(state: str, detail: str, **extra: Any) -> Dict[str, Any]:
    d = {"state": state, "detail": detail}
    d.update(extra)
    return d


def _window_of(ts: datetime, gw: GovernedWindow) -> str:
    if gw.closure_start is None:
        return "unknown"
    if ts < gw.closure_start:
        return "pre_close"
    if ts < gw.closure_end:
        return "closure"
    if gw.grace_end is not None and ts < gw.grace_end:
        return "grace"
    return "post_grace"


def validate(parsed: ParsedLog, gw: GovernedWindow, cadence_seconds: int,
             instrument: str, date: str, runtime_identity: Optional[dict],
             log_sha256: str, log_path: str) -> Dict[str, Any]:
    samples = sorted(parsed.samples, key=lambda s: s.outer_ts)
    conditions: Dict[str, Dict[str, Any]] = {}

    # -- Partition samples by governed window -------------------------------
    buckets: Dict[str, List[Sample]] = {"pre_close": [], "closure": [], "grace": [], "post_grace": [], "unknown": []}
    for s in samples:
        buckets[_window_of(s.outer_ts, gw)].append(s)

    pre = buckets["pre_close"]
    clo = buckets["closure"]
    gra = buckets["grace"]
    post = buckets["post_grace"]

    last_pre_close_candle = pre[-1].m1_ts if pre else None
    last_pre_close_tick = pre[-1].effective_quote_ts if pre else None

    # -- Continuity & monotonicity ------------------------------------------
    monotonic = all(samples[i].outer_ts <= samples[i + 1].outer_ts for i in range(len(samples) - 1))
    max_gap = 0.0
    gap_at = None
    for i in range(len(samples) - 1):
        g = (samples[i + 1].outer_ts - samples[i].outer_ts).total_seconds()
        if g > max_gap:
            max_gap = g
            gap_at = iso(samples[i].outer_ts)
    gap_threshold = 3 * cadence_seconds

    # -- END marker ---------------------------------------------------------
    end_present = parsed.observer_end is not None
    conditions["END_MARKER_PRESENT"] = _cond(
        PASS if end_present else INDETERMINATE,
        "OBSERVER END marker present" if end_present
        else "OBSERVER END marker absent -> observation incomplete; cannot conclude",
    )

    # -- Required window coverage -------------------------------------------
    max_ts = samples[-1].outer_ts if samples else None
    covered = bool(pre) and bool(clo) and (max_ts is not None and gw.grace_end is not None
                                           and max_ts >= gw.grace_end)
    conditions["REQUIRED_WINDOW_COVERED"] = _cond(
        PASS if covered else INDETERMINATE,
        (f"pre_close={len(pre)} closure={len(clo)} grace={len(gra)} post_grace={len(post)}; "
         f"last_sample={iso(max_ts)} grace_end={iso(gw.grace_end)}"),
    )

    # -- Sample continuity --------------------------------------------------
    if len(samples) < 2:
        cont_state = INDETERMINATE
    elif not monotonic:
        cont_state = FAIL
    elif max_gap > gap_threshold:
        cont_state = FAIL
    else:
        cont_state = PASS
    conditions["SAMPLE_CONTINUITY"] = _cond(
        cont_state,
        f"max_sample_gap_s={max_gap:.1f} threshold={gap_threshold} (3x cadence) "
        f"monotonic={monotonic} first_gap_at={gap_at}",
    )

    # -- Closure classification ---------------------------------------------
    if not clo:
        clo_state, clo_detail = NOT_OBSERVED, "no samples inside governed closure window"
    else:
        bad = [s for s in clo if s.truth_expected is True
               or s.phase not in ("MARKET_CLOSED_EXPECTED",)]
        if bad:
            clo_state = FAIL
            clo_detail = (f"{len(bad)} closure sample(s) not classified MARKET_CLOSED_EXPECTED "
                          f"(first at line {bad[0].line_no}: phase={bad[0].phase} "
                          f"truth_expected={bad[0].truth_expected})")
        else:
            clo_state = PASS
            clo_detail = f"all {len(clo)} closure samples classified MARKET_CLOSED_EXPECTED / truth_expected=false"
    conditions["CLOSURE_CLASSIFICATION"] = _cond(clo_state, clo_detail)

    # -- No fabricated freshness during closure -----------------------------
    fab_candle = None
    fab_tick = None
    if clo:
        for s in clo:
            if s.m1_ts is not None and last_pre_close_candle is not None and s.m1_ts > last_pre_close_candle:
                fab_candle = s
                break
        for s in clo:
            eff = s.effective_quote_ts
            if eff is not None and last_pre_close_tick is not None and \
                    (eff - last_pre_close_tick).total_seconds() > TICK_FABRICATION_TOLERANCE_S:
                fab_tick = s
                break
    if not clo:
        fab_state, fab_detail = NOT_OBSERVED, "no closure samples to check for fabricated freshness"
    elif fab_candle is not None or fab_tick is not None:
        fab_state = FAIL
        bits = []
        if fab_candle is not None:
            bits.append(f"M1 candle advanced during closure to {iso(fab_candle.m1_ts)} "
                        f"(> last pre-close {iso(last_pre_close_candle)}) at line {fab_candle.line_no}")
        if fab_tick is not None:
            bits.append(f"quote tick freshness advanced during closure to "
                        f"{iso(fab_tick.effective_quote_ts)} (> last pre-close {iso(last_pre_close_tick)}) "
                        f"at line {fab_tick.line_no}")
        fab_detail = "; ".join(bits)
    else:
        fab_state = PASS
        fab_detail = "last-data timestamps frozen through closure (no candle/tick advance)"
    conditions["NO_FABRICATED_FRESHNESS"] = _cond(
        fab_state, fab_detail,
        fabricated_candle=fab_candle is not None, fabricated_tick=fab_tick is not None)

    # -- Incident / reconnect counters --------------------------------------
    def _counts(name: str) -> Dict[str, Any]:
        def val(bkt: List[Sample]) -> Optional[int]:
            return getattr(bkt[-1], name) if bkt else None
        baseline = getattr(pre[-1], name) if pre else (getattr(clo[0], name) if clo else None)
        closure_end_val = getattr(clo[-1], name) if clo else None
        delta_closure = (closure_end_val - baseline) if (closure_end_val is not None and baseline is not None) else None
        return {
            "pre_close": val(pre), "closure": val(clo), "grace": val(gra), "post_grace": val(post),
            "baseline_pre_close": baseline, "delta_closure": delta_closure,
        }

    incident_counts = _counts("incidents")
    reconnect_counts = _counts("reconnect")

    def _no_closure_delta(counts: Dict[str, Any], label: str) -> Dict[str, Any]:
        if not clo:
            return _cond(NOT_OBSERVED, f"no closure samples to check {label}")
        d = counts["delta_closure"]
        if d is None:
            return _cond(INDETERMINATE, f"insufficient data to compute {label} delta across closure")
        if d > 0:
            return _cond(FAIL, f"{label} counter increased by {d} during governed closure (baseline "
                               f"{counts['baseline_pre_close']} -> {counts['closure']})")
        return _cond(PASS, f"{label} counter unchanged across closure (={counts['closure']})")

    conditions["NO_INACTIVITY_INCIDENT_DURING_CLOSURE"] = _no_closure_delta(incident_counts, "incident")
    conditions["NO_CLOSURE_DRIVEN_RECONNECT"] = _no_closure_delta(reconnect_counts, "reconnect")

    # -- Resumed flow / first resumed markers -------------------------------
    first_resumed_candle = None
    first_resumed_tick = None
    health_restoration_ts = None
    for s in samples:
        if gw.reopening is not None and s.outer_ts >= gw.reopening:
            if first_resumed_candle is None and s.m1_ts is not None and last_pre_close_candle is not None \
                    and s.m1_ts > last_pre_close_candle:
                first_resumed_candle = s.outer_ts
            if first_resumed_tick is None and s.effective_quote_ts is not None and last_pre_close_tick is not None \
                    and (s.effective_quote_ts - last_pre_close_tick).total_seconds() > TICK_FABRICATION_TOLERANCE_S:
                first_resumed_tick = s.effective_quote_ts
            if health_restoration_ts is None and s.phase in HEALTHY_PHASES \
                    and s.m1_ts is not None and last_pre_close_candle is not None \
                    and s.m1_ts > last_pre_close_candle:
                health_restoration_ts = s.outer_ts
    if not health_restoration_ts and parsed.health_restored_markers:
        # Only honour an explicit HEALTH RESTORED marker if genuine flow exists.
        if first_resumed_candle is not None:
            health_restoration_ts = min(parsed.health_restored_markers)

    resumed = first_resumed_candle is not None
    escalated = False
    esc_detail = ""
    if gw.grace_end is not None:
        post_base_incident = incident_counts["baseline_pre_close"]
        for s in post:
            inc_delta = (s.incidents - post_base_incident) if post_base_incident is not None else s.incidents
            if s.phase in ESCALATION_PHASES or s.fault in ("STALE", "DOWN") or (inc_delta and inc_delta > 0):
                escalated = True
                esc_detail = (f"post-grace escalation at {iso(s.outer_ts)} "
                              f"(phase={s.phase} fault={s.fault} incidents={s.incidents})")
                break
    if resumed:
        conditions["GENUINE_RESUMED_FLOW_OR_ESCALATION"] = _cond(
            PASS, f"genuine flow resumed: first_resumed_candle={iso(first_resumed_candle)} "
                  f"first_resumed_tick={iso(first_resumed_tick)}")
    elif escalated:
        conditions["GENUINE_RESUMED_FLOW_OR_ESCALATION"] = _cond(
            PASS, f"flow did not resume but correct post-grace escalation observed (Path B): {esc_detail}")
    elif not post and not gra:
        conditions["GENUINE_RESUMED_FLOW_OR_ESCALATION"] = _cond(
            NOT_OBSERVED, "no reopening/grace/post-grace samples observed")
    else:
        conditions["GENUINE_RESUMED_FLOW_OR_ESCALATION"] = _cond(
            FAIL, "flow absent after grace AND no post-grace escalation (silent stall)")

    # -- Grace timing: no premature escalation inside grace window ----------
    if gw.grace_end is None:
        grace_state, grace_detail = INDETERMINATE, "no governed grace window computed"
    else:
        premature = None
        base_incident = incident_counts["baseline_pre_close"] or 0
        for s in gra:
            inc_delta = s.incidents - base_incident
            if s.phase in ESCALATION_PHASES or s.fault in ("STALE", "DOWN") or inc_delta > 0:
                premature = s
                break
        if premature is not None:
            grace_state = FAIL
            grace_detail = (f"escalation fired inside grace window at {iso(premature.outer_ts)} "
                            f"(phase={premature.phase} fault={premature.fault}) before grace_end "
                            f"{iso(gw.grace_end)}")
        else:
            grace_state = PASS
            grace_detail = (f"grace window {iso(gw.reopening)}..{iso(gw.grace_end)} respected; "
                            f"no premature escalation")
    conditions["CORRECT_GRACE_TIMING"] = _cond(grace_state, grace_detail)

    # -- No false GREEN during grace ----------------------------------------
    if gw.grace_end is None:
        fg_state, fg_detail = INDETERMINATE, "no governed grace window computed"
    else:
        false_green = None
        for s in gra:
            healthy_claim = s.phase in HEALTHY_PHASES
            genuine = (s.m1_ts is not None and last_pre_close_candle is not None
                       and s.m1_ts > last_pre_close_candle)
            if healthy_claim and not genuine:
                false_green = s
                break
        if false_green is not None:
            fg_state = FAIL
            fg_detail = (f"health/GREEN declared inside grace at {iso(false_green.outer_ts)} "
                         f"(phase={false_green.phase}) while flow had NOT resumed "
                         f"(M1 still {iso(false_green.m1_ts)} <= last pre-close {iso(last_pre_close_candle)})")
        elif not gra:
            fg_state, fg_detail = NOT_OBSERVED, "no grace-window samples observed"
        else:
            fg_state = PASS
            fg_detail = "no premature health/GREEN declared during grace"
    conditions["NO_FALSE_GREEN_DURING_GRACE"] = _cond(fg_state, fg_detail)

    # -- REST quote vs stream separation ------------------------------------
    masking = None
    for s in samples:
        if s.truth_expected is True and s.quote_fresh == "FRESH" and s.feed_fresh == "STALE" \
                and s.fault == "NONE":
            masking = s
            break
    if masking is not None:
        sep_state = FAIL
        sep_detail = (f"REST quote FRESH masks a STALE stream feed with no fault raised at "
                      f"{iso(masking.outer_ts)} (quote={masking.quote_status}/{masking.quote_fresh} "
                      f"feed={masking.feed_status}/{masking.feed_fresh} fault={masking.fault})")
    else:
        sep_state = PASS
        sep_detail = "no sample where a FRESH REST quote masks a STALE stream feed without a raised fault"
    conditions["REST_QUOTE_VS_STREAM_SEPARATION"] = _cond(sep_state, sep_detail)

    # -- Runtime identity continuity ----------------------------------------
    ri_detail_bits = []
    ri_state = PASS
    sp, ep = parsed.observer_start_pid, parsed.observer_end_pid
    if sp is not None and ep is not None:
        if sp != ep:
            ri_state = FAIL
            ri_detail_bits.append(f"OBSERVER START pid {sp} != END pid {ep} (process restarted mid-window)")
        else:
            ri_detail_bits.append(f"observer pid stable ({sp})")
    elif sp is None or ep is None:
        if runtime_identity is None:
            ri_state = INDETERMINATE
            ri_detail_bits.append("observer start/end pid not both present and no runtime-identity file")
        else:
            ri_detail_bits.append("observer pid incomplete; relying on runtime-identity file")
    if runtime_identity is not None:
        start_snap = runtime_identity.get("start")
        end_snap = runtime_identity.get("end")
        if start_snap is not None and end_snap is not None:
            if start_snap != end_snap:
                ri_state = FAIL
                ri_detail_bits.append(f"runtime identity changed: start={start_snap} end={end_snap}")
            else:
                ri_detail_bits.append("runtime-identity start snapshot == end snapshot")
        else:
            if ri_state == PASS:
                ri_state = INDETERMINATE
            ri_detail_bits.append("runtime-identity file missing 'start'/'end' snapshots")
    conditions["RUNTIME_IDENTITY_CONTINUITY"] = _cond(ri_state, "; ".join(ri_detail_bits) or "no evidence")

    # -- WTICO / SPX500 fail-loud -------------------------------------------
    def _fail_loud_cond(instr: str) -> Dict[str, Any]:
        row = parsed.fail_loud.get(instr)
        if row is None:
            return _cond(NOT_OBSERVED, f"no FAIL-LOUD evidence line for {instr}")
        schedule = row["schedule"]
        state = row["state"]
        reason = row["reason"]
        guessed_closed = (schedule not in ("None", "none", "null")
                          or state.upper().startswith("MARKET_CLOSED")
                          or reason.upper() in ("MARKET_CLOSED", "SCHEDULED_MAINTENANCE"))
        observed = {"schedule": schedule, "resolved_state": state, "reason": reason}
        if guessed_closed:
            return _cond(FAIL, f"{instr} GUESSED a closed/known schedule instead of failing loud "
                               f"(schedule={schedule} state={state} reason={reason})",
                         observed=observed)
        return _cond(PASS, f"{instr} correctly fails loud (schedule={schedule} state={state} reason={reason})",
                     observed=observed)

    conditions["WTICO_FAIL_LOUD"] = _fail_loud_cond("WTICO_USD")
    conditions["SPX500_FAIL_LOUD"] = _fail_loud_cond("SPX500_USD")

    # -- Overall verdict ----------------------------------------------------
    required = [
        "END_MARKER_PRESENT", "REQUIRED_WINDOW_COVERED", "SAMPLE_CONTINUITY",
        "CLOSURE_CLASSIFICATION", "NO_FABRICATED_FRESHNESS",
        "NO_INACTIVITY_INCIDENT_DURING_CLOSURE", "NO_CLOSURE_DRIVEN_RECONNECT",
        "CORRECT_GRACE_TIMING", "GENUINE_RESUMED_FLOW_OR_ESCALATION",
        "RUNTIME_IDENTITY_CONTINUITY",
    ]
    extra_blocking = [
        "REST_QUOTE_VS_STREAM_SEPARATION", "NO_FALSE_GREEN_DURING_GRACE",
        "WTICO_FAIL_LOUD", "SPX500_FAIL_LOUD",
    ]
    all_states = {k: conditions[k]["state"] for k in required + extra_blocking}
    if any(all_states[k] == FAIL for k in required + extra_blocking):
        overall = FAIL
    elif any(all_states[k] in (INDETERMINATE, NOT_OBSERVED) for k in required):
        overall = INDETERMINATE
    else:
        overall = PASS

    result = {
        "tool": TOOL_ID,
        "tool_version": TOOL_VERSION,
        "generated_utc": iso(datetime.now(timezone.utc)),
        "inputs": {
            "instrument": instrument,
            "date": date,
            "cadence_seconds": cadence_seconds,
            "grace_seconds": gw.grace_seconds,
            "log_path": log_path,
            "log_sha256": log_sha256,
            "runtime_identity_provided": runtime_identity is not None,
        },
        "governed_window": {
            "market_timezone": gw.market_timezone,
            "named_schedule": gw.named_schedule,
            "schedule_resolution": gw.schedule_resolution,
            "break_label": gw.break_label,
            "detail": gw.detail,
        },
        "observer_start": iso(parsed.observer_start),
        "observer_start_pid": parsed.observer_start_pid,
        "observer_end": iso(parsed.observer_end),
        "observer_end_pid": parsed.observer_end_pid,
        "end_marker_present": end_present,
        "entering_window_ts": iso(parsed.entering_window_ts),
        "window_complete_ts": iso(parsed.window_complete_ts),
        "d1_after_seal": parsed.d1_after_seal,
        "sample_count": len(samples),
        "malformed_line_count": len(parsed.malformed_lines),
        "malformed_lines": parsed.malformed_lines,
        "monotonic": monotonic,
        "max_sample_gap_s": max_gap,
        "sample_gap_threshold_s": gap_threshold,
        "pre_close_window": {
            "start": iso(pre[0].outer_ts) if pre else None,
            "end": iso(gw.closure_start),
            "sample_count": len(pre),
        },
        "closure_window": {
            "start": iso(gw.closure_start),
            "end": iso(gw.closure_end),
            "sample_count": len(clo),
        },
        "reopening": iso(gw.reopening),
        "grace_end": iso(gw.grace_end),
        "post_grace_window": {
            "start": iso(gw.grace_end),
            "end": iso(post[-1].outer_ts) if post else None,
            "sample_count": len(post),
        },
        "last_pre_close_tick": iso(last_pre_close_tick),
        "last_pre_close_candle": iso(last_pre_close_candle),
        "samples_during_closure": len(clo),
        "first_resumed_tick": iso(first_resumed_tick),
        "first_resumed_candle": iso(first_resumed_candle),
        "health_restoration_ts": iso(health_restoration_ts),
        "incident_counts": incident_counts,
        "reconnect_counts": reconnect_counts,
        "rest_quote_vs_stream_separation": conditions["REST_QUOTE_VS_STREAM_SEPARATION"],
        "runtime_identity_continuity": conditions["RUNTIME_IDENTITY_CONTINUITY"],
        "wtico_fail_loud_evidence": conditions["WTICO_FAIL_LOUD"],
        "spx500_fail_loud_evidence": conditions["SPX500_FAIL_LOUD"],
        "conditions": conditions,
        "overall": overall,
    }
    return result


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #

def run(log_path: str, schedule_config: str, instrument: str, date: str,
        cadence_seconds: int, grace_seconds: Optional[int],
        runtime_identity_path: Optional[str]) -> Dict[str, Any]:
    log_bytes = Path(log_path).read_bytes()
    log_sha256 = hashlib.sha256(log_bytes).hexdigest()
    text = log_bytes.decode("utf-8", errors="replace")
    config = json.loads(Path(schedule_config).read_text(encoding="utf-8"))
    runtime_identity = None
    if runtime_identity_path:
        runtime_identity = json.loads(Path(runtime_identity_path).read_text(encoding="utf-8"))
    parsed = parse_log(text, date)
    gw = compute_governed_window(config, instrument, date, grace_seconds)
    return validate(parsed, gw, cadence_seconds, instrument, date, runtime_identity,
                    log_sha256, log_path)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog=TOOL_ID,
        description="Read-only, deterministic validator for XAU market-transition observer logs. "
                    "INERT: never writes to the observer, runtime, or network.",
    )
    p.add_argument("--log", required=True, help="Path to the observer log file (read-only)")
    p.add_argument("--schedule-config", required=True,
                   help="Path to market_hours_schedule.v1.json (read-only)")
    p.add_argument("--instrument", default="XAU_USD", help="Instrument symbol (default XAU_USD)")
    p.add_argument("--date", required=True, help="UTC date of the transition, YYYY-MM-DD")
    p.add_argument("--cadence-seconds", type=int, default=60,
                   help="Expected observer sampling cadence in seconds (default 60)")
    p.add_argument("--grace-seconds", type=int, default=None,
                   help="Reopening grace seconds; defaults to schedule reopening_grace_seconds")
    p.add_argument("--runtime-identity", default=None,
                   help="Optional JSON file with {'start':{...},'end':{...}} runtime identity snapshots")
    p.add_argument("--output", default=None, help="Optional path to write the JSON verdict")
    p.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    result = run(args.log, args.schedule_config, args.instrument, args.date,
                 args.cadence_seconds, args.grace_seconds, args.runtime_identity)
    payload = json.dumps(result, indent=2 if args.pretty else None, sort_keys=False)
    if args.output:
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return {PASS: 0, INDETERMINATE: 1, FAIL: 2}.get(result["overall"], 2)


if __name__ == "__main__":
    sys.exit(main())
