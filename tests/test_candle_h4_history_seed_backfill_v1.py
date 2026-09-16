"""HERMES H4 history SEED/BACKFILL engine v1 — code-only, in-memory fake Redis + fake MariaDB (no live I/O).
WO-HELM-HERMES-H4-CANONICAL-HISTORICAL-BOOTSTRAP-0001.

Proves: dark/dry-run by default, no writes in dry-run, gated live write, idempotency by open_epoch, bounded
behaviour, candidate validation (genuine 4/4 accepted; incomplete/gapped rejected, never fabricated), the
ONE governed derivation (candle_h4_derivation_v1.derive_h4 — no second implementation), durable MariaDB
candles_H1 as the only source (never candles_H4, never candles_D1), H4's own count-based retention applied
after a live write, existing live H4 history untouched.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

import utils.candle_h4_history_seed_backfill_v1 as bf
import utils.candle_h4_derivation_v1 as h4d
import utils.candle_history_v1 as chv
import utils.candle_contract_v1 as cc

UTC = timezone.utc
INST = "XAU_USD"


class _FakeRedis:
    """In-memory fake with a ZSET index + kv. Records writes so tests can assert none happen in dry-run."""
    def __init__(self):
        self.kv = {}
        self.z = {}
        self.sets = []
        self.zadds = []
        self.deletes = []

    def get(self, k):
        v = self.kv.get(k)
        return v.encode() if isinstance(v, str) else v

    def exists(self, k):
        return 1 if (k in self.kv or k in self.z) else 0

    def set(self, k, v, ex=None):
        self.kv[k] = v
        self.sets.append((k, ex))

    def zadd(self, k, mapping):
        self.z.setdefault(k, {}).update(mapping)
        self.zadds.append((k, dict(mapping)))

    def zcard(self, k):
        return len(self.z.get(k, {}))

    def zremrangebyrank(self, k, start, stop):
        members = sorted(self.z.get(k, {}).items(), key=lambda kv: kv[1])
        removed = members[start:stop + 1] if stop != -1 else members[start:]
        for m, _ in removed:
            self.z[k].pop(m, None)
        return len(removed)

    def delete(self, *keys):
        self.deletes.extend(keys)


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows
    def execute(self, sql, params):
        assert "candles_H1" in sql
        assert "candles_H4" not in sql and "candles_D1" not in sql
    def fetchall(self):
        return self._rows
    def close(self):
        pass


class _FakeDBConn:
    """Fake MariaDB connection yielding genuine-shaped candles_H1 rows (naive UTC datetimes, matching
    pymysql's typical DATETIME return shape)."""
    def __init__(self, rows):
        self._rows = rows
    def cursor(self):
        return _FakeCursor(self._rows)


def _h1_rows(n_hours, start=datetime(2026, 6, 1, 0, 0), gap_hours=()):
    """n_hours of continuous naive-UTC H1 rows, optionally skipping the given hour offsets (gaps)."""
    rows = []
    for i in range(n_hours):
        if i in gap_hours:
            continue
        ts = start + timedelta(hours=i)
        p = 2000.0 + (i % 7) * 0.5
        rows.append((ts, p, p + 2, p - 2, p + 0.5, 10))
    return rows


def _to_dicts(rows):
    """Convert raw SQL-row tuples (as _h1_rows produces) into the dict shape build_h4_candidates expects
    (the same conversion _read_h1_source_rows performs on real MariaDB rows)."""
    return [{"timestamp": ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts, "open": float(o),
            "high": float(h), "low": float(lo), "close": float(c), "volume": int(v or 0)}
           for ts, o, h, lo, c, v in rows]


def _cfg(**kw):
    base = dict(enabled=False, authorised=False, dry_run=True, max_candidates=60, min_depth=26, lookback_days=90)
    base.update(kw)
    return bf.BackfillConfig(**base)


# --------------------------------------------------------------------------- config / gates (dark by default)
def test_config_disabled_and_dry_run_by_default(monkeypatch):
    for e in (bf.BACKFILL_ENABLED_ENV, bf.BACKFILL_AUTHORISED_ENV, bf.BACKFILL_DRY_RUN_ENV):
        monkeypatch.delenv(e, raising=False)
    c = bf.parse_backfill_config_from_env()
    assert c.enabled is False and c.dry_run is True and c.live_write_allowed is False


def test_enabled_without_authorised_fails_loud(monkeypatch):
    monkeypatch.setenv(bf.BACKFILL_ENABLED_ENV, "true")
    monkeypatch.delenv(bf.BACKFILL_AUTHORISED_ENV, raising=False)
    with pytest.raises(SystemExit):
        bf.parse_backfill_config_from_env()


def test_live_write_requires_all_three_gates():
    assert _cfg(enabled=True, authorised=True, dry_run=True).live_write_allowed is False
    assert _cfg(enabled=True, authorised=False, dry_run=False).live_write_allowed is False
    assert _cfg(enabled=False, authorised=True, dry_run=False).live_write_allowed is False
    assert _cfg(enabled=True, authorised=True, dry_run=False).live_write_allowed is True


# --------------------------------------------------------------------------- exactly ONE derivation, governed source only
def test_source_is_mariadb_h1_only_never_h4_or_d1_table():
    import inspect
    raw = inspect.getsource(bf)
    code = raw.replace(bf.__doc__ or "", "")
    assert bf.H1_SOURCE_TABLE == "candles_H1"
    assert '"candles_H4"' not in code and "'candles_H4'" not in code
    assert '"candles_D1"' not in code and "'candles_D1'" not in code


def test_reuses_the_one_existing_derive_h4_no_second_implementation():
    import inspect
    raw = inspect.getsource(bf)
    assert "from utils import candle_h4_derivation_v1 as h4d" in raw
    assert "h4d.derive_h4(" in raw
    assert "h4d.h1_children_in_bucket(" in raw          # the SAME governed bucket selector, not a new one


# --------------------------------------------------------------------------- candidate reconstruction
def test_valid_genuine_4of4_candidate_accepted():
    rows = _h1_rows(24 * 10)                                 # 10 days continuous
    cands = bf.build_h4_candidates(_to_dicts(rows), max_candidates=30)
    accepted = [c for c in cands if c["accepted"]]
    assert len(accepted) >= 26
    env = accepted[0]["env"]
    assert env["status"] == "OK" and env["data"]["timeframe"] == "H4"
    assert env["data"]["source_count"] == 4 and env["data"]["gap_state"] == "NONE"
    assert env["provenance"]["derivation"] == cc.DERIVATION_DERIVED
    assert env["provenance"]["source_timeframe"] == "H1"


def test_incomplete_bucket_rejected_not_synthesised():
    # remove ONE hour so exactly one H4 bucket loses a child -> rejected, not backfilled to 4
    rows = _h1_rows(24 * 5, gap_hours=(50,))
    cands = bf.build_h4_candidates(_to_dicts(rows), max_candidates=40)
    reasons = {c["reason"] for c in cands if not c["accepted"]}
    assert "H4_INCOMPLETE_CHILDREN" in reasons
    # every accepted candidate is still genuinely 4/4 — never fabricated to cover the gap
    assert all(c["env"]["data"]["source_count"] == 4 for c in cands if c["accepted"])


def test_no_h1_rows_yields_no_candidates():
    assert bf.build_h4_candidates([], max_candidates=10) == []


# --------------------------------------------------------------------------- dry-run: NO writes
def test_dry_run_performs_no_writes():
    rows = _h1_rows(24 * 40)
    db = _FakeDBConn(rows)
    r = _FakeRedis()
    plan = bf.dry_run_plan(r, db, config=_cfg(max_candidates=200), now=datetime(2026, 7, 11, tzinfo=UTC))
    assert plan["mode"] == "DRY_RUN" and plan["no_write_proof"] is True
    assert plan["source_path"] == "candles_H1"
    assert plan["source_forbidden_h4_table"] is True and plan["source_forbidden_d1_table"] is True
    assert plan["accepted_count"] >= 26 and plan["current_depth"] == 0
    assert plan["projected_depth"] == plan["idempotency"]["new"] and plan["meets_min_depth"] is True
    assert plan["inert_write_plan_count"] == plan["idempotency"]["new"]
    assert plan["write_mode"] == "HISTORY_INERT_NO_WRITE"
    assert r.sets == [] and r.zadds == [] and r.deletes == []


def test_dry_run_reports_scanned_and_rejection_reasons():
    rows = _h1_rows(24 * 20, gap_hours=(100, 101))
    db = _FakeDBConn(rows)
    r = _FakeRedis()
    plan = bf.dry_run_plan(r, db, config=_cfg(max_candidates=100), now=datetime(2026, 6, 25, tzinfo=UTC))
    assert plan["scanned_h1_rows"] == len(rows)
    assert plan["rejection_reasons"].get("H4_INCOMPLETE_CHILDREN", 0) >= 1


# --------------------------------------------------------------------------- idempotency
def test_idempotency_new_match_conflict():
    rows = _h1_rows(24 * 5)
    cands = [c for c in bf.build_h4_candidates(_to_dicts(rows), max_candidates=20) if c["accepted"]]
    r = _FakeRedis()
    env = cands[0]["env"]
    assert bf._idempotency_status(r, env) == "new"
    ep = int(bf._parse_utc(env["data"]["timestamp_utc"]).timestamp())
    he = bf._history_envelope_for(env, now=datetime(2026, 6, 10, tzinfo=UTC))
    r.kv[chv.history_key(INST, "H4", ep)] = json.dumps(he)
    assert bf._idempotency_status(r, env) == "already_present_match"
    conflict = json.loads(r.kv[chv.history_key(INST, "H4", ep)]); conflict["data"]["close"] = 99999.0
    r.kv[chv.history_key(INST, "H4", ep)] = json.dumps(conflict)
    assert bf._idempotency_status(r, env) == "conflict"


# --------------------------------------------------------------------------- bounded
def test_bounded_max_candidates():
    rows = _h1_rows(24 * 60)
    cands = bf.build_h4_candidates(_to_dicts(rows), max_candidates=10)
    assert len(cands) <= 10


def test_max_candidates_out_of_bounds_rejected(monkeypatch):
    monkeypatch.setenv(bf.BACKFILL_ENABLED_ENV, "true")
    monkeypatch.setenv(bf.BACKFILL_AUTHORISED_ENV, "true")
    monkeypatch.setenv(bf.BACKFILL_MAX_CANDLES_ENV, "999999")
    with pytest.raises(ValueError):
        bf.parse_backfill_config_from_env()


# --------------------------------------------------------------------------- live execute: gated, idempotent, retention
def test_execute_refuses_without_gates():
    rows = _h1_rows(24 * 10)
    db = _FakeDBConn(rows)
    r = _FakeRedis()
    with pytest.raises(ValueError):
        bf.execute_backfill(r, db, config=_cfg(enabled=True, authorised=True, dry_run=True))
    assert r.sets == [] and r.zadds == []


def test_execute_writes_history_only_idempotent_applies_h4_retention():
    rows = _h1_rows(24 * 60)                                       # plenty for > H4_HISTORY_RETAIN_COUNT
    db = _FakeDBConn(rows)
    r = _FakeRedis()
    cfg = _cfg(enabled=True, authorised=True, dry_run=False, max_candidates=300)
    res = bf.execute_backfill(r, db, config=cfg, now=datetime(2026, 7, 31, tzinfo=UTC))
    assert res["mode"] == "LIVE_WRITE" and res["written"] > 0
    assert all(":H4:history:v1:" in k for k, _ in r.sets)
    assert all(not k.endswith(":latest:v1") for k in r.kv)          # never touches :latest
    assert r.deletes == []                                          # never deletes (only bounded ZREMRANGEBYRANK)
    idx = chv.history_index_key(INST, "H4")
    # count-based retention applied: depth never exceeds H4_HISTORY_RETAIN_COUNT after this write batch
    assert r.zcard(idx) <= chv.H4_HISTORY_RETAIN_COUNT
    # re-run is idempotent: everything now matches -> zero new writes
    n_sets = len(r.sets)
    res2 = bf.execute_backfill(r, db, config=cfg, now=datetime(2026, 8, 1, tzinfo=UTC))
    assert res2["written"] == 0 and len(r.sets) == n_sets


def test_execute_conflict_fails_loud_never_overwrites():
    rows = _h1_rows(24 * 5)
    db = _FakeDBConn(rows)
    r = _FakeRedis()
    cfg = _cfg(enabled=True, authorised=True, dry_run=False, max_candidates=20)
    cands = [c for c in bf.build_h4_candidates(_to_dicts(rows), max_candidates=20) if c["accepted"]]
    env0 = cands[0]["env"]; ep0 = int(bf._parse_utc(env0["data"]["timestamp_utc"]).timestamp())
    conflict = json.loads(json.dumps(env0)); conflict["data"]["close"] = 12345.0
    r.kv[chv.history_key(INST, "H4", ep0)] = json.dumps(conflict)
    before = dict(r.kv)
    with pytest.raises(ValueError):
        bf.execute_backfill(r, db, config=cfg, now=datetime(2026, 6, 10, tzinfo=UTC))
    assert r.kv[chv.history_key(INST, "H4", ep0)] == before[chv.history_key(INST, "H4", ep0)]
    assert r.deletes == []


# --------------------------------------------------------------------------- no interpretive semantics
def test_no_forbidden_interpretive_tokens_in_code():
    import inspect
    raw = inspect.getsource(bf)
    code = "\n".join(l for l in raw.replace(bf.__doc__ or "", "").splitlines()
                     if not l.strip().startswith("#")).lower()
    for tok in ("regime", "regime_confidence", "strategy", "signal", " buy ", " sell ", "no-go"):
        assert tok not in code, f"forbidden interpretive token {tok!r} in seed/backfill CODE"
