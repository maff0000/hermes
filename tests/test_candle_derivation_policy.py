"""Tests for B-DIV derivation-policy annotation (migration 023 + annotation runner + forward
runner policy write + GOV-FWD-BDIV gate + payload metadata).
WO-HELM-HERMES-CANDLE-DERIVATION-POLICY-ANNOTATION-0001. Pure-logic + fake-conn; no DB.
"""
import contextlib
import os
import sys
from datetime import datetime

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import utils.forward_derivation_runner as fdr  # noqa: E402
import utils.candle_features as cf  # noqa: E402
import annotate_candle_derivation_policy as ann  # noqa: E402

MIG = os.path.join(ROOT, "migrations", "023_candle_derivation_policy_columns.sql")
POLICY_COLS = ["derivation_policy", "source_complete_policy", "source_policy_epoch",
               "source_expected_candle_count", "source_actual_candle_count",
               "source_complete_candle_count", "source_incomplete_candle_count",
               "source_missing_candle_count", "derivation_run_id", "derivation_generated_at_utc",
               "derivation_policy_note"]


# ---------- migration 023 ----------
def test_migration_023_append_only_with_required_columns():
    sql = open(MIG).read()
    assert "ALTER TABLE candles_M30" in sql and "ALTER TABLE candles_H4" in sql
    for kw in ("DROP", "DELETE", "TRUNCATE", "MODIFY"):
        assert kw not in sql.upper().replace("DROP COLUMN", ""), f"destructive {kw} present"
    for col in POLICY_COLS:
        assert col in sql, f"missing column {col}"
    assert "ALGORITHM=INSTANT" in sql


# ---------- annotation runner ----------
class _Cur:
    def __init__(self, s): self.s = s; self._res = None; self.rowcount = 0; self.sqls = []
    def execute(self, sql, params=None):
        self.sqls.append(sql)
        if "information_schema.columns" in sql:
            self._res = [(c,) for c in self.s.get("columns", POLICY_COLS)]
        elif "derivation_policy IS NULL" in sql and sql.strip().upper().startswith("SELECT"):
            self._res = [(self.s.get("unannotated", 0),)]
        elif sql.strip().upper().startswith("UPDATE"):
            self.rowcount = self.s.get("unannotated", 0)
        else:
            self._res = [(0,)]
    def fetchall(self): return self._res
    def fetchone(self): return self._res[0] if self._res else None
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _Conn:
    def __init__(self, s): self.s = s; self.committed = False
    def cursor(self): self._c = _Cur(self.s); return self._c
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


def test_annotation_dry_run_counts_no_commit():
    out = ann.run(_conn({"columns": POLICY_COLS, "unannotated": 36377}), _Log(), execute=False, confirm=False)
    assert out["mode"] == "dry-run"
    assert out["tables"]["candles_M30"]["would_annotate"] == 36377
    assert out["tables"]["candles_M30"]["annotated"] == 0


def test_annotation_fail_loud_when_columns_missing():
    try:
        ann.run(_conn({"columns": ["id", "instrument"], "unannotated": 5}), _Log()); assert False
    except RuntimeError as e:
        assert "GOV-ANNOT-001" in str(e)


def test_annotation_sets_only_policy_columns_no_ohlc():
    # the UPDATE must not ASSIGN open/high/low/close/volume/complete/timestamp (precise tokens)
    for assign in ("open=", "high=", "low=", "close=", "volume=", "complete=", "timestamp="):
        assert assign not in ann.ANNOTATE_SQL, f"annotation must not assign {assign}"
    assert "derivation_policy=%s" in ann.ANNOTATE_SQL


def test_annotation_historical_values():
    assert ann.HISTORICAL["derivation_policy"] == "HISTORICAL_ALL_M1_COUNTED"
    assert ann.HISTORICAL["source_complete_policy"] == "ALL_M1_COUNTED"
    assert ann.HISTORICAL["source_policy_epoch"] == "PHASE2_BACKFILL_PRE_STRICT_COMPLETE_POLICY"


# ---------- forward runner policy write + gate ----------
def test_forward_upsert_includes_policy_columns():
    for col in ("derivation_policy", "source_complete_policy", "source_policy_epoch",
                "source_complete_candle_count", "derivation_run_id", "derivation_generated_at_utc"):
        assert col in fdr.UPSERT


