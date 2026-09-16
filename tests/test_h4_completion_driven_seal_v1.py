"""WO-HELM-HERMES-H4-COMPLETION-DRIVEN-SEAL-0001.

A completed H4 candle must become publishable the moment its 4th genuine H1 child closes — not ~1h later
when the next bucket's first H1 happens to arrive (the real-world symptom: the 06:00-10:00 H4 bucket sat
genuinely complete in memory from 10:00Z onward but never sealed until 11:00Z). This file proves the fix's
required invariants directly against the real CanonicalH4Producer (no second implementation).
"""
import json
from datetime import datetime, timedelta, timezone

import utils.candle_h4_publish_wire_v1 as wire
import utils.candle_publisher_v1 as cp
import utils.candle_contract_v1 as cc

UTC = timezone.utc
_BO = datetime(2026, 6, 2, 2, 0, tzinfo=UTC)


class FakeRedis:
    def __init__(self): self.store = {}; self.sets = []
    def get(self, k): return self.store.get(k, (None, None))[0]
    def set(self, k, v, ex=None):
        assert isinstance(v, (str, bytes)); self.store[k] = (v, ex); self.sets.append((k, v, ex)); return True


class _H1:
    """A completed, status-OK H1 child by default (same shape test_candle_h4_hydration_v1.py uses) — hydrate()'s
    eligibility check requires status/is_closed/source_count/coverage/gap_state, unlike the plain on_h1_close path."""
    def __init__(self, open_dt, o=2000.0, hi=2008.0, lo=1998.0, c=2004.0, v=10, instrument="XAU_USD",
                 status="OK", is_closed=True, source_count=1, expected_source_count=1,
                 source_coverage=1.0, gap_state="NONE"):
        self.instrument = instrument; self.timeframe = "H1"; self.timestamp = open_dt
        self.open, self.high, self.low, self.close, self.volume = o, hi, lo, c, v
        self.status, self.is_closed = status, is_closed
        self.source_count, self.expected_source_count = source_count, expected_source_count
        self.source_coverage, self.gap_state = source_coverage, gap_state


def _cfg():
    return cp.CandlePublisherConfig(publish_enabled=True, publish_authorised=True, shadow_publish_enabled=False,
                                    shadow_authorised=False, namespace="hermes", contract_version="v1",
                                    redis_host="192.168.11.10", redis_port=6379, redis_db=0)


def _producer(client=None, allowed=("XAU_USD",)):
    w = cp.SerializingCandleCanonicalWriter(config=_cfg(), redis_client=client or FakeRedis())
    return wire.CanonicalH4Producer(w, allowed_instruments=allowed)


# --------------------------------------------------------------------------- 1/4, 2/4, 3/4 -> no seal
def test_1of4_no_seal():
    p = _producer()
    res = p.on_h1_close(_H1(_BO))
    assert res["published"] is False and res["reason"] == "BUFFERED"


def test_2of4_no_seal():
    p = _producer()
    p.on_h1_close(_H1(_BO))
    res = p.on_h1_close(_H1(_BO + timedelta(hours=1)))
    assert res["published"] is False and res["reason"] == "BUFFERED"


def test_3of4_no_seal():
    p = _producer()
    p.on_h1_close(_H1(_BO)); p.on_h1_close(_H1(_BO + timedelta(hours=1)))
    res = p.on_h1_close(_H1(_BO + timedelta(hours=2)))
    assert res["published"] is False and res["reason"] == "BUFFERED"


# --------------------------------------------------------------------------- 4/4 -> immediate single seal
def test_4of4_seals_immediately_no_rollover_needed():
    r = FakeRedis(); p = _producer(r)
    for hh in range(3):
        p.on_h1_close(_H1(_BO + timedelta(hours=hh)))
    assert r.sets == []                                  # nothing written yet (genuinely incomplete)
    res = p.on_h1_close(_H1(_BO + timedelta(hours=3)))    # the 4th genuine child
    assert res["published"] is True and res["status"] == "OK"
    assert res["source_count"] == 4 and res["gap_state"] == "NONE"
    env = json.loads(r.store["hermes:candles:XAU_USD:H4:latest:v1"][0])
    assert env["provenance"]["derivation"] == cc.DERIVATION_DERIVED
    assert env["provenance"]["source_timeframe"] == "H1"
    assert env["data"]["derivation_policy"] == cc.DERIVATION_POLICY_H4_FROM_H1
    # correct grid/open/close: bucket open 02:00, close (next anchor) 06:00
    assert env["data"]["timestamp_utc"] == "2026-06-02T02:00:00.000Z"


