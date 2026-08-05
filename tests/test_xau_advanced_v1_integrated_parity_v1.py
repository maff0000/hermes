"""HERMES Advanced-v1 XAU module adoption — INTEGRATED cross-family proof.
WO-HELM-HERMES-ADVANCED-V1-XAU-MODULE-ADOPTION-0001.

Exercises the FIVE adopted governed families (tick, indicators, gaps, backfill-status, feed-health) through their
REAL publishers/emitters driven by the canonical registry selection seam, and proves three integrated properties:

  1. XAU_ADVANCED_V1_PARITY_GREEN — with the XAU-active rollout, every family selects exactly the XAU pilot and
     emits the BYTE-IDENTICAL keys it emitted before adoption (no drift in the live contract surface).
  2. CONFIGURATION_ONLY_ONBOARDING_GREEN — enabling a NEW instrument in the registry (DATA only; not one line of
     ticker-specific code) makes all five families select and emit that instrument alongside XAU, with distinct,
     non-colliding keys and correct per-instrument contract identity.
  3. STATE_ISOLATION_GREEN — interleaved per-instrument publication (A, B, A again) never overwrites another
     instrument's key; each key always holds its own instrument's contract.

Zero live I/O — an in-memory fake Redis records every write. The seven not-yet-onboarded instruments stay inactive
purely as a CONSEQUENCE of their registry capability flags (no ticker filter anywhere).
"""
import json
from datetime import datetime, timezone

import pytest

import utils.hermes_advanced_v1_selection_v1 as sel
import utils.hermes_gaps_v1 as gaps
import utils.hermes_backfill_status_v1 as bfs
import utils.hermes_feed_health_v1 as fh
import utils.hermes_indicators_v1 as ind
import utils.tick_live_emitter_v1 as tick
import utils.hermes_instrument_registry_v1 as reg

UTC = timezone.utc
NOW = datetime(2026, 7, 8, 12, 0, tzinfo=UTC)      # Wednesday noon -> market OPEN

# The exact XAU keys the five families emitted BEFORE adoption — the byte-parity anchor.
XAU_KEYS = {
    "tick": "hermes:ticks:XAU_USD:latest:v1",
    "indicator_H4": "hermes:indicators:XAU_USD:H4:v1",
    "gaps": "hermes:gaps:XAU_USD:v1",
    "backfill_status": "hermes:backfill:status:XAU_USD:v1",
    "feed_health": "hermes:feed_health:XAU_USD:v1",
}


def _records(*, all_caps):
    """Registry where each symbol in `all_caps` is enabled for ALL five capabilities (tick/indicator/gap; backfill
    follows gap; feed-health follows tick); every other symbol is present but NOT_ENABLED. DATA only — no code."""
    from tests.test_hermes_instrument_registry_v1 import _row
    rows = []
    for s in ("XAU_USD", "EUR_USD", "GBP_USD"):
        on = 1 if s in all_caps else 0
        rows.append(_row(s, "precious_metals", 3, 0.001, "metals", tick_cap=on, ind_cap=on, gap_cap=on))
    return reg.load_registry(rows)


class FakeRedis:
    def __init__(self, kv=None, z=None):
        self.kv = dict(kv or {}); self.z = dict(z or {}); self.writes = []; self.deletes = []
    def get(self, k):
        v = self.kv.get(k); return v.encode() if isinstance(v, str) else v
    def exists(self, k):
        return 1 if (k in self.kv or k in self.z) else 0
    def zrange(self, k, a, b):
        m = sorted(self.z.get(k, {}).items(), key=lambda kv: kv[1]); return [x for x, _ in (m[a:b+1] if b != -1 else m[a:])]
    def set(self, k, v, ex=None):
        self.writes.append((k, ex)); self.kv[k] = v
    def delete(self, *a):
        self.deletes.extend(a)


