"""Tests for the HERMES tick-gap ledger + stored-row seq semantic + Stage F gate.
WO-HELM-HERMES-TICK-GAP-SEMANTIC-AND-LEDGER-0001.

Pure-logic tests — no DB required. Proves:
  - accepted/unrecoverable gap records are constructed with the governed semantic;
  - scan_hash is deterministic + window-unique (idempotency basis);
  - the stored-row seq semantic is documented (doc file present + version string);
  - the Stage F gate detects required accepted gaps and blocks correctly;
  - no writer/Redis/retention activation is implied by the gate (it only reads flags).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import seed_tick_gap_ledger as seed
import stage_f_gate_check as gate

DOC = os.path.join(os.path.dirname(__file__), "..", "docs", "hermes_tick_seq_semantic.md")


# ---------- seed record construction ----------
def test_build_gap_record_governed_fields():
    r = seed.build_gap_record("AUD_USD", "2026-06-10 20:59:05.025", "2026-06-10 22:07:02.604", 4077,
                              prev_id=22541827, next_id=22542933)
    assert r["status"] == "ACCEPTED"
    assert r["recoverability"] == "UNRECOVERABLE"
    assert r["accepted_policy"] == "STORED_ROW_SEQUENCE"
    assert r["semantic_version"] == "hermes.tick.seq.stored_row.v1"
    assert r["source_cause"] == "POWER_OUTAGE_INGEST_DROPOUT"
    assert r["r2d2_finding_key"].startswith("r2d2:finding:hermes:power_outage_tick_gap")
    assert r["duration_seconds"] == 4077
    assert r["created_by"].startswith("WO-HELM-HERMES-TICK-GAP-SEMANTIC-AND-LEDGER")


# ---------- provenance (R2D2 binding: v2 primary, both v1+v2 recorded) ----------
def test_default_provenance_is_v2_primary_and_records_both():
    import json
    r = seed.build_gap_record("XAU_USD", "2026-06-10 20:59:05", "2026-06-10 22:07:02", 4077)
    # default must NOT be v1-only
    assert r["r2d2_finding_key"] != seed.R2D2_FINDING_KEY_V1
    # primary is the latest finding (v2)
    assert r["r2d2_finding_key"] == seed.R2D2_FINDING_KEY_V2
    # full chain preserved in diagnostic_json
    diag = json.loads(r["diagnostic_json"])
    assert diag["r2d2_findings"] == [seed.R2D2_FINDING_KEY_V1, seed.R2D2_FINDING_KEY_V2]
    assert "provenance_note" in diag


def test_v1_only_provenance_rejected_fail_loud():
    try:
        seed.build_gap_record("XAU_USD", "a", "b", 100, finding_keys=[seed.R2D2_FINDING_KEY_V1])
        assert False, "expected GOV-TICKGAP-002"
    except ValueError as e:
        assert "GOV-TICKGAP-002" in str(e)


def test_explicit_finding_keys_set_primary_to_latest():
    r = seed.build_gap_record("EUR_USD", "a", "b", 100, finding_keys=[seed.R2D2_FINDING_KEY_V2])
    assert r["r2d2_finding_key"] == seed.R2D2_FINDING_KEY_V2


def test_default_finding_keys_constant_not_v1_only():
    assert seed.DEFAULT_R2D2_FINDING_KEYS != [seed.R2D2_FINDING_KEY_V1]
    assert seed.R2D2_FINDING_KEY_V2 in seed.DEFAULT_R2D2_FINDING_KEYS


def test_build_gap_record_rejects_bad_duration():
    for bad in (0, -1, None):
        try:
            seed.build_gap_record("XAU_USD", "a", "b", bad)
            assert False, "expected GOV-TICKGAP-001"
        except ValueError as e:
            assert "GOV-TICKGAP-001" in str(e)


def test_scan_hash_deterministic_and_unique():
    h1 = seed.scan_hash("AUD_USD", "2026-06-10 18:27:45.740", "2026-06-10 19:08:07.561")
    h2 = seed.scan_hash("AUD_USD", "2026-06-10 18:27:45.740", "2026-06-10 19:08:07.561")
    h3 = seed.scan_hash("EUR_USD", "2026-06-10 18:27:45.740", "2026-06-10 19:08:07.561")
    assert h1 == h2 and h1 != h3 and len(h1) == 16


# ---------- dry-run never mutates ----------
class _FakeCur:
    def __init__(self, rows): self._rows = rows; self.executed = []
    def execute(self, sql, params=None): self.executed.append(sql)
    def fetchall(self): return self._rows
    def fetchone(self): return None
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _FakeConn:
    def __init__(self, rows): self._rows = rows; self.committed = False
    def cursor(self): return _FakeCur(self._rows)
    def commit(self): self.committed = True
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _conn_factory(rows):
    def get_conn(): return _FakeConn(rows)
    return get_conn


class _Log:
    def info(self, *a, **k): pass


def test_seed_dry_run_does_not_commit():
    recs = [seed.build_gap_record("AUD_USD", "2026-06-10 18:27:45", "2026-06-10 19:08:07", 2421)]
    out = seed.insert_records(_conn_factory([]), _Log(), recs, execute=False, confirm=False)
    assert out["mode"] == "dry-run"
    assert out["inserted"] == 0
    assert out["candidate"] == 1


# ---------- documentation present ----------
def test_semantic_documented():
    assert os.path.exists(DOC), "stored-row seq semantic doc must exist"
    text = open(DOC, encoding="utf-8").read()
    assert "hermes.tick.seq.stored_row.v1" in text
    assert "stored tick rows" in text
    assert "does not need reset" in text.lower()
    assert "hermes_tick_gap_ledger" in text


# ---------- Stage F gate logic ----------
def _good_state():
    return {
        "semantic_versions": ["hermes.tick.seq.stored_row.v1"],
        "accepted_instruments": ["AUD_USD", "EUR_USD", "USD_JPY", "WTICO_USD", "XAG_USD", "XAU_USD"],
        "open_or_unknown_count": 0,
        "uq_017_present": False,
        "tick_stream_enabled": "false",
        "tick_archive_enabled": "false",
    }


def test_gate_opens_when_all_satisfied():
    res = gate.evaluate_gate(_good_state())
    assert res["gate_open"] is True
    assert res["blocking"] == []


def test_gate_blocks_on_missing_accepted_gaps():
    s = _good_state(); s["accepted_instruments"] = ["AUD_USD"]
    res = gate.evaluate_gate(s)
    assert res["gate_open"] is False
    assert "accepted_all_instruments" in res["blocking"]


def test_gate_blocks_on_open_or_unknown():
    s = _good_state(); s["open_or_unknown_count"] = 1
    assert gate.evaluate_gate(s)["gate_open"] is False


def test_gate_blocks_when_017_present():
    s = _good_state(); s["uq_017_present"] = True
    res = gate.evaluate_gate(s)
    assert res["gate_open"] is False
    assert "mig_017_absent" in res["blocking"]


def test_gate_blocks_when_writer_or_retention_active():
    for k in ("tick_stream_enabled", "tick_archive_enabled"):
        s = _good_state(); s[k] = "true"
        assert gate.evaluate_gate(s)["gate_open"] is False


def test_gate_blocks_when_semantic_missing():
    s = _good_state(); s["semantic_versions"] = []
    res = gate.evaluate_gate(s)
    assert res["gate_open"] is False
    assert "semantic_present" in res["blocking"]


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        try:
            fn(); passed += 1; print(f"PASS {fn.__name__}")
        except Exception:
            print(f"FAIL {fn.__name__}"); traceback.print_exc()
    print(f"\n{passed}/{len(fns)} passed")
    raise SystemExit(0 if passed == len(fns) else 1)
