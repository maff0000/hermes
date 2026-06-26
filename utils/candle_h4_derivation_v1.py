"""HERMES governed H4 candle derivation from H1 (NY-5PM aligned anchor).
WO-HELM-HERMES-GOLD-H4-DERIVATION-FROM-H1-0001.

CODE/DESIGN ONLY — NO Redis I/O, NO publication. Derives an H4 candle from its 4 constituent H1 candles
using the ratified fixed NY-5PM-aligned anchor and the existing governed candle v1 contract
(`build_derived_candle_contract`). Completeness is accounted honestly: an H4 with <4 H1 children can
never be status OK; no missing H1 is ever synthesised.

Ratified anchor (architect decision): the H4 day boundary is fixed **22:00 UTC** (winter NY-5PM); H4
buckets OPEN at 22:00, 02:00, 06:00, 10:00, 14:00, 18:00 UTC. No intraday DST shift (fixed UTC grid).
6 H4 buckets nest into the (separate, deferred) NY-5PM D1.

Source policy: H4 is derived from H1 ONLY (expected 4 children). The dead/stale `candles_H4` table and any
Proteus-era source are NOT used. M15 is not a fallback here (a later repair WO may add that).
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone

from utils import candle_contract_v1 as cc

H4_TIMEFRAME = "H4"
H1_TIMEFRAME = "H1"
H4_SECONDS = cc.TF_SECONDS["H4"]                 # 14400
H1_SECONDS = cc.TF_SECONDS["H1"]                 # 3600
H4_EXPECTED_CHILDREN = 4
# Fixed NY-5PM-aligned anchor: day boundary 22:00 UTC; buckets open at these UTC hours.
H4_ANCHOR_HOURS_UTC = (22, 2, 6, 10, 14, 18)
H4_DAY_BOUNDARY_UTC_HOUR = 22
# The 22:00 anchor sits 2h before the next 00:00; shifting the epoch by +2h makes the grid 4h-aligned.
_ANCHOR_SHIFT_SECONDS = (24 - H4_DAY_BOUNDARY_UTC_HOUR) * 3600    # 7200
SOURCE_POLICY_EPOCH = "H4_FROM_H1_NY1700_V1"


def h4_bucket_open(dt):
    """Return the NY-5PM-aligned H4 bucket OPEN time (aware UTC) that contains `dt`.
    Buckets open at 22/02/06/10/14/18 UTC; the 22:00 bucket spans into the next UTC day."""
    d = cc.normalise_utc(dt)
    epoch = int(d.timestamp())
    bucket_epoch = ((epoch + _ANCHOR_SHIFT_SECONDS) // H4_SECONDS) * H4_SECONDS - _ANCHOR_SHIFT_SECONDS
    return datetime.fromtimestamp(bucket_epoch, tz=timezone.utc)


def h4_bucket_opens_for_utc_day(day_start_utc):
    """The 6 H4 bucket OPEN times whose open falls within the given UTC calendar day
    (02, 06, 10, 14, 18, 22 UTC) — the NY-5PM grid as seen within a 00:00-24:00 UTC day."""
    day = cc.normalise_utc(day_start_utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return [day + timedelta(hours=hh) for hh in sorted(H4_ANCHOR_HOURS_UTC) if hh != 0]  # 2,6,10,14,18,22


def h1_children_in_bucket(h4_open, h1_candles):
    """Select the H1 children whose OPEN falls in [h4_open, h4_open+4h), ordered by open time.
    Each candle must expose `.timestamp` (open) + open/high/low/close/volume (object or dict)."""
    start = cc.normalise_utc(h4_open)
    end = start + timedelta(seconds=H4_SECONDS)

    def _open(c):
        ts = c["timestamp"] if isinstance(c, dict) else getattr(c, "timestamp", None)
        return cc.normalise_utc(ts)

    sel = [c for c in h1_candles if start <= _open(c) < end]
    return sorted(sel, key=_open)


def _ohlcv(c):
    if isinstance(c, dict):
        return {k: c[k] for k in ("open", "high", "low", "close")} | {"volume": c.get("volume", 0)}
    return {"open": c.open, "high": c.high, "low": c.low, "close": c.close,
            "volume": getattr(c, "volume", 0)}


def derive_h4(*, instrument, h4_open, h1_children, generated_at_utc, is_closed=True, market_open=True):
    """Derive a governed H4 v1 envelope for `instrument` at bucket `h4_open` from its H1 children.

    OHLCV: open=first child open, high=max child high, low=min child low, close=last child close,
    volume=sum child volume (via the contract's deterministic aggregate). Provenance is DERIVED with
    source_timeframe=H1, expected_source_count=4, source_count=len(children), source_coverage. Completeness
    is enforced by the contract: <4 children -> never OK (SOURCE_INCOMPLETE / coverage<1 / gap INCOMPLETE);
    0 children -> NO_SOURCE_DATA/GAP_DETECTED; is_closed=False -> FORMING. Missing H1 are NEVER synthesised.

    Returns (envelope, derivation_meta). The envelope carries only contract-supported provenance; per-child
    open epochs are returned in derivation_meta (not a v1 contract field)."""
    if instrument in ("XAUUSD",) or instrument != "XAU_USD":
        raise ValueError(f"GOV-CANDLE-H4-001: instrument {instrument!r} not allowed (XAU_USD only; no alias)")
    children = sorted(h1_children, key=lambda c: cc.normalise_utc(c["timestamp"] if isinstance(c, dict)
                                                                  else c.timestamp))
    source = [_ohlcv(c) for c in children]
    env = cc.build_derived_candle_contract(
        instrument=instrument, timeframe=H4_TIMEFRAME, timestamp_utc=cc.normalise_utc(h4_open),
        source_candles=source, expected_source_count=H4_EXPECTED_CHILDREN,
        generated_at_utc=generated_at_utc, source_timeframe=H1_TIMEFRAME,
        derivation_policy=cc.DERIVATION_POLICY_H4_FROM_H1, source_policy_epoch=SOURCE_POLICY_EPOCH,
        is_closed=is_closed, market_open=market_open)
    cc.validate_candle_contract(env)
    child_epochs = [int(cc.normalise_utc(c["timestamp"] if isinstance(c, dict) else c.timestamp).timestamp())
                    for c in children]
    meta = {"h4_open_epoch": int(cc.normalise_utc(h4_open).timestamp()),
            "expected_children": H4_EXPECTED_CHILDREN, "source_count": len(children),
            "child_open_epochs": child_epochs, "source_timeframe": H1_TIMEFRAME,
            "derivation_policy": cc.DERIVATION_POLICY_H4_FROM_H1}
    return env, meta
