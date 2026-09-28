"""HMT-2 (real-money checkpoint) — tests for market_truth.acquisition.gc_definitions.

Every record here is synthetic — never real GC definitions data.

REAL-DATA CORRECTION (this checkpoint): running `generate_gc_definitions_v2.py` against the
real, retained `GC.FUT` definitions artefact for the first time revealed that the real Databento
outright `raw_symbol` wire form is ONE-digit-year (e.g. `GCG8`), not the two-digit-year shape
(`GCZ26`) this test file originally assumed — and that the one-digit form is genuinely,
structurally ambiguous across decades (27 real raw_symbols in this checkpoint's own corpus
denote two different real contracts, roughly a decade apart). This file is updated to test the
corrected design: `DefinitionRecord.maturity_year`/`maturity_month` (the real, authoritative
fields) now drive delivery-year/month identification; `parse_outright_symbol()`'s two-digit
regex is kept, unchanged, purely as a fallback for a record with no maturity fields at all.
"""
from __future__ import annotations

from market_truth.acquisition import gc_definitions as gd
from market_truth.futures import GcContractIdentity


def _outright(raw_symbol, instrument_id=1, expiration_utc=None, maturity_year=None, maturity_month=None):
    return gd.DefinitionRecord(
        raw_symbol=raw_symbol, instrument_class="F", instrument_id=instrument_id,
        expiration_utc=expiration_utc, maturity_year=maturity_year, maturity_month=maturity_month,
    )


def _spread(raw_symbol, instrument_id=2):
    return gd.DefinitionRecord(raw_symbol=raw_symbol, instrument_class="S", instrument_id=instrument_id)


# ----------------------------------------------------------------------------------------------
# is_outright / partition_outrights_and_others
# ----------------------------------------------------------------------------------------------

def test_is_outright_true_only_for_instrument_class_f():
    assert gd.is_outright(_outright("GCZ26"))
    assert not gd.is_outright(_spread("GCZ26-GCH27"))


def test_partition_outrights_and_others_splits_cleanly():
    records = [_outright("GCZ26"), _spread("GCZ26-GCH27"), _outright("GCF17")]
    outrights, others = gd.partition_outrights_and_others(records)
    assert {r.raw_symbol for r in outrights} == {"GCZ26", "GCF17"}
    assert {r.raw_symbol for r in others} == {"GCZ26-GCH27"}
    assert len(outrights) + len(others) == len(records)


# ----------------------------------------------------------------------------------------------
# parse_outright_symbol (unchanged two-digit-year fallback parser)
# ----------------------------------------------------------------------------------------------

def test_parse_outright_symbol_valid_shapes():
    assert gd.parse_outright_symbol("GCZ26") == (2026, 12)
    assert gd.parse_outright_symbol("GCF17") == (2017, 1)
    assert gd.parse_outright_symbol("gcz26") == (2026, 12)  # case-insensitive


def test_parse_outright_symbol_invalid_shapes_return_none():
    assert gd.parse_outright_symbol("GCZ26-GCH27") is None
    assert gd.parse_outright_symbol("GC.FUT") is None
    assert gd.parse_outright_symbol("GCA26") is None  # 'A' is not a valid month code
    assert gd.parse_outright_symbol("") is None
    assert gd.parse_outright_symbol("XAU_USD") is None


def test_parse_outright_symbol_never_raises_on_garbage():
    for garbage in ("", "GC", "GCZ", "GCZ2", "GCZ266", "GCZ26X"):
        assert gd.parse_outright_symbol(garbage) is None


def test_parse_outright_symbol_does_not_recognise_the_real_one_digit_year_wire_form():
    """Documents, explicitly, that the two-digit parser deliberately does NOT parse the real
    Databento one-digit-year wire form — that form is only ever handled via the authoritative
    maturity_year/maturity_month fields (see build_contract_mapping_entries)."""
    assert gd.parse_outright_symbol("GCG8") is None
    assert gd.parse_outright_symbol("GCZ6") is None


# ----------------------------------------------------------------------------------------------
# parse_month_code_letter
# ----------------------------------------------------------------------------------------------

