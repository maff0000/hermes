"""Isolated EIGHT-INSTRUMENT Advanced-v1 shadow exercise — all five governed families driven generically over the
full initial cohort by registry capability flags. WO-HELM-HERMES-ADVANCED-V1-EIGHT-INSTRUMENT-SHADOW-0001.

Pure, in-memory, zero live I/O (a fake Redis records every write). Deterministic captured/generated input. No trader
consumer, no order consumer, no production keys, no production SQL, no second stream, no autonomous backfill. Every
instrument is processed by the SAME generic code path — no ticker-specific branch, no per-ticker publisher/state/key.

Cohort (eight) = XAU_USD, XAG_USD, EUR_USD, GBP_USD, AUD_USD, USD_JPY, SPX500_USD, WTICO_USD.
Non-cohort (six) = XPT_USD, XCU_USD, USD_CHF, USD_CAD, NZD_USD, EUR_GBP  (registry-present, Advanced-v1 NOT_ENABLED).

Outputs are ISOLATED SHADOW EVIDENCE — never canonical/production-live.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

import utils.hermes_instrument_registry_v1 as reg
import utils.hermes_advanced_v1_selection_v1 as sel
import utils.tick_live_emitter_v1 as ticke
import utils.hermes_indicators_v1 as ind
import utils.hermes_gaps_v1 as gaps
import utils.hermes_backfill_status_v1 as bfs
import utils.hermes_feed_health_v1 as fh
import utils.candle_contract_v1 as cc
import utils.tick_contract_v1 as tc

UTC = timezone.utc
NOW = datetime(2026, 7, 8, 12, 0, tzinfo=UTC)          # Wednesday noon UTC -> common open window for the cohort
TFS = ("M1", "M5", "M15", "H1", "H4")                   # D1 stays GATED until D1-latest green (asserted separately)

COHORT = ("XAU_USD", "XAG_USD", "EUR_USD", "GBP_USD", "AUD_USD", "USD_JPY", "SPX500_USD", "WTICO_USD")
NON_COHORT = ("XPT_USD", "XCU_USD", "USD_CHF", "USD_CAD", "NZD_USD", "EUR_GBP")
ALL14 = COHORT + NON_COHORT

# Representative per-instrument metadata (mirrors migration 025 seed + activation). Category/policy/precision are DATA.
_META = {
    "XAU_USD": ("precious_metals", "metals", 3, 0.001), "XAG_USD": ("precious_metals", "metals", 4, 0.0001),
    "XPT_USD": ("precious_metals", "metals", 2, 0.01),   "XCU_USD": ("base_metals", "metals", 4, 0.0001),
    "EUR_USD": ("forex_major", "fx_24x5", 5, 0.00001),   "GBP_USD": ("forex_major", "fx_24x5", 5, 0.00001),
    "USD_JPY": ("forex_major", "fx_24x5", 3, 0.001),     "USD_CHF": ("forex_major", "fx_24x5", 5, 0.00001),
    "USD_CAD": ("forex_major", "fx_24x5", 5, 0.00001),   "AUD_USD": ("forex_major", "fx_24x5", 5, 0.00001),
    "NZD_USD": ("forex_major", "fx_24x5", 5, 0.00001),   "EUR_GBP": ("forex_minor", "fx_24x5", 5, 0.00001),
    "SPX500_USD": ("indices", "index_cash", 1, 0.1),     "WTICO_USD": ("energy", "energy", 3, 0.001),
}


def _row(symbol, *, caps):
    cat, mhp, prec, tick = _META[symbol]
    return {"symbol": symbol, "broker_symbol": symbol, "name": symbol, "category": cat,
            "enabled": 1, "oanda_compatible": 1, "price_precision": prec, "tick_size": tick,
            "price_authority": "mid", "market_hours_policy": mhp, "expected_freshness_sec": 120,
            "enabled_timeframes": json.dumps(["M1", "M5", "M15", "H1", "H4", "D1"]),
            "indicator_profile": "standard_v1", "tick_contract_enabled": caps,
            "indicator_contract_enabled": caps, "gap_detection_enabled": caps,
            "backfill_policy": "status_only", "retention_policy": "default_v1", "metadata_version": "v1"}


def records(cohort=COHORT):
    """The full 14-instrument registry; `cohort` members get all Advanced-v1 caps, the rest are NOT_ENABLED."""
    return reg.load_registry([_row(s, caps=(1 if s in cohort else 0)) for s in ALL14])


class FakeRedis:
    """In-memory isolated store — records every write so tests prove no collision and no unexpected mutation."""
    def __init__(self):
        self.kv = {}; self.z = {}; self.sets = []; self.deletes = []
    def get(self, k):
        v = self.kv.get(k); return v.encode() if isinstance(v, str) else v
    def set(self, k, v, ex=None):
        self.sets.append((k, ex)); self.kv[k] = v; return True
    def exists(self, k):
        return 1 if (k in self.kv or k in self.z) else 0
    def zrange(self, k, a, b):
        m = sorted(self.z.get(k, {}).items(), key=lambda kv: kv[1]); return [x for x, _ in (m[a:b + 1] if b != -1 else m[a:])]
    def zadd(self, k, m):
        self.z.setdefault(k, {}).update(m)
    def delete(self, *a):
        self.deletes.extend(a)


def _seed_candles(r, instrument, *, tfs=TFS, now=NOW, fresh=True):
    """Seed a governed candle latest envelope + a complete open-market history index for `instrument` per tf."""
    for tf in tfs:
        span = cc.TF_SECONDS[tf]
        open_dt = now - timedelta(seconds=span if fresh else span * 6)   # fresh: last closed candle; stale: 6 spans old
        env = cc.build_candle_contract(instrument=instrument, timeframe=tf, timestamp_utc=open_dt,
                                       ohlc={"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 10},
                                       is_closed=True, generated_at_utc=now, source_timeframe=tf, source_count=1,
                                       expected_source_count=1, derivation=cc.DERIVATION_DIRECT,
                                       derivation_policy=cc.DERIVATION_POLICY_DIRECT, source_policy_epoch="DIRECT_NATIVE_V1")
        r.kv[f"hermes:candles:{instrument}:{tf}:latest:v1"] = json.dumps(env)
        idx = f"hermes:candles:{instrument}:{tf}:history:v1:index"
        # a bounded complete history back over the retention floor so gaps classify without a false GAPS_FOUND
        floor = int(now.timestamp()) - gaps.RETENTION_DAYS[tf] * 86400
        for o in gaps._grid_opens(tf, floor - gaps._OOR_LOOKBACK_PERIODS * span, int(now.timestamp())):
            if o >= floor and gaps._period_fully_open(o, tf):
                r.z.setdefault(idx, {})[str(o)] = o


def _tick(instrument, *, bid=100.0, ask=100.2, now=NOW):
    return {"instrument": instrument, "bid": bid, "ask": ask, "source_received_at_utc": now - timedelta(seconds=1)}


# =========================================================================== selection / cohort
def test_exact_eight_capability_selection_and_six_not_enabled():
    recs = records()
    for cap in ("tick", "indicator", "gap"):
        assert set(sel.selection_for(cap, recs)) == set(COHORT), cap
    assert set(sel.backfill_status_instruments(recs)) == set(COHORT)
    assert set(fh.feed_health_selection(recs)) == set(COHORT)
    for cap in ("tick", "indicator", "gap"):
        assert all(s not in sel.selection_for(cap, recs) for s in NON_COHORT)


# =========================================================================== 10. tick shadow
def test_tick_shadow_eight_generic():
    recs = records(); r = FakeRedis()
    em = ticke.build_tick_live_emitter_from_registry(recs, redis_client=r)
    assert em.allowed_instruments == frozenset(COHORT)
    for s in COHORT:
        res = em.emit_tick(_tick(s), now=NOW)
        assert res["emitted"] and res["key"] == f"hermes:ticks:{s}:latest:v1"
    assert em.emit_tick(_tick("XAUUSD"), now=NOW)["emitted"] is False   # alias never in scope -> skipped
    keys = [k for k, _ in r.sets]
    assert sorted(keys) == sorted(f"hermes:ticks:{s}:latest:v1" for s in COHORT)   # 8 distinct, no collision
    for s in COHORT:
        env = json.loads(r.kv[f"hermes:ticks:{s}:latest:v1"])
        d = env["data"]
        assert d["instrument"] == s and d["bid"] == 100.0 and d["ask"] == 100.2
        assert d["mid"] == pytest.approx(100.1) and d["spread"] == pytest.approx(0.2)
        assert ":XAUUSD:" not in env["key"]


# =========================================================================== 11. indicator shadow
def test_indicator_shadow_eight_across_timeframes(monkeypatch):
    recs = records()
    monkeypatch.setattr(reg, "load_from_db", lambda fetch=None: recs)
    monkeypatch.setenv(ind.ENABLED_ENV, "true"); monkeypatch.setenv(ind.AUTHORISED_ENV, "true")
    monkeypatch.setenv(ind.TIMEFRAMES_ENV, ",".join(TFS))
    pub = ind.build_indicator_publisher_from_env()
    assert set(pub.allowed_instruments) == set(COHORT)
    sample = {"ema_12": 100.4, "ema_26": 100.2, "ema_50": 100.1, "rsi_14": 55.0, "atr_14": 1.2,
              "bb_mid": 100.3, "bb_upper": 101.0, "bb_lower": 99.6, "adx_14": 22.0, "plus_di_14": 25.0, "minus_di_14": 18.0}
    keys = set()
    for s in COHORT:
        for tf in TFS:
            c = ind.build_indicator_contract(instrument=s, timeframe=tf, generated_at_utc=NOW,
                                             value_open_time_utc=NOW - timedelta(seconds=cc.TF_SECONDS[tf]), indicators=sample)
            assert c["instrument"] == s and ind.indicator_key(s, tf) == f"hermes:indicators:{s}:{tf}:v1"
            assert ind.validate_indicator_contract(c) is True
            keys.add(ind.indicator_key(s, tf))
    assert len(keys) == len(COHORT) * len(TFS)         # 8 x 5 = 40 distinct keys, no collision


# =========================================================================== 12 & 13. gaps + backfill shadow
def test_gaps_and_backfill_shadow_eight_generic():
    recs = records(); r = FakeRedis()
    for s in COHORT:
        _seed_candles(r, s)
    gres = gaps.GapsPublisher(redis_client=r, records=recs).publish(now=NOW, forward_enabled=False, forward_authorised=False)
    assert set(gres["keys"]) == {f"hermes:gaps:{s}:v1" for s in COHORT}       # 8 gaps keys, one per instrument
    for s in COHORT:
        c = json.loads(r.kv[f"hermes:gaps:{s}:v1"])
        assert c["instrument"] == s and gaps.validate_gaps_contract(c) is True
        # complete fresh history in an open window -> no fabricated outage for any instrument (incl SPX500/WTICO)
        assert c["overall_gap_state"] in gaps.GAP_STATES
    # backfill-status projection reads each instrument's own gaps key
    bres = bfs.BackfillStatusPublisher(redis_client=r, records=recs).publish(now=NOW)
    assert set(bres["keys"]) == {f"hermes:backfill:status:{s}:v1" for s in COHORT}
    for s in COHORT:
        c = json.loads(r.kv[f"hermes:backfill:status:{s}:v1"])
        assert c["instrument"] == s and c["gaps_source"]["key"] == f"hermes:gaps:{s}:v1"
        assert c["execution_enabled"] is False and c["backfill_executed"] is False and c["repair_executed"] is False
        assert c["active_job"] is None and c["completed_pct"] is None
        assert bfs.validate_backfill_status_contract(c) is True


def test_no_backfill_executor_exists():
    src = "".join(open(m).read() for m in (bfs.__file__,))
    for tok in ("execute_backfill", "run_backfill", "seed_backfill(", "subprocess"):
        assert tok not in src


# =========================================================================== 14. feed-health 14-instrument enumeration
def test_feed_health_fourteen_instrument_enumeration():
    recs = records()
    pub = fh.FeedHealthPublisher(allowed_instruments=frozenset(fh.feed_health_selection(recs)), source_name="SHADOW_OANDA_REPLAY")
    for s in COHORT:
        assert pub.instrument_state(s) == fh.INSTRUMENT_ACTIVE
        assert pub.key(s) == f"hermes:feed_health:{s}:v1"
    for s in NON_COHORT:
        assert pub.instrument_state(s) == fh.INSTRUMENT_NOT_ENABLED   # NOT_ENABLED, not RED
    # per-instrument health payload builds generically for each cohort instrument
    r = FakeRedis()
    for s in COHORT:
        _seed_candles(r, s)
    for s in COHORT:
        snap = fh.collect_feed_health_snapshot(r, instrument=s, timeframes=("M1", "M5", "M15", "H1", "H4", "D1"),
                                                generated_at_utc=NOW, source_name="SHADOW_OANDA_REPLAY")
        p = fh.build_feed_health_contract(**snap)
        assert p["instrument"] == s and fh.validate_feed_health_contract(p) is True
        assert p["per_timeframe_health"]["D1"]["status"] == fh.STATUS_GATED   # D1 gated (no D1 latest seeded)


# =========================================================================== 16. state isolation / interleaving
def test_eight_instrument_state_isolation_green():
    recs = records(); shared = FakeRedis()
    for s in COHORT:
        _seed_candles(shared, s)
    gp = gaps.GapsPublisher(redis_client=shared, records=recs)
    # deliberate wide interleave: publish A, B, C, then A again, then all — no shared watermark/state
    for order in (COHORT, tuple(reversed(COHORT)), (COHORT[0], COHORT[3], COHORT[0])):
        for s in order:
            gaps.GapsPublisher(redis_client=shared, records=records((s,))).publish(now=NOW)
    for s in COHORT:
        c = json.loads(shared.kv[f"hermes:gaps:{s}:v1"])
        assert c["instrument"] == s and c["canonical_instrument"] == s    # never overwritten by another instrument
    assert set(k for k in shared.kv if k.startswith("hermes:gaps:")) == {f"hermes:gaps:{s}:v1" for s in COHORT}
    assert shared.deletes == []


# =========================================================================== 17. fault isolation
def test_fault_isolation_matrix():
    recs = records(); r = FakeRedis()
    # all healthy except: EUR_USD stale (old candles), XAG_USD missing (no candles seeded)
    for s in COHORT:
        if s == "XAG_USD":
            continue                                   # missing source
        _seed_candles(r, s, fresh=(s != "EUR_USD"))    # EUR_USD stale
    gres = gaps.GapsPublisher(redis_client=r, records=recs).publish(now=NOW)
    assert set(gres["keys"]) == {f"hermes:gaps:{s}:v1" for s in COHORT}   # every instrument still produced a contract
    xag = json.loads(r.kv["hermes:gaps:XAG_USD:v1"])
    healthy = json.loads(r.kv["hermes:gaps:GBP_USD:v1"])
    assert xag["overall_gap_state"] == "SOURCE_MISSING"                   # faulty instrument isolated + fail-closed
    assert healthy["instrument"] == "GBP_USD"                             # unaffected instrument uncontaminated
    # registry-unavailable is a global fail-closed (not a silent GREEN)
    with pytest.raises(reg.RegistryError):
        reg.load_from_db(fetch=lambda: (_ for _ in ()).throw(RuntimeError("db down")))


# =========================================================================== 18. data-only activation
def test_data_only_activation_green():
    xau_only = records(("XAU_USD",))
    eight = records(COHORT)
    # the SAME code, only the registry data differs, moves selection from {XAU} to the full eight.
    assert set(sel.selection_for("gap", xau_only)) == {"XAU_USD"}
    assert set(sel.selection_for("gap", eight)) == set(COHORT)
    # no per-instrument code path: every cohort member flows through the identical publisher classes
    r = FakeRedis()
    for s in COHORT:
        _seed_candles(r, s)
    before = gaps.GapsPublisher(redis_client=r, records=xau_only).publish(now=NOW)["keys"]
    after = gaps.GapsPublisher(redis_client=r, records=eight).publish(now=NOW)["keys"]
    assert before == ["hermes:gaps:XAU_USD:v1"] and set(after) == {f"hermes:gaps:{s}:v1" for s in COHORT}


# =========================================================================== 20. contract validation + alias rejection
def test_alias_rejected_every_family():
    for fn in (lambda: gaps.gaps_key("XAUUSD") and gaps.build_gaps_contract(instrument="XAUUSD", timeframes={}, d1_boundary={}, generated_at_utc=NOW),
               lambda: bfs.build_backfill_status_contract(instrument="XAUUSD", gaps_contract=None, now=NOW),
               lambda: fh.feed_health_key("XAUUSD"),
               lambda: ind.indicator_key("XAUUSD", "H4")):
        with pytest.raises(ValueError):
            fn()


def test_market_hours_policy_metadata_consumed_by_gaps_CORRECTED():
    """CORRECTION LANDED: the generic gap classifier now resolves market-hours behaviour from the registry
    `market_hours_policy` key via the reusable mhp resolver — no uniform default, no ticker branch. Each cohort
    instrument's publisher-produced gaps contract records its governed policy key; the six non-cohort instruments still
    carry valid policy metadata. Pins the corrected behaviour so a regression to the uniform calendar is caught."""
    import utils.hermes_market_hours_policy_v1 as mhp
    recs = records()
    policies = {r.symbol: r.market_hours_policy for r in recs}
    assert policies["SPX500_USD"] == "index_cash" and policies["WTICO_USD"] == "energy"
    assert policies["EUR_USD"] == "fx_24x5" and policies["XAU_USD"] == "metals"
    # the gap publisher resolves and records the governed policy key per instrument (metadata-driven, not uniform)
    r = FakeRedis()
    for s in COHORT:
        _seed_candles(r, s)
    gaps.GapsPublisher(redis_client=r, records=recs).publish(now=NOW)
    for s in COHORT:
        c = json.loads(r.kv[f"hermes:gaps:{s}:v1"])
        assert c["market_hours_policy"] == policies[s]                 # per-instrument governed policy key recorded
        assert c["calendar_source"] == f"MARKET_HOURS_POLICY:{policies[s]}"
    # resolver is fail-closed: unknown/missing policy metadata never silently becomes a uniform default
    with pytest.raises(mhp.PolicyError):
        mhp.resolve_policy("not_a_policy")
    with pytest.raises(mhp.PolicyError):
        mhp.resolve_policy(None)


def test_eight_instrument_market_hours_policy_green():
    """EIGHT_INSTRUMENT_MARKET_HOURS_POLICY_GREEN — at the daily 17:00-18:00 NY halt, the SAME publisher classifies each
    cohort instrument by its registry policy: fx_24x5 stays OPEN, metals/index_cash/energy enter CLOSED_SESSION — no
    ticker branch, per-instrument, recorded in each contract's market_hours_policy + market_phase."""
    recs = records(); r = FakeRedis()
    halt = datetime(2026, 7, 8, 21, 30, tzinfo=UTC)         # Wed 17:30 EDT — inside the daily halt window
    gaps.GapsPublisher(redis_client=r, records=recs).publish(now=halt)
    expected_phase = {"fx_24x5": "OPEN", "metals": "CLOSED_SESSION", "index_cash": "CLOSED_SESSION", "energy": "CLOSED_SESSION"}
    pol = {rec.symbol: rec.market_hours_policy for rec in recs}
    for s in COHORT:
        c = json.loads(r.kv[f"hermes:gaps:{s}:v1"])
        assert c["market_hours_policy"] == pol[s]
        assert c["timeframes"]["M1"]["market_phase"] == expected_phase[pol[s]], s
    # fx instruments OPEN while metals/index/energy are CLOSED_SESSION — simultaneously, from one generic pass
    assert json.loads(r.kv["hermes:gaps:EUR_USD:v1"])["timeframes"]["M1"]["market_phase"] == "OPEN"
    assert json.loads(r.kv["hermes:gaps:SPX500_USD:v1"])["timeframes"]["M1"]["market_phase"] == "CLOSED_SESSION"


