"""HERMES H4 H1 warm-start hydration v1.
WO-HELM-HERMES-H4-H1-HYDRATION-WARMSTART-0001.

Fixes the H4 producer RESET TRAP (one layer below the D1 warm-start): the live H4 producer's four-H1-child buffer
is volatile process memory, so a deploy/restart mid-H4-bucket flushes it and the bucket seals INCOMPLETE (the
2026-07-01 06:00 H4 sealed 2/4 after a mid-bucket restart, leaving the D1 day at 5/6 and correctly blocking D1).
This module reconstructs the CURRENT (unsealed) H4 block from already-existing GOVERNED H1 history at boot, seeds
the producer's H1 buffer, then lets normal live H1 close hooks continue and seal on a genuine live roll-over.

HYDRATION ONLY — NOT a backfill engine. NO Redis/SQL writes. NO synthesis. NO gap-laundering. NO H4 publication
here (the current bucket seals ONLY on a later LIVE roll-over). XAU_USD only. NY-5PM fixed 22:00-UTC-aligned H4
grid (no DST). Source = 4xH1 only.

Gates (externally-supplied config, NOT in code):
  * HERMES_H4_WARMSTART_ENABLED    (default false) -> safe no-op cold-start (empty buffer, legacy behaviour).
  * HERMES_H4_WARMSTART_AUTHORISED (default false) -> ENABLED-without-AUTHORISED is fail-loud SystemExit(104).
Source select (config, not code): HERMES_H4_WARMSTART_SOURCE (default "redis_h1_history" — the canonical runtime
surface hermes:candles:XAU_USD:H1:history:v1:*). NO hard-coded host/port/db/credential — the read client is
INJECTED (or taken from the producer's existing canonical writer client). NO Redis/SQL/socket/thread/I-O at import.
"""
from __future__ import annotations
import json
import sys
from datetime import datetime, timedelta, timezone

from utils import candle_contract_v1 as cc
from utils import candle_h4_derivation_v1 as h4d
from utils import candle_h4_publish_wire_v1 as h4w
from utils import candle_history_v1 as hist
from utils import candle_runtime_seam_v1 as seam

WARMSTART_ENABLED_ENV = "HERMES_H4_WARMSTART_ENABLED"
WARMSTART_AUTHORISED_ENV = "HERMES_H4_WARMSTART_AUTHORISED"
WARMSTART_SOURCE_ENV = "HERMES_H4_WARMSTART_SOURCE"
SOURCE_REDIS_H1_HISTORY = "redis_h1_history"        # canonical runtime surface (preferred)
DEFAULT_SOURCE = SOURCE_REDIS_H1_HISTORY
HALT_CODE = 104                                      # ENABLED-without-AUTHORISED -> process-fatal
CANONICAL_INSTRUMENT = "XAU_USD"
COLD_START_LOG = "H4 warmstart skipped: cold-start strategy active"
_MAX_BLOCK_MEMBERS = 32                              # hard safety bound on a single-block index read


class HydratedH1Child:
    """Adapter presenting a governed H1 history ENVELOPE as the H1 candle-like object the H4 producer consumes:
    canonical OHLCV + open time + instrument + H1 completeness/provenance, so h1_child_is_complete / derive_h4
    can fail-closed on any non-OK / incomplete child."""
    timeframe = "H1"

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
    """Build a HydratedH1Child from a governed H1 history v1 envelope dict. Raises on a malformed envelope
    (missing data/timestamp) so the caller records it as malformed (never silently accepted)."""
    d = env["data"]
    ts = datetime.strptime(d["timestamp_utc"][:-1], cc._UTC_MS).replace(tzinfo=timezone.utc)
    return HydratedH1Child(
        instrument=d["instrument"], timestamp=ts,
        open=d.get("open"), high=d.get("high"), low=d.get("low"), close=d.get("close"),
        volume=d.get("volume", 0), status=env.get("status"), is_closed=d.get("is_closed"),
        source_count=d.get("source_count"), expected_source_count=d.get("expected_source_count"),
        source_coverage=d.get("source_coverage"), gap_state=d.get("gap_state"))


