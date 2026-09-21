"""HMT-1 — ExactPrice: fixed-point exactness, never binary float (WO §2)."""
from decimal import Decimal

import pytest

from market_truth.contracts import ContractValidationError, ExactPrice


def test_two_textual_forms_of_the_same_price_normalise_identically():
    a = ExactPrice(mantissa=238740, scale=2)      # "2387.40"
    b = ExactPrice(mantissa=2387400, scale=3)     # "2387.400"
    assert a == b
    assert hash(a) == hash(b)
    assert a.identity_text() == b.identity_text()


def test_from_decimal_string_never_routes_through_binary_float():
    a = ExactPrice.from_decimal_string("2387.40")
    b = ExactPrice.from_decimal_string("2387.400")
    assert a == b
    assert a.normalized() == ExactPrice(mantissa=238740, scale=2)


def test_from_decimal_string_matches_direct_construction_for_a_value_float_cannot_represent_exactly():
    # 0.1 + 0.2 != 0.3 in binary float; the fixed-point path must not inherit that behaviour.
    a = ExactPrice.from_decimal_string("0.30")
    b = ExactPrice(mantissa=30, scale=2)
    assert a == b
    assert a.to_decimal() == Decimal("0.30")


def test_from_decimal_string_rejects_non_decimal_text():
    with pytest.raises(ContractValidationError):
        ExactPrice.from_decimal_string("not-a-number")


def test_mantissa_must_be_a_plain_int():
    with pytest.raises(ContractValidationError):
        ExactPrice(mantissa=2387.40, scale=2)  # a float mantissa is exactly the error this type exists to prevent


def test_scale_must_be_non_negative():
    with pytest.raises(ContractValidationError):
        ExactPrice(mantissa=238740, scale=-1)


def test_normalized_strips_only_genuine_trailing_zeros():
    a = ExactPrice(mantissa=238700, scale=2)  # "2387.00" -> normalises down to scale 0
    assert a.normalized() == ExactPrice(mantissa=2387, scale=0)
    b = ExactPrice(mantissa=238701, scale=2)  # no trailing zero to strip
    assert b.normalized() == b


def test_replay_never_varies_due_to_price_serialization_differences():
    """The determinism property this whole numeric type exists for: two independently constructed
    representations of the same logical price must produce byte-identical identity text on every
    call, never dependent on process/run state."""
    values = [
        ExactPrice(mantissa=100, scale=0),
        ExactPrice(mantissa=1000, scale=1),
        ExactPrice.from_decimal_string("100.00"),
        ExactPrice.from_decimal_string("100"),
    ]
    identity_texts = {v.identity_text() for v in values}
    assert len(identity_texts) == 1
