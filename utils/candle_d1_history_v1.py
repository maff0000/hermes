"""HERMES governed D1 candle HISTORY series v1 — dark/inert by default.
WO-HELM-HERMES-D1-CANDLE-HISTORY-SERIES-0001.

The M1/M5/M15/H1/H4 history grid (`candle_history_v1`) DELIBERATELY excludes D1 (GOV-CANDLE-HIST-003 /
GOV-CANDLE-HIST-FWD-005). This module adds the missing D1 history surface WITHOUT loosening those intentional
guards: it reuses the EXACT history key convention but with its own D1-only, sealed-6/6-only guards, so the
existing M1-H4 lane is untouched.

Key convention (identical to M1-H4 history):
  per-candle immutable key:  hermes:candles:XAU_USD:D1:history:v1:{open_epoch}
  ordered index (ZSET):      hermes:candles:XAU_USD:D1:history:v1:index   (score=member=open_epoch)

Source: the APPROVED D1 6/6 seal path only (`candle_d1_publish_wire_v1._seal_and_publish` -> the exact sealed
D1 latest envelope). This module SNAPSHOTS that already-valid derived envelope (source_count=6, NY-5PM grid) and
appends only a `history` provenance block (schema identical to `candle_history_v1`), then re-validates — it never
rebuilds/re-derives, never fabricates a D1 candle, and NEVER writes an unsealed/incomplete or <6 D1 candle.

Guarantees: DISABLED by default (no client, no I/O). Never touches `:latest:v1`. Never dual-publishes XAUUSD.
No SQL. No deletes. NO Redis I/O at import. UTC only. No regime/risk/strategy/signal/trade semantics.
"""
from __future__ import annotations
import copy
import json
from datetime import datetime, timezone

from utils import candle_contract_v1 as cc
from utils import candle_history_v1 as chv     # reuse contract version + provenance schema (uniform history surface)
from utils.hermes_redis_auth_v1 import redis_auth_kwargs

CANONICAL_INSTRUMENT = "XAU_USD"
_ALIAS_DENY = ("XAUUSD",)
D1_TIMEFRAME = "D1"
CANDLE_PREFIX = chv.CANDLE_PREFIX               # "hermes:candles:" — shared root with M1-H4 history/latest
HISTORY_MARKER = chv.HISTORY_MARKER            # "history"
HISTORY_CONTRACT_VERSION = chv.HISTORY_CONTRACT_VERSION   # "v1" — uniform across backfill/forward/D1
HALT_CODE = 101

# Retention: D1 candles are DAILY, so retention is BY COUNT (not the M1-H4 time-window). Keep the newest
# D1_HISTORY_RETAIN_COUNT sealed D1 candles; the per-candle TTL is a generous upper bound only (chosen well
# above the calendar span of the retained count so a retained candle can never expire before it is trimmed).
D1_HISTORY_RETAIN_COUNT = 35                    # preferred retained depth (project standard for indicator series)
D1_HISTORY_TTL_SECONDS = 120 * 86400           # 120d safety cap >> ~49 calendar days spanned by 35 trading days

# Minimum depth required before D1 indicators may read the series (ema_26 needs 26; rsi_14 15; atr_14 14;
# prev-day features 2). Downstream D1 indicator activation MUST fail-loud below this.
D1_MIN_DEPTH_FOR_INDICATORS = 26

WRITE_MODE_HISTORY_INERT = chv.WRITE_MODE_HISTORY_INERT   # "HISTORY_INERT_NO_WRITE"

# D1 seal-completeness contract (mirrors candle_d1_publish_wire_v1.h4_child_is_complete / _seal_and_publish):
# a D1 candle may enter history ONLY if it is a status-OK, closed, complete 6/6 NY-5PM daily bucket.
D1_EXPECTED_SOURCE_COUNT = 6


# --------------------------------------------------------------------------- guards / builders
def _assert_canonical_d1(instrument, timeframe):
    if instrument in _ALIAS_DENY:
        raise ValueError(f"GOV-CANDLE-D1-HIST-002: alias instrument {instrument!r} may not be a D1 history "
                         "output id — canonicalise to XAU_USD first (no alias keys)")
    if instrument != CANONICAL_INSTRUMENT:
        raise ValueError(f"GOV-CANDLE-D1-HIST-001: instrument {instrument!r} not allowed (canonical "
                         f"{CANONICAL_INSTRUMENT} only, fail-closed)")
    if timeframe != D1_TIMEFRAME:
        raise ValueError(f"GOV-CANDLE-D1-HIST-003: timeframe {timeframe!r} not allowed (this lane is D1 only)")


