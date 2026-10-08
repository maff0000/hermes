"""WO-HERMES-INDICATOR-HISTORY-SAME-BAR-RECOMPUTATION-0001 — B1 corrective test matrix.

Implements PID-HERMES-MVP-001.md §17's binding semantics: `_write_indicator_history`'s same-bar conflict
check (`utils/hermes_runtime_publisher_steps_v1.py`) must (a) exclude `freshness_state` from conflict
comparison (joining the already-excluded `generated_at_utc`/`history.published_at_utc`), and (b) accept a
field transitioning from null/absent to a populated, schema-valid value as legitimate deterministic
recomputation — generically, across every indicator field, never EMA-200-specific — while continuing to
fail loud (`GOV-HERMES-IND-HIST-020`) for any other difference (populated->different-populated, or
populated->null regression).

All 13 WO §6 test-matrix items are covered below (numbered in docstrings/test names), plus the mandatory
8-step R2D2 regression sequence from WO §7, exercising the REAL, unmodified `_compute_indicators()` /
`_write_indicator_history()` functions end-to-end — never a reimplementation or an isolated mock of the
comparison logic. No real Redis/SQL I/O anywhere (FakeRedis in-memory double, same convention as the
existing `WO-HERMES-INDICATOR-HISTORY-CONTRACT-0001` suite). Item 13 (the existing 41-test suite remains
green) is verified by running that suite alongside this one, not reproduced here.
"""
import datetime
import json

import pytest

from utils import hermes_runtime_publisher_steps_v1 as steps
from utils import indicator_history_v1 as indh

UTC = datetime.timezone.utc
INST = "XAU_USD"

# H4-aligned anchor epoch for the fixed "latest closed bar" every fixture below ends at.
ANCHOR_EPOCH = 1_700_000_000 - (1_700_000_000 % 14400)
H4_SECONDS = 14400


class FakeRedis:
    """Minimal in-memory double for exactly what `_write_indicator_history`/`_read_history` need (kv + ZSET).
    Same convention as `tests/test_hermes_indicator_history_write_path_v1.py`'s own fake."""

    def __init__(self):
        self.kv = {}
        self.z = {}

    def zadd(self, key, mapping):
        self.z.setdefault(key, {}).update(mapping)

    def zrevrange(self, key, start, end):
        items = sorted(self.z.get(key, {}).items(), key=lambda kv: kv[1], reverse=True)
        sl = items[start:(end + 1 if end != -1 else None)]
        return [m for m, _ in sl]

    def zcard(self, key):
        return len(self.z.get(key, {}))

    def zremrangebyrank(self, key, start, stop):
        members = sorted(self.z.get(key, {}).items(), key=lambda kv: kv[1])
        removed = members[start:stop + 1] if stop != -1 else members[start:]
        for m, _ in removed:
            self.z[key].pop(m, None)
        return len(removed)

    def zremrangebyscore(self, key, lo, hi):
        lo_v = float("-inf") if lo == "-inf" else float(lo)
        hi_v = float("inf") if hi == "+inf" else float(hi)
        members = [(m, s) for m, s in self.z.get(key, {}).items() if lo_v <= s <= hi_v]
        for m, _ in members:
            self.z[key].pop(m, None)
        return len(members)

    def get(self, key):
        return self.kv.get(key)

    def set(self, key, val, ex=None):
        self.kv[key] = val


def _candle_at(k, base=2000.0):
    """k=0 is the most-recent/closing bar; k increases moving backward in time. A pure function of `k`
    ALONE — stable regardless of how many deeper (larger-k) bars a fixture additionally supplies, so a
    'same bar' recomputation against a deeper fixture reproduces byte-identical OHLC for every bar the
    shallower fixture already had (never a different candle masquerading as a recomputation)."""
    p = base + (k % 9) * 0.7 - (k % 4) * 0.5 - k * 0.01
    return {"open": round(p, 5), "high": round(p + 1.3, 5), "low": round(p - 1.15, 5),
            "close": round(p + 0.35, 5), "candle_direction": "up"}