# --------------------------------------------------------------------------- duplicate event -> no duplicate publish
def test_duplicate_final_child_event_no_duplicate_publication():
    r = FakeRedis(); p = _producer(r)
    for hh in range(3):
        p.on_h1_close(_H1(_BO + timedelta(hours=hh)))
    fourth = _H1(_BO + timedelta(hours=3))
    res1 = p.on_h1_close(fourth)
    assert res1["published"] is True and res1["status"] == "OK"
    r.sets.clear()
    res2 = p.on_h1_close(fourth)                          # the SAME close delivered again (replay/duplicate)
    assert res2["published"] is False                     # re-buffers into a fresh (post-seal) slot but never
    assert r.sets == []                                   # re-triggers a seal — no second write, no duplication
    assert p.metrics["h4_published_ok"] == 1

def test_duplicate_of_all_four_children_still_seals_exactly_once():
    # a full replay of all 4 children (e.g. an at-least-once redelivery of the whole batch) must still
    # only ever produce ONE OK publish for that bucket_epoch.
    r = FakeRedis(); p = _producer(r)
    for hh in range(4):
        p.on_h1_close(_H1(_BO + timedelta(hours=hh)))
    assert p.metrics["h4_published_ok"] == 1
    r.sets.clear()
    for hh in range(4):
        p.on_h1_close(_H1(_BO + timedelta(hours=hh)))   # full replay of the same 4 closes
    assert r.sets == []
    assert p.metrics["h4_published_ok"] == 1


# --------------------------------------------------------------------------- rollover after already-sealed -> no corruption
def test_next_bucket_rollover_after_completion_seal_does_not_corrupt():
    r = FakeRedis(); p = _producer(r)
    for hh in range(4):
        p.on_h1_close(_H1(_BO + timedelta(hours=hh)))     # seals on the 4th (hh=3)
    good = json.loads(r.store["hermes:candles:XAU_USD:H4:latest:v1"][0])
    assert good["data"]["source_count"] == 4 and good["status"] == "OK"
    r.sets.clear()
    res = p.on_h1_close(_H1(_BO + timedelta(hours=4)))    # 06:00 -> next bucket's first child (the old rollover trigger)
    assert res["reason"] == "ALREADY_SEALED"
    assert r.sets == []                                   # the good candle was NOT overwritten with an empty one
    still_there = json.loads(r.store["hermes:candles:XAU_USD:H4:latest:v1"][0])
    assert still_there == good


# --------------------------------------------------------------------------- incomplete/gapped set never manufactures H4
def test_incomplete_set_at_rollover_never_manufactured_as_ok():
    r = FakeRedis(); p = _producer(r)
    for hh in range(2):                                   # only 2/4 ever arrive (genuine gap)
        p.on_h1_close(_H1(_BO + timedelta(hours=hh)))
    res = p.on_h1_close(_H1(_BO + timedelta(hours=4)))     # rollover forces the honest seal attempt
    assert res["published"] is True and res["status"] != "OK"
    assert res["source_count"] == 2 and res["gap_state"] == "INCOMPLETE"


# --------------------------------------------------------------------------- restart/hydration determinism
def test_hydration_then_live_completion_seals_deterministically_once():
    # Simulates a restart mid-bucket: hydrate() warm-starts 3 already-closed children from Redis history
    # (exactly as candle_h4_hydration_v1.warmstart_h4_from_env does at boot), then the live 4th child
    # arrives via on_h1_close — must seal exactly once, with exactly the 4 real children, no duplication.
    r = FakeRedis(); p = _producer(r)
    hydrated = [_H1(_BO + timedelta(hours=hh)) for hh in range(3)]
    report = p.hydrate(hydrated, now=_BO + timedelta(hours=3, minutes=30))
    assert report["succeeded"] is True and report["buffer_length"] == 3
    assert report["h4_status_after_hydration"] == "AWAITING_LIVE_H1"
    res = p.on_h1_close(_H1(_BO + timedelta(hours=3)))     # the live 4th child
    assert res["published"] is True and res["status"] == "OK" and res["source_count"] == 4
    assert p.metrics["h4_published_ok"] == 1
    # a further duplicate/rollover event must still be a safe no-op after hydration + live completion
    res2 = p.on_h1_close(_H1(_BO + timedelta(hours=4)))
    assert res2["reason"] == "ALREADY_SEALED"
    assert p.metrics["h4_published_ok"] == 1
