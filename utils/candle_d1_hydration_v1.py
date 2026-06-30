"""HERMES D1 H4 warm-start hydration v1.
WO-HELM-HERMES-D1-H4-HYDRATION-WARMSTART-0001.

Fixes the D1 in-memory accumulation RESET TRAP: the live D1 producer's six-H4-child buffer is volatile process
memory, so every deploy/restart flushes it and pushes the first clean D1 seal forward. This module reconstructs
the CURRENT (unsealed) D1 block from already-existing GOVERNED H4 history at boot, seeds the producer buffer, then
lets normal live H4 close hooks continue.

HYDRATION ONLY — NOT a backfill engine. NO Redis/SQL writes. NO synthesis. NO gap-laundering. NO D1 publication
here: the current block is published ONLY by a later LIVE roll-over (existing producer semantics), so D1 stays
AMBER until a genuine live seal. XAU_USD only. Fixed 22:00:00 UTC anchor (no DST). Source = 6xH4 only (never a
direct candles_D1, never a 24xH1 shortcut).

Gates (externally-supplied config, NOT in code):
  * HERMES_D1_WARMSTART_ENABLED   (default false) -> safe no-op cold-start (empty buffer, legacy behaviour).
  * HERMES_D1_WARMSTART_AUTHORISED (default false) -> ENABLED-without-AUTHORISED is fail-loud SystemExit(103).
Source select (config, not code): HERMES_D1_WARMSTART_SOURCE (default "redis_h4_history" — the canonical runtime
surface hermes:candles:XAU_USD:H4:history:v1:*). NO hard-coded host/port/db/credential — the read client is
INJECTED (or taken from the producer's existing canonical writer client). NO Redis/SQL/socket/thread/I-O at import.
"""
from __future__ import annotations
import json
import sys
from datetime import datetime, timedelta, timezone

from utils import candle_contract_v1 as cc
from utils import candle_d1_derivation_v1 as d1d
from utils import candle_d1_publish_wire_v1 as d1w
from utils import candle_history_v1 as hist
from utils import candle_runtime_seam_v1 as seam

WARMSTART_ENABLED_ENV = "HERMES_D1_WARMSTART_ENABLED"
WARMSTART_AUTHORISED_ENV = "HERMES_D1_WARMSTART_AUTHORISED"
WARMSTART_SOURCE_ENV = "HERMES_D1_WARMSTART_SOURCE"
SOURCE_REDIS_H4_HISTORY = "redis_h4_history"        # canonical runtime surface (preferred)
DEFAULT_SOURCE = SOURCE_REDIS_H4_HISTORY
HALT_CODE = 103                                      # ENABLED-without-AUTHORISED -> process-fatal
CANONICAL_INSTRUMENT = "XAU_USD"
COLD_START_LOG = "D1 warmstart skipped: cold-start strategy active"
_MAX_BLOCK_MEMBERS = 64                              # hard safety bound on a single-block index read


class HydratedH4Child:
    """Adapter presenting a governed H4 history ENVELOPE as the H4 candle-like object the D1 producer consumes
    (same surface as the live _SealedH4View): canonical OHLCV + open time + instrument + H4 completeness/
    provenance, so h4_child_is_complete / derive_d1 can fail-closed on any non-OK / incomplete child."""
    timeframe = "H4"

    def __init__(self, *, instrument, timestamp, open, high, low, close, volume,
                 status, is_closed, source_count, expected_source_count, source_coverage, gap_state):
        self.instrument = instrument
        self.timestamp = timestamp
        self.open, self.high, self.low, self.close, self.volume = open, high, low, close, volume
        self.status = status
        self.is_closed = is_closed
        self.source_count = source_count
        self.expected_source_count = expected_source_count
        self.source_coverage = source_coverage
        self.gap_state = gap_state


def child_from_history_envelope(env):
    """Build a HydratedH4Child from a governed H4 history v1 envelope dict. Raises on a malformed envelope
    (missing data/timestamp) so the caller can record it as malformed (never silently accepted)."""
    d = env["data"]
    # contract timestamps are UTC ISO 'Z' strings (millisecond precision) -> parse to aware-UTC datetime
    ts = datetime.strptime(d["timestamp_utc"][:-1], cc._UTC_MS).replace(tzinfo=timezone.utc)
    return HydratedH4Child(
        instrument=d["instrument"], timestamp=ts,
        open=d.get("open"), high=d.get("high"), low=d.get("low"), close=d.get("close"),
        volume=d.get("volume", 0), status=env.get("status"), is_closed=d.get("is_closed"),
        source_count=d.get("source_count"), expected_source_count=d.get("expected_source_count"),
        source_coverage=d.get("source_coverage"), gap_state=d.get("gap_state"))