def _ts(epoch):
    return datetime.datetime.fromtimestamp(epoch, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _vot_of(candle):
    return datetime.datetime.strptime(candle["timestamp_utc"][:-1], "%Y-%m-%dT%H:%M:%S.%f").replace(tzinfo=UTC)


def _seed_h4_backward(r, depth, anchor=ANCHOR_EPOCH):
    """Seed `depth` consecutive H4 bars ending at the FIXED `anchor` latest bar. Calling again with a
    LARGER `depth` only ADDS older (smaller-epoch) bars — the latest bar, and every bar already present,
    stay byte-identical, so the latest closed bar never changes identity or content as depth grows (the
    exact 'governed missing history is restored' scenario WO §7 requires)."""
    idx = f"hermes:candles:{INST}:H4:history:v1:index"
    for k in range(depth):
        ep = anchor - k * H4_SECONDS
        c = _candle_at(k)
        c["timestamp_utc"] = _ts(ep)
        r.zadd(idx, {str(ep): ep})
        r.kv[f"hermes:candles:{INST}:H4:history:v1:{ep}"] = json.dumps({"data": c})
    r.kv[f"hermes:candles:{INST}:H4:latest:v1"] = json.dumps({"status": "OK"})


def _write(r, candles, freshness_state="FRESH"):
    """Convenience: compute + write via the REAL functions, return (vot, inds, key)."""
    vot = _vot_of(candles[-1])
    inds = steps._compute_indicators(candles)
    steps._write_indicator_history(r, tf="H4", now=steps._now(), vot=vot, inds=inds, freshness_state=freshness_state)
    key = indh.history_key(INST, "H4", int(vot.timestamp()))
    return vot, inds, key


# --------------------------------------------------------------------------- item 1
def test_01_first_write_of_a_bar_succeeds():
    r = FakeRedis()
    _seed_h4_backward(r, 20)
    candles = steps._read_history(r, "H4", steps.EMA_TREND_WINDOW)
    _, inds, key = _write(r, candles)
    assert key in r.kv
    stored = json.loads(r.kv[key])
    assert stored["indicators"] == inds


# --------------------------------------------------------------------------- item 2 (regression)
def test_02_byte_semantic_equivalent_same_bar_replay_succeeds():
    r = FakeRedis()
    _seed_h4_backward(r, 20)
    candles = steps._read_history(r, "H4", steps.EMA_TREND_WINDOW)
    _write(r, candles)
    _write(r, candles)   # identical governed content, different publish moment -> must NOT raise


# --------------------------------------------------------------------------- item 3 (regression)
def test_03_generated_at_utc_difference_does_not_conflict():
    r = FakeRedis()
    _seed_h4_backward(r, 20)
    candles = steps._read_history(r, "H4", steps.EMA_TREND_WINDOW)
    vot = _vot_of(candles[-1])
    inds = steps._compute_indicators(candles)
    steps._write_indicator_history(r, tf="H4", now=datetime.datetime(2026, 1, 1, tzinfo=UTC), vot=vot,
                                    inds=inds, freshness_state="FRESH")
    steps._write_indicator_history(r, tf="H4", now=datetime.datetime(2026, 6, 1, tzinfo=UTC), vot=vot,
                                    inds=inds, freshness_state="FRESH")   # different generated_at_utc -> no raise


# --------------------------------------------------------------------------- item 4 (regression)
def test_04_published_at_utc_difference_does_not_conflict():
    # `published_at_utc` is ALWAYS a fresh clock read inside `_write_indicator_history` (never caller-
    # supplied, per its own docstring), so every real write already differs from the last on this field.
    # The real assertion is that two same-bar writes with (necessarily) different `published_at_utc` both
    # succeed without raising — proven directly by the comparison function on two explicit, distinct
    # `history.published_at_utc` values via `_indicator_history_conflict`, independent of clock resolution.
    r = FakeRedis()
    _seed_h4_backward(r, 20)
    candles = steps._read_history(r, "H4", steps.EMA_TREND_WINDOW)
    vot = _vot_of(candles[-1])
    inds = steps._compute_indicators(candles)
    steps._write_indicator_history(r, tf="H4", now=steps._now(), vot=vot, inds=inds, freshness_state="FRESH")
    key = indh.history_key(INST, "H4", int(vot.timestamp()))
    existing = json.loads(r.kv[key])
    new = dict(existing)
    new["history"] = dict(existing["history"])
    new["history"]["published_at_utc"] = "2099-01-01T00:00:00.000Z"   # deliberately far apart
    assert existing["history"]["published_at_utc"] != new["history"]["published_at_utc"]
    assert steps._indicator_history_conflict(existing, new) is False


# --------------------------------------------------------------------------- item 5 (new — PID §17 item 2)
def test_05_clock_derived_freshness_state_transition_does_not_conflict():
    r = FakeRedis()
    _seed_h4_backward(r, 20)
    candles = steps._read_history(r, "H4", steps.EMA_TREND_WINDOW)
    _, inds, key = _write(r, candles, freshness_state="FRESH")
    steps._write_indicator_history(r, tf="H4", now=steps._now(), vot=_vot_of(candles[-1]), inds=inds,
                                    freshness_state="STALE_EXPIRED")   # must NOT raise
    assert json.loads(r.kv[key])["freshness_state"] == "STALE_EXPIRED"


# --------------------------------------------------------------------------- item 6 (new — proven blocking case)
def test_06_legitimate_ema200_null_to_populated_recomputation_succeeds_and_persists():
    r = FakeRedis()
    _seed_h4_backward(r, 69)   # < 200 usable closes -> ema_200 null
    shallow = steps._read_history(r, "H4", steps.EMA_TREND_WINDOW)
    inds_shallow = steps._compute_indicators(shallow)
    assert inds_shallow["ema_200"] is None
    _write(r, shallow)

    _seed_h4_backward(r, 210)   # governed H4 history depth restored; SAME latest bar
    deep = steps._read_history(r, "H4", steps.EMA_TREND_WINDOW)
    assert deep[-1] == shallow[-1]   # same bar, byte-identical
    inds_deep = steps._compute_indicators(deep)
    assert inds_deep["ema_200"] is not None

    vot, inds, key = _write(r, deep)   # must SUCCEED — no GOV-HERMES-IND-HIST-020
    stored = json.loads(r.kv[key])
    assert stored["indicators"]["ema_200"] == inds_deep["ema_200"]


# --------------------------------------------------------------------------- item 7 (new)
def test_07_equivalent_legitimate_recomputation_remains_idempotent_afterward():
    r = FakeRedis()
    _seed_h4_backward(r, 69)
    shallow = steps._read_history(r, "H4", steps.EMA_TREND_WINDOW)
    _write(r, shallow)
    _seed_h4_backward(r, 210)
    deep = steps._read_history(r, "H4", steps.EMA_TREND_WINDOW)
    _write(r, deep)
    _write(r, deep)   # replaying the now-populated value again — must remain idempotent (no error)


# --------------------------------------------------------------------------- item 8 (new — generic, NOT ema_200)
def test_08_null_to_populated_acceptance_proven_generically_for_another_field_rsi14():
    """Items 6/7 prove the rule end-to-end for `ema_200` specifically (the proven production-blocking case,
    and the one real field that CAN legitimately vary independently of the stable `INDICATOR_WINDOW`-tail-
    sliced `closes` — see `_compute_indicators`'s own docstring). Item 8 instead proves the comparison RULE
    itself — in `_write_indicator_history`'s real conflict check — is generic over the payload's field set,
    not special-cased to `ema_200`: here `rsi_14` is the varied field. `inds` is deliberately crafted (as
    items 11/12 below also do) with every OTHER field held at its real, already-populated computed value,
    so only `rsi_14`'s null->populated transition is exercised, isolating the rule under test."""
    r = FakeRedis()
    _seed_h4_backward(r, 210)
    candles = steps._read_history(r, "H4", steps.EMA_TREND_WINDOW)
    vot = _vot_of(candles[-1])
    real_inds = steps._compute_indicators(candles)
    assert real_inds["rsi_14"] is not None

    inds_null_rsi = dict(real_inds)
    inds_null_rsi["rsi_14"] = None   # simulate: rsi_14 not yet computable for this bar
    steps._write_indicator_history(r, tf="H4", now=steps._now(), vot=vot, inds=inds_null_rsi,
                                    freshness_state="FRESH")
    key = indh.history_key(INST, "H4", int(vot.timestamp()))
    assert json.loads(r.kv[key])["indicators"]["rsi_14"] is None

    # recomputation: rsi_14 becomes populated, every OTHER field unchanged -> must SUCCEED
    steps._write_indicator_history(r, tf="H4", now=steps._now(), vot=vot, inds=real_inds, freshness_state="FRESH")
    assert json.loads(r.kv[key])["indicators"]["rsi_14"] == real_inds["rsi_14"]


# --------------------------------------------------------------------------- item 9 (regression)
def test_09_identity_mismatch_cannot_overwrite_another_bar():
    r = FakeRedis()
    _seed_h4_backward(r, 25)
    all_candles = steps._read_history(r, "H4", steps.EMA_TREND_WINDOW)
    bar_a = all_candles              # latest bar = ANCHOR_EPOCH
    bar_b = all_candles[:-1]         # one bar earlier -> a DIFFERENT open_epoch/key
    _, inds_a, key_a = _write(r, bar_a)
    _, inds_b, key_b = _write(r, bar_b)
    assert key_a != key_b
    assert json.loads(r.kv[key_a])["indicators"] == inds_a
    assert json.loads(r.kv[key_b])["indicators"] == inds_b   # writing bar_b never touched bar_a's record
    # re-writing bar_a again must still succeed (its own key, unaffected by bar_b's existence)
    _write(r, bar_a)


# --------------------------------------------------------------------------- item 10 (regression)
def test_10_malformed_invalid_payload_remains_rejected():
    r = FakeRedis()
    _seed_h4_backward(r, 20)
    candles = steps._read_history(r, "H4", steps.EMA_TREND_WINDOW)
    vot = _vot_of(candles[-1])
    bad_inds = {"ema_12": 1.0, "risk_score": 5}   # forbidden field token -> existing contract validation rejects
    with pytest.raises(ValueError):
        steps._write_indicator_history(r, tf="H4", now=steps._now(), vot=vot, inds=bad_inds,
                                        freshness_state="FRESH")


# --------------------------------------------------------------------------- item 11 (must NOT be weakened)
def test_11_populated_value_changing_to_different_populated_value_still_fails_loud():
    r = FakeRedis()
    _seed_h4_backward(r, 210)
    candles = steps._read_history(r, "H4", steps.EMA_TREND_WINDOW)
    vot = _vot_of(candles[-1])
    inds1 = steps._compute_indicators(candles)
    assert inds1["ema_200"] is not None
    steps._write_indicator_history(r, tf="H4", now=steps._now(), vot=vot, inds=inds1, freshness_state="FRESH")

    inds2 = dict(inds1)
    inds2["ema_200"] = round(inds1["ema_200"] + 1.2345, 6)   # genuinely different populated value
    with pytest.raises(ValueError, match="GOV-HERMES-IND-HIST-020"):
        steps._write_indicator_history(r, tf="H4", now=steps._now(), vot=vot, inds=inds2, freshness_state="FRESH")


# --------------------------------------------------------------------------- item 12 (must NOT be weakened)
def test_12_populated_value_regressing_to_null_still_fails_loud():
    r = FakeRedis()
    _seed_h4_backward(r, 210)
    candles = steps._read_history(r, "H4", steps.EMA_TREND_WINDOW)
    vot = _vot_of(candles[-1])
    inds1 = steps._compute_indicators(candles)
    assert inds1["ema_200"] is not None
    steps._write_indicator_history(r, tf="H4", now=steps._now(), vot=vot, inds=inds1, freshness_state="FRESH")

    inds2 = dict(inds1)
    inds2["ema_200"] = None   # regression back to null for an already-populated field
    with pytest.raises(ValueError, match="GOV-HERMES-IND-HIST-020"):
        steps._write_indicator_history(r, tf="H4", now=steps._now(), vot=vot, inds=inds2, freshness_state="FRESH")


# --------------------------------------------------------------------------- WO §7 — mandatory R2D2 regression
def test_r2d2_regression_same_bar_ema200_null_to_populated_end_to_end():
    """The exact production-activation-blocking sequence (WO §7), step-numbered, exercising the REAL
    `_compute_indicators()`/`_write_indicator_history()` end-to-end — never a reimplementation or a mock of
    the comparison logic alone."""
    r = FakeRedis()

    # (1) H4 history is insufficient for ema_200 (fixture: fewer than 200 usable closes in the deep window).
    _seed_h4_backward(r, 69)
    shallow = steps._read_history(r, "H4", steps.EMA_TREND_WINDOW)
    assert len(shallow) == 69

    # (2) The same H4 bar is evaluated and its historical indicator snapshot is written with ema_200=null.
    inds_shallow = steps._compute_indicators(shallow)   # real, unmodified
    assert inds_shallow["ema_200"] is None
    vot = _vot_of(shallow[-1])
    steps._write_indicator_history(r, tf="H4", now=steps._now(), vot=vot, inds=inds_shallow,
                                    freshness_state="FRESH")
    key = indh.history_key(INST, "H4", int(vot.timestamp()))
    assert json.loads(r.kv[key])["indicators"]["ema_200"] is None

    # (3) Governed missing H4 history is restored (fixture: the deep window now has >= 200 usable closes).
    _seed_h4_backward(r, 210)
    deep = steps._read_history(r, "H4", steps.EMA_TREND_WINDOW)
    assert len(deep) == 210
    assert deep[-1] == shallow[-1]   # identically the SAME closed bar

    # (4)/(5) The same H4 bar is recomputed by the real governed indicator implementation (unmodified);
    # ema_200 becomes populated in the freshly computed result.
    inds_deep = steps._compute_indicators(deep)
    assert inds_deep["ema_200"] is not None

    # (6) The same historical key is written again with this new result — the write SUCCEEDS.
    # (7) No GOV-HERMES-IND-HIST-020 / ValueError is raised (asserted implicitly: no exception propagates).
    steps._write_indicator_history(r, tf="H4", now=steps._now(), vot=vot, inds=inds_deep, freshness_state="FRESH")
    stored = json.loads(r.kv[key])
    assert stored["indicators"]["ema_200"] == inds_deep["ema_200"]

    # (8) A subsequent equivalent replay (writing the same now-populated result again) remains idempotent.
    steps._write_indicator_history(r, tf="H4", now=steps._now(), vot=vot, inds=inds_deep, freshness_state="FRESH")
    assert json.loads(r.kv[key])["indicators"]["ema_200"] == inds_deep["ema_200"]
