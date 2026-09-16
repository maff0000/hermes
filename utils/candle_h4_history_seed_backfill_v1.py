"""HERMES governed H4 history SEED/BACKFILL engine v1 — dark/dry-run by default, NO live write here.
WO-HELM-HERMES-H4-CANONICAL-HISTORICAL-BOOTSTRAP-0001.

Seeds hermes:candles:XAU_USD:H4:history:v1:index (+ member keys ...:{open_epoch}) to depth sufficient for
indicator EMA-200 warm-up (EMA_TREND_WINDOW=210 in hermes_runtime_publisher_steps_v1), using ONLY the
governed H4 derivation already used by the live producer (candle_h4_derivation_v1.derive_h4) — there is
exactly ONE H4 candle-construction implementation; this module never reimplements OHLC/grid/completeness
logic, it only supplies derive_h4 with a different (durable, historical) source of H1 children.

APPROVED SOURCE: the durable MariaDB `candles_H1` table (XAU_USD, complete=1) — the trustworthy table fed
continuously by the legacy oanda_stream_task path, which was NEVER affected by the 2026-09 canonical-Redis
outage (confirmed: 10,867 rows, 2024-12-24 -> present). NEVER `candles_H4` (dead since 2026-06-15, already
distrusted elsewhere in this codebase) or `candles_D1` (119 gaps >1 day, max gap 5 days). NEVER fabricates
a missing H1 child: a bucket is admitted ONLY when exactly 4 genuine, on-the-hour-grid H1 children exist
for it; anything short is rejected, not synthesised, exactly mirroring derive_h4's own honest-incompleteness
contract (an under-4 bucket can never produce status=OK).

Guarantees: DISABLED + DRY-RUN by default. NO Redis writes/deletes in dry-run (no_write_proof=True by
construction). Live write requires ALL THREE of enabled+authorised+dry_run=False AND is NOT executed by
this WO. Idempotent by open_epoch (skip match / fail-loud on conflict / never overwrite / never delete).
Bounded (explicit max candidates, bounded SQL read window). UTC only. No regime/risk/strategy/signal/trade
semantics. Writes reuse the EXACT existing M1-H4 governed history contract (candle_history_v1) — same
key/schema/index/TTL conventions as the live forward writer; H4's own count-based retention
(candle_history_v1.H4_HISTORY_RETAIN_COUNT) protects whatever this seeds from the generic time-based prune.
"""
from __future__ import annotations
import hashlib
import json
from datetime import datetime, timedelta, timezone

from utils import candle_contract_v1 as cc
from utils import candle_h4_derivation_v1 as h4d
from utils import candle_history_v1 as chv

CANONICAL_INSTRUMENT = "XAU_USD"
_ALIAS_DENY = ("XAUUSD",)
H1_SOURCE_TABLE = "candles_H1"          # the ONLY approved source table — never candles_H4, never candles_D1
HALT_CODE = 101

# Bounded defaults (explicit — never unbounded). Target enough for EMA-200 (210) plus warm-up/restart
# margin; H4_HISTORY_RETAIN_COUNT (candle_history_v1) is the same 250 this backfill aims to reach.
DEFAULT_MAX_CANDLES = 260
DEFAULT_MIN_DEPTH = 26                              # same floor D1 uses (ema_26) — a sane non-trivial minimum
PREFERRED_TARGET_DEPTH = chv.H4_HISTORY_RETAIN_COUNT  # 250
_MAX_CANDLES_HARD_CAP = 500
# Bounded SQL lookback: empirically ~5.5 genuine complete H4 buckets/day (60d measured -> 332), so 90 days
# comfortably covers DEFAULT_MAX_CANDLES with margin without an unbounded scan.
DEFAULT_LOOKBACK_DAYS = 90
_LOOKBACK_DAYS_HARD_CAP = 400

# Gates (all DARK by default). Live write is impossible unless BOTH are set AND dry_run is explicitly false.
BACKFILL_ENABLED_ENV = "HERMES_H4_HISTORY_BACKFILL_ENABLED"
BACKFILL_AUTHORISED_ENV = "HERMES_H4_HISTORY_BACKFILL_AUTHORISED"
BACKFILL_DRY_RUN_ENV = "HERMES_H4_HISTORY_BACKFILL_DRY_RUN"       # default true (safe)
BACKFILL_MAX_CANDLES_ENV = "HERMES_H4_HISTORY_BACKFILL_MAX_CANDLES"
BACKFILL_MIN_DEPTH_ENV = "HERMES_H4_HISTORY_BACKFILL_MIN_DEPTH"
BACKFILL_LOOKBACK_DAYS_ENV = "HERMES_H4_HISTORY_BACKFILL_LOOKBACK_DAYS"