def read_block_h4_children_from_redis(redis_client, *, instrument, d1_open):
    """BOUNDED read of the governed H4 history for ONE D1 block from the canonical runtime surface. Uses the
    H4 history INDEX ZSET score-range [d1_open, d1_open+24h) so only this block's epochs are touched (never a
    full scan), capped at _MAX_BLOCK_MEMBERS. Returns (children, malformed_epochs, capped). NO writes."""
    idx = hist.history_index_key(instrument, d1d.H4_TIMEFRAME)
    start = int(cc.normalise_utc(d1_open).timestamp())
    end = start + d1d.D1_SECONDS - 1
    members = redis_client.zrangebyscore(idx, start, end)
    epochs = [int(m) for m in members]
    capped = len(epochs) > _MAX_BLOCK_MEMBERS
    epochs = epochs[:_MAX_BLOCK_MEMBERS]
    children, malformed = [], []
    for ep in epochs:
        raw = redis_client.get(hist.history_key(instrument, d1d.H4_TIMEFRAME, ep))
        if not raw:
            continue
        try:
            env = json.loads(raw)
            children.append(child_from_history_envelope(env))
        except Exception:  # noqa: BLE001 - malformed history record -> recorded, never accepted
            malformed.append(ep)
    return children, malformed, capped


def _resolve_read_client(producer, redis_client):
    """Read client resolution WITHOUT any hard-coded target: use the injected client, else the producer's
    existing canonical writer client (the same Redis the H4 history is written to)."""
    if redis_client is not None:
        return redis_client
    writer = getattr(producer, "writer", None)
    return getattr(writer, "redis_client", None)


def warmstart_d1_from_env(producer, *, redis_client=None, now, logger=None, get_env=None, get_env_bool=None):
    """Boot-time warm-start orchestrator (client-injected; runtime-initialised only — never at import).

    Gate:
      * ENABLED unset/false -> SAFE NO-OP: empty cold-start buffer, log COLD_START_LOG, legacy behaviour preserved.
      * ENABLED=true + AUTHORISED=false -> FAIL LOUD: SystemExit(HALT_CODE=103) with stderr/log evidence.
      * ENABLED+AUTHORISED -> bounded hydration of the CURRENT D1 block, then return to live hook processing.
    Hydration performs NO Redis/SQL writes and NEVER publishes D1. Returns a detailed status dict for R2D2."""
    if get_env is None or get_env_bool is None:
        from env_config import get_env as _ge, get_env_bool as _geb  # lazy; HERMES-owned config only
        get_env = get_env or _ge
        get_env_bool = get_env_bool or _geb

    def _log(msg):
        (logger.info if logger is not None else (lambda m: print(m, file=sys.stderr)))(msg)

    if not get_env_bool(WARMSTART_ENABLED_ENV, False):
        _log(COLD_START_LOG)
        return {"attempted": False, "skipped": True, "reason": "COLD_START_STRATEGY_ACTIVE",
                "buffer_length": 0, "d1_remains_gated_amber": True}

    if not get_env_bool(WARMSTART_AUTHORISED_ENV, False):
        msg = (f"FATAL: {WARMSTART_ENABLED_ENV}=true requires {WARMSTART_AUTHORISED_ENV}=true — refusing D1 "
               f"warm-start hydration without authorisation (exit {HALT_CODE})")
        _log(msg)
        print(msg, file=sys.stderr)
        raise SystemExit(HALT_CODE)

    source = (get_env(WARMSTART_SOURCE_ENV, default=DEFAULT_SOURCE) or DEFAULT_SOURCE).strip()
    if producer is None or not getattr(producer, "enabled", False) \
            or not isinstance(producer, d1w.CanonicalD1Producer):
        return {"attempted": False, "skipped": True, "reason": "D1_PRODUCER_DISABLED",
                "source_selected": source, "buffer_length": 0, "d1_remains_gated_amber": True}

    if source != SOURCE_REDIS_H4_HISTORY:
        # Only the canonical Redis H4 history surface is wired. A SQL fallback is config-gated future work; an
        # unknown source must fail loud in evidence, never silently cold-start into a false-GREEN path.
        return {"attempted": True, "succeeded": False, "reason": "UNSUPPORTED_WARMSTART_SOURCE",
                "source_selected": source, "buffer_length": 0, "d1_remains_gated_amber": True}

    client = _resolve_read_client(producer, redis_client)
    if client is None:
        return {"attempted": True, "succeeded": False, "reason": "NO_READ_CLIENT_AVAILABLE",
                "source_selected": source, "buffer_length": 0, "d1_remains_gated_amber": True}

    d1_open = d1d.d1_bucket_open(now)
    children, malformed, capped = read_block_h4_children_from_redis(
        client, instrument=CANONICAL_INSTRUMENT, d1_open=d1_open)
    report = producer.hydrate(children, now=now, instrument=CANONICAL_INSTRUMENT)
    report.update({"source_selected": source, "candidate_count": len(children),
                   "malformed_history_epochs": malformed, "index_capped": capped})
    if malformed:
        report["malformed_count"] = len(malformed)
    _log("[D1_WARMSTART] source=%s block=%s..%s candidates=%d accepted=%d rejected=%d buffer=%d remaining=%d "
         "status=%s" % (source, report.get("d1_block_start_utc"), report.get("d1_block_end_utc"),
                        len(children), report.get("accepted_count", 0), report.get("rejected_count", 0),
                        report.get("buffer_length", 0), report.get("remaining_children_required", 0),
                        report.get("d1_status_after_hydration")))
    return report