def test_parse_month_code_letter_accepts_one_and_two_digit_year_shapes():
    assert gd.parse_month_code_letter("GCG8") == "G"
    assert gd.parse_month_code_letter("GCZ26") == "Z"
    assert gd.parse_month_code_letter("GCZ6") == "Z"


def test_parse_month_code_letter_invalid_shapes_return_none():
    assert gd.parse_month_code_letter("GCZ26-GCH27") is None
    assert gd.parse_month_code_letter("GCA8") is None
    assert gd.parse_month_code_letter("") is None
    assert gd.parse_month_code_letter(None) is None


# ----------------------------------------------------------------------------------------------
# build_contract_mapping_entries — PRIMARY (real) path: maturity_year/maturity_month
# ----------------------------------------------------------------------------------------------

def test_build_contract_mapping_entries_uses_maturity_fields_for_the_real_one_digit_wire_form():
    records = [
        _outright("GCG8", maturity_year=2018, maturity_month=2, expiration_utc="2018-02-26"),
        _outright("GCZ6", maturity_year=2026, maturity_month=12, expiration_utc="2026-12-28"),
    ]
    result = gd.build_contract_mapping_entries(records)
    assert result.anomalies == ()
    assert result.entries["GCG8"] == GcContractIdentity(delivery_year=2018, delivery_month=2)
    assert result.entries["GCZ6"] == GcContractIdentity(delivery_year=2026, delivery_month=12)


def test_build_contract_mapping_entries_real_ambiguous_symbol_collision_is_caught_not_guessed():
    """The real, structural finding this checkpoint made: the SAME raw_symbol string (e.g.
    "GCF8") genuinely denotes two different real contracts a decade apart, because a contract
    is listed in the definitions feed years before its own expiration. This MUST be caught as
    CONFLICTING_DUPLICATE_MAPPING and excluded — never silently resolved by picking either
    delivery year."""
    records = [
        _outright("GCF8", instrument_id=100, maturity_year=2018, maturity_month=1, expiration_utc="2018-01-26"),
        _outright("GCF8", instrument_id=200, maturity_year=2028, maturity_month=1, expiration_utc="2028-01-26"),
    ]
    result = gd.build_contract_mapping_entries(records)
    assert "GCF8" not in result.entries
    assert any(a["reason"] == "CONFLICTING_DUPLICATE_MAPPING" for a in result.anomalies)


def test_build_contract_mapping_entries_conflicting_symbol_stays_permanently_excluded():
    """Bug-fix regression: once a raw_symbol is flagged CONFLICTING_DUPLICATE_MAPPING, no LATER
    record for that same symbol may silently re-populate the table — even if a later record's
    contract value happens to match one of the earlier, now-excluded values. Real definitions
    feeds republish the same instrument many times; order must never matter for this guarantee.
    """
    records = [
        _outright("GCF8", instrument_id=100, maturity_year=2018, maturity_month=1),
        _outright("GCF8", instrument_id=100, maturity_year=2018, maturity_month=1),  # re-published, same contract
        _outright("GCF8", instrument_id=200, maturity_year=2028, maturity_month=1),  # genuine conflict — poisons it
        _outright("GCF8", instrument_id=100, maturity_year=2018, maturity_month=1),  # re-appears AFTER poisoning
        _outright("GCF8", instrument_id=200, maturity_year=2028, maturity_month=1),  # also re-appears
    ]
    result = gd.build_contract_mapping_entries(records)
    assert "GCF8" not in result.entries
    conflict_anomalies = [a for a in result.anomalies if a["reason"] == "CONFLICTING_DUPLICATE_MAPPING"]
    assert len(conflict_anomalies) == 1  # flagged exactly once, at first detection


def test_build_contract_mapping_entries_flags_symbol_month_code_mismatch():
    # Symbol says month code "G" (February) but maturity_month says June (6) — a genuine
    # data anomaly, never silently trusted one way or the other.
    records = [_outright("GCG8", maturity_year=2018, maturity_month=6)]
    result = gd.build_contract_mapping_entries(records)
    assert result.entries == {}
    assert result.anomalies[0]["reason"] == "SYMBOL_MONTH_CODE_MISMATCH"


