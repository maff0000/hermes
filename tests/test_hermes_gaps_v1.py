"""HERMES PH2 gaps surface (hermes:gaps:XAU_USD:v1) — dark/read-only, code-only, no live I/O.
WO-HELM-HERMES-PH2-GAPS-SURFACE-0001.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

import utils.hermes_gaps_v1 as gaps
import utils.candle_d1_derivation_v1 as d1d
import utils.candle_contract_v1 as cc

UTC = timezone.utc
INST = "XAU_USD"
NOW_OPEN = datetime(2026, 7, 8, 12, 0, tzinfo=UTC)      # Wednesday noon -> market OPEN
NOW_CLOSED = datetime(2026, 7, 12, 13, 0, tzinfo=UTC)   # Sunday 13:00 -> market CLOSED_WEEKEND (< 22:00)


def _complete_open_history(tf, now):
    """The full set of OPEN-market grid opens in [retention_floor, now) — a 'no-gaps' history for tf."""
    now_e = int(now.timestamp()); p = gaps.PERIOD_SECONDS[tf]
    floor = now_e - gaps.RETENTION_DAYS[tf] * 86400
    return {o for o in gaps._grid_opens(tf, floor - gaps._OOR_LOOKBACK_PERIODS * p, now_e)
            if o >= floor and gaps._period_fully_open(o, tf)}


def _latest_env(tf, open_dt, status="OK"):
    return {"status": status, "data": {"instrument": INST, "timeframe": tf,
            "timestamp_utc": cc._fmt(cc.normalise_utc(open_dt)), "open": 2000.0, "high": 2010.0, "low": 1990.0,
            "close": 2005.0}}


def _sealed_d1(d1_open=datetime(2026, 7, 7, 22, 0, tzinfo=UTC)):
    opens = d1d.d1_child_h4_opens(d1_open)
    specs = [(2000, 2010, 1990, 2005, 10), (2005, 2030, 1995, 2020, 11), (2020, 2080, 2010, 2050, 12),
             (2050, 2060, 2000, 2030, 13), (2030, 2040, 1900, 1950, 14), (1950, 1975, 1940, 1970, 15)]
    kids = [{"timestamp": opens[i], "open": specs[i][0], "high": specs[i][1], "low": specs[i][2],
             "close": specs[i][3], "volume": specs[i][4]} for i in range(6)]
    env, _ = d1d.derive_d1(instrument=INST, d1_open=d1_open, h4_children=kids,
                           generated_at_utc=cc.normalise_utc(d1_open) + timedelta(seconds=d1d.D1_SECONDS))
    return env


# --------------------------------------------------------------------------- market calendar
def test_market_phase_weekly_weekend():
    assert gaps.market_phase(datetime(2026, 7, 8, 12, 0, tzinfo=UTC)) == "OPEN"           # Wed
    assert gaps.market_phase(datetime(2026, 7, 10, 20, 0, tzinfo=UTC)) == "OPEN"          # Fri 20:00 < 21:00
    assert gaps.market_phase(datetime(2026, 7, 10, 21, 30, tzinfo=UTC)) == "CLOSED_WEEKEND"  # Fri after close
    assert gaps.market_phase(datetime(2026, 7, 11, 12, 0, tzinfo=UTC)) == "CLOSED_WEEKEND"   # Sat
    assert gaps.market_phase(datetime(2026, 7, 12, 12, 0, tzinfo=UTC)) == "CLOSED_WEEKEND"   # Sun < 22:00
    assert gaps.market_phase(datetime(2026, 7, 12, 22, 30, tzinfo=UTC)) == "OPEN"            # Sun >= 22:00 reopen


# --------------------------------------------------------------------------- 1 & 7: weekend closure
def test_m1_weekend_missing_is_market_closed_not_gaps():
    hist = _complete_open_history("H1", NOW_CLOSED)            # H1 (cheaper) complete open-market history
    b = gaps.classify_timeframe("H1", latest=_latest_env("H1", datetime(2026, 7, 10, 20, 0, tzinfo=UTC), "STALE"),
                                history_opens=hist, now=NOW_CLOSED)
    assert b["market_phase"] == "CLOSED_WEEKEND"
    assert b["missing_slots"] == 0                            # no OPEN-market slot missing
    assert b["gap_state"] == "MARKET_CLOSED"                  # weekend -> MARKET_CLOSED, never GAPS_FOUND


def test_stale_latest_during_closed_is_market_closed_not_stale():
    hist = _complete_open_history("H1", NOW_CLOSED)
    b = gaps.classify_timeframe("H1", latest=_latest_env("H1", datetime(2026, 7, 10, 10, 0, tzinfo=UTC)),  # very old latest
                                history_opens=hist, now=NOW_CLOSED)
    assert b["gap_state"] == "MARKET_CLOSED"                  # stale suppressed while market closed


# --------------------------------------------------------------------------- 2: open-market gap
def test_open_market_missing_slot_is_gaps_found():
    hist = _complete_open_history("H1", NOW_OPEN)
    victim = max(o for o in hist)                            # drop the newest open-market H1 slot
    hist2 = hist - {victim}
    b = gaps.classify_timeframe("H1", latest=_latest_env("H1", datetime(2026, 7, 8, 11, 0, tzinfo=UTC)),
                                history_opens=hist2, now=NOW_OPEN)
    assert b["missing_slots"] >= 1 and victim in b["missing_open_epochs_sample"]
    assert b["gap_state"] == "GAPS_FOUND"


# --------------------------------------------------------------------------- 3: source missing
def test_source_missing():
    b = gaps.classify_timeframe("M5", latest=None, history_opens=[], now=NOW_OPEN)
    assert b["gap_state"] == "SOURCE_MISSING" and b["latest_status"] == "ABSENT"


# --------------------------------------------------------------------------- 4 & 5: invalid anchor / insufficient
def test_invalid_anchor_dominates():
    # an H4 member off the NY-5PM grid (e.g. 03:00) -> INVALID_ANCHOR (worst of any co-occurring state)
    hist = _complete_open_history("H4", NOW_OPEN) | {int(datetime(2026, 7, 8, 3, 0, tzinfo=UTC).timestamp())}
    b = gaps.classify_timeframe("H4", latest=_latest_env("H4", datetime(2026, 7, 8, 6, 0, tzinfo=UTC)),
                                history_opens=hist, now=NOW_OPEN)
    assert b["invalid_anchor_count"] >= 1 and b["gap_state"] == "INVALID_ANCHOR"


def test_insufficient_history():
    few = sorted(_complete_open_history("H1", NOW_OPEN))[-10:]   # only 10 -> below 26
    b = gaps.classify_timeframe("H1", latest=_latest_env("H1", datetime(2026, 7, 8, 11, 0, tzinfo=UTC)),
                                history_opens=few, now=NOW_OPEN)
    assert b["history_depth"] == 10 and b["sufficient_depth"] is False
    assert b["gap_state"] == "INSUFFICIENT_HISTORY"          # severity above GAPS_FOUND


# --------------------------------------------------------------------------- 6: stale during open
def test_stale_latest_during_open():
    hist = _complete_open_history("H1", NOW_OPEN)
    # history complete (no gaps) but the latest KEY is old -> STALE while market open
    b = gaps.classify_timeframe("H1", latest=_latest_env("H1", datetime(2026, 7, 8, 6, 0, tzinfo=UTC)),
                                history_opens=hist, now=NOW_OPEN)
    assert b["gap_state"] == "STALE"


# --------------------------------------------------------------------------- 8: out of retention
def test_out_of_retention_not_gap():
    hist = _complete_open_history("H1", NOW_OPEN)
    p = gaps.PERIOD_SECONDS["H1"]; floor = int(NOW_OPEN.timestamp()) - gaps.RETENTION_DAYS["H1"] * 86400
    pre = ((floor - 2 * p) // p) * p                        # a slot a couple periods BEFORE the retention floor
    # complete in-window + one missing pre-floor slot -> OUT_OF_RETENTION (never a gap)
    b = gaps.classify_timeframe("H1", latest=_latest_env("H1", datetime(2026, 7, 8, 11, 0, tzinfo=UTC)),
                                history_opens=hist, now=NOW_OPEN)
    assert b["missing_slots"] == 0
    assert b["out_of_retention_slots"] >= 1 and b["gap_state"] == "OUT_OF_RETENTION"


# --------------------------------------------------------------------------- 9: weekend derived candles annotated, grid unchanged
def test_weekend_candles_retained_not_expanding_grid():
    hist = _complete_open_history("H4", NOW_CLOSED)
    weekend_h4 = int(datetime(2026, 7, 11, 14, 0, tzinfo=UTC).timestamp())   # a Sat H4 (closed-market)
    assert gaps.anchor_hour_ok(weekend_h4, "H4")            # still a valid NY-5PM anchor (not destructive relabel)
    b = gaps.classify_timeframe("H4", latest=_latest_env("H4", datetime(2026, 7, 12, 6, 0, tzinfo=UTC)),
                                history_opens=hist | {weekend_h4}, now=NOW_CLOSED)
    assert b["invalid_anchor_count"] == 0                   # weekend candle NOT flagged as invalid
    assert b["market_phase"] == "CLOSED_WEEKEND" and b["gap_state"] == "MARKET_CLOSED"
    # weekend candle did not increase expected OPEN-market grid (it is closed-market)
    assert not gaps._period_fully_open(weekend_h4, "H4")


# --------------------------------------------------------------------------- 10-12: D1 boundary
def test_d1_2200_sealed_ok():
    d1o = datetime(2026, 7, 7, 22, 0, tzinfo=UTC)
    d1b = gaps.classify_d1_boundary(d1_latest=_sealed_d1(d1o), d1_history_opens=[int(d1o.timestamp())],
                                    now=datetime(2026, 7, 8, 12, 0, tzinfo=UTC), forward_enabled=True, forward_authorised=True)
    assert d1b["d1_boundary_state"] == "OK" and d1b["sealed_complete"] is True
    assert d1b["non_22_anchor_count"] == 0 and d1b["expected_source_count"] == 6 and d1b["latest_matches_history_newest"]
    assert d1b["forward_writer_enabled"] and d1b["forward_writer_authorised"]


def test_d1_0000_anchor_invalid():
    env = _sealed_d1(); env["data"]["timestamp_utc"] = "2026-07-07T00:00:00.000Z"    # false midnight anchor
    d1b = gaps.classify_d1_boundary(d1_latest=env, d1_history_opens=[int(datetime(2026, 7, 7, 0, 0, tzinfo=UTC).timestamp())],
                                    now=datetime(2026, 7, 8, 12, 0, tzinfo=UTC))
    assert d1b["d1_boundary_state"] == "INVALID_ANCHOR" and d1b["non_22_anchor_count"] >= 1


def test_d1_latest_not_matching_history_fails_closed():
    d1o = datetime(2026, 7, 7, 22, 0, tzinfo=UTC)
    older = int(datetime(2026, 7, 6, 22, 0, tzinfo=UTC).timestamp())
    d1b = gaps.classify_d1_boundary(d1_latest=_sealed_d1(d1o), d1_history_opens=[older], now=datetime(2026, 7, 8, tzinfo=UTC))
    assert d1b["latest_matches_history_newest"] is False and d1b["d1_boundary_state"] == "LATEST_HISTORY_INCONSISTENT"


# --------------------------------------------------------------------------- 13-15: contract invariants
def test_xauusd_denied():
    env = _sealed_d1(); env["data"]["instrument"] = "XAUUSD"
    d1b = gaps.classify_d1_boundary(d1_latest=env, d1_history_opens=[int(datetime(2026, 7, 7, 22, 0, tzinfo=UTC).timestamp())],
                                    now=datetime(2026, 7, 8, tzinfo=UTC))
    assert d1b["sealed_complete"] is False                  # XAUUSD -> not sealed
    with pytest.raises(ValueError):
        gaps.validate_gaps_contract({"instrument": "XAU_USD", "canonical_instrument": "XAU_USD",
                                     "repair_executed": False, "backfill_executed": False, "consumer_live": False,
                                     "overall_gap_state": "OK", "timeframes": {"D1": {"gap_state": "OK", "market_phase": "OPEN",
                                     "note": "XAUUSD"}}})


def test_worst_of_severity_and_invariants():
    tfb = {"M1": {"gap_state": "OK", "market_phase": "OPEN"}, "H1": {"gap_state": "GAPS_FOUND", "market_phase": "OPEN"},
           "D1": {"gap_state": "INVALID_ANCHOR", "market_phase": "OPEN"}}
    c = gaps.build_gaps_contract(timeframes=tfb, d1_boundary={"d1_boundary_state": "OK"},
                                 generated_at_utc=NOW_OPEN)
    assert c["overall_gap_state"] == "INVALID_ANCHOR"       # worst-of
    assert c["repair_executed"] is False and c["backfill_executed"] is False and c["consumer_live"] is False
    assert c["instrument"] == "XAU_USD" and c["calendar_source"] == "WEEKLY_WEEKEND_UTC"
    assert c["severity_order"][0] == "SOURCE_MISSING" and c["severity_order"][-1] == "OK"


# --------------------------------------------------------------------------- 16-18: read-only / no-write / dark
class _FakeRedis:
    def __init__(self, kv=None, z=None):
        self.kv = kv or {}; self.z = z or {}; self.writes = []; self.deletes = []
    def get(self, k):
        v = self.kv.get(k); return v.encode() if isinstance(v, str) else v
    def exists(self, k): return 1 if (k in self.kv or k in self.z) else 0
    def zrange(self, k, a, b):
        m = sorted(self.z.get(k, {}).items(), key=lambda kv: kv[1]); return [x for x, _ in (m[a:b+1] if b != -1 else m[a:])]
    def set(self, *a, **k): self.writes.append(a)
    def zadd(self, *a, **k): self.writes.append(a)
    def delete(self, *a): self.deletes.extend(a)


def test_analyze_gaps_read_only_no_writes():
    fake = _FakeRedis()
    # seed only D1 latest + history (others absent -> SOURCE_MISSING, which is fine for this test)
    d1o = datetime(2026, 7, 7, 22, 0, tzinfo=UTC)
    fake.kv["hermes:candles:XAU_USD:D1:latest:v1"] = json.dumps(_sealed_d1(d1o))
    fake.z["hermes:candles:XAU_USD:D1:history:v1:index"] = {str(int(d1o.timestamp())): int(d1o.timestamp())}
    c = gaps.analyze_gaps(fake, now=datetime(2026, 7, 8, 12, 0, tzinfo=UTC), forward_enabled=True, forward_authorised=True)
    gaps.validate_gaps_contract(c)
    assert fake.writes == [] and fake.deletes == []          # NO Redis writes/deletes
    assert c["repair_executed"] is False and c["backfill_executed"] is False
    assert c["d1_boundary"]["d1_boundary_state"] == "OK"


def test_publisher_dark_by_default(monkeypatch):
    monkeypatch.delenv(gaps.GAPS_ENABLED_ENV, raising=False)
    assert gaps.build_gaps_publisher_from_env().enabled is False
    monkeypatch.setenv(gaps.GAPS_ENABLED_ENV, "true"); monkeypatch.delenv(gaps.GAPS_AUTHORISED_ENV, raising=False)
    with pytest.raises(SystemExit):
        gaps.build_gaps_publisher_from_env()


def test_no_sql_no_marketmap_no_falcon_no_interpretive():
    import inspect
    src = inspect.getsource(gaps)
    # scan CODE only (strip the module docstring which carries the negative declarations 'no SQL/vendor/market_map', etc.)
    code = src.replace(gaps.__doc__ or "", "")
    for imp in ("import pymysql", "pymysql", "get_db_config", "import market_map", "from market_map",
                "candles_H4", "candles_M30", "import falcon", "from falcon"):
        assert imp not in code, f"gaps module CODE must not reference {imp!r}"
    # publisher never writes the key in this WO: GapsPublisher performs no .set(...)
    assert ".set(" not in inspect.getsource(gaps.GapsPublisher)
    # no interpretive/strategy semantics introduced (scan code, drop comment lines)
    body = "\n".join(l for l in code.splitlines() if not l.strip().startswith("#")).lower()
    for tok in ("regime", " signal ", " buy ", " sell ", "position_siz", "risk_score"):
        assert tok not in body, f"gaps module must not add interpretive token {tok!r}"