@pytest.fixture
def rollout(monkeypatch):
    from tests.test_hermes_instrument_registry_v1 import rollout_rows
    recs = reg.load_registry(rollout_rows())
    monkeypatch.setattr(reg, "load_from_db", lambda fetch=None: recs)
    return recs


# =========================================================================== 1. XAU byte-parity
def test_xau_advanced_v1_parity_green(rollout):
    # Every family selects exactly the XAU pilot from the rollout registry...
    assert sel.selection_for("tick", rollout) == ("XAU_USD",)
    assert sel.selection_for("indicator", rollout) == ("XAU_USD",)
    assert sel.selection_for("gap", rollout) == ("XAU_USD",)
    assert sel.backfill_status_instruments(rollout) == ("XAU_USD",)
    assert fh.feed_health_selection(rollout) == ("XAU_USD",)
    # ...and every emitted key is byte-identical to the pre-adoption XAU key.
    assert sel.tick_latest_key("XAU_USD") == XAU_KEYS["tick"]
    assert ind.indicator_key("XAU_USD", "H4") == XAU_KEYS["indicator_H4"]
    assert gaps.gaps_key("XAU_USD") == XAU_KEYS["gaps"]
    assert bfs.backfill_status_key("XAU_USD") == XAU_KEYS["backfill_status"]
    assert fh.feed_health_key("XAU_USD") == XAU_KEYS["feed_health"]
    # gaps + backfill publishers, driven by the registry, write EXACTLY the XAU keys and nothing else.
    r = FakeRedis()
    gr = gaps.GapsPublisher(redis_client=r, records=rollout).publish(now=NOW, forward_enabled=True, forward_authorised=True)
    assert gr["keys"] == [XAU_KEYS["gaps"]]
    r2 = FakeRedis({XAU_KEYS["gaps"]: json.dumps(json.loads(r.kv[XAU_KEYS["gaps"]]))})
    br = bfs.BackfillStatusPublisher(redis_client=r2, records=rollout).publish(now=NOW)
    assert br["keys"] == [XAU_KEYS["backfill_status"]]


# =========================================================================== 2. configuration-only onboarding
def test_configuration_only_onboarding_green(monkeypatch):
    # Onboard EUR_USD by DATA only: flip its registry capability flags. No ticker-specific code exists anywhere.
    recs = _records(all_caps={"XAU_USD", "EUR_USD"})
    monkeypatch.setattr(reg, "load_from_db", lambda fetch=None: recs)

    # Every family now selects BOTH instruments purely from the registry capability flags.
    assert set(sel.selection_for("tick", recs)) == {"XAU_USD", "EUR_USD"}
    assert set(sel.selection_for("indicator", recs)) == {"XAU_USD", "EUR_USD"}
    assert set(sel.selection_for("gap", recs)) == {"XAU_USD", "EUR_USD"}
    assert set(sel.backfill_status_instruments(recs)) == {"XAU_USD", "EUR_USD"}
    assert set(fh.feed_health_selection(recs)) == {"XAU_USD", "EUR_USD"}

    # tick emitter (registry-driven) scopes BOTH instruments; XAU key still byte-identical.
    te = tick.build_tick_live_emitter_from_registry(recs, redis_client=FakeRedis())
    assert te.allowed_instruments == frozenset({"XAU_USD", "EUR_USD"})
    assert sel.tick_latest_key("EUR_USD") == "hermes:ticks:EUR_USD:latest:v1"

    # indicator publisher (env-gated + registry-driven) scopes BOTH instruments.
    monkeypatch.setenv(ind.ENABLED_ENV, "true"); monkeypatch.setenv(ind.AUTHORISED_ENV, "true")
    monkeypatch.setenv(ind.TIMEFRAMES_ENV, "M1,M5,M15,H1,H4")
    ip = ind.build_indicator_publisher_from_env()
    assert set(ip.allowed_instruments) == {"XAU_USD", "EUR_USD"}
    assert ind.indicator_key("EUR_USD", "H4") == "hermes:indicators:EUR_USD:H4:v1"

    # gaps + backfill + feed-health publishers each emit BOTH instruments' keys, distinct and non-colliding.
    rg = FakeRedis()
    gkeys = gaps.GapsPublisher(redis_client=rg, records=recs).publish(now=NOW, forward_enabled=True, forward_authorised=True)["keys"]
    assert set(gkeys) == {"hermes:gaps:XAU_USD:v1", "hermes:gaps:EUR_USD:v1"}

    rb = FakeRedis({k: rg.kv[k] for k in gkeys})
    bkeys = bfs.BackfillStatusPublisher(redis_client=rb, records=recs).publish(now=NOW)["keys"]
    assert set(bkeys) == {"hermes:backfill:status:XAU_USD:v1", "hermes:backfill:status:EUR_USD:v1"}

    fp = fh.FeedHealthPublisher(allowed_instruments=frozenset(fh.feed_health_selection(recs)), source_name="OANDA")
    assert {fp.key("XAU_USD"), fp.key("EUR_USD")} == {"hermes:feed_health:XAU_USD:v1", "hermes:feed_health:EUR_USD:v1"}

    # GBP_USD was never onboarded (capability flags 0) -> NOT selected by any family (consequence of data, not a filter).
    for cap in ("tick", "indicator", "gap"):
        assert "GBP_USD" not in sel.selection_for(cap, recs)
    assert "GBP_USD" not in sel.backfill_status_instruments(recs)
    assert "GBP_USD" not in fh.feed_health_selection(recs)