def test_build_contract_mapping_entries_flags_expiration_year_mismatch_on_maturity_path():
    records = [_outright("GCZ6", maturity_year=2026, maturity_month=12, expiration_utc="2030-01-01")]
    result = gd.build_contract_mapping_entries(records)
    assert result.entries == {}
    assert result.anomalies[0]["reason"] == "EXPIRATION_YEAR_CROSS_CHECK_MISMATCH"


def test_build_contract_mapping_entries_tolerates_expiration_one_year_adjacent_on_maturity_path():
    records = [_outright("GCZ6", maturity_year=2026, maturity_month=12, expiration_utc="2027-01-02")]
    result = gd.build_contract_mapping_entries(records)
    assert result.anomalies == ()
    assert result.entries["GCZ6"] == GcContractIdentity(delivery_year=2026, delivery_month=12)


def test_build_contract_mapping_entries_is_idempotent_for_a_duplicate_identical_record_on_maturity_path():
    records = [
        _outright("GCZ6", instrument_id=1, maturity_year=2026, maturity_month=12, expiration_utc="2026-12-28"),
        _outright("GCZ6", instrument_id=2, maturity_year=2026, maturity_month=12, expiration_utc="2026-12-28"),
    ]
    result = gd.build_contract_mapping_entries(records)
    assert result.anomalies == ()
    assert len(result.entries) == 1


# ----------------------------------------------------------------------------------------------
# build_contract_mapping_entries — FALLBACK path (no maturity fields at all)
# ----------------------------------------------------------------------------------------------

def test_build_contract_mapping_entries_falls_back_to_symbol_parsing_when_no_maturity_fields():
    records = [_outright("GCZ26", expiration_utc="2026-12-28"), _outright("GCF17", expiration_utc="2017-01-27")]
    result = gd.build_contract_mapping_entries(records)
    assert result.anomalies == ()
    assert result.entries["GCZ26"] == GcContractIdentity(delivery_year=2026, delivery_month=12)
    assert result.entries["GCF17"] == GcContractIdentity(delivery_year=2017, delivery_month=1)


def test_build_contract_mapping_entries_fallback_flags_unparseable_symbol_as_anomaly_not_a_crash():
    records = [_outright("GARBAGE-SYMBOL")]
    result = gd.build_contract_mapping_entries(records)
    assert result.entries == {}
    assert len(result.anomalies) == 1
    assert result.anomalies[0]["reason"] == "UNPARSEABLE_OUTRIGHT_SYMBOL_SHAPE"


def test_build_contract_mapping_entries_fallback_flags_expiration_year_mismatch():
    records = [_outright("GCZ26", expiration_utc="2030-01-01")]
    result = gd.build_contract_mapping_entries(records)
    assert result.entries == {}
    assert result.anomalies[0]["reason"] == "EXPIRATION_YEAR_CROSS_CHECK_MISMATCH"


def test_build_contract_mapping_entries_ignores_non_outright_records_passed_in_by_mistake():
    """Defence-in-depth: even if a caller forgets to pre-filter, a spread's raw_symbol will
    simply fail to parse as an outright shape and be flagged as an anomaly, never silently
    mapped."""
    records = [_spread("GCZ26-GCH27")]
    result = gd.build_contract_mapping_entries(records)
    assert result.entries == {}
    assert result.anomalies[0]["reason"] == "UNPARSEABLE_OUTRIGHT_SYMBOL_SHAPE"


def test_build_contract_mapping_entries_missing_maturity_and_unparseable_symbol_is_an_anomaly():
    """A record with neither maturity fields nor a parseable (two-digit) symbol shape (e.g. the
    real one-digit wire form with no maturity data at all) is honestly flagged, never guessed."""
    records = [_outright("GCG8")]  # no maturity_year/month, one-digit shape unparseable by fallback
    result = gd.build_contract_mapping_entries(records)
    assert result.entries == {}
    assert result.anomalies[0]["reason"] == "UNPARSEABLE_OUTRIGHT_SYMBOL_SHAPE"