def test_forward_constants():
    assert fdr.DERIVATION_POLICY == "FORWARD_COMPLETE_M1_ONLY"
    assert fdr.SOURCE_COMPLETE_POLICY == "COMPLETE_ONLY"
    assert fdr.FORWARD_POLICY_EPOCH == "FORWARD_STRICT_COMPLETE_POLICY_V1"


def test_verify_reconciliation_true_when_annotated():
    r = fdr.ForwardDerivationRunner(_conn({"columns": POLICY_COLS, "unannotated": 0}), _Log())
    ok, why = r.verify_reconciliation()
    assert ok is True


def test_verify_reconciliation_false_when_unannotated():
    r = fdr.ForwardDerivationRunner(_conn({"columns": POLICY_COLS, "unannotated": 100}), _Log())
    ok, why = r.verify_reconciliation()
    assert ok is False and "unannotated" in why


def test_verify_reconciliation_false_when_columns_missing():
    r = fdr.ForwardDerivationRunner(_conn({"columns": ["id"], "unannotated": 0}), _Log())
    assert r.verify_reconciliation()[0] is False


def test_gate_bdiv001_without_ack():
    s = {"columns": POLICY_COLS, "unannotated": 0, "instruments": ["XAU_USD"], "latest": None, "m1": [], "incomplete": 0}
    r = fdr.ForwardDerivationRunner(_conn_runner(s), _Log())
    try:
        r.run_cycle(datetime(2026, 6, 13, 12, 1), mode="single-cycle", timeframes=["M30"],
                    execute=True, confirm=True, reconciliation_ack=False); assert False
    except RuntimeError as e:
        assert "GOV-FWD-BDIV-001" in str(e)


def test_gate_bdiv002_ack_but_not_verified():
    s = {"columns": POLICY_COLS, "unannotated": 500, "instruments": ["XAU_USD"], "latest": None, "m1": [], "incomplete": 0}
    r = fdr.ForwardDerivationRunner(_conn_runner(s), _Log())
    try:
        r.run_cycle(datetime(2026, 6, 13, 12, 1), mode="single-cycle", timeframes=["M30"],
                    execute=True, confirm=True, reconciliation_ack=True); assert False
    except RuntimeError as e:
        assert "GOV-FWD-BDIV-002" in str(e)


# runner-aware fake conn (handles instruments + canonical_m1 + policy columns + unannotated)
class _CurR(_Cur):
    def execute(self, sql, params=None):
        if "FROM instruments" in sql:
            self._res = [(i,) for i in self.s["instruments"]]
        elif "MAX(minute_bucket_utc)" in sql:
            self._res = [(self.s.get("latest"),)]
        elif "complete=0" in sql:
            self._res = [(self.s.get("incomplete", 0),)]
        elif "FROM canonical_m1" in sql:
            self._res = self.s["m1"]
        else:
            super().execute(sql, params)


class _ConnR(_Conn):
    def cursor(self): self._c = _CurR(self.s); return self._c


def _conn_runner(s):
    @contextlib.contextmanager
    def gc():
        yield _ConnR(s)
    return gc


# ---------- payload metadata ----------
def test_feature_payload_exposes_policy_meta_no_strategy():
    CFG = cf.CandleFeatureConfig(0.10, 0.80, 0.50, 2.0, 0.35, 2, config_id=1).validate()
    meta = {"derivation_policy": "FORWARD_COMPLETE_M1_ONLY", "source_complete_policy": "COMPLETE_ONLY",
            "source_policy_epoch": "FORWARD_STRICT_COMPLETE_POLICY_V1", "source_complete_candle_count": 240,
            "regime": "should_be_dropped"}
    f = cf.build_candle_feature(instrument="XAU_USD", timeframe="H4", candle_id=1, source_table="candles_H4",
        source_candle_id=1, source_open_utc="a", source_close_utc="b", generated_at_utc="c",
        open_=10, high=15, low=9, close=14, complete_state="COMPLETE", expected=240, actual=240,
        freshness_state="FRESH", config=CFG, derivation_policy_meta=meta)
    pm = f["derivation_policy_meta"]
    assert pm["derivation_policy"] == "FORWARD_COMPLETE_M1_ONLY" and pm["source_complete_candle_count"] == 240
    assert "regime" not in pm  # strategy/Falcon token dropped (only allowed policy fields kept)


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
