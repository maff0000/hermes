#!/usr/bin/env python3
"""
Deterministic generator for XAU market-transition observer-log fixtures.
WO-HELM-HERMES-MARKET-TRANSITION-EVIDENCE-VALIDATOR-0001

Emits 16 small synthetic observer logs (one per acceptance scenario) plus a
runtime-identity JSON, into this directory. The committed .log files are the
canonical test inputs; this generator exists only to (re)produce them and to
document exactly how each scenario differs from the healthy baseline.

Timeline (UTC, date 2026-07-16, XAU_USD metals, EDT):
  closure 21:00:00..22:00:00, reopening 22:00:00, grace_end 22:05:00
  cadence 60s. Samples every minute 20:55:00 .. 22:10:00.

Run:  python3 tests/fixtures/transition_evidence_v1/_generate.py
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATE = "2026-07-16"
D = datetime(2026, 7, 16, tzinfo=timezone.utc)

CLOSURE_START = D.replace(hour=21, minute=0)
CLOSURE_END = D.replace(hour=22, minute=0)   # reopening
GRACE_END = D.replace(hour=22, minute=5)
START_SAMPLE = D.replace(hour=20, minute=55)
END_SAMPLE = D.replace(hour=22, minute=10)
PID = 886481

# The instant of the last genuine tick/candle before close. Frozen through
# closure. last pre-close sample is 20:59:00 with M1 stamped 20:59:00.
FROZEN_CANDLE = D.replace(hour=20, minute=59)


def isoz(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def m1_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:00.000Z")


def clock(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%H:%M:%SZ")


def phase_for(ts: datetime):
    """Return the healthy/normal (phase, truth_expected, reason) for an instant."""
    if ts < CLOSURE_START:
        return "MARKET_OPEN", "false", "MARKET_OPEN"       # truth_expected simplified below
    if ts < CLOSURE_END:
        return "MARKET_CLOSED_EXPECTED", "false", "MARKET_CLOSED"
    return "MARKET_OPEN", "true", "MARKET_OPEN"


def sample_line(ts: datetime, *, phase, truth, reason, m1_ts, m1_fresh,
                q_status, q_fresh, q_age, f_status, f_fresh, conn, fault, m1age,
                incidents, reconnect, recovery=0, false_close=0) -> str:
    return (
        f"[{isoz(ts)}] {clock(ts)} | "
        f"phase={phase} truth_expected={truth} reason={reason} | "
        f"M1={m1_ts} {m1_fresh} | "
        f"quote={q_status}/{q_fresh} age={q_age:.1f} | "
        f"feed={f_status}/{f_fresh} conn={conn} fault={fault} M1age={m1age} | "
        f"incidents={incidents} reconnect={reconnect} recovery={recovery} false_close_log={false_close}"
    )


def baseline_sample(ts: datetime, *, override=None):
    """Healthy baseline sample record dict; override merges tweaks."""
    if ts < CLOSURE_START:
        # Pre-close: open, flowing, candle == current minute, quote fresh.
        rec = dict(phase="MARKET_OPEN", truth="true", reason="MARKET_OPEN",
                   m1_ts=m1_iso(ts), m1_fresh="FRESH",
                   q_status="OK", q_fresh="FRESH", q_age=1.0,
                   f_status="OK", f_fresh="FRESH", conn="UP", fault="NONE", m1age=1,
                   incidents=0, reconnect=0)
    elif ts < CLOSURE_END:
        # Closure: expected-closed, frozen candle, quote/feed stale, age grows.
        age = (ts - (FROZEN_CANDLE - timedelta(seconds=1))).total_seconds()
        m1age = int((ts - FROZEN_CANDLE).total_seconds())
        rec = dict(phase="MARKET_CLOSED_EXPECTED", truth="false", reason="MARKET_CLOSED",
                   m1_ts=m1_iso(FROZEN_CANDLE), m1_fresh="STALE",
                   q_status="OK", q_fresh="STALE", q_age=age,
                   f_status="OK", f_fresh="STALE", conn="UP", fault="NONE", m1age=m1age,
                   incidents=0, reconnect=0)
    else:
        # Reopened & flowing: candle == current minute, quote fresh again.
        rec = dict(phase="MARKET_OPEN", truth="true", reason="MARKET_OPEN",
                   m1_ts=m1_iso(ts), m1_fresh="FRESH",
                   q_status="OK", q_fresh="FRESH", q_age=1.0,
                   f_status="OK", f_fresh="FRESH", conn="UP", fault="NONE", m1age=1,
                   incidents=0, reconnect=0)
    if override:
        rec.update(override)
    return rec


def minutes(a: datetime, b: datetime):
    t = a
    while t <= b:
        yield t
        t += timedelta(minutes=1)


def build(*, per_sample=None, include_end=True, skip=None, end_pid=PID,
          extra_markers=None, failloud=None):
    """Assemble a full observer log.

    per_sample(ts, rec) -> optionally mutate the baseline record dict in place.
    skip: set of datetimes to omit (to create sampling gaps).
    failloud: dict instr -> (schedule, state, reason); default healthy fail-loud.
    """
    skip = skip or set()
    if failloud is None:
        failloud = {
            "WTICO_USD": ("None", "MARKET_OPEN_STALE", "NO_POLICY"),
            "SPX500_USD": ("None", "MARKET_OPEN_STALE", "NO_POLICY"),
        }
    lines = []
    lines.append(f"[{isoz(D.replace(hour=20, minute=50))}] OBSERVER START (pid {PID}). "
                 f"Window: pre-close ~20:50, closure 21:00-22:00, reopen 22:00, grace->22:05, post 22:20 UTC.")
    for instr, (sch, st, rn) in failloud.items():
        lines.append(f"[{isoz(D.replace(hour=20, minute=51))}] FAIL-LOUD {instr}: "
                     f"schedule={sch} state={st} reason={rn}")
    lines.append(f"[{isoz(CLOSURE_START)}] === ENTERING OBSERVATION WINDOW ===")

    for ts in minutes(START_SAMPLE, END_SAMPLE):
        if ts in skip:
            continue
        rec = baseline_sample(ts)
        if per_sample:
            per_sample(ts, rec)
        lines.append(sample_line(ts, **rec))

    for mk in (extra_markers or []):
        lines.append(mk)

    lines.append(f"[{isoz(GRACE_END)}] D1 latest after seal: {m1_iso(D.replace(hour=0, minute=0))} FRESH")
    lines.append(f"[{isoz(END_SAMPLE)}] === WINDOW COMPLETE ===")
    if include_end:
        lines.append(f"[{isoz(END_SAMPLE + timedelta(seconds=5))}] OBSERVER END (pid {end_pid}).")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# Scenario builders                                                           #
# --------------------------------------------------------------------------- #

def scenario_01_pass():
    return build()


def scenario_02_missing_end():
    return build(include_end=False)


def scenario_03_sampling_gap():
    # Drop a contiguous run inside closure (21:20..21:26) -> 7-min gap > 180s.
    skip = {D.replace(hour=21, minute=m) for m in range(20, 27)}
    return build(skip=skip)


def scenario_04_false_open_during_closure():
    def mut(ts, rec):
        if ts == D.replace(hour=21, minute=30):
            rec.update(phase="MARKET_OPEN", truth="true", reason="MARKET_OPEN")
    return build(per_sample=mut)


def scenario_05_tick_refreshed_during_closure():
    def mut(ts, rec):
        if ts == D.replace(hour=21, minute=30):
            rec.update(q_age=0.5)  # effective tick jumps to ~21:30
    return build(per_sample=mut)


def scenario_06_candle_fabricated_during_closure():
    def mut(ts, rec):
        if ts == D.replace(hour=21, minute=30):
            rec.update(m1_ts=m1_iso(D.replace(hour=21, minute=29)), m1_fresh="FRESH")
    return build(per_sample=mut)


def scenario_07_incident_during_closure():
    def mut(ts, rec):
        if CLOSURE_START <= ts < CLOSURE_END and ts >= D.replace(hour=21, minute=30):
            rec.update(incidents=1)
    return build(per_sample=mut)


def scenario_08_reconnect_during_closure():
    def mut(ts, rec):
        if CLOSURE_START <= ts < CLOSURE_END and ts >= D.replace(hour=21, minute=30):
            rec.update(reconnect=1)
    return build(per_sample=mut)


def scenario_09_rest_masks_stream():
    # Pre-close open period: stream feed STALE but REST quote FRESH, no fault.
    def mut(ts, rec):
        if ts == D.replace(hour=20, minute=57):
            rec.update(q_status="OK", q_fresh="FRESH", q_age=0.5,
                       f_status="STALE", f_fresh="STALE", fault="NONE")
    return build(per_sample=mut)


def scenario_10_false_green_during_grace():
    # No genuine resume; grace sample declares MARKET_OPEN (healthy) with frozen M1.
    def mut(ts, rec):
        if ts >= CLOSURE_END:
            # keep flow ABSENT after reopening (frozen candle, stale)
            age = (ts - (FROZEN_CANDLE - timedelta(seconds=1))).total_seconds()
            rec.update(m1_ts=m1_iso(FROZEN_CANDLE), m1_fresh="STALE",
                       q_status="OK", q_fresh="STALE", q_age=age,
                       f_status="OK", f_fresh="STALE", conn="UP", fault="NONE",
                       m1age=int((ts - FROZEN_CANDLE).total_seconds()))
        if CLOSURE_END <= ts < GRACE_END:
            # premature GREEN inside grace: phase healthy while M1 frozen
            rec.update(phase="MARKET_OPEN", truth="true", reason="MARKET_OPEN")
        elif ts >= GRACE_END:
            rec.update(phase="REOPENING_GRACE", truth="true", reason="AWAITING_FLOW")
    return build(per_sample=mut)


def scenario_11_resume_within_grace():
    # Flow resumes at 22:03 (inside grace [22:00,22:05)); before that, grace-hold.
    resume = D.replace(hour=22, minute=3)
    def mut(ts, rec):
        if CLOSURE_END <= ts < resume:
            age = (ts - (FROZEN_CANDLE - timedelta(seconds=1))).total_seconds()
            rec.update(phase="REOPENING_GRACE", truth="true", reason="AWAITING_FLOW",
                       m1_ts=m1_iso(FROZEN_CANDLE), m1_fresh="STALE",
                       q_status="OK", q_fresh="STALE", q_age=age,
                       f_status="OK", f_fresh="STALE", conn="UP", fault="NONE",
                       m1age=int((ts - FROZEN_CANDLE).total_seconds()))
        # ts >= resume keeps healthy baseline (flowing)
    return build(per_sample=mut)


def scenario_12_pathb_escalation():
    # Flow never resumes; correct escalation AFTER grace_end.
    def mut(ts, rec):
        if ts >= CLOSURE_END:
            age = (ts - (FROZEN_CANDLE - timedelta(seconds=1))).total_seconds()
            rec.update(m1_ts=m1_iso(FROZEN_CANDLE), m1_fresh="STALE",
                       q_status="OK", q_fresh="STALE", q_age=age,
                       f_status="STALE", f_fresh="STALE", conn="UP",
                       m1age=int((ts - FROZEN_CANDLE).total_seconds()))
        if CLOSURE_END <= ts < GRACE_END:
            rec.update(phase="REOPENING_GRACE", truth="true", reason="AWAITING_FLOW", fault="NONE")
        elif ts >= GRACE_END:
            rec.update(phase="MARKET_OPEN_STALE", truth="true", reason="STREAM_STALL",
                       fault="STALE", incidents=1)
    return build(per_sample=mut)


def scenario_13_no_escalation_silent():
    # Flow never resumes AND no escalation -> silent stall.
    def mut(ts, rec):
        if ts >= CLOSURE_END:
            age = (ts - (FROZEN_CANDLE - timedelta(seconds=1))).total_seconds()
            rec.update(phase="REOPENING_GRACE", truth="true", reason="AWAITING_FLOW",
                       m1_ts=m1_iso(FROZEN_CANDLE), m1_fresh="STALE",
                       q_status="OK", q_fresh="STALE", q_age=age,
                       f_status="OK", f_fresh="STALE", conn="UP", fault="NONE",
                       m1age=int((ts - FROZEN_CANDLE).total_seconds()))
    return build(per_sample=mut)


def scenario_14_runtime_identity_change():
    # OBSERVER END pid differs from START pid -> process restarted mid-window.
    return build(end_pid=990111)


def scenario_15_wtico_guessed_closed():
    fl = {
        "WTICO_USD": ("metals", "MARKET_CLOSED_EXPECTED", "MARKET_CLOSED"),
        "SPX500_USD": ("None", "MARKET_OPEN_STALE", "NO_POLICY"),
    }
    return build(failloud=fl)


def scenario_16_spx500_guessed_closed():
    fl = {
        "WTICO_USD": ("None", "MARKET_OPEN_STALE", "NO_POLICY"),
        "SPX500_USD": ("None", "MARKET_CLOSED_EXPECTED", "MARKET_CLOSED"),
    }
    return build(failloud=fl)


SCENARIOS = {
    "01_pass_correct_closure_reopening.log": scenario_01_pass,
    "02_missing_end_marker.log": scenario_02_missing_end,
    "03_sampling_gap.log": scenario_03_sampling_gap,
    "04_false_open_during_closure.log": scenario_04_false_open_during_closure,
    "05_tick_refreshed_during_closure.log": scenario_05_tick_refreshed_during_closure,
    "06_candle_fabricated_during_closure.log": scenario_06_candle_fabricated_during_closure,
    "07_incident_during_closure.log": scenario_07_incident_during_closure,
    "08_reconnect_during_closure.log": scenario_08_reconnect_during_closure,
    "09_rest_masks_stream.log": scenario_09_rest_masks_stream,
    "10_false_green_during_grace.log": scenario_10_false_green_during_grace,
    "11_resume_within_grace.log": scenario_11_resume_within_grace,
    "12_pathb_escalation_after_grace.log": scenario_12_pathb_escalation,
    "13_no_escalation_silent_stall.log": scenario_13_no_escalation_silent,
    "14_runtime_identity_change.log": scenario_14_runtime_identity_change,
    "15_wtico_guessed_closed.log": scenario_15_wtico_guessed_closed,
    "16_spx500_guessed_closed.log": scenario_16_spx500_guessed_closed,
}


def main():
    for name, fn in SCENARIOS.items():
        (HERE / name).write_text(fn(), encoding="utf-8")
    # runtime-identity fixtures for the file-based continuity path
    (HERE / "runtime_identity_stable.json").write_text(json.dumps({
        "start": {"container_id": "sha256:aaaa", "image_sha": "img:1"},
        "end": {"container_id": "sha256:aaaa", "image_sha": "img:1"},
    }, indent=2) + "\n", encoding="utf-8")
    (HERE / "runtime_identity_changed.json").write_text(json.dumps({
        "start": {"container_id": "sha256:aaaa", "image_sha": "img:1"},
        "end": {"container_id": "sha256:bbbb", "image_sha": "img:2"},
    }, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(SCENARIOS)} fixtures + 2 runtime-identity files to {HERE}")


if __name__ == "__main__":
    main()