def _to_open_epoch(value):
    if isinstance(value, bool):
        raise ValueError("GOV-CANDLE-D1-HIST-004: open_epoch must be an int/datetime, not bool")
    if isinstance(value, datetime):
        return int(cc.normalise_utc(value).timestamp())
    if isinstance(value, (int, float)) and float(value).is_integer() and value >= 0:
        return int(value)
    raise ValueError(f"GOV-CANDLE-D1-HIST-004: open_epoch must be a non-negative int epoch or datetime (got {value!r})")


def d1_history_key(instrument, open_epoch):
    """Immutable per-candle D1 history key: hermes:candles:XAU_USD:D1:history:v1:{open_epoch}."""
    _assert_canonical_d1(instrument, D1_TIMEFRAME)
    return f"{CANDLE_PREFIX}{instrument}:{D1_TIMEFRAME}:{HISTORY_MARKER}:{HISTORY_CONTRACT_VERSION}:{_to_open_epoch(open_epoch)}"


def d1_history_index_key(instrument):
    """Ordered index ZSET: hermes:candles:XAU_USD:D1:history:v1:index."""
    _assert_canonical_d1(instrument, D1_TIMEFRAME)
    return f"{CANDLE_PREFIX}{instrument}:{D1_TIMEFRAME}:{HISTORY_MARKER}:{HISTORY_CONTRACT_VERSION}:index"


def assert_d1_history_target(key):
    """A D1 history write target may ONLY be a per-D1-candle history key or its index — NEVER a `:latest:v1`
    key, a non-history candle key, an alias/non-canonical instrument, or a non-D1 timeframe. Fail-loud."""
    if not key.startswith(CANDLE_PREFIX):
        raise ValueError(f"GOV-CANDLE-D1-HIST-TGT-001: {key!r} is not a hermes candle key")
    if "XAUUSD" in key:
        raise ValueError(f"GOV-CANDLE-D1-HIST-TGT-002: refusing alias XAUUSD key {key!r} (canonical XAU_USD only)")
    parts = key.split(":")
    # hermes:candles:{inst}:D1:history:v1:{open_epoch|index} -> exactly 7 colon-parts
    if len(parts) != 7 or parts[0] != "hermes" or parts[1] != "candles" or parts[4] != HISTORY_MARKER \
            or parts[5] != HISTORY_CONTRACT_VERSION:
        raise ValueError(f"GOV-CANDLE-D1-HIST-TGT-003: {key!r} not a D1 history target "
                         "(expected hermes:candles:{inst}:D1:history:v1:{open_epoch|index})")
    inst, tf, tail = parts[2], parts[3], parts[6]
    if inst != CANONICAL_INSTRUMENT:
        raise ValueError(f"GOV-CANDLE-D1-HIST-TGT-004: instrument {inst!r} not canonical XAU_USD")
    if tf != D1_TIMEFRAME:
        raise ValueError(f"GOV-CANDLE-D1-HIST-TGT-005: timeframe {tf!r} not D1 (this lane is D1 only)")
    if tail != "index" and not tail.isdigit():
        raise ValueError(f"GOV-CANDLE-D1-HIST-TGT-006: history key tail must be an open_epoch or 'index' (got {tail!r})")
    return True


def assert_sealed_complete_d1(envelope):
    """Fail-loud completeness gate: only a status-OK, closed, COMPLETE 6/6 D1 candle may enter history. Mirrors
    the D1 producer's publish rule (a sealed <6 or non-OK bucket is NEVER published, so it never enters history)."""
    if not isinstance(envelope, dict) or "data" not in envelope:
        raise ValueError("GOV-CANDLE-D1-HIST-010: D1 history envelope must be a candle contract dict")
    d = envelope["data"]
    if d.get("instrument") != CANONICAL_INSTRUMENT or d.get("timeframe") != D1_TIMEFRAME:
        raise ValueError("GOV-CANDLE-D1-HIST-011: D1 history envelope must be canonical XAU_USD / D1")
    if envelope.get("status") != "OK":
        raise ValueError(f"GOV-CANDLE-D1-HIST-012: only status-OK D1 may enter history (got {envelope.get('status')!r})")
    if d.get("is_closed") is not True:
        raise ValueError("GOV-CANDLE-D1-HIST-013: only a CLOSED D1 candle may enter history (no live-forming candle)")
    sc, esc = d.get("source_count"), d.get("expected_source_count")
    if sc != D1_EXPECTED_SOURCE_COUNT or esc != D1_EXPECTED_SOURCE_COUNT:
        raise ValueError(f"GOV-CANDLE-D1-HIST-014: only a COMPLETE 6/6 D1 may enter history "
                         f"(source_count={sc}, expected={esc}, required={D1_EXPECTED_SOURCE_COUNT})")
    if d.get("source_coverage") not in (1.0, 1):
        raise ValueError(f"GOV-CANDLE-D1-HIST-015: D1 source_coverage must be 1.0 (got {d.get('source_coverage')!r})")
    if d.get("gap_state", "NONE") not in (None, "NONE"):
        raise ValueError(f"GOV-CANDLE-D1-HIST-016: D1 with a gap_state may not enter history (got {d.get('gap_state')!r})")
    return True


