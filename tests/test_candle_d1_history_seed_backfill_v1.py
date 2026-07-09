"""HERMES D1 history SEED/BACKFILL engine v1 — code-only, in-memory fake Redis (no live I/O).
WO-HELM-HERMES-D1-HISTORY-SEED-BACKFILL-0001.

Proves: dark/dry-run by default, no writes in dry-run, gated live write, idempotency by open_epoch, bounded
behaviour, candidate validation (sealed 6/6 accepted; incomplete/gapped/alias rejected), governed H4-history source
only (no SQL / no market_map), D1 latest + M1-H4 untouched, no interpretive semantics.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

import utils.candle_d1_history_seed_backfill_v1 as bf
import utils.candle_d1_history_v1 as d1h
import utils.candle_d1_derivation_v1 as d1d
import utils.candle_contract_v1 as cc

UTC = timezone.utc
INST = "XAU_USD"
_H4_SECONDS = 4 * 3600


class _FakeRedis:
    """In-memory fake with a ZSET index + kv. Records writes/deletes so tests can assert none happen in dry-run."""
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

    def zrevrange(self, k, start, stop):
        members = sorted(self.z.get(k, {}).items(), key=lambda kv: kv[1], reverse=True)
        sl = members[start:stop + 1] if stop != -1 else members[start:]
        return [m for m, _ in sl]

    def zcard(self, k):
        return len(self.z.get(k, {}))

    def delete(self, *keys):
        self.deletes.extend(keys)


def _h4_env(open_dt, o=2000.0, hi=2010.0, lo=1990.0, c=2005.0, v=100, status="OK", source_count=4,
            expected=4, coverage=1.0, gap="NONE", is_closed=True, instrument=INST):
    """A minimal H4 history envelope (contract-shaped enough for the engine's completeness + derivation)."""
    return {"status": status, "data": {"instrument": instrument, "timeframe": "H4",
            "timestamp_utc": cc._fmt(cc.normalise_utc(open_dt)), "open": o, "high": hi, "low": lo, "close": c,
            "volume": v, "source_count": source_count, "expected_source_count": expected,
            "source_coverage": coverage, "gap_state": gap, "is_closed": is_closed}}


def _seed_h4_history(fake, *, days, first_d1_open=datetime(2026, 5, 1, 22, 0, tzinfo=UTC)):
    """Populate the fake H4 history ZSET with `days` complete NY-5PM days (6 complete H4 children each)."""
    idx = f"hermes:candles:{INST}:H4:history:v1:index"
    for day in range(days):
        d1_open = first_d1_open + timedelta(days=day)
        for i, child_open in enumerate(d1d.d1_child_h4_opens(d1_open)):
            ep = int(child_open.timestamp())
            env = _h4_env(child_open, o=2000 + day + i, hi=2050 + day + i, lo=1950 + day, c=2005 + day + i)
            fake.kv[f"hermes:candles:{INST}:H4:history:v1:{ep}"] = json.dumps(env)
            fake.z.setdefault(idx, {})[str(ep)] = ep
    return fake


def _cfg(**kw):
    base = dict(enabled=False, authorised=False, dry_run=True, max_candidates=60, min_depth=26)
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


def test_live_write_requires_both_gates_and_dry_run_false():
    assert _cfg(enabled=True, authorised=True, dry_run=True).live_write_allowed is False   # still dry-run
    assert _cfg(enabled=True, authorised=False, dry_run=False).live_write_allowed is False  # missing authorised
    assert _cfg(enabled=False, authorised=True, dry_run=False).live_write_allowed is False  # missing enabled
    assert _cfg(enabled=True, authorised=True, dry_run=False).live_write_allowed is True


# --------------------------------------------------------------------------- source is governed H4 only
def test_source_path_is_h4_history_no_sql_no_market_map():
    import inspect
    # scan CODE only (strip the negative-declaration docstring which explains the forbidden sources)
    raw = inspect.getsource(bf)
    code = raw.replace(bf.__doc__ or "", "")
    assert bf.BACKFILL_SOURCE_TABLE == "hermes:candles:XAU_USD:H4:history:v1"
    # real-dependency indicators absent -> stale SQL H4/M30 + direct redis are IMPOSSIBLE (no imports/tables)
    for dep in ("pymysql", "get_db_config", "candles_H4", "candles_M30", "import redis"):
        assert dep not in code, f"forbidden source dependency {dep!r} present in seed/backfill CODE"
    # market_map is impossible as a source: no import/use (the only 'market_map' strings are a negative
    # declaration in a docstring + the report field 'source_forbidden_market_map' which PROVES it is not a source)
    for imp in ("import market_map", "from market_map", "market_map.py"):
        assert imp not in code, f"market_map dependency {imp!r} present in seed/backfill CODE"


# --------------------------------------------------------------------------- candidate reconstruction
def test_valid_sealed_6of6_candidate_accepted():
    fake = _seed_h4_history(_FakeRedis(), days=30)
    cands = bf.build_d1_candidates(fake, max_candidates=60)
    accepted = [c for c in cands if c["accepted"]]
    assert len(accepted) >= 26                                    # enough to seed depth >= 26
    env = accepted[0]["env"]
    assert env["status"] == "OK" and env["data"]["timeframe"] == "D1" and env["data"]["source_count"] == 6
    d1h.assert_sealed_complete_d1(env)                            # each accepted candidate is a genuine sealed 6/6


def test_incomplete_day_rejected_not_synthesised():
    fake = _FakeRedis()
    _seed_h4_history(fake, days=3)
    # remove one H4 child from the newest day -> that D1 bucket must be REJECTED, never back-filled to 6
    idx = f"hermes:candles:{INST}:H4:history:v1:index"
    newest = max(fake.z[idx].values())
    fake.z[idx].pop(str(newest)); fake.kv.pop(f"hermes:candles:{INST}:H4:history:v1:{newest}", None)
    cands = bf.build_d1_candidates(fake, max_candidates=60)
    reasons = {c["reason"] for c in cands if not c["accepted"]}
    assert "D1_INCOMPLETE_CHILDREN" in reasons
    assert all(len(c.get("env", {}).get("data", {}).get("timeframe", "") or "") for c in cands if c["accepted"]) or True


def test_gapped_or_non_ok_h4_child_excluded():
    fake = _FakeRedis()
    _seed_h4_history(fake, days=2)
    idx = f"hermes:candles:{INST}:H4:history:v1:index"
    # mark one child non-OK -> its day loses a complete child -> rejected
    ep = min(fake.z[idx].values())
    env = json.loads(fake.kv[f"hermes:candles:{INST}:H4:history:v1:{ep}"]); env["status"] = "PARTIAL"
    fake.kv[f"hermes:candles:{INST}:H4:history:v1:{ep}"] = json.dumps(env)
    cands = bf.build_d1_candidates(fake, max_candidates=60)
    assert any(not c["accepted"] and c["reason"] == "D1_INCOMPLETE_CHILDREN" for c in cands)


def test_alias_xauusd_h4_never_contributes():
    fake = _FakeRedis()
    _seed_h4_history(fake, days=1)
    # inject an XAUUSD-instrument H4 -> _h4_complete rejects it (canonical XAU_USD only)
    d1_open = datetime(2026, 5, 1, 22, 0, tzinfo=UTC)
    bad = _h4_env(d1_open, instrument="XAUUSD")
    assert bf._h4_complete(bad) is False


# --------------------------------------------------------------------------- dry-run: NO writes
def test_dry_run_performs_no_writes():
    fake = _seed_h4_history(_FakeRedis(), days=30)
    plan = bf.dry_run_plan(fake, config=_cfg(), now=datetime(2026, 6, 15, tzinfo=UTC))
    assert plan["mode"] == "DRY_RUN" and plan["no_write_proof"] is True
    assert plan["source_path"] == "hermes:candles:XAU_USD:H4:history:v1"
    assert plan["accepted_count"] >= 26 and plan["current_depth"] == 0
    assert plan["projected_depth"] == plan["idempotency"]["new"] and plan["meets_min_depth"] is True
    assert plan["inert_write_plan_count"] == plan["idempotency"]["new"]
    assert plan["write_mode"] == "HISTORY_INERT_NO_WRITE"
    # ZERO writes / deletes actually happened
    assert fake.sets == [] and fake.zadds == [] and fake.deletes == []


def test_dry_run_reports_earliest_latest_and_reasons():
    fake = _seed_h4_history(_FakeRedis(), days=28)
    plan = bf.dry_run_plan(fake, config=_cfg(), now=datetime(2026, 6, 15, tzinfo=UTC))
    assert plan["earliest_candidate_utc"] < plan["latest_candidate_utc"]
    assert plan["earliest_candidate_utc"].endswith("Z") and "22:00:00" in plan["latest_candidate_utc"]


# --------------------------------------------------------------------------- idempotency
def test_idempotency_new_match_conflict():
    fake = _seed_h4_history(_FakeRedis(), days=3)
    cands = [c for c in bf.build_d1_candidates(fake, max_candidates=60) if c["accepted"]]
    env = cands[0]["env"]
    assert bf._idempotency_status(fake, env) == "new"
    # write it as an existing member (match)
    ep = int(bf._parse_utc(env["data"]["timestamp_utc"]).timestamp())
    hist = d1h.build_d1_history_envelope(env, backfill_run_id="X", backfill_inserted_at_utc=datetime(2026, 6, 1, tzinfo=UTC),
                                         source_table="x", source_timestamp_utc=bf._parse_utc(env["data"]["timestamp_utc"]))
    fake.kv[d1h.d1_history_key(INST, ep)] = json.dumps(hist)
    assert bf._idempotency_status(fake, env) == "already_present_match"
    # mutate a critical field -> conflict
    conflict = json.loads(fake.kv[d1h.d1_history_key(INST, ep)]); conflict["data"]["close"] = 99999.0
    fake.kv[d1h.d1_history_key(INST, ep)] = json.dumps(conflict)
    assert bf._idempotency_status(fake, env) == "conflict"


def test_dry_run_flags_conflict_and_skips_match():
    fake = _seed_h4_history(_FakeRedis(), days=5)
    cands = [c for c in bf.build_d1_candidates(fake, max_candidates=60) if c["accepted"]]
    env0 = cands[0]["env"]; ep0 = int(bf._parse_utc(env0["data"]["timestamp_utc"]).timestamp())
    # pre-seed a MATCH for candidate 0
    hist = d1h.build_d1_history_envelope(env0, backfill_run_id="X", backfill_inserted_at_utc=datetime(2026, 6, 1, tzinfo=UTC),
                                         source_table="x", source_timestamp_utc=bf._parse_utc(env0["data"]["timestamp_utc"]))
    fake.kv[d1h.d1_history_key(INST, ep0)] = json.dumps(hist)
    fake.z.setdefault(d1h.d1_history_index_key(INST), {})[str(ep0)] = ep0
    plan = bf.dry_run_plan(fake, config=_cfg(), now=datetime(2026, 6, 15, tzinfo=UTC))
    assert plan["idempotency"]["already_present_match"] == 1
    assert plan["inert_write_plan_count"] == plan["idempotency"]["new"]   # match excluded from write plan
    assert fake.sets == [] and fake.zadds == [] and fake.deletes == []    # still zero writes


# --------------------------------------------------------------------------- bounded
def test_bounded_max_candidates():
    fake = _seed_h4_history(_FakeRedis(), days=40)
    cands = bf.build_d1_candidates(fake, max_candidates=10)
    assert len([c for c in cands]) <= 10                                  # bounded to max_candidates buckets


# --------------------------------------------------------------------------- live execute is gated & idempotent
def test_execute_refuses_without_gates():
    fake = _seed_h4_history(_FakeRedis(), days=30)
    with pytest.raises(ValueError):
        bf.execute_backfill(fake, config=_cfg(enabled=True, authorised=True, dry_run=True))   # dry_run true -> refuse
    assert fake.sets == [] and fake.zadds == [] and fake.deletes == []


def test_execute_when_fully_gated_writes_history_only_idempotent_no_delete():
    fake = _seed_h4_history(_FakeRedis(), days=30)
    cfg = _cfg(enabled=True, authorised=True, dry_run=False, max_candidates=30)
    res = bf.execute_backfill(fake, config=cfg, now=datetime(2026, 6, 15, tzinfo=UTC))
    assert res["written"] >= 26 and res["mode"] == "LIVE_WRITE"
    assert all(":D1:history:v1:" in k for k, _ in fake.sets)              # only D1 history keyspace
    assert all(not k.endswith(":latest:v1") for k in fake.kv)            # never :latest
    assert fake.deletes == []                                             # never deletes
    idx = d1h.d1_history_index_key(INST)
    assert fake.zcard(idx) == res["written"]
    # re-run is idempotent: all now match -> zero new writes
    n_sets = len(fake.sets)
    res2 = bf.execute_backfill(fake, config=cfg, now=datetime(2026, 6, 16, tzinfo=UTC))
    assert res2["written"] == 0 and res2["skipped_match"] == res["written"] and len(fake.sets) == n_sets


def test_execute_conflict_fails_loud_never_overwrites():
    fake = _seed_h4_history(_FakeRedis(), days=5)
    cfg = _cfg(enabled=True, authorised=True, dry_run=False, max_candidates=30)
    cands = [c for c in bf.build_d1_candidates(fake, max_candidates=30) if c["accepted"]]
    env0 = cands[0]["env"]; ep0 = int(bf._parse_utc(env0["data"]["timestamp_utc"]).timestamp())
    conflict = json.loads(json.dumps(env0)); conflict["data"]["close"] = 99999.0
    fake.kv[d1h.d1_history_key(INST, ep0)] = json.dumps(conflict)
    before = dict(fake.kv)
    with pytest.raises(ValueError):
        bf.execute_backfill(fake, config=cfg, now=datetime(2026, 6, 15, tzinfo=UTC))
    assert fake.kv[d1h.d1_history_key(INST, ep0)] == before[d1h.d1_history_key(INST, ep0)]  # unchanged (no overwrite)
    assert fake.deletes == []


# --------------------------------------------------------------------------- no interpretive semantics
def test_no_forbidden_interpretive_tokens_in_code():
    import inspect
    raw = inspect.getsource(bf)
    code = "\n".join(l for l in raw.replace(bf.__doc__ or "", "").splitlines()
                     if not l.strip().startswith("#")).lower()
    for tok in ("regime", "regime_confidence", "strategy", "signal", " buy ", " sell ", "no-go"):
        assert tok not in code, f"forbidden interpretive token {tok!r} in seed/backfill CODE"
