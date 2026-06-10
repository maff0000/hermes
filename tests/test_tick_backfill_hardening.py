"""WO-HELM-HERMES-MIGRATION-AND-BACKFILL-HARDENING-0001 — hardening tests (no live DB)."""
import os, re
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import sys; sys.path.insert(0, BASE)
import scripts.backfill_tick_seq as bf
from scripts.activation_preflight import assert_no_dual_writer

def _read(rel):
    with open(os.path.join(BASE, rel), encoding="utf-8") as f: return f.read()

M015 = "migrations/015_ticks_seq_contract_version.sql"
M017 = "migrations/017_add_unique_ticks_instrument_seq.sql"
BFILE = "scripts/backfill_tick_seq.py"

# 1. 015 split DDL + explicit ALGORITHM/LOCK
def test_015_split_ddl_with_algorithm_lock():
    s = _read(M015)
    assert "ALGORITHM=INSTANT" in s          # column add
    assert "ALGORITHM=INPLACE" in s and "LOCK=NONE" in s   # index add
    assert s.count("ALTER TABLE ticks") >= 2  # split into separate statements
    assert "idx_ticks_instrument_seq" in s

# 2. 015 does NOT add UNIQUE(instrument,seq)
def test_015_no_unique_instrument_seq():
    s = _read(M015).upper()
    # No UNIQUE-index DDL in 015 (only the non-unique helper index). Prose may mention it.
    assert "ADD UNIQUE" not in s and "UNIQUE INDEX" not in s and "UNIQUE KEY" not in s

# 4/5. 017 present, contains UNIQUE(instrument,seq), documented post-backfill only
def test_017_unique_scaffold_post_backfill_only():
    s = _read(M017)
    assert "UNIQUE" in s.upper() and "uq_ticks_instrument_seq" in s
    assert "instrument, seq" in s or "instrument,seq" in s
    assert "DO NOT APPLY" in s.upper()
    assert "ALGORITHM=INPLACE" in s and "LOCK=NONE" in s

# 6. backfill no per-id row-by-row UPDATE loop
def test_backfill_no_row_by_row_loop():
    s = _read(BFILE)
    assert "for _id in ids" not in s
    assert not re.search(r"UPDATE ticks SET seq=%s.*WHERE id=%s", s)

# 7. backfill uses staging table + set-based mapping
def test_backfill_set_based_staging():
    s = _read(BFILE)
    assert "tick_seq_backfill_staging" in s
    assert "ROW_NUMBER() OVER" in s
    assert "UPDATE ticks t JOIN" in s

# 8/9. dry-run default; --execute requires --confirm
def test_backfill_main_gating():
    assert bf.main([]) == 0                  # dry-run path
    assert bf.main(["--execute"]) == 2       # refuse without --confirm

def test_backfill_run_dry_run_default():
    # run() must NOT execute unless execute AND confirm
    class _Cur:
        def __init__(s, data): s.d=list(data)
        def __enter__(s): return s
        def __exit__(s,*a): return False
        def execute(s, sql, params=None): pass
        def fetchall(s): return s.d.pop(0)
        def fetchone(s): return s.d.pop(0)
    class _Conn:
        def __init__(s, curs): s.c=list(curs)
        def __enter__(s): return s
        def __exit__(s,*a): return False
        def cursor(s): return s.c.pop(0)
        def commit(s): pass
    def get_conn():
        return _Conn([_Cur([[("XAU_USD",)]]), _Cur([(100, 100, 0)])])
    def get_config(k, vt):
        return {"tick_seq_backfill_batch_size": 50000, "tick_contract_version": "hermes.tick.v1"}[k] if vt!="int" else 50000
    class _Log:
        def info(self,*a): pass
    res = bf.run(get_conn, lambda k,vt: 50000 if k=="tick_seq_backfill_batch_size" else "hermes.tick.v1",
                 _Log(), execute=True, confirm=False)   # confirm missing -> dry-run
    assert res["mode"] == "dry-run" and res.get("executed") is False

# 10. evidence JSON shape exists
def test_backfill_emits_evidence_json():
    s = _read(BFILE)
    assert "json.dumps" in s
    assert "TICK_SEQ_BACKFILL_DRYRUN" in s and "TICK_SEQ_BACKFILL_POST_VALIDATE" in s

# 11/12. validation catches duplicate instrument+seq and monotonic/density failures
def test_validation_catches_duplicate_instrument_seq():
    rows = [{"instrument":"X","id":1,"seq":1,"order_index":1},
            {"instrument":"X","id":2,"seq":1,"order_index":2}]   # dup seq
    try: bf.validate_seq_assignment(rows); assert False
    except ValueError as e: assert "GOV-SEQ-102" in str(e)

def test_validation_catches_non_monotonic():
    rows = [{"instrument":"X","id":1,"seq":1,"order_index":1},
            {"instrument":"X","id":2,"seq":3,"order_index":2}]   # gap (not dense)
    try: bf.validate_seq_assignment(rows); assert False
    except ValueError as e: assert "GOV-SEQ-103" in str(e)

def test_validation_catches_duplicate_id():
    rows = [{"instrument":"X","id":1,"seq":1,"order_index":1},
            {"instrument":"X","id":1,"seq":2,"order_index":2}]
    try: bf.validate_seq_assignment(rows); assert False
    except ValueError as e: assert "GOV-SEQ-101" in str(e)

def test_validation_passes_clean():
    rows = [{"instrument":"X","id":1,"seq":1,"order_index":1},
            {"instrument":"X","id":2,"seq":2,"order_index":2},
            {"instrument":"Y","id":3,"seq":5,"order_index":1},
            {"instrument":"Y","id":4,"seq":6,"order_index":2}]
    assert bf.validate_seq_assignment(rows) is True

# dual-writer tripwire
def test_dual_writer_tripwire():
    assert assert_no_dual_writer(structure_engine_authoritative=True, hermes_writer_enabled=False) is True
    assert assert_no_dual_writer(structure_engine_authoritative=False, hermes_writer_enabled=True) is True
    try:
        assert_no_dual_writer(structure_engine_authoritative=True, hermes_writer_enabled=True); assert False
    except RuntimeError as e: assert "GOV-DUAL-WRITER-001" in str(e)

# 13/14/15. no activation/structure_engine/sys.path in changed files
def test_no_activation_or_boundary_violation_in_changed_files():
    # production/migration files only (this test file legitimately names the forbidden strings to assert their absence)
    for rel in [M015, M017, BFILE, "scripts/activation_preflight.py"]:
        s = _read(rel)
        assert "tick_stream_enabled=true" not in s.lower().replace(" ", "")  # no live stream enable
        assert "/srv-dev/tradingProteus" not in s and "/srv/tradingProteus" not in s  # no proteus path
        for line in s.splitlines():
            ls = line.strip()
            if ls.startswith(("import ", "from ")):
                assert "structure_engine" not in ls, rel