# --------------------------------------------------------------------------- payload (snapshot, never rebuild)
def build_d1_history_envelope(sealed_d1_env, *, backfill_run_id, backfill_inserted_at_utc,
                              source_table, source_timestamp_utc):
    """SNAPSHOT an already-valid, sealed 6/6 D1 latest envelope into an immutable D1 history record — append
    ONLY a `history` provenance block (identical 5-field schema to candle_history_v1) so the D1 history surface
    is uniform with M1-H4. The D1 envelope is DERIVED (source_count=6), so it is snapshotted verbatim, NOT
    rebuilt as a DIRECT/source_count=1 candle (that would falsify D1 provenance). Re-validates the full payload."""
    assert_sealed_complete_d1(sealed_d1_env)
    env = copy.deepcopy(sealed_d1_env)
    env[HISTORY_MARKER] = {
        "history_contract_version": HISTORY_CONTRACT_VERSION,
        "backfill_run_id": str(backfill_run_id),
        "backfill_inserted_at_utc": cc._fmt(cc.normalise_utc(backfill_inserted_at_utc)),
        "source_table": str(source_table),
        "source_timestamp_utc": cc._fmt(cc.normalise_utc(source_timestamp_utc)),
    }
    cc.validate_candle_contract(env)           # validator STILL required (also scans the history block)
    return env


def build_d1_history_write_plan(envelope):
    """INERT D1 history write PLAN — NEVER performs a write. Validates the governed contract + D1 completeness,
    derives the immutable key + index (score/member=open_epoch), applies the retention TTL, asserts the target
    guards. Idempotent by construction: same D1 candle -> same key + same ZSET member/score (re-run = no-op)."""
    cc.validate_candle_contract(envelope)
    assert_sealed_complete_d1(envelope)
    d = envelope["data"]
    open_dt = datetime.strptime(d["timestamp_utc"][:-1], cc._UTC_MS).replace(tzinfo=timezone.utc)
    open_epoch = int(open_dt.timestamp())
    key = d1_history_key(d["instrument"], open_epoch)
    idx = d1_history_index_key(d["instrument"])
    assert_d1_history_target(key)
    assert_d1_history_target(idx)
    return {
        "operation": "SET", "key": key, "value": envelope, "ttl_seconds": D1_HISTORY_TTL_SECONDS,
        "index_key": idx, "index_score": open_epoch, "index_member": str(open_epoch),
        "idempotent": True, "write_mode": WRITE_MODE_HISTORY_INERT,
    }


def d1_history_retention_trim_plan(index_member_count, keep=D1_HISTORY_RETAIN_COUNT):
    """INERT count-based retention PLAN for the D1 index (D1 is daily -> trim by COUNT, keep newest `keep`).
    Returns the number of oldest members that WOULD be trimmed (ZREMRANGEBYRANK 0, n-1). No delete happens here
    and none happens in this WO; a future governed writer/backfill applies it."""
    n = max(0, int(index_member_count) - int(keep))
    return {"operation": "ZREMRANGEBYRANK", "index_stub": "0..%d" % (n - 1) if n else "none",
            "would_trim": n, "keep_newest": int(keep), "write_mode": WRITE_MODE_HISTORY_INERT}


# --------------------------------------------------------------------------- depth validator (downstream gate)
def d1_history_depth_sufficient(depth):
    """True iff the D1 history series is deep enough for D1 indicators (>= D1_MIN_DEPTH_FOR_INDICATORS)."""
    return int(depth) >= D1_MIN_DEPTH_FOR_INDICATORS


def assert_sufficient_d1_history_depth(depth):
    """Fail-loud gate for DOWNSTREAM D1 indicator activation: refuse when the D1 history series is too shallow to
    compute the deepest deterministic indicator (ema_26). D1 indicators must call this before reading the series."""
    if not d1_history_depth_sufficient(depth):
        raise ValueError(f"GOV-CANDLE-D1-HIST-DEPTH-001: D1 history depth {int(depth)} < required "
                         f"{D1_MIN_DEPTH_FOR_INDICATORS} — D1 indicators are BLOCKED until the series is seeded "
                         "(forward accumulation and/or a governed D1 backfill)")
    return True


# --------------------------------------------------------------------------- gated writer (dark by default)
D1_HISTORY_ENABLED_ENV = "HERMES_CANDLE_D1_HISTORY_ENABLED"
D1_HISTORY_AUTHORISED_ENV = "HERMES_CANDLE_D1_HISTORY_AUTHORISED"
FORWARD_RUN_MARKER = "D1_FORWARD_SEAL_V1"       # provenance run-id for forward (runtime-sealed) D1 history records
FORWARD_SOURCE_TABLE = "hermes:candles:XAU_USD:D1:latest:v1"   # the approved runtime D1 seal surface (not SQL)


