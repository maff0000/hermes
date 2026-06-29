"""HERMES governed Redis CANDLE HISTORY/WINDOW contract v1.
WO-HELM-HERMES-GOLD-MTF-CANDLE-WINDOW-CONTRACT-0001.

DESIGN / CODE ONLY — there is NO Redis I/O in this module and NOTHING is written. It defines the governed
history keyspace, payload, target guards, retention policy, idempotent index semantics, and the dry-run
gap-profile format that a LATER, separately-authorised backfill WO must use.

History is HARD-SEPARATED from live truth `hermes:candles:{instr}:{tf}:latest:v1`:
  per-candle immutable key:  hermes:candles:XAU_USD:{TF}:history:v1:{open_epoch}
  ordered index (ZSET):      hermes:candles:XAU_USD:{TF}:history:v1:index   (score=open_epoch, member=open_epoch)

`assert_history_target` is the single guard a backfill writer MUST call before any write; it makes it
impossible to target a live `:latest:v1` key, a non-history key, an alias/non-allowlisted instrument,
H4/D1, or an unversioned key. The governed v1 contract validator is STILL required before any write.

Scope (this WO): instrument XAU_USD only; timeframes M1/M5/M15/H1 only. No H4/D1, no regime, no shadow.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone

from utils import candle_contract_v1 as cc

HISTORY_CONTRACT_VERSION = "v1"
CANDLE_PREFIX = "hermes:candles:"               # shared root with canonical/latest
HISTORY_MARKER = "history"

# Fail-closed allowlists. Instruments are CANONICAL ids only — the alias XAUUSD is denied as OUTPUT
# (callers must canonicalise first). H4 and D1 are governed (derived, NY-5PM aligned) history timeframes;
# the legacy "D" token remains excluded. A D1 history record is additionally gated by assert_d1_history_payload
# (status OK, 22:00 open, source H4, six complete children) — D1 history is NEVER a raw/direct daily candle.
HISTORY_INSTRUMENTS = ("XAU_USD",)
HISTORY_TIMEFRAMES = ("M1", "M5", "M15", "H1", "H4", "D1")
_ALIAS_DENY = ("XAUUSD",)

# D1 history is derived-only: six complete OK H4 children on the fixed 22:00 UTC NY-5PM anchor.
D1_HISTORY_SOURCE_TIMEFRAME = "H4"
D1_HISTORY_EXPECTED_CHILDREN = 6
D1_HISTORY_ANCHOR_HOUR_UTC = 22
# provenance strings a D1 history payload must NEVER carry (no midnight direct table, no 24xH1 shortcut)
_D1_FORBIDDEN_SOURCE_TOKENS = ("candles_d1", "24xh1", "24 x h1", "24×h1", "direct_d1")

# Retention: EXPLICIT bounded policy for dev canonical history — 35 days (4 weeks + operational buffer).
# Never implicit. Per-candle keys carry this TTL; the index is trimmed by score to the same cutoff by the
# (separate) backfill writer. NO deletes happen in this module.
HISTORY_RETENTION_DAYS = 35
HISTORY_TTL_SECONDS = HISTORY_RETENTION_DAYS * 86400      # 3_024_000

WRITE_MODE_HISTORY_INERT = "HISTORY_INERT_NO_WRITE"
# History provenance field names — deliberately free of any forbidden interpretive token.
_HISTORY_FIELDS = ("history_contract_version", "backfill_run_id", "backfill_inserted_at_utc",
                   "source_table", "source_timestamp_utc")


# --------------------------------------------------------------------------- guards / builders
def _assert_inst_tf(instrument, timeframe):
    if instrument in _ALIAS_DENY:
        raise ValueError(f"GOV-CANDLE-HIST-002: alias instrument {instrument!r} may not be a history "
                         "output id — canonicalise to XAU_USD first (no alias keys)")
    if instrument not in HISTORY_INSTRUMENTS:
        raise ValueError(f"GOV-CANDLE-HIST-001: instrument {instrument!r} not in history allowlist "
                         f"{HISTORY_INSTRUMENTS} (fail-closed)")
    if timeframe not in HISTORY_TIMEFRAMES:
        raise ValueError(f"GOV-CANDLE-HIST-003: timeframe {timeframe!r} not in history grid "
                         f"{HISTORY_TIMEFRAMES} (D1/D excluded)")


def _to_open_epoch(value):
    """Accept an int/float epoch or a datetime -> int UTC epoch seconds (fail-loud on anything else)."""
    if isinstance(value, bool):
        raise ValueError("GOV-CANDLE-HIST-004: open_epoch must be an int/datetime, not bool")
    if isinstance(value, datetime):
        return int(cc.normalise_utc(value).timestamp())
    if isinstance(value, (int, float)) and float(value).is_integer() and value >= 0:
        return int(value)
    raise ValueError(f"GOV-CANDLE-HIST-004: open_epoch must be a non-negative int epoch or datetime (got {value!r})")


def history_key(instrument, timeframe, open_epoch):
    """Immutable per-candle history key: hermes:candles:{inst}:{tf}:history:v1:{open_epoch}."""
    _assert_inst_tf(instrument, timeframe)
    oe = _to_open_epoch(open_epoch)
    return f"{CANDLE_PREFIX}{instrument}:{timeframe}:{HISTORY_MARKER}:{HISTORY_CONTRACT_VERSION}:{oe}"


def history_index_key(instrument, timeframe):
    """Ordered index key (ZSET): hermes:candles:{inst}:{tf}:history:v1:index."""
    _assert_inst_tf(instrument, timeframe)
    return f"{CANDLE_PREFIX}{instrument}:{timeframe}:{HISTORY_MARKER}:{HISTORY_CONTRACT_VERSION}:index"


def assert_history_target(key):
    """THE guard a backfill writer MUST call before any write. Proves `key` is a governed history key or
    index and can NEVER be: a live `:latest:v1` key, a non-history candle key, an alias/non-allowlisted
    instrument, an H4/D1 key, or an unversioned key. Fail-loud."""
    if not isinstance(key, str) or not key.startswith(CANDLE_PREFIX):
        raise ValueError(f"GOV-CANDLE-HIST-TGT-001: not a hermes:candles key {key!r}")
    if ":latest:" in key or key.endswith(":latest"):
        raise ValueError(f"GOV-CANDLE-HIST-TGT-002: refusing to target a LIVE latest key {key!r}")
    parts = key.split(":")
    # hermes:candles:{inst}:{tf}:history:v1:{open_epoch|index}  -> exactly 7 colon-parts
    if len(parts) != 7 or parts[4] != HISTORY_MARKER or parts[5] != HISTORY_CONTRACT_VERSION:
        raise ValueError(f"GOV-CANDLE-HIST-TGT-003: not a versioned history key/index {key!r} "
                         "(expected hermes:candles:{inst}:{tf}:history:v1:{open_epoch|index})")
    inst, tf, tail = parts[2], parts[3], parts[6]
    if inst in _ALIAS_DENY:
        raise ValueError(f"GOV-CANDLE-HIST-TGT-004: alias instrument {inst!r} key forbidden")
    if inst not in HISTORY_INSTRUMENTS:
        raise ValueError(f"GOV-CANDLE-HIST-TGT-005: instrument {inst!r} not in history allowlist")
    if tf not in HISTORY_TIMEFRAMES:
        raise ValueError(f"GOV-CANDLE-HIST-TGT-006: timeframe {tf!r} not in history grid "
                         f"{HISTORY_TIMEFRAMES} (legacy 'D' excluded; D1 only via the governed D1-history path)")
    if tail != "index" and not tail.isdigit():
        raise ValueError(f"GOV-CANDLE-HIST-TGT-007: history key tail must be an open_epoch or 'index' (got {tail!r})")
    return True


# --------------------------------------------------------------------------- payload
def build_history_envelope(*, instrument, timeframe, timestamp_utc, ohlc, backfill_run_id,
                           backfill_inserted_at_utc, source_table, source_timestamp_utc):
    """Build a governed history envelope = the latest v1 candle payload PLUS a `history` provenance block.
    Each historical candle is generated_at = its own close time, so it is a self-consistent point-in-time
    record (status OK at its own close), not a stale-vs-now artefact. The v1 contract validator is run
    over the full payload (incl. the history block) so no regime/interpretive field can leak."""
    _assert_inst_tf(instrument, timeframe)
    close_time = cc.normalise_utc(timestamp_utc) + timedelta(seconds=cc.TF_SECONDS[timeframe])
    env = cc.build_candle_contract(
        instrument=instrument, timeframe=timeframe, timestamp_utc=timestamp_utc, ohlc=ohlc,
        is_closed=True, generated_at_utc=close_time, source_timeframe=timeframe,
        source_count=1, expected_source_count=1, derivation=cc.DERIVATION_DIRECT,
        derivation_policy=cc.DERIVATION_POLICY_DIRECT, source_policy_epoch="DIRECT_NATIVE_V1",
        market_open=True)
    env["history"] = {
        "history_contract_version": HISTORY_CONTRACT_VERSION,
        "backfill_run_id": str(backfill_run_id),
        "backfill_inserted_at_utc": cc._fmt(cc.normalise_utc(backfill_inserted_at_utc)),
        "source_table": str(source_table),
        "source_timestamp_utc": cc._fmt(cc.normalise_utc(source_timestamp_utc)),
    }
    cc.validate_candle_contract(env)          # validator STILL required (also scans history block)
    return env


def assert_d1_history_payload(envelope):
    """D1-SPECIFIC pre-write guard (IN ADDITION to validate_candle_contract + assert_history_target). A D1
    history record may be written ONLY if it is a COMPLETE, status-OK D1 derived from SIX H4 children on the
    fixed 22:00 UTC NY-5PM anchor. Fail-loud on any violation; NEVER writes. Incomplete D1 can never be
    written as OK. No direct candles_D1 / 24xH1 provenance may appear anywhere in the payload."""
    d = envelope["data"]
    if d.get("timeframe") != "D1":
        raise ValueError(f"GOV-CANDLE-HIST-D1-001: not a D1 envelope (timeframe {d.get('timeframe')!r})")
    if envelope.get("status") != "OK":
        raise ValueError(f"GOV-CANDLE-HIST-D1-002: D1 history requires status OK (got {envelope.get('status')!r}); "
                         "an incomplete D1 is NEVER written")
    open_dt = datetime.strptime(d["timestamp_utc"][:-1], cc._UTC_MS).replace(tzinfo=timezone.utc)
    if (open_dt.hour, open_dt.minute, open_dt.second, open_dt.microsecond) != (D1_HISTORY_ANCHOR_HOUR_UTC, 0, 0, 0):
        raise ValueError(f"GOV-CANDLE-HIST-D1-003: D1 open must be 22:00:00 UTC (got {d['timestamp_utc']}); "
                         "no UTC-midnight / DST / broker anchor")
    if d.get("source_timeframe") != D1_HISTORY_SOURCE_TIMEFRAME:
        raise ValueError(f"GOV-CANDLE-HIST-D1-004: D1 source_timeframe must be H4 (got {d.get('source_timeframe')!r}); "
                         "no 24xH1, no direct candles_D1")
    if d.get("expected_source_count") != D1_HISTORY_EXPECTED_CHILDREN:
        raise ValueError(f"GOV-CANDLE-HIST-D1-005: D1 expected_source_count must be 6 (got {d.get('expected_source_count')!r})")
    if d.get("source_count") != D1_HISTORY_EXPECTED_CHILDREN:
        raise ValueError(f"GOV-CANDLE-HIST-D1-006: D1 source_count must be 6 (got {d.get('source_count')!r}); "
                         "an incomplete D1 (fewer than six complete H4 children) is NEVER written")
    if d.get("source_coverage") != 1.0:
        raise ValueError(f"GOV-CANDLE-HIST-D1-007: D1 source_coverage must be 1.0 (got {d.get('source_coverage')!r})")
    blob = str(envelope).lower()
    for tok in _D1_FORBIDDEN_SOURCE_TOKENS:
        if tok in blob:
            raise ValueError(f"GOV-CANDLE-HIST-D1-008: forbidden D1 source provenance {tok!r} "
                             "(no direct candles_D1 / 24xH1 production shortcut)")
    return True


def build_history_write_plan(envelope):
    """INERT history write PLAN — NEVER performs a write. Validates the governed contract, derives the
    immutable key + index (score/member = open_epoch), applies the retention TTL, and asserts the target
    guards (incl. the D1-specific guard for D1). Idempotent by construction: same candle -> same key + same
    ZSET member/score (re-run = no-op)."""
    cc.validate_candle_contract(envelope)
    d = envelope["data"]
    inst, tf = d["instrument"], d["timeframe"]
    if tf == "D1":
        assert_d1_history_payload(envelope)        # six complete OK H4, 22:00 anchor, no direct/24xH1
    open_dt = datetime.strptime(d["timestamp_utc"][:-1], cc._UTC_MS).replace(tzinfo=timezone.utc)
    open_epoch = int(open_dt.timestamp())
    key = history_key(inst, tf, open_epoch)
    idx = history_index_key(inst, tf)
    assert_history_target(key)
    assert_history_target(idx)
    return {
        "operation": "SET", "key": key, "value": envelope, "ttl_seconds": HISTORY_TTL_SECONDS,
        "index_key": idx, "index_score": open_epoch, "index_member": str(open_epoch),
        "idempotent": True, "write_mode": WRITE_MODE_HISTORY_INERT,
    }


def history_retention_cutoff_epoch(now_utc):
    """Epoch-seconds cutoff for retention trimming (now - 35 days). The backfill writer uses this for
    ZREMRANGEBYSCORE on the index; NO delete happens here."""
    return int(cc.normalise_utc(now_utc).timestamp()) - HISTORY_TTL_SECONDS


# --------------------------------------------------------------------------- dry-run gap profile
def _norm_minute(dt):
    return cc.normalise_utc(dt).replace(microsecond=0)


def expected_opens_for_day(timeframe, day_start_utc):
    """The full theoretical grid of candle OPEN times for one UTC calendar day (no market-hours masking;
    weekend/holiday absence is surfaced as a gap, never silently expected away)."""
    if timeframe not in HISTORY_TIMEFRAMES:
        raise ValueError(f"GOV-CANDLE-HIST-003: timeframe {timeframe!r} not in history grid")
    day = cc.normalise_utc(day_start_utc).replace(hour=0, minute=0, second=0, microsecond=0)
    if timeframe == "H4":
        # NY-5PM aligned H4 grid (fixed 22:00 UTC boundary): opens 02,06,10,14,18,22 UTC within the day.
        return [day + timedelta(hours=hh) for hh in (2, 6, 10, 14, 18, 22)]
    if timeframe == "D1":
        # NY-5PM aligned D1: a single daily OPEN at 22:00 UTC (the day spans 22:00Z -> next 22:00Z).
        return [day + timedelta(hours=22)]
    step = cc.TF_SECONDS[timeframe]
    return [day + timedelta(seconds=i * step) for i in range(86400 // step)]


def gap_profile(timeframe, day_start_utc, actual_opens, max_samples=10):
    """Per-day / per-TF dry-run gap profile (pure; no I/O). `actual_opens` = candle OPEN datetimes present
    in the SQL source for that day. Returns the required dry-run record: expected/actual/missing counts,
    coverage %, first/last actual ts, a gap_explanation label, and sampled missing intervals."""
    exp = expected_opens_for_day(timeframe, day_start_utc)
    expset = {_norm_minute(e) for e in exp}
    actset = {a for a in (_norm_minute(x) for x in actual_opens) if a in expset}
    missing = sorted(expset - actset)
    day = cc.normalise_utc(day_start_utc).replace(hour=0, minute=0, second=0, microsecond=0)
    wd = day.weekday()                        # Mon=0 .. Sun=6
    if not missing:
        expl = "FULL_COVERAGE"
    elif wd == 5:
        expl = "WEEKEND_MARKET_CLOSED"        # Saturday — forex closed all day
    elif wd == 6:
        expl = "WEEKEND_MARKET_CLOSED_OPEN_SUN_2200Z"   # Sunday — opens ~22:00Z
    elif wd == 4:
        expl = "FRI_PARTIAL_CLOSE_2100Z"      # Friday — closes ~21:00Z
    else:
        expl = "WEEKDAY_GAP_INVESTIGATE"      # genuine weekday hole — flag for investigation
    return {
        "timeframe": timeframe,
        "day_utc": cc._fmt(day),
        "expected_count": len(exp),
        "actual_count": len(actset),
        "missing_count": len(missing),
        "coverage_pct": round(100.0 * len(actset) / len(exp), 2) if exp else 0.0,
        "first_actual_utc": cc._fmt(min(actset)) if actset else None,
        "last_actual_utc": cc._fmt(max(actset)) if actset else None,
        "gap_explanation": expl,
        "sampled_missing_utc": [cc._fmt(m) for m in missing[:max_samples]],
    }
