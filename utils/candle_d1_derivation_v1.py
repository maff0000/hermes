"""HERMES governed D1 candle derivation from H4 (fixed 22:00 UTC NY-5PM daily anchor).
WO-HELM-HERMES-GOLD-D1-DERIVATION-FROM-H4-0001.

CODE/DESIGN ONLY — NO Redis I/O, NO publication, NO history write. Derives a D1 candle from its SIX
constituent H4 candles using the ratified fixed NY-5PM daily anchor and the existing governed candle v1
contract (`build_derived_candle_contract`). Completeness is accounted honestly: a D1 with <6 H4 children
can never be status OK; no missing H4 is ever synthesised.

Ratified D1 source policy (architect):
  * PRIMARY source = 6 x H4 (the NY-5PM H4 grid 22/02/06/10/14/18 UTC nests exactly into one D1 day).
  * The daily boundary is FIXED 22:00 UTC: a D1 bucket OPENS at 22:00Z and spans 22:00Z -> next 22:00Z.
  * NO UTC-midnight D1 (the dead, midnight-anchored `candles_D1` table is rejected as a source).
  * NO 24 x H1 production shortcut — H1 may appear ONLY as an audit/cross-check note, never as a source path.
  * No DST-shifting / broker-local opaque anchor (fixed UTC grid).

This module does not publish or write any `hermes:candles:XAU_USD:D1:*` key. The canonical-publish,
history, and seam grids still EXCLUDE D1; future guarded publication/history is a separate WO.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone

from utils import candle_contract_v1 as cc

D1_TIMEFRAME = "D1"
H4_TIMEFRAME = "H4"
D1_SECONDS = cc.TF_SECONDS["D1"]                 # 86400
H4_SECONDS = cc.TF_SECONDS["H4"]                 # 14400
D1_EXPECTED_CHILDREN = 6                          # 6 x H4 per NY-5PM day
# Fixed NY-5PM daily anchor: the D1 day boundary is 22:00 UTC. A D1 bucket opens at 22:00Z.
D1_ANCHOR_HOUR_UTC = 22
D1_DAY_BOUNDARY_UTC_HOUR = 22
# The 22:00 anchor sits 2h before 00:00; shifting the epoch by +2h makes the daily grid 86400-aligned.
_ANCHOR_SHIFT_SECONDS = (24 - D1_DAY_BOUNDARY_UTC_HOUR) * 3600    # 7200
# The 6 H4 child OPEN hours (UTC) that nest into one 22:00->22:00 D1 day, in order.
D1_CHILD_H4_OPEN_HOURS_UTC = (22, 2, 6, 10, 14, 18)
SOURCE_POLICY_EPOCH = "D1_FROM_H4_NY1700_FIXED_UTC_V1"


def d1_bucket_open(dt):
    """Return the NY-5PM-aligned D1 bucket OPEN time (aware UTC) that contains `dt`.
    Buckets open at 22:00 UTC and span 22:00Z -> next 22:00Z (never a UTC-midnight day)."""
    d = cc.normalise_utc(dt)
    epoch = int(d.timestamp())
    bucket_epoch = ((epoch + _ANCHOR_SHIFT_SECONDS) // D1_SECONDS) * D1_SECONDS - _ANCHOR_SHIFT_SECONDS
    return datetime.fromtimestamp(bucket_epoch, tz=timezone.utc)


def d1_child_h4_opens(d1_open):
    """The 6 H4 child OPEN times for the D1 bucket opening at `d1_open` (22:00Z), in order:
    22:00, 02:00, 06:00, 10:00, 14:00, 18:00 (the second..sixth fall on the next UTC calendar day)."""
    start = cc.normalise_utc(d1_open)
    return [start + timedelta(seconds=i * H4_SECONDS) for i in range(D1_EXPECTED_CHILDREN)]


def h4_children_in_bucket(d1_open, h4_candles):
    """Select the H4 children whose OPEN falls in [d1_open, d1_open+24h), ordered by open time.
    Each candle must expose `.timestamp` (open) + open/high/low/close/volume (object or dict)."""
    start = cc.normalise_utc(d1_open)
    end = start + timedelta(seconds=D1_SECONDS)

    def _open(c):
        ts = c["timestamp"] if isinstance(c, dict) else getattr(c, "timestamp", None)
        return cc.normalise_utc(ts)

    sel = [c for c in h4_candles if start <= _open(c) < end]
    return sorted(sel, key=_open)


def _ohlcv(c):
    if isinstance(c, dict):
        return {k: c[k] for k in ("open", "high", "low", "close")} | {"volume": c.get("volume", 0)}
    return {"open": c.open, "high": c.high, "low": c.low, "close": c.close,
            "volume": getattr(c, "volume", 0)}


def assert_d1_open_anchor(d1_open):
    """Fail-loud guard: a D1 open MUST be exactly 22:00:00 UTC (no UTC-midnight, no DST/broker anchor)."""
    d = cc.normalise_utc(d1_open)
    if (d.hour, d.minute, d.second, d.microsecond) != (D1_ANCHOR_HOUR_UTC, 0, 0, 0):
        raise ValueError(f"GOV-CANDLE-D1-002: D1 open must be 22:00:00 UTC (NY-5PM fixed), got {cc._fmt(d)} "
                         "(no UTC-midnight / DST / broker-local anchor)")
    return True


def derive_d1(*, instrument, d1_open, h4_children, generated_at_utc, is_closed=True, market_open=True):
    """Derive a governed D1 v1 envelope for `instrument` at bucket `d1_open` (22:00Z) from its H4 children.

    OHLCV: open=first child open, high=max child high, low=min child low, close=last child close,
    volume=sum child volume (via the contract's deterministic aggregate). Provenance is DERIVED with
    source_timeframe=H4, expected_source_count=6, source_count=len(children), source_coverage. Completeness
    is enforced by the contract: <6 children -> never OK (SOURCE_INCOMPLETE / coverage<1 / gap INCOMPLETE);
    0 children -> NO_SOURCE_DATA/GAP_DETECTED; is_closed=False -> FORMING. Missing H4 are NEVER synthesised.
    H1 is NOT a source here (audit/cross-check only). The midnight-anchored candles_D1 table is NOT used.

    Returns (envelope, derivation_meta). The envelope carries only contract-supported provenance; per-child
    open epochs are returned in derivation_meta (not a v1 contract field)."""
    if instrument in ("XAUUSD",) or instrument != "XAU_USD":
        raise ValueError(f"GOV-CANDLE-D1-001: instrument {instrument!r} not allowed (XAU_USD only; no alias)")
    assert_d1_open_anchor(d1_open)
    children = sorted(h4_children, key=lambda c: cc.normalise_utc(c["timestamp"] if isinstance(c, dict)
                                                                  else c.timestamp))
    source = [_ohlcv(c) for c in children]
    env = cc.build_derived_candle_contract(
        instrument=instrument, timeframe=D1_TIMEFRAME, timestamp_utc=cc.normalise_utc(d1_open),
        source_candles=source, expected_source_count=D1_EXPECTED_CHILDREN,
        generated_at_utc=generated_at_utc, source_timeframe=H4_TIMEFRAME,
        derivation_policy=cc.DERIVATION_POLICY_D1_FROM_H4, source_policy_epoch=SOURCE_POLICY_EPOCH,
        is_closed=is_closed, market_open=market_open)
    cc.validate_candle_contract(env)
    child_epochs = [int(cc.normalise_utc(c["timestamp"] if isinstance(c, dict) else c.timestamp).timestamp())
                    for c in children]
    meta = {"d1_open_epoch": int(cc.normalise_utc(d1_open).timestamp()),
            "expected_children": D1_EXPECTED_CHILDREN, "source_count": len(children),
            "child_open_epochs": child_epochs, "source_timeframe": H4_TIMEFRAME,
            "derivation_policy": cc.DERIVATION_POLICY_D1_FROM_H4}
    return env, meta