class DisabledD1HistoryWriter:
    """Safe no-op D1 history writer (DEFAULT). Holds NO redis client, performs NO I/O. Never writes. Deploying
    this code with the D1-history gate unset keeps the D1 history surface dark (no auto-activation)."""
    enabled = False

    def on_d1_sealed(self, sealed_d1_env, *, now=None, logger=None):
        return {"written": False, "reason": "D1_HISTORY_DISABLED"}

    def status(self):
        return {"enabled": False}


class D1HistoryWriter:
    """Enabled forward D1 history writer — snapshots a SEALED 6/6 D1 latest envelope into its immutable history
    key + index. Additive to the D1 latest publisher: never touches `:latest:v1`, and on_d1_sealed is fault-
    isolated so a history-write fault can NEVER disrupt the D1 latest seal/publish path."""
    enabled = True

    def __init__(self, *, redis_client):
        if redis_client is None:
            raise ValueError("GOV-CANDLE-D1-HIST-020: enabled D1 history writer requires an explicit redis client")
        self.redis_client = redis_client
        self.written = 0
        self.faults = 0
        self.last_fault = None

    def _write(self, sealed_d1_env, *, now):
        # the D1 open (candle timestamp) is a governed "...Z" UTC string -> parse to a datetime for provenance
        src_ts = datetime.strptime(sealed_d1_env["data"]["timestamp_utc"][:-1], cc._UTC_MS).replace(tzinfo=timezone.utc)
        env = build_d1_history_envelope(
            sealed_d1_env, backfill_run_id=FORWARD_RUN_MARKER, backfill_inserted_at_utc=now,
            source_table=FORWARD_SOURCE_TABLE, source_timestamp_utc=src_ts)
        plan = build_d1_history_write_plan(env)
        assert_d1_history_target(plan["key"])
        assert_d1_history_target(plan["index_key"])
        self.redis_client.set(plan["key"], json.dumps(plan["value"]), ex=plan["ttl_seconds"])
        self.redis_client.zadd(plan["index_key"], {plan["index_member"]: plan["index_score"]})
        self.written += 1
        return {"written": True, "key": plan["key"], "index_member": plan["index_member"]}

    def on_d1_sealed(self, sealed_d1_env, *, now=None, logger=None):
        """Forward hook — call AFTER a successful D1 6/6 latest publish. Never raises (fault-isolated): a D1
        history fault is counted + logged, never propagated to the D1 latest path."""
        now = now or datetime.now(timezone.utc)
        try:
            return self._write(sealed_d1_env, now=now)
        except Exception as exc:  # noqa: BLE001 - bounded: D1 history must never break the D1 latest seal path
            self.faults += 1
            self.last_fault = repr(exc)[:200]
            if logger is not None:
                logger.warning("[D1_HISTORY_WRITE_FAIL] faults=%d error=%r", self.faults, exc)
            return {"written": False, "reason": "D1_HISTORY_WRITE_FAIL", "error": repr(exc)}

    def status(self):
        return {"enabled": True, "written": self.written, "faults": self.faults}


def _default_canonical_redis_client():
    """Lazy canonical HERMES redis client — only ever built when the writer is enabled+authorised."""
    import redis
    from env_config import get_env, get_env_int
    return redis.Redis(host=get_env("HERMES_CANDLE_CANONICAL_REDIS_HOST", required=True),
                       port=get_env_int("HERMES_CANDLE_CANONICAL_REDIS_PORT", required=True),
                       db=get_env_int("HERMES_CANDLE_CANONICAL_REDIS_DB", required=True), socket_timeout=5, **redis_auth_kwargs())


def build_d1_history_writer_from_env(*, redis_client=None, redis_client_factory=None):
    """Boot entrypoint. DISABLED by default -> DisabledD1HistoryWriter (no client, no I/O). Enabled-without-
    authorised -> SystemExit(101). Enabled+authorised -> D1HistoryWriter writing ONLY the D1 history keyspace.
    NO Redis client is constructed when disabled (deploy with the gate unset stays dark -> no auto-activation)."""
    from env_config import get_env_bool
    if not get_env_bool(D1_HISTORY_ENABLED_ENV, False):
        return DisabledD1HistoryWriter()
    if not get_env_bool(D1_HISTORY_AUTHORISED_ENV, False):
        raise SystemExit(HALT_CODE)
    client = redis_client
    if client is None:
        client = (redis_client_factory or _default_canonical_redis_client)()
    return D1HistoryWriter(redis_client=client)