# Provenance for seeded (backfilled) records — distinct from the live forward writer's own run marker so
# the origin is always honest and traceable in the history envelope's `history.backfill_run_id` field.
BACKFILL_RUN_MARKER = "H4_SEED_BACKFILL_V1"
BACKFILL_SOURCE_TABLE = H1_SOURCE_TABLE


# --------------------------------------------------------------------------- config (dark by default)
class BackfillConfig:
    def __init__(self, *, enabled, authorised, dry_run, max_candidates, min_depth, lookback_days):
        self.enabled = bool(enabled)
        self.authorised = bool(authorised)
        self.dry_run = bool(dry_run)
        self.max_candidates = int(max_candidates)
        self.min_depth = int(min_depth)
        self.lookback_days = int(lookback_days)

    @property
    def live_write_allowed(self):
        """Live write is allowed ONLY with enabled AND authorised AND dry_run explicitly false."""
        return self.enabled and self.authorised and not self.dry_run

    def as_dict(self):
        return {"enabled": self.enabled, "authorised": self.authorised, "dry_run": self.dry_run,
                "max_candidates": self.max_candidates, "min_depth": self.min_depth,
                "lookback_days": self.lookback_days, "live_write_allowed": self.live_write_allowed}


def parse_backfill_config_from_env():
    """DISABLED + DRY-RUN by default. Enabled-without-authorised -> SystemExit(101). Bounded max/min/
    lookback from env."""
    from env_config import get_env_bool, get_env_int
    enabled = get_env_bool(BACKFILL_ENABLED_ENV, False)
    if not enabled:
        return BackfillConfig(enabled=False, authorised=False, dry_run=True,
                              max_candidates=DEFAULT_MAX_CANDLES, min_depth=DEFAULT_MIN_DEPTH,
                              lookback_days=DEFAULT_LOOKBACK_DAYS)
    if not get_env_bool(BACKFILL_AUTHORISED_ENV, False):
        raise SystemExit(HALT_CODE)
    dry_run = get_env_bool(BACKFILL_DRY_RUN_ENV, True)          # default SAFE (dry-run) even when enabled+authorised
    max_candidates = get_env_int(BACKFILL_MAX_CANDLES_ENV, DEFAULT_MAX_CANDLES) or DEFAULT_MAX_CANDLES
    min_depth = get_env_int(BACKFILL_MIN_DEPTH_ENV, DEFAULT_MIN_DEPTH) or DEFAULT_MIN_DEPTH
    lookback_days = get_env_int(BACKFILL_LOOKBACK_DAYS_ENV, DEFAULT_LOOKBACK_DAYS) or DEFAULT_LOOKBACK_DAYS
    if max_candidates <= 0 or max_candidates > _MAX_CANDLES_HARD_CAP:
        raise ValueError(f"GOV-CANDLE-H4-BF-001: {BACKFILL_MAX_CANDLES_ENV}={max_candidates} out of bounds "
                         f"(1..{_MAX_CANDLES_HARD_CAP})")
    if min_depth < h4d.H4_EXPECTED_CHILDREN:
        raise ValueError(f"GOV-CANDLE-H4-BF-002: {BACKFILL_MIN_DEPTH_ENV}={min_depth} below minimum viable depth")
    if lookback_days <= 0 or lookback_days > _LOOKBACK_DAYS_HARD_CAP:
        raise ValueError(f"GOV-CANDLE-H4-BF-003: {BACKFILL_LOOKBACK_DAYS_ENV}={lookback_days} out of bounds "
                         f"(1..{_LOOKBACK_DAYS_HARD_CAP})")
    return BackfillConfig(enabled=True, authorised=True, dry_run=dry_run, max_candidates=max_candidates,
                          min_depth=min_depth, lookback_days=lookback_days)


