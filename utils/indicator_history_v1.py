"""HERMES governed Redis INDICATOR HISTORY/WINDOW contract v1.
WO-HERMES-INDICATOR-HISTORY-CONTRACT-0001.

DESIGN / CODE ONLY — there is NO Redis I/O in this module and NOTHING is written. It mirrors
`utils/candle_history_v1.py`'s proven history pattern EXACTLY, substituting `indicators` for `candles`, so
HELIOS (and any future consumer) can eventually read a historical governed indicator value for a specific
closed bar — computed by HERMES's existing deterministic calculation (`utils.indicators`/
`utils.atr_calculator`, via `hermes_indicators_v1.build_indicator_contract`), never a second implementation.

History is HARD-SEPARATED from the mutable `hermes:indicators:{instr}:{tf}:v1` ("latest") key:
  per-bar immutable key:  hermes:indicators:XAU_USD:{TF}:history:v1:{open_epoch}
  ordered index (ZSET):   hermes:indicators:XAU_USD:{TF}:history:v1:index   (score=open_epoch, member=open_epoch)

`assert_history_target` is the single guard a writer MUST call before any write; it makes it impossible to
target the mutable `latest` key, a non-history key, an alias instrument, D1 (gated/out of this WO's scope),
or an unversioned key. The governed v1 indicator-contract validator (`hermes_indicators_v1.
validate_indicator_contract`) is STILL required before any write — it also scans the `history` block for
forbidden regime/risk/decision tokens.

Retention is NOT reinvented here: `history_ttl_seconds_for`/`h4_history_retention_trim_plan`/
`history_retention_cutoff_epoch` are imported directly from the existing, governed
`utils.candle_history_v1` module and reused byte-for-byte (same `HERMES_REDIS_HISTORY_RETENTION_DAYS` env
var for M1/M5/M15/H1; same `H4_HISTORY_RETAIN_COUNT=250` count-based policy + 120-day TTL cap for H4) — no
new configuration surface, no new env var, no duplicate retention policy.

Scope (this WO): M1/M5/M15/H1/H4 only (identical grid to the governed candle-history grid). D1 indicator
history is NOT designed by this WO (D1 indicators are themselves gated/dark pending D1 latest GREEN, per
`hermes_indicators_v1`) and is therefore excluded here exactly as D1 is excluded from `candle_history_v1`.
"""
from __future__ import annotations
from datetime import datetime, timezone

from utils import candle_contract_v1 as cc
from utils import candle_history_v1 as chv
from utils import hermes_indicators_v1 as ind

HISTORY_CONTRACT_VERSION = "v1"
INDICATOR_PREFIX = "hermes:indicators:"
HISTORY_MARKER = "history"

# Identical grid to the governed candle-history grid (D1 excluded — gated/out of this WO's scope; see
# module docstring). Reused directly from candle_history_v1, never redefined, so the two grids can never
# silently drift apart.
HISTORY_TIMEFRAMES = chv.HISTORY_TIMEFRAMES   # ("M1", "M5", "M15", "H1", "H4")

# Provenance run-id marker for real-time writer-produced indicator-history records, mirroring the exact
# `FORWARD:<WO-ID>` convention `candle_history_forward_writer_v1.FORWARD_RUN_MARKER` already established.
PUBLISH_RUN_MARKER = "FORWARD:WO-HERMES-INDICATOR-HISTORY-CONTRACT-0001"

WRITE_MODE_HISTORY_INERT = chv.WRITE_MODE_HISTORY_INERT
# History provenance field names (natural identity = instrument/timeframe/open_epoch already carried by the
# key itself; NO event ID, UUID, or content hash — per the Architect's explicit ruling, WO §4/§7).
_HISTORY_FIELDS = ("history_contract_version", "publish_run_id", "published_at_utc",
                   "source_candle_history_key", "source_timestamp_utc")


# --------------------------------------------------------------------------- guards / builders
def _assert_inst_tf(instrument, timeframe):
    if instrument in ind._ALIAS_DENY:
        raise ValueError(f"GOV-HERMES-IND-HIST-001: alias instrument {instrument!r} may not be an "
                         "indicator-history output id — canonicalise first (no alias keys)")
    if timeframe not in HISTORY_TIMEFRAMES:
        raise ValueError(f"GOV-HERMES-IND-HIST-002: timeframe {timeframe!r} not in indicator-history grid "
                         f"{HISTORY_TIMEFRAMES} (D1 excluded — gated/out of this WO's scope)")


def history_key(instrument, timeframe, open_epoch):
    """Immutable per-bar indicator-history key: hermes:indicators:{inst}:{tf}:history:v1:{open_epoch}."""
    _assert_inst_tf(instrument, timeframe)
    oe = chv._to_open_epoch(open_epoch)   # reused exactly — no second epoch-coercion implementation
    return f"{INDICATOR_PREFIX}{instrument}:{timeframe}:{HISTORY_MARKER}:{HISTORY_CONTRACT_VERSION}:{oe}"


def history_index_key(instrument, timeframe):
    """Ordered index key (ZSET): hermes:indicators:{inst}:{tf}:history:v1:index."""
    _assert_inst_tf(instrument, timeframe)
    return f"{INDICATOR_PREFIX}{instrument}:{timeframe}:{HISTORY_MARKER}:{HISTORY_CONTRACT_VERSION}:index"


