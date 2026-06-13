"""Tests for the HERMES M30/H4 forward derivation runner (incl. B-DIV policy + live gate).
WO-HELM-HERMES-CANDLE-H4-M30-FORWARD-DERIVATION-AND-FEATURES-0001. Pure-logic + fake-conn; no DB.
"""
import contextlib
import os
import sys
from datetime import datetime

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
import utils.forward_derivation_runner as fdr  # noqa: E402


# ---------- bucket math / anchor / tf ----------
def test_h4_utc_anchor_boundaries():
    for hour, exp in {2: 0, 5: 4, 11: 8, 13: 12, 17: 16, 23: 20}.items():
        assert fdr.forming_bucket_start(datetime(2026, 6, 13, hour, 30), "H4") == datetime(2026, 6, 13, exp)


def test_m30_last_closed():
    now = datetime(2026, 6, 13, 13, 37)
    assert fdr.last_closed_bucket_start(now, "M30") == datetime(2026, 6, 13, 13, 0)


def test_unknown_timeframe_fail_loud():
    try:
        fdr.expected_m1("H2"); assert False
    except ValueError as e:
        assert "GOV-FWD-TF-001" in str(e)


def test_complete_state_matrix():
    assert fdr.derive_complete_state(30, "M30", False) == ("COMPLETE", True)
    assert fdr.derive_complete_state(29, "M30", False) == ("INCOMPLETE", False)
    assert fdr.derive_complete_state(240, "H4", False) == ("COMPLETE", True)
    assert fdr.derive_complete_state(30, "M30", True) == ("FORMING", False)
    assert fdr.derive_complete_state(0, "M30", False) == ("UNAVAILABLE", False)


def test_aggregate_and_freshness():
    rows = [{"open": 10, "high": 12, "low": 9, "close": 11, "volume": 5},
            {"open": 11, "high": 15, "low": 8, "close": 9, "volume": 7}]
    assert fdr.aggregate_complete(rows) == (10.0, 15.0, 8.0, 9.0, 12)
    now = datetime(2026, 6, 13, 12, 0)
    assert fdr.classify_freshness(now, datetime(2026, 6, 13, 11, 59), "M30")[0] == "FRESH"
    assert fdr.classify_freshness(now, None, "M30")[0] == "UNAVAILABLE"
    assert fdr.classify_freshness(now, datetime(2026, 6, 13, 9, 0), "M30")[0] == "STALE"  # visible


def test_upsert_never_downgrades():
    assert "complete=GREATEST(complete, VALUES(complete))" in fdr.UPSERT


# ---------- fake-conn ----------
class _Cur:
    def __init__(self, s): self.s = s; self._res = None
    def execute(self, sql, params=None):
        if "FROM instruments" in sql:
            self._res = [(i,) for i in self.s["instruments"]]
        elif "MAX(minute_bucket_utc)" in sql:
            self._res = [(self.s.get("latest"),)]
        elif "complete=0" in sql:
            self._res = [(self.s.get("incomplete", 0),)]
        elif "FROM canonical_m1" in sql:
            self._res = self.s["m1"]
    def fetchall(self): return self._res
    def fetchone(self): return self._res[0] if self._res else None
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _Conn:
    def __init__(self, s): self.s = s; self.committed = False
    def cursor(self): return _Cur(self.s)
    def commit(self): self.committed = True
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _conn(s):
    @contextlib.contextmanager
    def gc():
        yield _Conn(s)
    return gc


class _Log:
    def info(self, *a, **k): pass


def test_load_instruments_fail_loud_when_empty():
    try:
        fdr.ForwardDerivationRunner(_conn({"instruments": []}), _Log()).load_instruments(); assert False
    except RuntimeError as e:
        assert "GOV-FWD-INSTR-001" in str(e)


def test_derive_bucket_states_and_policy_metadata():
    store = {"instruments": ["XAU_USD"], "latest": datetime(2026, 6, 13, 12, 0),
             "m1": [(10, 12, 9, 11, 1)] * 30, "incomplete": 0}
    r = fdr.ForwardDerivationRunner(_conn(store), _Log())
    res = r.derive_bucket("XAU_USD", "M30", datetime(2026, 6, 13, 11, 0), datetime(2026, 6, 13, 12, 0))
    assert res["complete_state"] == "COMPLETE" and res["complete"] is True
    # B-DIV policy metadata present
    assert res["derivation_policy"] == "FORWARD_COMPLETE_M1_ONLY"
    assert res["source_complete_policy"] == "COMPLETE_ONLY"
    assert res["complete_source_candle_count"] == 30 and res["incomplete_source_candle_count"] == 0
    # incomplete present -> INCOMPLETE + reason
    store["m1"] = [(10, 12, 9, 11, 1)] * 29; store["incomplete"] = 1
    res2 = r.derive_bucket("XAU_USD", "M30", datetime(2026, 6, 13, 11, 0), datetime(2026, 6, 13, 12, 0))
    assert res2["complete_state"] == "INCOMPLETE" and res2["incomplete_source_candle_count"] == 1
    assert "SOURCE_M1_INCOMPLETE_PRESENT" in res2["reason_codes"]
    # forming never complete
    res3 = r.derive_bucket("XAU_USD", "M30", datetime(2026, 6, 13, 12, 0), datetime(2026, 6, 13, 12, 5))
    assert res3["complete_state"] == "FORMING" and res3["complete"] is False


def test_run_cycle_dry_run_exposes_policy_no_write():
    store = {"instruments": ["XAU_USD"], "latest": datetime(2026, 6, 13, 12, 0),
             "m1": [(10, 12, 9, 11, 1)] * 30, "incomplete": 0}
    ev = fdr.ForwardDerivationRunner(_conn(store), _Log()).run_cycle(
        datetime(2026, 6, 13, 12, 1), mode="single-cycle", timeframes=["M30"], execute=False, confirm=False)
    assert ev["mode"] == "dry-run" and ev["totals"]["written"] == 0
    assert ev["derivation_policy"] == "FORWARD_COMPLETE_M1_ONLY"
    assert ev["source_complete_policy"] == "COMPLETE_ONLY" and "HISTORICAL_ALL_M1_COUNTED" in ev["historical_policy_note"]


def test_bdiv_execute_blocked_without_reconciliation_ack():
    store = {"instruments": ["XAU_USD"], "latest": datetime(2026, 6, 13, 12, 0),
             "m1": [(10, 12, 9, 11, 1)] * 30, "incomplete": 0}
    r = fdr.ForwardDerivationRunner(_conn(store), _Log())
    try:
        r.run_cycle(datetime(2026, 6, 13, 12, 1), mode="single-cycle", timeframes=["M30"],
                    execute=True, confirm=True, reconciliation_ack=False); assert False
    except RuntimeError as e:
        assert "GOV-FWD-BDIV-001" in str(e)


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    p = 0
    for fn in fns:
        try:
            fn(); p += 1; print("PASS", fn.__name__)
        except Exception:
            print("FAIL", fn.__name__); traceback.print_exc()
    print(f"{p}/{len(fns)} passed"); raise SystemExit(0 if p == len(fns) else 1)
