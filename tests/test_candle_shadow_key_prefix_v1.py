"""Prefix-parameter unit tests for the candle shadow keyspace.
WO-HERMES-CANDLE-PREFIX-PARAM.

Exactly 2 tests: (1) string interpolation of HERMES_CANDLE_FORWARD_SHADOW_KEY_PREFIX into the shadow
key, and (2) trailing-colon formatting safety. Hermetic — config passed in, no global env mutation,
no live Redis/DB. Active runtime entry points are untouched.
"""
from utils import candle_publisher_v1 as cp

_CANON = "hermes:candles:XAU_USD:M5:latest:v1"


def test_shadow_key_prefix_interpolation():
    # Unset/blank env -> legacy default; behaviour is byte-for-byte unchanged from today.
    assert cp.resolve_shadow_key_prefix({}) == "hermes:shadow:candles:"
    assert cp.to_shadow_key(_CANON) == "hermes:shadow:candles:XAU_USD:M5:latest:v1"

    # Env set -> interpolates into the key; the shadow guard accepts it under the same resolved prefix.
    pfx = cp.resolve_shadow_key_prefix({cp.SHADOW_KEY_PREFIX_ENV: "hermes:shadow:prod:candles"})
    assert pfx == "hermes:shadow:prod:candles:"
    skey = cp.to_shadow_key(_CANON, prefix=pfx)
    assert skey == "hermes:shadow:prod:candles:XAU_USD:M5:latest:v1"
    assert cp.assert_shadow_key(skey, prefix=pfx) is True
    # canonical is still refused under a custom prefix (guard intact)
    try:
        cp.assert_shadow_key(_CANON, prefix=pfx)
        assert False, "canonical key must be refused in shadow mode"
    except ValueError as e:
        assert "GOV-CANDLE-PUB-KEY-002" in str(e)


def test_shadow_key_prefix_trailing_colon_safety():
    # Zero, one, or many trailing colons (and surrounding whitespace) all normalise to exactly one ':'.
    variants = ["hermes:shadow:prod:candles", "hermes:shadow:prod:candles:",
                "hermes:shadow:prod:candles::", "  hermes:shadow:prod:candles  "]
    resolved = {cp.resolve_shadow_key_prefix({cp.SHADOW_KEY_PREFIX_ENV: v}) for v in variants}
    assert resolved == {"hermes:shadow:prod:candles:"}

    # Interpolated key has a single separator — never `::`, never a missing colon.
    skey = cp.to_shadow_key(_CANON, prefix="hermes:shadow:prod:candles:")
    assert "::" not in skey
    assert skey.count("hermes:shadow:prod:candles:") == 1

    # Degenerate input (colons/whitespace only) degrades to the legacy default, not a bare ':' prefix.
    assert cp.resolve_shadow_key_prefix({cp.SHADOW_KEY_PREFIX_ENV: "   "}) == "hermes:shadow:candles:"
    assert cp.resolve_shadow_key_prefix({cp.SHADOW_KEY_PREFIX_ENV: ":::"}) == "hermes:shadow:candles:"