# --------------------------------------------------------------------------- H1 source (governed MariaDB only)
def _parse_utc(ts):
    return datetime.strptime(ts[:-1], cc._UTC_MS).replace(tzinfo=timezone.utc)


def _read_h1_source_rows(db_conn, *, lookback_days, now=None):
    """Bounded read of genuine, complete H1 candles from the durable MariaDB source. Read-only SELECT;
    never candles_H4, never candles_D1 (those identifiers never appear anywhere in this module)."""
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=lookback_days)).replace(tzinfo=None)
    cur = db_conn.cursor()
    try:
        cur.execute(
            f"SELECT timestamp, open, high, low, close, volume FROM {H1_SOURCE_TABLE} "
            "WHERE instrument=%s AND complete=1 AND timestamp>=%s ORDER BY timestamp ASC",
            (CANONICAL_INSTRUMENT, cutoff))
        rows = cur.fetchall()
    finally:
        cur.close()
    out = []
    for ts, o, h, lo, c, v in rows:
        open_dt = ts if getattr(ts, "tzinfo", None) is not None else ts.replace(tzinfo=timezone.utc)
        out.append({"timestamp": open_dt, "open": float(o), "high": float(h), "low": float(lo),
                    "close": float(c), "volume": int(v or 0)})
    return out


# --------------------------------------------------------------------------- candidate reconstruction
def build_h4_candidates(h1_rows, *, max_candidates=DEFAULT_MAX_CANDLES, now=None):
    """Reconstruct candidate H4 candles from genuine durable H1 rows (newest-first, BOUNDED to
    max_candidates buckets), using the EXACT governed selector (h4d.h1_children_in_bucket) and derivation
    (h4d.derive_h4) the live producer uses. Only a bucket with EXACTLY 4 genuine on-the-hour-grid H1
    children is derived + admitted (status OK, source_count==4, gap_state NONE); anything short/gapped is
    REJECTED (never synthesised). Pure/read-only; no I/O of its own."""
    now = now or datetime.now(timezone.utc)
    if not h1_rows:
        return []
    start_bucket = h4d.h4_bucket_open(h1_rows[0]["timestamp"])
    end_bucket = h4d.h4_bucket_open(h1_rows[-1]["timestamp"])
    results = []
    b = end_bucket        # newest-first so a bounded max_candidates keeps the MOST RECENT candidates
    while b >= start_bucket and len(results) < max_candidates:
        kids = h4d.h1_children_in_bucket(b, h1_rows)
        bucket_epoch = int(b.timestamp())
        if len(kids) != h4d.H4_EXPECTED_CHILDREN:
            results.append({"bucket_open_epoch": bucket_epoch, "accepted": False,
                            "reason": "H4_INCOMPLETE_CHILDREN", "child_count": len(kids)})
            b = b - timedelta(seconds=h4d.H4_SECONDS)
            continue
        try:
            env, _meta = h4d.derive_h4(instrument=CANONICAL_INSTRUMENT, h4_open=b, h1_children=kids,
                                       generated_at_utc=b + timedelta(seconds=h4d.H4_SECONDS), is_closed=True)
        except Exception as exc:  # noqa: BLE001 - a non-derivable bucket is a REJECT, never a fabricated candle
            results.append({"bucket_open_epoch": bucket_epoch, "accepted": False,
                            "reason": "H4_DERIVE_FAILED", "error": repr(exc)[:160]})
            b = b - timedelta(seconds=h4d.H4_SECONDS)
            continue
        d = env["data"]
        if env["status"] != "OK" or d["source_count"] != h4d.H4_EXPECTED_CHILDREN \
                or d["source_coverage"] != 1.0 or d["gap_state"] not in (None, "NONE"):
            results.append({"bucket_open_epoch": bucket_epoch, "accepted": False,
                            "reason": "H4_DERIVED_NOT_OK", "derived_status": env["status"]})
            b = b - timedelta(seconds=h4d.H4_SECONDS)
            continue
        results.append({"bucket_open_epoch": bucket_epoch, "accepted": True, "reason": None, "env": env})
        b = b - timedelta(seconds=h4d.H4_SECONDS)
    return results


