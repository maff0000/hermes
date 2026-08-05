"""Static (DB-free, CI-runnable) validation of migration 025 + its rollback.

WO-HELM-HERMES-ADVANCED-V1-REGISTRY-FOUNDATION-0001. The live apply/rollback is proven separately on an
isolated ephemeral MariaDB (evidence); this guards the migration SQL contract in infra-free CI.
"""
import re
from pathlib import Path

MIG = Path(__file__).resolve().parents[1] / "migrations" / "025_advanced_v1_registry_metadata.sql"
RB = Path(__file__).resolve().parents[1] / "migrations" / "025_advanced_v1_registry_metadata_rollback.sql"


def _mig():
    return MIG.read_text()


def _rb():
    return RB.read_text()


def test_both_files_exist():
    assert MIG.exists() and RB.exists()


def test_energy_added_to_category_enum():
    assert "'energy'" in _mig()
    assert re.search(r"MODIFY category\s+ENUM\([^)]*'energy'[^)]*\)", _mig())


def test_wtico_corrected_to_energy():
    assert re.search(r"UPDATE instruments\s+SET category='energy'.*WHERE symbol='WTICO_USD'", _mig(), re.S)


def test_idempotent_append_only():
    # additive columns use IF NOT EXISTS; migration must not DROP columns or DROP/TRUNCATE any table
    assert "ADD COLUMN IF NOT EXISTS" in _mig()
    assert "DROP COLUMN" not in _mig()
    for forbidden in ("DROP TABLE", "TRUNCATE", "DELETE FROM candles", "DELETE FROM ticks"):
        assert forbidden not in _mig().upper() if forbidden.isupper() else forbidden not in _mig()


def _strip_sql_comments(sql: str) -> str:
    # drop line comments (-- ...) so a word in a comment is not mistaken for a table reference
    return "\n".join(re.sub(r"--.*$", "", line) for line in sql.splitlines())


def test_no_candle_or_tick_history_mutation():
    body = _strip_sql_comments(_mig()).lower()
    for tbl in ("candles_m1", "candles_m5", "candles_m15", "candles_h1", "candles_h4", "candles_d1",
                "candles_m30", "ticks"):
        # a MUTATING statement (DML/DDL) against a history table is forbidden; a word in prose is not
        assert not re.search(rf"\b(insert\s+into|update|delete\s+from|drop\s+table|truncate(\s+table)?|"
                             rf"alter\s+table)\s+`?{tbl}`?\b", body), f"migration must not mutate {tbl}"


def test_capability_flags_default_off():
    # the 7 new instruments must not be activated: capability columns default 0
    m = _mig()
    for col in ("tick_contract_enabled", "indicator_contract_enabled", "gap_detection_enabled"):
        assert re.search(rf"{col}\s+TINYINT\(1\)\s+NOT NULL DEFAULT 0", m), col
    # only XAU_USD is set to capability=1 in the seed (the existing active pilot)
    assert re.search(r"tick_contract_enabled=1.*WHERE symbol='XAU_USD'", m, re.S)
    for sym in ("XAG_USD", "EUR_USD", "GBP_USD", "AUD_USD", "USD_JPY", "SPX500_USD", "WTICO_USD"):
        # no line enabling a capability for a new instrument
        assert not re.search(rf"tick_contract_enabled=1[^;]*WHERE symbol='{sym}'", m)


def test_no_per_ticker_table():
    assert "CREATE TABLE" not in _mig().upper()


def test_rollback_reverts_wtico_before_enum_and_drops_columns():
    rb = _rb()
    # order: revert WTICO off energy BEFORE removing energy from the enum
    i_wtico = rb.find("category='base_metals'")
    i_enum = rb.find("MODIFY category")
    assert 0 <= i_wtico < i_enum, "rollback must revert WTICO before dropping the energy enum member"
    assert "DROP COLUMN IF EXISTS price_precision" in rb
    assert "'energy'" not in re.search(r"MODIFY category\s+ENUM\(([^)]*)\)", rb).group(1)