def test_market_hours_policy_isolation_green():
    """MARKET_HOURS_POLICY_ISOLATION_GREEN — different policies coexist with no shared/global market phase; one
    instrument's expected closure does not change another's classification."""
    recs = records(); r = FakeRedis()
    halt = datetime(2026, 7, 8, 21, 30, tzinfo=UTC)
    gaps.GapsPublisher(redis_client=r, records=recs).publish(now=halt)
    phases = {s: json.loads(r.kv[f"hermes:gaps:{s}:v1"])["timeframes"]["M1"]["market_phase"] for s in COHORT}
    # WTICO (energy) closed does not suppress EUR_USD (fx) open; each key holds its own policy-correct phase
    assert phases["WTICO_USD"] == "CLOSED_SESSION" and phases["EUR_USD"] == "OPEN"
    assert phases["XAU_USD"] == "CLOSED_SESSION" and phases["USD_JPY"] == "OPEN"
    assert set(k for k in r.kv if k.startswith("hermes:gaps:")) == {f"hermes:gaps:{s}:v1" for s in COHORT}


def test_full_14_registry_preserved():
    recs = records()
    assert len(recs) == 14
    assert len([r for r in recs if r.tick_contract_enabled and r.indicator_contract_enabled and r.gap_detection_enabled]) == 8
    assert len([r for r in recs if not (r.tick_contract_enabled or r.indicator_contract_enabled or r.gap_detection_enabled)]) == 6
    assert {r.symbol for r in recs} == set(ALL14)