# --------------------------------------------------------------------------- idempotency
def _fingerprint(env):
    """Stable fingerprint over the CRITICAL H4 fields (excludes volatile provenance/history block). Used
    to detect a genuine duplicate (match -> skip) vs a conflicting existing member (fail-loud, never
    overwrite)."""
    d = env["data"]
    key = json.dumps({"instrument": d["instrument"], "timeframe": d["timeframe"],
                      "timestamp_utc": d["timestamp_utc"], "open": d["open"], "high": d["high"],
                      "low": d["low"], "close": d["close"], "source_count": d["source_count"]},
                     sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(key.encode()).hexdigest()


def _idempotency_status(redis_client, env):
    """new | already_present_match | conflict — by open_epoch. Never writes, never deletes."""
    d = env["data"]
    open_epoch = int(_parse_utc(d["timestamp_utc"]).timestamp())
    existing = redis_client.get(chv.history_key(CANONICAL_INSTRUMENT, "H4", open_epoch))
    if not existing:
        return "new"
    try:
        cur = json.loads(existing)
    except Exception:  # noqa: BLE001
        return "conflict"
    return "already_present_match" if _fingerprint(cur) == _fingerprint(env) else "conflict"


def _history_envelope_for(env, *, now):
    """Wrap an already-derived, already-validated H4 envelope with the governed `history` provenance
    block — the exact same 5-field schema every other history record (M1-H4 forward, D1 backfill) uses.
    Snapshots the derived envelope verbatim; never rebuilds it."""
    import copy
    he = copy.deepcopy(env)
    d = he["data"]
    open_dt = _parse_utc(d["timestamp_utc"])
    he["history"] = {
        "history_contract_version": chv.HISTORY_CONTRACT_VERSION,
        "backfill_run_id": BACKFILL_RUN_MARKER,
        "backfill_inserted_at_utc": cc._fmt(cc.normalise_utc(now)),
        "source_table": BACKFILL_SOURCE_TABLE,
        "source_timestamp_utc": cc._fmt(open_dt),
    }
    cc.validate_candle_contract(he)
    return he


# --------------------------------------------------------------------------- dry-run planner (NO writes)
def dry_run_plan(redis_client, db_conn, *, config=None, now=None):
    """Bounded, read-only dry-run. Reads genuine H1 rows from durable MariaDB, builds candidates via the
    governed derivation, classifies idempotency against the live Redis index, projects resulting depth,
    and returns INERT write-plans. Performs NO Redis writes/deletes (no_write_proof=True by construction).
    Reports scanned/accepted/rejected/new/already-present/conflict counts as required."""
    now = now or datetime.now(timezone.utc)
    config = config or parse_backfill_config_from_env()
    idx = chv.history_index_key(CANONICAL_INSTRUMENT, "H4")
    current_depth = redis_client.zcard(idx) if redis_client.exists(idx) else 0
    h1_rows = _read_h1_source_rows(db_conn, lookback_days=config.lookback_days, now=now)
    cands = build_h4_candidates(h1_rows, max_candidates=config.max_candidates, now=now)
    accepted = [c for c in cands if c["accepted"]]
    rejected = [c for c in cands if not c["accepted"]]
    reason_counts = {}
    for c in rejected:
        reason_counts[c["reason"]] = reason_counts.get(c["reason"], 0) + 1
    idem = {"new": 0, "already_present_match": 0, "conflict": 0, "failed": 0}
    write_plans, conflicts, failed, ts_all = [], [], [], []
    for c in accepted:
        env = c["env"]
        ts_all.append(env["data"]["timestamp_utc"])
        try:
            st = _idempotency_status(redis_client, env)
        except Exception as exc:  # noqa: BLE001 - a read fault on one candidate must not abort the plan
            idem["failed"] += 1
            failed.append({"timestamp_utc": env["data"]["timestamp_utc"], "error": repr(exc)[:160]})
            continue
        idem[st] += 1
        if st == "conflict":
            conflicts.append(env["data"]["timestamp_utc"])
        elif st == "new":
            he = _history_envelope_for(env, now=now)
            plan = chv.build_history_write_plan(he)          # INERT plan — no write performed
            chv.assert_history_target(plan["key"])
            chv.assert_history_target(plan["index_key"])
            write_plans.append(plan)
    projected_depth = current_depth + idem["new"]
    return {
        "mode": "DRY_RUN", "no_write_proof": True, "source_path": BACKFILL_SOURCE_TABLE,
        "source_forbidden_h4_table": True, "source_forbidden_d1_table": True,
        "anchor": "22:00Z NY-5PM (h4d.h4_bucket_open)",
        "config": config.as_dict(),
        "current_depth": current_depth, "candidate_count": len(cands),
        "scanned_h1_rows": len(h1_rows),
        "accepted_count": len(accepted), "rejected_count": len(rejected), "rejection_reasons": reason_counts,
        "earliest_candidate_utc": min(ts_all) if ts_all else None,
        "latest_candidate_utc": max(ts_all) if ts_all else None,
        "idempotency": idem, "conflicts": conflicts, "failed": failed,
        "projected_depth": projected_depth,
        "meets_min_depth": projected_depth >= config.min_depth,
        "meets_preferred_depth": projected_depth >= PREFERRED_TARGET_DEPTH,
        "inert_write_plan_count": len(write_plans),
        "write_mode": chv.WRITE_MODE_HISTORY_INERT,
    }


# --------------------------------------------------------------------------- live execute (GATED; NOT run in this WO)
def execute_backfill(redis_client, db_conn, *, config=None, now=None):
    """LIVE seed execution — REFUSES unless enabled+authorised+dry_run=false (fail-loud). Idempotent by
    open_epoch: skip already_present_match, FAIL-LOUD on conflict (never overwrite), write only `new` via
    the governed history write-plan (SET EX + ZADD), reusing the EXACT same key/schema/index conventions
    as the live forward writer. NEVER deletes. Bounded by max_candidates. Reports written/already-present/
    rejected/failed counts."""
    now = now or datetime.now(timezone.utc)
    config = config or parse_backfill_config_from_env()
    if not config.live_write_allowed:
        raise ValueError("GOV-CANDLE-H4-BF-010: live H4 history seed refused — requires "
                         f"{BACKFILL_ENABLED_ENV}=true AND {BACKFILL_AUTHORISED_ENV}=true AND "
                         f"{BACKFILL_DRY_RUN_ENV}=false (dark/dry-run by default)")
    h1_rows = _read_h1_source_rows(db_conn, lookback_days=config.lookback_days, now=now)
    cands = build_h4_candidates(h1_rows, max_candidates=config.max_candidates, now=now)
    written, skipped_match, failed_writes = 0, 0, []
    for c in (x for x in cands if x["accepted"]):
        env = c["env"]
        # Idempotency + conflict check is UNGUARDED — a conflict is a deliberate fail-loud refusal (never
        # overwrite genuine existing history), not an incidental fault, and must propagate out of this
        # function exactly like the D1 seed/backfill precedent does. Only the write mechanics below are
        # wrapped, so an incidental fault on ONE candidate (never a conflict) doesn't abort the whole batch.
        st = _idempotency_status(redis_client, env)
        if st == "conflict":
            raise ValueError("GOV-CANDLE-H4-BF-011: existing H4 history member conflicts with candidate "
                             f"at {env['data']['timestamp_utc']} — refusing to overwrite (needs a "
                             "separate repair WO)")
        if st == "already_present_match":
            skipped_match += 1
            continue
        try:
            he = _history_envelope_for(env, now=now)
            plan = chv.build_history_write_plan(he)
            chv.assert_history_target(plan["key"])
            chv.assert_history_target(plan["index_key"])
            redis_client.set(plan["key"], json.dumps(plan["value"]), ex=plan["ttl_seconds"])
            redis_client.zadd(plan["index_key"], {plan["index_member"]: plan["index_score"]})
            written += 1
        except Exception as exc:  # noqa: BLE001 - one candidate's write fault must not abort the bounded batch
            failed_writes.append({"timestamp_utc": env["data"]["timestamp_utc"], "error": repr(exc)[:200]})
    # Count-based retention applies AFTER the batch, exactly once, via the SAME governed trim plan the live
    # forward writer uses — never a second retention implementation.
    idx = chv.history_index_key(CANONICAL_INSTRUMENT, "H4")
    trim = chv.h4_history_retention_trim_plan(redis_client.zcard(idx))
    trimmed = redis_client.zremrangebyrank(idx, 0, trim["would_trim"] - 1) if trim["would_trim"] > 0 else 0
    return {"written": written, "skipped_match": skipped_match, "failed": len(failed_writes),
            "failed_detail": failed_writes, "retention_trimmed": trimmed, "mode": "LIVE_WRITE"}