def read_block_h1_children_from_redis(redis_client, *, instrument, h4_open):
    """BOUNDED read of the governed H1 history for ONE H4 block from the canonical runtime surface. Uses the H1
    history INDEX ZSET score-range [h4_open, h4_open+4h) so only this block's epochs are touched (never a full
    scan), capped at _MAX_BLOCK_MEMBERS. Returns (children, malformed_epochs, capped). NO writes."""
    idx = hist.history_index_key(instrument, h4d.H1_TIMEFRAME)
    start = int(cc.normalise_utc(h4_open).timestamp())
    end = start + h4d.H4_SECONDS - 1
    members = redis_client.zrangebyscore(idx, start, end)
    epochs = [int(m) for m in members]
    capped = len(epochs) > _MAX_BLOCK_MEMBERS
    epochs = epochs[:_MAX_BLOCK_MEMBERS]
    children, malformed = [], []
    for ep in epochs:
        raw = redis_client.get(hist.history_key(instrument, h4d.H1_TIMEFRAME, ep))
        if not raw:
            continue
        try:
            children.append(child_from_history_envelope(json.loads(raw)))
        except Exception:  # noqa: BLE001 - malformed history record -> recorded, never accepted
            malformed.append(ep)
    return children, malformed, capped


def _resolve_read_client(producer, redis_client):
    """Read client WITHOUT any hard-coded target: injected client, else the producer's existing canonical writer
    client (the same Redis the H1 history is written to)."""
    if redis_client is not None:
        return redis_client
    writer = getattr(producer, "writer", None)
    return getattr(writer, "redis_client", None)


def warmstart_h4_from_env(producer, *, redis_client=None, now, logger=None, get_env=None, get_env_bool=None):
    """Boot-time H4 warm-start orchestrator (client-injected; runtime-initialised only — never at import).

    Gate:
      * ENABLED unset/false -> SAFE NO-OP: empty cold-start buffer, log COLD_START_LOG, legacy behaviour preserved.
      * ENABLED=true + AUTHORISED=false -> FAIL LOUD: SystemExit(HALT_CODE=104) with stderr/log evidence.
      * ENABLED+AUTHORISED -> bounded hydration of the CURRENT H4 block, then return to live H1 hook processing.
    Hydration performs NO Redis/SQL writes and NEVER publishes/seals H4. Returns a detailed status dict for R2D2."""
    if get_env is None or get_env_bool is None:
        from env_config import get_env as _ge, get_env_bool as _geb  # lazy; HERMES-owned config only
        get_env = get_env or _ge
        get_env_bool = get_env_bool or _geb

    def _log(msg):
        (logger.info if logger is not None else (lambda m: print(m, file=sys.stderr)))(msg)

    if not get_env_bool(WARMSTART_ENABLED_ENV, False):
        _log(COLD_START_LOG)
        return {"attempted": False, "skipped": True, "reason": "COLD_START_STRATEGY_ACTIVE", "buffer_length": 0}

    if not get_env_bool(WARMSTART_AUTHORISED_ENV, False):
        msg = (f"FATAL: {WARMSTART_ENABLED_ENV}=true requires {WARMSTART_AUTHORISED_ENV}=true — refusing H4 "
               f"warm-start hydration without authorisation (exit {HALT_CODE})")
        _log(msg)
        print(msg, file=sys.stderr)
        raise SystemExit(HALT_CODE)

    source = (get_env(WARMSTART_SOURCE_ENV, default=DEFAULT_SOURCE) or DEFAULT_SOURCE).strip()
    if producer is None or not getattr(producer, "enabled", False) \
            or not isinstance(producer, h4w.CanonicalH4Producer):
        return {"attempted": False, "skipped": True, "reason": "H4_PRODUCER_DISABLED",
                "source_selected": source, "buffer_length": 0}

    if source != SOURCE_REDIS_H1_HISTORY:
        # Only the canonical Redis H1 history surface is wired. An unknown source must fail loud in evidence,
        # never silently cold-start into a false path.
        return {"attempted": True, "succeeded": False, "reason": "UNSUPPORTED_WARMSTART_SOURCE",
                "source_selected": source, "buffer_length": 0}

    client = _resolve_read_client(producer, redis_client)
    if client is None:
        return {"attempted": True, "succeeded": False, "reason": "NO_READ_CLIENT_AVAILABLE",
                "source_selected": source, "buffer_length": 0}

    h4_open = h4d.h4_bucket_open(now)
    children, malformed, capped = read_block_h1_children_from_redis(
        client, instrument=CANONICAL_INSTRUMENT, h4_open=h4_open)
    report = producer.hydrate(children, now=now, instrument=CANONICAL_INSTRUMENT)
    report.update({"source_selected": source, "candidate_count": len(children),
                   "malformed_history_epochs": malformed, "index_capped": capped})
    if malformed:
        report["malformed_count"] = len(malformed)
    _log("[H4_WARMSTART] source=%s block=%s..%s candidates=%d accepted=%d rejected=%d buffer=%d remaining=%d "
         "status=%s" % (source, report.get("h4_block_start_utc"), report.get("h4_block_end_utc"),
                        len(children), report.get("accepted_count", 0), report.get("rejected_count", 0),
                        report.get("buffer_length", 0), report.get("remaining_children_required", 0),
                        report.get("h4_status_after_hydration")))
    return report
