"""Bounded MariaDB->Redis candle-history warm-start (base timeframes M1/M5/M15/H1).
WO-HELM-HERMES-DEV-MULTI-INSTRUMENT-CANDLE-HISTORY-WARMSTART-0001.

On startup, seeds a RECENT, BOUNDED window of candle history from the durable MariaDB candle tables into the
EXISTING governed Redis candle-history contract (candle_history_v1), so a consumer gets an immediate recent
lookback (e.g. XAG_USD 12h) without waiting for forward accumulation. This is NOT a full-history backfill and
NOT a second Redis schema: it reuses the canonical history envelope/keys/TTL and every governed guard. It runs
ONCE per process start, establishes the bounded window, then hands over to the normal forward publisher.

Governance:
- Config-gated + authorised (fail-loud): HERMES_CANDLE_HISTORY_WARMSTART_ENABLED/_AUTHORISED.
- Lookback is EXTERNAL config (HERMES_CANDLE_HISTORY_WARMSTART_HOURS), hard-bounded by MAX_HOURS (never a full backfill).
- Instruments reuse the governed candle-history config allowlist (HERMES_CANDLE_HISTORY_FORWARD_INSTRUMENTS).
- Base grid only (M1/M5/M15/H1); H4/D1 are NEVER seeded here (governed, unchanged).
- Idempotent: deterministic key + ZSET member/score(open_epoch) -> re-run refreshes TTL only.
- Per-instrument/timeframe failure isolation: one bad instrument never blocks the others or the boot.
- No new OANDA calls; no candle recomputation; wick/body geometry is carried by the canonical envelope.
Same code runs DEV and PROD; only external config VALUES differ.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from utils import candle_history_v1 as chv
from utils import candle_contract_v1 as cc
from utils import candle_runtime_seam_v1 as seam
from utils import candle_history_forward_writer_v1 as fwd   # reuse the governed instrument-allowlist parser

ENABLED_ENV = "HERMES_CANDLE_HISTORY_WARMSTART_ENABLED"
AUTHORISED_ENV = "HERMES_CANDLE_HISTORY_WARMSTART_AUTHORISED"
HOURS_ENV = "HERMES_CANDLE_HISTORY_WARMSTART_HOURS"

WARMSTART_TIMEFRAMES = ("M1", "M5", "M15", "H1")     # generic clock grid; H4/D1 governed-excluded
MAX_HOURS = 168                                       # hard safety bound (7d) — this is a warm-start, not a backfill
RUN_MARKER = "warmstart-v1"
_SOURCE_TABLE = {tf: f"candles_{tf}" for tf in WARMSTART_TIMEFRAMES}


class DisabledWarmstart:
    enabled = False

    def run(self, *, redis_client=None, db_conn=None, now=None):
        return {"enabled": False, "seeded": {}, "errors": []}

    def status(self):
        return {"enabled": False}


class CandleHistoryWarmstart:
    """Bounded, idempotent warm-start over the canonical history contract."""

    def __init__(self, *, instruments, hours, timeframes=WARMSTART_TIMEFRAMES, run_id=RUN_MARKER):
        allowed = frozenset(instruments)
        if not allowed:
            raise ValueError("GOV-CANDLE-HIST-WS-001: warm-start instrument allowlist must be non-empty (fail-closed)")
        h = int(hours)
        if not (1 <= h <= MAX_HOURS):
            raise ValueError(f"GOV-CANDLE-HIST-WS-002: hours {hours!r} out of bounds [1,{MAX_HOURS}] "
                             "(warm-start is a bounded recent window, never a full backfill)")
        self.instruments = allowed
        self.hours = h
        self.timeframes = tuple(tf for tf in timeframes if tf in WARMSTART_TIMEFRAMES)
        self.run_id = run_id
        self.enabled = True

    def run(self, *, redis_client, db_conn, now=None):
        now = now or datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=self.hours)
        summary = {"enabled": True, "hours": self.hours, "timeframes": list(self.timeframes),
                   "seeded": {}, "errors": []}
        for inst in sorted(self.instruments):
            summary["seeded"][inst] = {}
            for tf in self.timeframes:
                try:
                    summary["seeded"][inst][tf] = self._seed_one(redis_client, db_conn, inst, tf, cutoff, now)
                except Exception as exc:  # noqa: BLE001 - per-instrument/tf isolation; never block boot or the others
                    summary["errors"].append({"instrument": inst, "timeframe": tf, "error": repr(exc)[:200]})
        return summary

    def _seed_one(self, redis_client, db_conn, inst, tf, cutoff, now):
        canon = seam.canonical_instrument(inst)
        cur = db_conn.cursor()
        try:
            cur.execute(
                f"SELECT timestamp,open,high,low,close,volume FROM {_SOURCE_TABLE[tf]} "
                "WHERE instrument=%s AND complete=1 AND timestamp>=%s ORDER BY timestamp ASC",
                (canon, cutoff.replace(tzinfo=None)))
            rows = cur.fetchall()
        finally:
            cur.close()
        pipe = redis_client.pipeline(transaction=False)
        written = 0
        for ts, o, h, lo, c, v in rows:
            open_dt = ts.replace(tzinfo=timezone.utc) if getattr(ts, "tzinfo", None) is None else ts
            env = chv.build_history_envelope(
                instrument=canon, timeframe=tf, timestamp_utc=open_dt,
                ohlc={"open": float(o), "high": float(h), "low": float(lo), "close": float(c),
                      "volume": int(v or 0)},
                backfill_run_id=self.run_id, backfill_inserted_at_utc=now,
                source_table=_SOURCE_TABLE[tf], source_timestamp_utc=open_dt)
            plan = chv.build_history_write_plan(env)
            chv.assert_history_target(plan["key"])          # belt-and-braces structural guard before any write
            chv.assert_history_target(plan["index_key"])
            pipe.set(plan["key"], json.dumps(plan["value"]), ex=plan["ttl_seconds"])
            pipe.zadd(plan["index_key"], {plan["index_member"]: plan["index_score"]})
            written += 1
        if written:
            pipe.execute()
        return written

    def status(self):
        return {"enabled": True, "hours": self.hours, "timeframes": list(self.timeframes),
                "instruments": sorted(self.instruments)}


def build_warmstart_from_env():
    """Factory: DISABLED unless HERMES_CANDLE_HISTORY_WARMSTART_ENABLED=true (then AUTHORISED=true required, else
    fail-loud). Lookback hours is REQUIRED external config when enabled. Instruments reuse the governed candle-
    history allowlist (HERMES_CANDLE_HISTORY_FORWARD_INSTRUMENTS)."""
    from env_config import get_env, get_env_bool, get_env_int   # lazy; HERMES-owned config only
    if not get_env_bool(ENABLED_ENV, False):
        return DisabledWarmstart()
    if not get_env_bool(AUTHORISED_ENV, False):
        raise ValueError(f"GOV-CANDLE-HIST-WS-003: {ENABLED_ENV}=true requires {AUTHORISED_ENV}=true (fail-closed)")
    hours = get_env_int(HOURS_ENV, required=True)
    instruments = fwd.parse_forward_instruments(get_env(fwd.INSTRUMENTS_ENV, required=True))
    return CandleHistoryWarmstart(instruments=instruments, hours=hours)
