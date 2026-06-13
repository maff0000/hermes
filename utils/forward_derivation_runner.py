"""HERMES M30/H4 forward derivation runner.
WO-HELM-HERMES-CANDLE-H4-M30-FORWARD-DERIVATION-AND-FEATURES-0001.

Derives durable M30/H4 candles from canonical COMPLETE M1 truth, continuously / at the
forward edge. HERMES-only market truth. No Redis, no publisher, no cron/systemd here.

Doctrine enforced:
  - Source = canonical_m1 rows with complete=1 ONLY. Incomplete M1 are never counted as
    complete; a derived M30/H4 is complete=1 ONLY when all expected complete-M1 are present
    (M30=30, H4=240). (Stricter than the historical backfill, which counted all M1 rows.)
  - completeness != freshness — two independent axes. complete_state in
    {COMPLETE, INCOMPLETE, FORMING, UNAVAILABLE}; freshness in {FRESH, STALE, DEGRADED, UNAVAILABLE}.
  - H4 buckets are UTC-anchored 00/04/08/12/16/20 (H4_ANCHOR_UTC), epoch-truncated. M30 :00/:30.
    No session/trader interpretation.
  - Current forming bucket is never marked complete (complete_state=FORMING).
  - Idempotent upsert on UNIQUE(instrument,timestamp): a COMPLETE derivation refreshes the row;
    a partial/forming derivation NEVER downgrades or clobbers an existing complete candle.
  - Instruments from the governed `instruments` registry (enabled=1). Empty/missing => fail loud.
  - DB target via env_config (fail-loud); NO hardcoded host/port/db, no fallback. DB-only.

Modes: dry-run (default, no writes), single-cycle (last closed bucket), catch-up (bounded N).
Mutation requires execute=True AND confirm=True. ALL timestamps UTC. conn injected for tests.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import sys
from datetime import timedelta

sys.path.insert(0, __file__.rsplit("/utils/", 1)[0])
from utils.m1_deriver import TIMEFRAME_SECONDS, get_bucket_start  # noqa: E402

TIMEFRAMES = ["M30", "H4"]
TF_SECONDS = {"M30": TIMEFRAME_SECONDS["M30"], "H4": TIMEFRAME_SECONDS["H4"]}
EXPECTED_M1 = {"M30": 30, "H4": 240}
TARGET_TABLE = {"M30": "candles_M30", "H4": "candles_H4"}
H4_ANCHOR_UTC = "00,04,08,12,16,20"
SOURCE = "m1_forward_derive"
MAX_CATCHUP_BUCKETS = 96
# R2D2 B-DIV: forward semantics are STRICTER than the Phase-2 historical backfill
# (HISTORICAL_ALL_M1_COUNTED). Forward derivation counts ONLY complete=1 source M1.
DERIVATION_POLICY = "FORWARD_COMPLETE_M1_ONLY"
SOURCE_COMPLETE_POLICY = "COMPLETE_ONLY"

COMPLETE, INCOMPLETE, FORMING, UNAVAILABLE = "COMPLETE", "INCOMPLETE", "FORMING", "UNAVAILABLE"
FRESH, STALE, DEGRADED = "FRESH", "STALE", "DEGRADED"


def _check_tf(tf):
    if tf not in TF_SECONDS:
        raise ValueError(f"GOV-FWD-TF-001: unknown timeframe {tf} (fail-loud)")


# ---------- pure, unit-testable ----------
def expected_m1(tf):
    _check_tf(tf)
    return EXPECTED_M1[tf]


def forming_bucket_start(now_utc, tf):
    _check_tf(tf)
    return get_bucket_start(now_utc, TF_SECONDS[tf])


def last_closed_bucket_start(now_utc, tf):
    return forming_bucket_start(now_utc, tf) - timedelta(seconds=TF_SECONDS[tf])


def is_forming(bucket_start, now_utc, tf):
    return bucket_start >= forming_bucket_start(now_utc, tf)


def derive_complete_state(complete_m1_count, tf, forming):
    """(complete_state, complete_bool). Fail-closed: complete only when ALL expected complete-M1
    present AND the bucket is closed."""
    _check_tf(tf)
    if forming:
        return FORMING, False
    if complete_m1_count <= 0:
        return UNAVAILABLE, False
    if complete_m1_count == expected_m1(tf):
        return COMPLETE, True
    return INCOMPLETE, False


def aggregate_complete(rows):
    if not rows:
        raise ValueError("GOV-FWD-001: cannot aggregate empty complete-M1 set (fail-loud)")
    return (float(rows[0]["open"]),
            max(float(r["high"]) for r in rows),
            min(float(r["low"]) for r in rows),
            float(rows[-1]["close"]),
            sum(int(r["volume"]) for r in rows))


def classify_freshness(now_utc, latest_complete_m1_utc, tf):
    """Freshness from age of latest COMPLETE source M1 vs the timeframe period. Independent of
    completeness. Honest: weekend/market-closed staleness IS reported STALE (visible), never
    laundered to FRESH. HERMES does not interpret market hours (ARES' lane)."""
    _check_tf(tf)
    if latest_complete_m1_utc is None:
        return UNAVAILABLE, ["SOURCE_M1_UNAVAILABLE"]
    age = (now_utc - latest_complete_m1_utc).total_seconds()
    period = TF_SECONDS[tf]
    if age <= 1.5 * period:
        return FRESH, []
    if age <= 3.0 * period:
        return DEGRADED, ["SOURCE_M1_AGING"]
    return STALE, ["SOURCE_M1_STALE"]


def run_id_for(now_utc, tf, instruments):
    seed = f"{now_utc.isoformat()}|{tf}|{','.join(sorted(instruments))}"
    return f"fwd-{tf}-{hashlib.sha256(seed.encode()).hexdigest()[:12]}"


UPSERT = (
    "INSERT INTO {table} (instrument, timestamp, open, high, low, close, volume, complete, source) "
    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
    "ON DUPLICATE KEY UPDATE "
    "  open=IF(VALUES(complete)=1, VALUES(open), open), "
    "  high=IF(VALUES(complete)=1, VALUES(high), high), "
    "  low=IF(VALUES(complete)=1, VALUES(low), low), "
    "  close=IF(VALUES(complete)=1, VALUES(close), close), "
    "  volume=IF(VALUES(complete)=1, VALUES(volume), volume), "
    "  complete=GREATEST(complete, VALUES(complete))"
)


class ForwardDerivationRunner:
    def __init__(self, get_conn, logger):
        self._get_conn = get_conn
        self.log = logger

    def load_instruments(self):
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT symbol FROM instruments WHERE enabled=1 ORDER BY symbol")
                rows = [r[0] for r in cur.fetchall()]
        if not rows:
            raise RuntimeError("GOV-FWD-INSTR-001: instruments registry has no enabled rows "
                               "(no hidden default) (fail-loud)")
        return rows

    def derive_bucket(self, instrument, tf, bucket_start, now_utc):
        _check_tf(tf)
        start, end = bucket_start, bucket_start + timedelta(seconds=TF_SECONDS[tf])
        forming = is_forming(bucket_start, now_utc, tf)
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT open, high, low, close, volume FROM canonical_m1 "
                    "WHERE instrument=%s AND complete=1 AND minute_bucket_utc>=%s "
                    "AND minute_bucket_utc<%s ORDER BY minute_bucket_utc",
                    (instrument, start, end))
                rows = [{"open": r[0], "high": r[1], "low": r[2], "close": r[3], "volume": r[4]}
                        for r in cur.fetchall()]
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM canonical_m1 WHERE instrument=%s AND complete=0 "
                    "AND minute_bucket_utc>=%s AND minute_bucket_utc<%s", (instrument, start, end))
                incomplete_cnt = int(cur.fetchone()[0])
        cnt = len(rows)
        state, complete = derive_complete_state(cnt, tf, forming)
        exp = expected_m1(tf)
        res = {"instrument": instrument, "timeframe": tf, "timestamp": bucket_start.isoformat(sep=" "),
               "complete": complete, "complete_state": state,
               "derivation_policy": DERIVATION_POLICY, "source_complete_policy": SOURCE_COMPLETE_POLICY,
               "source_complete_m1_count": cnt, "complete_source_candle_count": cnt,
               "incomplete_source_candle_count": incomplete_cnt,
               "actual_source_candle_count": cnt + incomplete_cnt,
               "expected_source_candle_count": exp, "expected_m1": exp,
               "missing_source_candle_count": max(0, exp - cnt), "missing_m1": max(0, exp - cnt),
               "h4_anchor_utc": H4_ANCHOR_UTC if tf == "H4" else None, "reason_codes": []}
        if incomplete_cnt > 0 and state != FORMING:
            res["reason_codes"].append("SOURCE_M1_INCOMPLETE_PRESENT")
        if state in (UNAVAILABLE, FORMING):
            res["reason_codes"].append("SOURCE_CANDLE_" + ("UNAVAILABLE" if state == UNAVAILABLE else "FORMING"))
            res["ohlcv"] = None
        else:
            o, h, l, c, v = aggregate_complete(rows)
            res["ohlcv"] = {"open": o, "high": h, "low": l, "close": c, "volume": v}
            if state == INCOMPLETE:
                res["reason_codes"].append("SOURCE_CANDLE_INCOMPLETE")
        return res

    def _latest_complete_m1(self, instrument):
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT MAX(minute_bucket_utc) FROM canonical_m1 "
                            "WHERE instrument=%s AND complete=1", (instrument,))
                row = cur.fetchone()
        return row[0] if row else None

    def _target_buckets(self, tf, now_utc, mode, lookback_buckets):
        last = last_closed_bucket_start(now_utc, tf)
        if mode == "single-cycle":
            return [last]
        n = max(1, min(int(lookback_buckets), MAX_CATCHUP_BUCKETS))
        return [last - timedelta(seconds=TF_SECONDS[tf] * i) for i in range(n)][::-1]

    def run_cycle(self, now_utc, mode="dry-run", timeframes=None, lookback_buckets=1,
                  execute=False, confirm=False, reconciliation_ack=False):
        timeframes = timeframes or TIMEFRAMES
        for tf in timeframes:
            _check_tf(tf)
        # R2D2 B-DIV live-execution gate: forward COMPLETE_ONLY semantics diverge from the
        # historical backfill (ALL_M1_COUNTED). Live writes are blocked until the divergence is
        # reconciled and explicitly acknowledged (architect-approved epoch cutover / re-derive /
        # annotate). Dry-run is always allowed.
        if execute and confirm and not reconciliation_ack:
            raise RuntimeError("GOV-FWD-BDIV-001: live --execute blocked — forward COMPLETE_ONLY "
                               "policy diverges from historical ALL_M1_COUNTED rows; reconciliation "
                               "ack required (no silent live write) (fail-loud)")
        instruments = self.load_instruments()
        ev = {"run_id": run_id_for(now_utc, "+".join(timeframes), instruments),
              "generated_at_utc": now_utc.isoformat(), "mode": "execute" if (execute and confirm) else "dry-run",
              "derivation_policy": DERIVATION_POLICY, "source_complete_policy": SOURCE_COMPLETE_POLICY,
              "historical_policy_note": "HISTORICAL_ALL_M1_COUNTED (Phase-2 backfill) vs FORWARD_COMPLETE_M1_ONLY",
              "h4_anchor_utc": H4_ANCHOR_UTC, "instruments": instruments, "timeframes": timeframes,
              "results": [], "totals": {"derived": 0, "complete": 0, "incomplete": 0, "forming": 0,
                                        "unavailable": 0, "written": 0, "skipped": 0},
              "freshness": {}, "failure_codes": []}
        if mode == "catch-up" and int(lookback_buckets) > MAX_CATCHUP_BUCKETS:
            ev["failure_codes"].append(f"CATCHUP_BOUNDED_TO_{MAX_CATCHUP_BUCKETS}")
        for tf in timeframes:
            buckets = self._target_buckets(tf, now_utc, mode, lookback_buckets)
            for inst in instruments:
                latest = self._latest_complete_m1(inst)
                fstate, fcodes = classify_freshness(now_utc, latest, tf)
                ev["freshness"][f"{inst}:{tf}"] = {"state": fstate, "reason_codes": fcodes,
                    "latest_complete_m1_utc": latest.isoformat(sep=" ") if latest else None}
                for b in buckets:
                    r = self.derive_bucket(inst, tf, b, now_utc)
                    r["freshness_state"] = fstate
                    ev["totals"]["derived"] += 1
                    ev["totals"][{COMPLETE: "complete", INCOMPLETE: "incomplete",
                                  FORMING: "forming", UNAVAILABLE: "unavailable"}[r["complete_state"]]] += 1
                    if execute and confirm and r["complete_state"] in (COMPLETE, INCOMPLETE) and r["ohlcv"]:
                        self._upsert(tf, r)
                        ev["totals"]["written"] += 1
                    else:
                        ev["totals"]["skipped"] += 1
                    ev["results"].append(r)
        self.log.info("[FWD_DERIVE] %s", json.dumps(ev["totals"]))
        return ev

    def _upsert(self, tf, r):
        o = r["ohlcv"]
        with self._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(UPSERT.format(table=TARGET_TABLE[tf]),
                            (r["instrument"], r["timestamp"], o["open"], o["high"], o["low"],
                             o["close"], o["volume"], 1 if r["complete"] else 0, SOURCE))
            conn.commit()


def main(argv=None):
    ap = argparse.ArgumentParser(description="HERMES M30/H4 forward derivation runner (DB-only).")
    ap.add_argument("--mode", choices=["dry-run", "single-cycle", "catch-up"], default="dry-run")
    ap.add_argument("--timeframe", action="append", choices=["M30", "H4"], default=None)
    ap.add_argument("--lookback-buckets", type=int, default=1)
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--confirm", action="store_true")
    args = ap.parse_args(argv)
    if args.execute and not args.confirm:
        print("REFUSING: --execute requires --confirm (governed, not auto-run)", file=sys.stderr)
        return 2
    print("HERMES M30/H4 forward derivation runner. Container entry point. "
          "Modes: dry-run (default) / single-cycle / catch-up. Mutation needs --execute --confirm. "
          f"H4 UTC anchor = {H4_ANCHOR_UTC}. DB target via env_config (fail-loud, no hardcode). "
          "No Redis, no publisher, no cron/systemd. Invoke run_cycle() from an authorised runner/container.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