def assert_history_target(key):
    """THE guard a writer MUST call before any write. Proves `key` is a governed indicator-history key or
    index and can NEVER be: the mutable `latest` key (hermes:indicators:{inst}:{tf}:v1), a non-history
    indicator key, an alias instrument, D1, or an unversioned key. Fail-loud."""
    if not isinstance(key, str) or not key.startswith(INDICATOR_PREFIX):
        raise ValueError(f"GOV-HERMES-IND-HIST-TGT-001: not a hermes:indicators key {key!r}")
    parts = key.split(":")
    # hermes:indicators:{inst}:{tf}:history:v1:{open_epoch|index} -> exactly 7 colon-parts. The mutable
    # latest key (hermes:indicators:{inst}:{tf}:v1) has exactly 5 parts and is structurally rejected here.
    if len(parts) != 7 or parts[4] != HISTORY_MARKER or parts[5] != HISTORY_CONTRACT_VERSION:
        raise ValueError(f"GOV-HERMES-IND-HIST-TGT-002: not a versioned indicator-history key/index {key!r} "
                         "(expected hermes:indicators:{inst}:{tf}:history:v1:{open_epoch|index})")
    inst, tf, tail = parts[2], parts[3], parts[6]
    if inst in ind._ALIAS_DENY:
        raise ValueError(f"GOV-HERMES-IND-HIST-TGT-003: alias instrument {inst!r} key forbidden")
    if tf not in HISTORY_TIMEFRAMES:
        raise ValueError(f"GOV-HERMES-IND-HIST-TGT-004: timeframe {tf!r} not in indicator-history grid "
                         "(D1 excluded)")
    if tail != "index" and not tail.isdigit():
        raise ValueError(f"GOV-HERMES-IND-HIST-TGT-005: indicator-history key tail must be an open_epoch "
                         f"or 'index' (got {tail!r})")
    return True


# --------------------------------------------------------------------------- payload
def build_history_envelope(*, instrument, timeframe, generated_at_utc, value_open_time_utc, indicators,
                           freshness_state, publish_run_id, published_at_utc, source_candle_history_key,
                           source_timestamp_utc):
    """Build a governed indicator-history envelope = the EXACT `latest` indicator payload
    (`hermes_indicators_v1.build_indicator_contract` — same fields, same declared `methods` block, same
    deterministic computation) PLUS a `history` provenance block. `indicators` MUST be the already-computed
    output of `hermes_runtime_publisher_steps_v1._compute_indicators()` — this function never recomputes
    anything. The v1 indicator-contract validator is re-run over the full payload (incl. the history block)
    so no regime/risk/decision/auth field can leak, exactly mirroring `candle_history_v1.
    build_history_envelope`'s own re-validation discipline."""
    _assert_inst_tf(instrument, timeframe)
    payload = ind.build_indicator_contract(
        instrument=instrument, timeframe=timeframe, generated_at_utc=generated_at_utc,
        value_open_time_utc=value_open_time_utc, indicators=indicators, freshness_state=freshness_state)
    payload["history"] = {
        "history_contract_version": HISTORY_CONTRACT_VERSION,
        "publish_run_id": str(publish_run_id),
        "published_at_utc": cc._fmt(cc.normalise_utc(published_at_utc)),
        "source_candle_history_key": str(source_candle_history_key),
        "source_timestamp_utc": cc._fmt(cc.normalise_utc(source_timestamp_utc)),
    }
    ind.validate_indicator_contract(payload)          # validator STILL required (also scans history block)
    return payload


def build_history_write_plan(envelope):
    """INERT indicator-history write PLAN — NEVER performs a write. Validates the governed indicator
    contract (incl. history block), derives the immutable key + index (score/member = open_epoch), applies
    the EXACT existing per-timeframe retention policy (`candle_history_v1.history_ttl_seconds_for` — no new
    retention policy, no new config), and asserts the target guards. Idempotent by construction: same
    (instrument, timeframe, open_epoch) -> same key + same ZSET member/score (re-run = no-op)."""
    ind.validate_indicator_contract(envelope)
    if "history" not in envelope or set(envelope["history"].keys()) != set(_HISTORY_FIELDS):
        raise ValueError("GOV-HERMES-IND-HIST-010: envelope missing or malformed `history` provenance block "
                         f"(expected exactly {_HISTORY_FIELDS})")
    inst, tf = envelope["instrument"], envelope["timeframe"]
    open_dt = datetime.strptime(envelope["value_open_time_utc"][:-1], cc._UTC_MS).replace(tzinfo=timezone.utc)
    open_epoch = int(open_dt.timestamp())
    key = history_key(inst, tf, open_epoch)
    idx = history_index_key(inst, tf)
    assert_history_target(key)
    assert_history_target(idx)
    return {
        "operation": "SET", "key": key, "value": envelope,
        "ttl_seconds": chv.history_ttl_seconds_for(tf),           # reused EXACTLY — no new retention policy
        "index_key": idx, "index_score": open_epoch, "index_member": str(open_epoch),
        "idempotent": True, "write_mode": WRITE_MODE_HISTORY_INERT,
    }