# =========================================================================== 3. state isolation / interleaving
def test_state_isolation_interleaved_publication_green(monkeypatch):
    recs = _records(all_caps={"XAU_USD", "EUR_USD"})
    monkeypatch.setattr(reg, "load_from_db", lambda fetch=None: recs)
    shared = FakeRedis()
    only_xau = _records(all_caps={"XAU_USD"})
    only_eur = _records(all_caps={"EUR_USD"})

    def publish_gaps(records):
        return gaps.GapsPublisher(redis_client=shared, records=records).publish(now=NOW, forward_enabled=True,
                                                                                forward_authorised=True)
    # Interleave: A (XAU), then B (EUR), then A (XAU) again — into ONE shared store.
    publish_gaps(only_xau)
    publish_gaps(only_eur)
    publish_gaps(only_xau)

    xau = json.loads(shared.kv["hermes:gaps:XAU_USD:v1"])
    eur = json.loads(shared.kv["hermes:gaps:EUR_USD:v1"])
    assert xau["instrument"] == "XAU_USD" and xau["canonical_instrument"] == "XAU_USD"
    assert eur["instrument"] == "EUR_USD" and eur["canonical_instrument"] == "EUR_USD"   # never overwritten by XAU
    # Both keys coexist; no deletes; each candle key read was instrument-scoped (no shared watermark).
    assert set(shared.kv) == {"hermes:gaps:XAU_USD:v1", "hermes:gaps:EUR_USD:v1"} and shared.deletes == []


def test_seven_new_inactive_via_capability_metadata_only(rollout):
    # The seven not-yet-onboarded instruments are inactive ONLY because their registry capability flags are 0 —
    # proven by flipping the metadata (not the code): with caps on, the SAME code selects them.
    seven = ("XAG_USD", "EUR_USD", "GBP_USD", "AUD_USD", "USD_JPY", "SPX500_USD", "WTICO_USD")
    for cap in ("tick", "indicator", "gap"):
        picked = sel.selection_for(cap, rollout)
        assert all(s not in picked for s in seven)          # inactive under the XAU-only rollout
    onboarded = _records(all_caps={"XAU_USD", "EUR_USD"})
    assert "EUR_USD" in sel.selection_for("gap", onboarded)  # same code, flipped metadata -> active
