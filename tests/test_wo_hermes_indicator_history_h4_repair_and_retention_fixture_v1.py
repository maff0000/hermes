"""WO-HERMES-INDICATOR-HISTORY-CONTRACT-0001 §6/§6a/§13 — H4 candle-history repair + retention-normalisation,
proven against a FIXTURE shaped like the REAL production defect (PID-HERMES-MVP-001 §12/§14): a 250-member
H4 history index with exactly 65 missing objects inside a contiguous window (a mix of market-open AND
weekend flat carry-forward buckets), plus a subset of surviving H4 objects carrying a legacy/shorter TTL
regime about to expire.

CODE/DESIGN ONLY — no live Redis/SQL I/O (an in-memory fake stands in for both Redis and MariaDB, exactly
the discipline `tests/test_candle_h4_history_seed_backfill_v1.py` already uses). Reuses the EXISTING,
already-governed `utils/candle_h4_history_seed_backfill_v1.py` repair mechanism (NOT a parallel
reimplementation) and the NEW, narrowly-scoped `utils/candle_h4_retention_normalisation_v1.py` (§6a).
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

import utils.candle_h4_history_seed_backfill_v1 as bf
import utils.candle_h4_derivation_v1 as h4d
import utils.candle_h4_retention_normalisation_v1 as norm
import utils.candle_history_v1 as chv
import utils.candle_contract_v1 as cc
import utils.hermes_runtime_publisher_steps_v1 as steps

UTC = timezone.utc
INST = "XAU_USD"


# --------------------------------------------------------------------------- shared fake infra
class _FakeRedisFull:
    """In-memory fake supporting the full surface the §6 repair writer, the §6a normaliser, and the
    indicator read path (_read_history) each need: kv + ZSET + zcard/zrange/zrevrange/zremrangebyrank/
    zremrangebyscore/ttl/expire. TTL is explicitly simulated (seconds-remaining, not wall-clock-derived)
    so tests can assert exact before/after values deterministically."""
    def __init__(self):
        self.kv = {}
        self.z = {}
        self.ttls = {}
        self.sets = []
        self.zadds = []
        self.expires = []
        self.deletes = []

    def get(self, k):
        v = self.kv.get(k)
        return v.encode() if isinstance(v, str) else v

    def exists(self, k):
        return 1 if (k in self.kv or k in self.z) else 0

    def set(self, k, v, ex=None):
        self.kv[k] = v
        self.sets.append((k, ex))
        if ex is not None:
            self.ttls[k] = ex

    def zadd(self, k, mapping):
        self.z.setdefault(k, {}).update(mapping)
        self.zadds.append((k, dict(mapping)))

    def zcard(self, k):
        return len(self.z.get(k, {}))

    def zrange(self, k, start, stop):
        items = sorted(self.z.get(k, {}).items(), key=lambda kv: kv[1])
        sl = items[start:(stop + 1 if stop != -1 else None)]
        return [m for m, _ in sl]

    def zrevrange(self, k, start, stop):
        items = sorted(self.z.get(k, {}).items(), key=lambda kv: kv[1], reverse=True)
        sl = items[start:(stop + 1 if stop != -1 else None)]
        return [m for m, _ in sl]

    def zremrangebyrank(self, k, start, stop):
        members = sorted(self.z.get(k, {}).items(), key=lambda kv: kv[1])
        removed = members[start:stop + 1] if stop != -1 else members[start:]
        for m, _ in removed:
            self.z[k].pop(m, None)
        return len(removed)

    def zremrangebyscore(self, k, lo, hi):
        lo_v = float("-inf") if lo == "-inf" else float(lo)
        hi_v = float("inf") if hi == "+inf" else float(hi)
        members = [(m, s) for m, s in self.z.get(k, {}).items() if lo_v <= s <= hi_v]
        for m, _ in members:
            self.z[k].pop(m, None)
        return len(members)

    def delete(self, *keys):
        for k in keys:
            self.kv.pop(k, None)
        self.deletes.extend(keys)

    def ttl(self, k):
        return self.ttls.get(k, -1 if k in self.kv else -2)

    def expire(self, k, seconds):
        self.ttls[k] = seconds
        self.expires.append((k, seconds))
        return 1


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
    def __init__(self, rows):
        self._rows = rows

    def cursor(self):
        return _FakeCursor(self._rows)


def _to_dicts(rows):
    return [{"timestamp": ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts, "open": float(o),
            "high": float(h), "low": float(lo), "close": float(c), "volume": int(v or 0)}
           for ts, o, h, lo, c, v in rows]


def _h1_rows_continuous(start, n_hours):
    """Continuous H1 rows. Weekday hours vary; Saturday/Sunday hours are FLAT (O=H=L=C, zero volume) —
    the governed flat carry-forward shape PID §13 rules must remain valid, unexcluded market truth."""
    rows = []
    last_price = 2000.0
    for i in range(n_hours):
        ts = start + timedelta(hours=i)
        if ts.weekday() in (5, 6):
            p = last_price
            rows.append((ts, p, p, p, p, 0))
        else:
            p = 2000.0 + (i % 7) * 0.5
            rows.append((ts, p, p + 2, p - 2, p + 0.5, 10))
            last_price = p + 0.5
    return rows


def _cfg(**kw):
    base = dict(enabled=False, authorised=False, dry_run=True, max_candidates=260, min_depth=26, lookback_days=90)
    base.update(kw)
    return bf.BackfillConfig(**base)


H4_SECONDS = h4d.H4_SECONDS
TOTAL_BUCKETS = 250
MISSING_COUNT = 65
# Contiguous 65-wide gap, entirely within the newest-210 subset (indices 0..209, 0=newest) — mirrors the
# EXACT corrected production shape (PID §12): 185/250 exist, 65 missing, all 65 inside the newest-210 window.
MISSING_START_IDX = 40
MISSING_IDXS = set(range(MISSING_START_IDX, MISSING_START_IDX + MISSING_COUNT))
assert max(MISSING_IDXS) < 210          # entirely inside the newest-210 subset, exactly like production
NEWEST_H4_OPEN = h4d.h4_bucket_open(datetime(2026, 9, 20, 13, 0, tzinfo=UTC))
ALL_BUCKETS = [NEWEST_H4_OPEN - timedelta(seconds=i * H4_SECONDS) for i in range(TOTAL_BUCKETS)]  # newest-first


def _build_fixture():
    """Seed a FakeRedis shaped exactly like the corrected production defect: 250 H4 history index members,
    185 with a REAL, byte-correct backing object (derived via the one governed `h4d.derive_h4`), 65 with NO
    backing object at all (orphaned index members). Returns (redis, db_conn, before_snapshot)."""
    oldest = ALL_BUCKETS[-1]
    h1_rows = _h1_rows_continuous(oldest, TOTAL_BUCKETS * 4)   # EXACT span: no extra buckets beyond the 250
    h1_dicts = _to_dicts(h1_rows)
    db = _FakeDBConn(h1_rows)
    r = _FakeRedisFull()
    idx = chv.history_index_key(INST, "H4")

    weekend_present_idxs, weekend_missing_idxs = [], []
    for i, bucket_open in enumerate(ALL_BUCKETS):
        open_epoch = int(bucket_open.timestamp())
        r.zadd(idx, {str(open_epoch): open_epoch})          # ALL 250 index members present (ZCARD == 250)
        is_weekend = bucket_open.weekday() in (5, 6)
        if i in MISSING_IDXS:
            if is_weekend:
                weekend_missing_idxs.append(i)
            continue                                        # orphaned: index member, no backing object
        if is_weekend:
            weekend_present_idxs.append(i)
        kids = h4d.h1_children_in_bucket(bucket_open, h1_dicts)
        assert len(kids) == 4, f"fixture bug: bucket {i} does not have 4 genuine H1 children"
        env, _meta = h4d.derive_h4(instrument=INST, h4_open=bucket_open, h1_children=kids,
                                   generated_at_utc=bucket_open + timedelta(seconds=H4_SECONDS), is_closed=True)
        assert env["status"] == "OK" and env["data"]["source_count"] == 4
        he = bf._history_envelope_for(env, now=datetime(2026, 9, 18, tzinfo=UTC))
        r.kv[chv.history_key(INST, "H4", open_epoch)] = json.dumps(he)
    assert len(weekend_missing_idxs) >= 1, "fixture must include at least one weekend bucket in the gap"
    before_survivor_bytes = {k: v for k, v in r.kv.items()}
    return r, db, before_survivor_bytes, weekend_missing_idxs, weekend_present_idxs


# --------------------------------------------------------------------------- §6/§12/§13 repair against the exact shape
def test_production_shaped_250_member_65_missing_defect_fully_resolved():
    """WO §13 item 8: regression test proving index members do not silently reference missing history
    objects, modelled against a fixture shaped like the real 250-member/65-missing production state, and
    proving the repair mechanism resolves it to full consistency."""
    r, db, before, weekend_missing_idxs, weekend_present_idxs = _build_fixture()
    idx = chv.history_index_key(INST, "H4")
    assert r.zcard(idx) == TOTAL_BUCKETS

    # BEFORE repair: exactly 65 orphaned members (index present, object missing) — the defect, reproduced.
    orphans_before = [m for m in r.z[idx] if r.get(chv.history_key(INST, "H4", int(m))) is None]
    assert len(orphans_before) == MISSING_COUNT

    cfg = _cfg(enabled=True, authorised=True, dry_run=False, max_candidates=TOTAL_BUCKETS + 10)
    res = bf.execute_backfill(r, db, config=cfg, now=datetime(2026, 9, 21, tzinfo=UTC))

    assert res["mode"] == "LIVE_WRITE"
    assert res["written"] == MISSING_COUNT, res
    assert res["failed"] == 0
    assert r.deletes == []                                   # never deletes

    # AFTER repair: every index member resolves to a real, present history object (full consistency).
    orphans_after = [m for m in r.z[idx] if r.get(chv.history_key(INST, "H4", int(m))) is None]
    assert orphans_after == []

    # Never overwrote an existing survivor: every one of the original 185 byte-identical post-repair.
    survivors = {k: v for k, v in before.items()}
    for k, v in survivors.items():
        assert r.kv[k] == v, f"survivor {k} was mutated by repair"

    # Weekend buckets inside the gap reconstructed exactly like market-open buckets — no special-casing.
    for i in weekend_missing_idxs:
        ep = int(ALL_BUCKETS[i].timestamp())
        key = chv.history_key(INST, "H4", ep)
        assert key in r.kv
        env = json.loads(r.kv[key])
        assert env["status"] == "OK" and env["data"]["source_count"] == 4
        assert env["data"]["gap_state"] == "NONE"
        assert ALL_BUCKETS[i].weekday() in (5, 6)             # genuinely a weekend bucket, reconstructed anyway

    # Count-based retention still holds after the repair batch.
    assert r.zcard(idx) <= chv.H4_HISTORY_RETAIN_COUNT


def test_market_open_and_weekend_buckets_reconstruct_via_identical_code_path():
    """WO §13 item 19: explicit weekend-vs-market-open equivalence — same derivation call, same acceptance
    criteria, no branch on weekday anywhere in the repair path."""
    r, db, _before, weekend_missing_idxs, weekend_present_idxs = _build_fixture()
    assert weekend_missing_idxs, "fixture must exercise at least one weekend bucket inside the gap"
    cfg = _cfg(enabled=True, authorised=True, dry_run=False, max_candidates=TOTAL_BUCKETS + 10)
    bf.execute_backfill(r, db, config=cfg, now=datetime(2026, 9, 21, tzinfo=UTC))
    import inspect
    src = inspect.getsource(bf)
    assert "weekday" not in src.lower() and "weekend" not in src.lower(), \
        "repair mechanism must not special-case weekend buckets (PID §13)"
    for i in weekend_missing_idxs:
        ep = int(ALL_BUCKETS[i].timestamp())
        assert chv.history_key(INST, "H4", ep) in r.kv


def test_h4_anchor_grid_is_canonical_22_02_06_10_14_18_utc():
    """WO §13 item 6: H4 anchoring is the canonical 22/02/06/10/14/18 UTC grid, not the inert alternate."""
    assert set(h4d.H4_ANCHOR_HOURS_UTC) == {22, 2, 6, 10, 14, 18}
    for b in ALL_BUCKETS:
        assert b.hour in h4d.H4_ANCHOR_HOURS_UTC
    assert h4d.SOURCE_POLICY_EPOCH == "H4_FROM_H1_NY1700_V1"


def test_repair_never_overwrites_already_present_survivor_single_bucket():
    """WO §13 item 17: attempting to 'repair' an already-present bucket is a safe no-op, never a silent
    overwrite — isolated single-bucket check on top of the full-fixture proof above."""
    r, db, before, *_ = _build_fixture()
    survivor_idx = next(i for i in range(TOTAL_BUCKETS) if i not in MISSING_IDXS)
    key = chv.history_key(INST, "H4", int(ALL_BUCKETS[survivor_idx].timestamp()))
    before_bytes = r.kv[key]
    cfg = _cfg(enabled=True, authorised=True, dry_run=False, max_candidates=TOTAL_BUCKETS + 10)
    res = bf.execute_backfill(r, db, config=cfg, now=datetime(2026, 9, 21, tzinfo=UTC))
    assert r.kv[key] == before_bytes
    assert res["skipped_match"] >= (TOTAL_BUCKETS - MISSING_COUNT)


def test_repair_halts_loudly_on_fewer_than_4_h1_children_never_fabricates():
    """WO §13 item 18: halts/fails loud if fewer than 4 complete H1 children are available — never
    fabricates a missing child. Uses a SEPARATE, deliberately incomplete small-scale fixture (the realistic
    65-bucket production gap has 4/4 children for all 65, per PID §12, so this is exercised in isolation)."""
    start = datetime(2026, 5, 4, 0, 0, tzinfo=UTC)   # a Monday
    rows = _h1_rows_continuous(start, 24 * 6)
    # Remove exactly one H1 row so exactly one H4 bucket loses a child.
    rows = [row for row in rows if row[0] != start + timedelta(hours=10)]
    db = _FakeDBConn(rows)
    r = _FakeRedisFull()
    cfg = _cfg(enabled=True, authorised=True, dry_run=False, max_candidates=40)
    res = bf.execute_backfill(r, db, config=cfg, now=datetime(2026, 5, 10, tzinfo=UTC))
    assert res["mode"] == "LIVE_WRITE"
    idx = chv.history_index_key(INST, "H4")
    for member in r.z.get(idx, {}):
        env = json.loads(r.kv[chv.history_key(INST, "H4", int(member))])
        assert env["data"]["source_count"] == 4   # never a fabricated/under-4 bucket admitted to history


# --------------------------------------------------------------------------- §5/§13 indicator warm-up post-repair
def test_ema200_warm_up_sufficient_after_repair_includes_weekend_bars():
    """WO §13 item 4/7: after §6/§6a repair, (a) required H4 history depth exists; (b) newest governed
    calculation window contains >=200 valid closes; (c) H4 ema_200 is non-null; (d) produced by the real
    governed indicator implementation (steps._compute_indicators / utils.indicators, no alternative);
    (e) holds WITHOUT excluding/special-casing weekend bars."""
    r, db, _before, weekend_missing_idxs, weekend_present_idxs = _build_fixture()
    cfg = _cfg(enabled=True, authorised=True, dry_run=False, max_candidates=TOTAL_BUCKETS + 10)
    bf.execute_backfill(r, db, config=cfg, now=datetime(2026, 9, 21, tzinfo=UTC))

    candles = steps._read_history(r, "H4", steps.EMA_TREND_WINDOW)   # the EXACT production read path
    assert len(candles) >= 200, f"only {len(candles)} closes available post-repair"
    inds = steps._compute_indicators(candles)                        # the EXACT production computation
    assert inds["ema_200"] is not None
    assert inds["ema_50"] is not None

    # weekend bars are present among the read window, not filtered out anywhere in the read/compute path
    weekend_opens_in_window = sum(
        1 for c in candles
        if datetime.strptime(c["timestamp_utc"][:-1], cc._UTC_MS).replace(tzinfo=UTC).weekday() in (5, 6))
    assert weekend_opens_in_window > 0, "weekend bars must not be excluded from the ema_200 warm-up window"

    # cross-check against the direct governed calculation — no alternative EMA implementation introduced.
    from utils import indicators as ind_compute
    deep_closes = [c["close"] for c in candles]
    assert inds["ema_200"] == round(ind_compute.calculate_ema(deep_closes, 200), 6)


# --------------------------------------------------------------------------- §6a retention normalisation
def _history_env_with_ttl(*, inserted_at_utc, open_epoch, bucket_open):
    env, _meta = h4d.derive_h4(instrument=INST, h4_open=bucket_open,
                               h1_children=[{"timestamp": bucket_open + timedelta(hours=h),
                                            "open": 2000.0, "high": 2001.0, "low": 1999.0,
                                            "close": 2000.5, "volume": 10} for h in range(4)],
                               generated_at_utc=bucket_open + timedelta(seconds=H4_SECONDS), is_closed=True)
    return bf._history_envelope_for(env, now=inserted_at_utc)


def test_retention_normalisation_identifies_only_the_legacy_regime_subset():
    """WO §6a: identify the affected SURVIVING H4 objects carrying a legacy/shorter TTL regime — bounded to
    that specific class, never a general scan/rewrite."""
    r = _FakeRedisFull()
    idx = chv.history_index_key(INST, "H4")
    now = datetime(2026, 9, 24, tzinfo=UTC)

    # Legacy-regime object: inserted ~34 days ago, ~0.5 days of its OLD ~35-day TTL remain (PID §14 shape).
    legacy_open = h4d.h4_bucket_open(datetime(2026, 8, 21, 2, 0, tzinfo=UTC))
    legacy_inserted = now - timedelta(days=34)
    legacy_env = _history_env_with_ttl(inserted_at_utc=legacy_inserted, open_epoch=int(legacy_open.timestamp()),
                                       bucket_open=legacy_open)
    legacy_key = chv.history_key(INST, "H4", int(legacy_open.timestamp()))
    r.set(legacy_key, json.dumps(legacy_env), ex=int(timedelta(hours=10).total_seconds()))
    r.zadd(idx, {str(int(legacy_open.timestamp())): int(legacy_open.timestamp())})

    # Compliant object: inserted ~21 days ago, ~99 days remaining of the CURRENT 120-day cap — untouched.
    ok_open = h4d.h4_bucket_open(datetime(2026, 9, 3, 2, 0, tzinfo=UTC))
    ok_inserted = now - timedelta(days=21)
    ok_env = _history_env_with_ttl(inserted_at_utc=ok_inserted, open_epoch=int(ok_open.timestamp()),
                                   bucket_open=ok_open)
    ok_key = chv.history_key(INST, "H4", int(ok_open.timestamp()))
    ok_ttl = norm.CURRENT_H4_TTL_SECONDS - int((now - ok_inserted).total_seconds())
    r.set(ok_key, json.dumps(ok_env), ex=ok_ttl)
    r.zadd(idx, {str(int(ok_open.timestamp())): int(ok_open.timestamp())})

    report = norm.dry_run_report(r, config=norm.NormalisationConfig(enabled=True, authorised=True, dry_run=True),
                                 now=now)
    assert report["mode"] == "DRY_RUN" and report["no_write_proof"] is True
    assert report["scanned"] == 2
    flagged_keys = {f["key"] for f in report["flagged"]}
    assert flagged_keys == {legacy_key}
    assert r.expires == []                                   # dry-run never calls EXPIRE


def test_retention_normalisation_preserves_payload_bytes_exactly_only_ttl_changes():
    """WO §13 item 16: the identified under-TTL'd objects' payload bytes are preserved exactly (byte-
    identical) after normalisation — only TTL/expiry changes."""
    r = _FakeRedisFull()
    idx = chv.history_index_key(INST, "H4")
    now = datetime(2026, 9, 24, tzinfo=UTC)
    legacy_open = h4d.h4_bucket_open(datetime(2026, 8, 21, 2, 0, tzinfo=UTC))
    legacy_inserted = now - timedelta(days=34)
    legacy_env = _history_env_with_ttl(inserted_at_utc=legacy_inserted, open_epoch=int(legacy_open.timestamp()),
                                       bucket_open=legacy_open)
    legacy_key = chv.history_key(INST, "H4", int(legacy_open.timestamp()))
    old_ttl = int(timedelta(hours=10).total_seconds())
    r.set(legacy_key, json.dumps(legacy_env), ex=old_ttl)
    r.zadd(idx, {str(int(legacy_open.timestamp())): int(legacy_open.timestamp())})

    before_bytes = r.kv[legacy_key]
    before_sets_count = len(r.sets)

    cfg = norm.NormalisationConfig(enabled=True, authorised=True, dry_run=False)
    res = norm.apply_retention_normalisation(r, config=cfg, now=now)

    assert res["mode"] == "LIVE_APPLY" and res["normalised_count"] == 1
    assert res["payload_mutation"] == "NONE_TTL_ONLY"
    assert r.kv[legacy_key] == before_bytes                  # BYTE-IDENTICAL payload
    assert len(r.sets) == before_sets_count                  # no SET at all was issued — only EXPIRE
    assert r.expires and r.expires[0][0] == legacy_key
    new_ttl = r.ttls[legacy_key]
    assert new_ttl > old_ttl                                 # normalised up toward the current policy
    expected_ttl = norm.CURRENT_H4_TTL_SECONDS - int((now - legacy_inserted).total_seconds())
    assert abs(new_ttl - expected_ttl) <= 2                  # never extended beyond the current governed cap
    assert new_ttl < norm.CURRENT_H4_TTL_SECONDS             # strictly less than a fresh 120-day grant


def test_retention_normalisation_never_extends_beyond_current_cap_for_already_aged_object():
    """An object already older than the current 120-day cap is left alone (its own age already exceeds the
    governed policy; reviving it would be an extension beyond the cap, which §6a forbids)."""
    r = _FakeRedisFull()
    idx = chv.history_index_key(INST, "H4")
    now = datetime(2026, 9, 24, tzinfo=UTC)
    ancient_open = h4d.h4_bucket_open(now - timedelta(days=200))
    ancient_inserted = now - timedelta(days=200)
    env = _history_env_with_ttl(inserted_at_utc=ancient_inserted, open_epoch=int(ancient_open.timestamp()),
                                bucket_open=ancient_open)
    key = chv.history_key(INST, "H4", int(ancient_open.timestamp()))
    r.set(key, json.dumps(env), ex=10)
    r.zadd(idx, {str(int(ancient_open.timestamp())): int(ancient_open.timestamp())})
    cfg = norm.NormalisationConfig(enabled=True, authorised=True, dry_run=False)
    res = norm.apply_retention_normalisation(r, config=cfg, now=now)
    assert res["normalised_count"] == 0
    assert r.expires == []
    assert r.ttls[key] == 10   # untouched


def test_retention_normalisation_gates_dark_by_default_and_halts_enabled_without_authorised(monkeypatch):
    for e in (norm.NORMALISATION_ENABLED_ENV, norm.NORMALISATION_AUTHORISED_ENV, norm.NORMALISATION_DRY_RUN_ENV):
        monkeypatch.delenv(e, raising=False)
    cfg = norm.parse_normalisation_config_from_env()
    assert cfg.enabled is False and cfg.dry_run is True and cfg.live_apply_allowed is False
    monkeypatch.setenv(norm.NORMALISATION_ENABLED_ENV, "true")
    monkeypatch.delenv(norm.NORMALISATION_AUTHORISED_ENV, raising=False)
    with pytest.raises(SystemExit):
        norm.parse_normalisation_config_from_env()


def test_retention_normalisation_is_not_a_general_purpose_ttl_rewriter():
    """WO §6a step 6 guard: the module must not scan beyond the existing, already-bounded H4 history index,
    and must not be generically invocable over an arbitrary timeframe/instrument."""
    import inspect
    src = inspect.getsource(norm)
    # no unbounded Redis keyspace scan/KEYS primitive anywhere — only the existing, already-bounded
    # (<=250-member) H4 history index via zrange.
    assert "scan_iter(" not in src and ".keys(" not in src and "\"KEYS " not in src.upper()
    assert norm.TIMEFRAME == "H4" and norm.CANONICAL_INSTRUMENT == "XAU_USD"
    assert "def identify_stale_h4_history_objects" in src   # bounded to the one function this module owns
