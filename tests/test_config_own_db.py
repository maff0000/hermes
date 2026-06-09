"""WO-HELM-HERMES-CONFIG-OWN-DB-0001: DEP-4 severance static checks.
No DB/runtime required — verifies source + migration are zeusv4-free, fail-loud, GOV-CFG-001.
"""
import json, os, re

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def _read(rel):
    with open(os.path.join(BASE, rel), encoding="utf-8") as f:
        return f.read()

def test_no_zeusv4_in_main_or_recovery():
    assert "get_zeusv4_config" not in _read("main.py")
    assert "zeusv4_config" not in _read("main.py")
    assert "get_zeusv4_config" not in _read("utils/recovery_executor.py")

def test_keys_read_via_hermes_config():
    m = _read("main.py")
    assert "get_hermes_config('hermes_lookback_hours', 'int')" in m
    assert "get_hermes_config('hermes_min_candles_floor', 'int')" in m
    r = _read("utils/recovery_executor.py")
    assert "get_hermes_config('hermes_lookback_hours', 'int')" in r
    assert "get_hermes_config('hermes_min_candles_floor', 'int')" in r

def test_get_hermes_config_fail_loud_no_default():
    fn = _read("main.py")
    seg = fn[fn.index("def get_hermes_config("):]
    seg = seg[:seg.index("\ndef ", 1)]
    assert "raise ValueError" in seg and "not found in hermes_config" in seg
    assert "enabled = 1" in seg            # disabled rows are not silently used
    assert "tradingProteus" not in seg     # no fallback to retired DB
    assert "GRAYLOG" not in seg            # sanity: unrelated

def test_migration_014_rows_complete_and_json_valid():
    sql = _read("migrations/014_hermes_config_lookback_keys.sql")
    for key, val in (("hermes_lookback_hours", "'24'"), ("hermes_min_candles_floor", "'210'")):
        assert key in sql and val in sql
    # every llm_reasoning blob must be valid JSON with a rationale + provenance
    blobs = re.findall(r"'(\{.*?\})'\)", sql, re.S)
    assert len(blobs) >= 2, f"expected >=2 llm_reasoning JSON blobs, got {len(blobs)}"
    for b in blobs:
        d = json.loads(b)                  # raises if invalid JSON
        assert d.get("rationale") and d.get("migrated_from") and d.get("source")
    # append-only INSERT, no UPSERT
    assert "ON DUPLICATE KEY" not in sql.upper()
    assert "INSERT INTO hermes_config" in sql
