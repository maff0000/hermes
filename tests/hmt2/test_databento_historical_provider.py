"""HMT-2B — tests for market_truth.acquisition.providers.databento_historical.

Every test here uses a stub/fake client (a plain Python object with the same method names the
real `databento.Historical` client exposes) injected via `client_factory=` — NEVER a real
network call, and NEVER the real `databento` package's own client class. This mirrors the
HMT-1/HMT-2A no-network-guard discipline (tests/hmt2/test_no_network_guard.py) exactly: this
whole test module is itself network-free, even though the module under test is a real, callable
vendor adapter.

The credential-loading tests use only a temporary file this test creates and deletes — never a
real Databento key, and never the real governed `/srv-dev/secrets/...` path.
"""
from __future__ import annotations

import pytest

from market_truth.acquisition.providers import databento_historical as dbh


# ----------------------------------------------------------------------------------------------
# classify_gc_symbol_shape / filter helpers — pure, vendor-independent
# ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "symbol,expected",
    [
        ("GCZ26", dbh.GcSymbolShape.OUTRIGHT),
        ("GCF17", dbh.GcSymbolShape.OUTRIGHT),
        ("gcz26", dbh.GcSymbolShape.OUTRIGHT),  # case-insensitive
        ("GCZ26-GCH27", dbh.GcSymbolShape.SPREAD_OR_OTHER),  # calendar spread notation
        ("GC.FUT", dbh.GcSymbolShape.SPREAD_OR_OTHER),  # continuous/bare-root-shaped, not an outright
        ("XAU_USD", dbh.GcSymbolShape.SPREAD_OR_OTHER),  # not even a GC symbol
        ("", dbh.GcSymbolShape.SPREAD_OR_OTHER),
        ("GCZ6", dbh.GcSymbolShape.SPREAD_OR_OTHER),  # single-digit year - not the 2-digit shape
    ],
)
def test_classify_gc_symbol_shape(symbol, expected):
    assert dbh.classify_gc_symbol_shape(symbol) == expected


def test_filter_likely_outrights_and_spreads_partition_a_symbol_list():
    symbols = ["GCZ26", "GCZ26-GCH27", "GCF17", "XAU_USD"]
    outrights = dbh.filter_likely_outrights(symbols)
    spreads = dbh.filter_likely_spreads_or_other(symbols)
    assert set(outrights) == {"GCZ26", "GCF17"}
    assert set(spreads) == {"GCZ26-GCH27", "XAU_USD"}
    # Partition property: every symbol appears in exactly one bucket.
    assert set(outrights) | set(spreads) == set(symbols)
    assert set(outrights) & set(spreads) == set()


# ----------------------------------------------------------------------------------------------
# Credential loading — fails closed, never fabricates, never network-touching
# ----------------------------------------------------------------------------------------------

def test_load_api_key_missing_file_fails_closed(tmp_path):
    missing_path = tmp_path / "does_not_exist"
    with pytest.raises(dbh.CredentialLoadError):
        dbh.load_databento_api_key(str(missing_path))


def test_load_api_key_empty_file_fails_closed(tmp_path):
    key_file = tmp_path / "empty_key"
    key_file.write_text("", encoding="utf-8")
    with pytest.raises(dbh.CredentialLoadError):
        dbh.load_databento_api_key(str(key_file))


def test_load_api_key_whitespace_only_file_fails_closed(tmp_path):
    key_file = tmp_path / "whitespace_key"
    key_file.write_text("   \n\t  \n", encoding="utf-8")
    with pytest.raises(dbh.CredentialLoadError):
        dbh.load_databento_api_key(str(key_file))


def test_load_api_key_succeeds_and_strips_whitespace(tmp_path):
    key_file = tmp_path / "real_looking_key"
    key_file.write_text("  not-a-real-databento-key-just-a-test-fixture-value  \n", encoding="utf-8")
    value = dbh.load_databento_api_key(str(key_file))
    assert value == "not-a-real-databento-key-just-a-test-fixture-value"


def test_load_api_key_respects_env_var_override(tmp_path, monkeypatch):
    key_file = tmp_path / "env_pointed_key"
    key_file.write_text("test-fixture-key-value", encoding="utf-8")
    monkeypatch.setenv(dbh.DATABENTO_API_KEY_PATH_ENV_VAR, str(key_file))
    value = dbh.load_databento_api_key()
    assert value == "test-fixture-key-value"


def test_load_api_key_default_path_is_the_governed_secret_location():
    """This is a structural check of the DOCUMENTED constant, not a filesystem access — it must
    never attempt to read the real file."""
    assert dbh.DEFAULT_DATABENTO_API_KEY_PATH == "/srv-dev/secrets/databento_historical_api_key"


def test_credential_load_error_never_contains_a_real_looking_secret_by_construction(tmp_path):
    """The error message on a load failure must only ever reference the PATH, never any file
    content (there is none to leak on a missing/empty file, but this pins the invariant)."""
    missing_path = tmp_path / "nope"
    try:
        dbh.load_databento_api_key(str(missing_path))
        pytest.fail("expected CredentialLoadError")
    except dbh.CredentialLoadError as exc:
        assert str(missing_path) in str(exc)


# ----------------------------------------------------------------------------------------------
# Fake vendor client — mirrors the real databento.Historical client's method surface exactly
# enough for these tests, but is a plain Python object, never the real SDK class.
# ----------------------------------------------------------------------------------------------

class _FakeSymbology:
    def __init__(self, response):
        self._response = response
        self.calls = []

    def resolve(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


class _FakeMetadata:
    def __init__(self, *, cost=0.0, record_count=0, billable_size=0, raise_error=None):
        self._cost = cost
        self._record_count = record_count
        self._billable_size = billable_size
        self._raise_error = raise_error
        self.calls = []

    def get_cost(self, **kwargs):
        self.calls.append(("get_cost", kwargs))
        if self._raise_error:
            raise self._raise_error
        return self._cost

    def get_record_count(self, **kwargs):
        self.calls.append(("get_record_count", kwargs))
        if self._raise_error:
            raise self._raise_error
        return self._record_count

    def get_billable_size(self, **kwargs):
        self.calls.append(("get_billable_size", kwargs))
        if self._raise_error:
            raise self._raise_error
        return self._billable_size


class _FakeHistoricalClient:
    """Stands in for `databento.Historical` — a plain object, never the real vendor class, never
    touching the network."""

    def __init__(self, symbology_response=None, metadata_kwargs=None):
        self.symbology = _FakeSymbology(symbology_response or {})
        self.metadata = _FakeMetadata(**(metadata_kwargs or {}))


def _make_provider(*, api_key="test-fixture-only-key", symbology_response=None, metadata_kwargs=None):
    fake_client = _FakeHistoricalClient(symbology_response=symbology_response, metadata_kwargs=metadata_kwargs)
    provider = dbh.DatabentoHistoricalProvider(api_key=api_key, client_factory=lambda _key: fake_client)
    return provider, fake_client


# ----------------------------------------------------------------------------------------------
# Construction / capabilities
# ----------------------------------------------------------------------------------------------

def test_construction_rejects_empty_api_key():
    with pytest.raises(dbh.CredentialLoadError):
        dbh.DatabentoHistoricalProvider(api_key="", client_factory=lambda _key: _FakeHistoricalClient())
    with pytest.raises(dbh.CredentialLoadError):
        dbh.DatabentoHistoricalProvider(api_key="   ", client_factory=lambda _key: _FakeHistoricalClient())


def test_construction_never_calls_the_network_and_declares_expected_capabilities():
    provider, fake_client = _make_provider()
    assert provider.supports("DEFINITIONS_SYMBOLOGY_RESOLUTION")
    assert provider.supports("COST_METADATA_ESTIMATE")
    assert provider.supports("RECORD_COUNT_METADATA_ESTIMATE")
    assert provider.supports("BILLABLE_SIZE_METADATA_ESTIMATE")
    assert not provider.supports("BULK_HISTORICAL_DOWNLOAD")
    assert not provider.supports("LIVE_STREAMING")
    # Construction must not have triggered any client method call.
    assert fake_client.symbology.calls == []
    assert fake_client.metadata.calls == []


def test_client_factory_receives_the_api_key_and_nothing_leaks():
    received = {}

    def factory(key):
        received["key"] = key
        return _FakeHistoricalClient()

    dbh.DatabentoHistoricalProvider(api_key="test-fixture-only-key", client_factory=factory)
    assert received["key"] == "test-fixture-only-key"


# ----------------------------------------------------------------------------------------------
# resolve_symbology
# ----------------------------------------------------------------------------------------------

def test_resolve_symbology_translates_vendor_response_to_plain_dataclasses():
    response = {
        "result": {
            "GCZ26": [{"d0": "2026-01-01", "d1": "2026-12-31", "s": "GCZ26"}],
        },
        "symbols": ["GCZ26", "GCX99"],
        "not_found": ["GCX99"],
        "partial": [],
    }
    provider, fake_client = _make_provider(symbology_response=response)

    result = provider.resolve_symbology(
        dataset="GLBX.MDP3",
        symbols=["GCZ26", "GCX99"],
        stype_in="raw_symbol",
        stype_out="instrument_id",
        start_date="2026-01-01",
        end_date="2026-12-31",
    )

    assert isinstance(result, dbh.SymbologyResolutionResult)
    assert result.found_symbols() == ("GCZ26",)
    assert result.not_found_symbols() == ("GCX99",)
    resolved = {r.raw_symbol: r for r in result.resolutions}
    assert resolved["GCZ26"].found is True
    assert resolved["GCZ26"].resolved_output_symbols == ("GCZ26",)
    assert resolved["GCX99"].found is False
    assert resolved["GCX99"].resolved_output_symbols == ()

    # Exactly one vendor call was made, with the exact kwargs given.
    assert len(fake_client.symbology.calls) == 1
    call = fake_client.symbology.calls[0]
    assert call["dataset"] == "GLBX.MDP3"
    assert call["stype_in"] == "raw_symbol"
    assert call["stype_out"] == "instrument_id"


def test_resolve_symbology_never_returns_a_vendor_typed_object():
    """The whole point of the adapter boundary: the raw dict-shaped vendor response must never
    itself be handed back to the caller."""
    response = {"result": {"GCZ26": [{"s": "GCZ26"}]}, "not_found": []}
    provider, _ = _make_provider(symbology_response=response)
    result = provider.resolve_symbology(
        dataset="GLBX.MDP3", symbols=["GCZ26"], stype_in="raw_symbol", stype_out="instrument_id",
        start_date="2026-01-01", end_date="2026-01-02",
    )
    assert type(result) is dbh.SymbologyResolutionResult
    assert not hasattr(result, "keys")  # not a dict/mapping - a plain dataclass


def test_resolve_symbology_wraps_and_scrubs_vendor_errors():
    class _RaisingSymbology:
        def resolve(self, **kwargs):
            raise RuntimeError("boom near key=test-fixture-only-key end")

    fake_client = _FakeHistoricalClient()
    fake_client.symbology = _RaisingSymbology()
    provider = dbh.DatabentoHistoricalProvider(
        api_key="test-fixture-only-key", client_factory=lambda _key: fake_client
    )
    with pytest.raises(dbh.DatabentoAdapterError) as exc_info:
        provider.resolve_symbology(
            dataset="GLBX.MDP3", symbols=["GCZ26"], stype_in="raw_symbol", stype_out="instrument_id",
            start_date="2026-01-01", end_date="2026-01-02",
        )
    assert "test-fixture-only-key" not in str(exc_info.value)
    assert "REDACTED" in str(exc_info.value)


# ----------------------------------------------------------------------------------------------
# metadata estimate calls
# ----------------------------------------------------------------------------------------------

def test_get_cost_estimate():
    provider, fake_client = _make_provider(metadata_kwargs={"cost": 12.3456})
    est = provider.get_cost_estimate(
        dataset="GLBX.MDP3", schema="mbp-1", symbols=["GCZ26"], start="2026-01-01", end="2026-01-02",
    )
    assert isinstance(est, dbh.CostEstimate)
    assert est.quoted_cost_usd == 12.3456
    assert fake_client.metadata.calls[0][0] == "get_cost"
    # Default stype_in is the vendor SDK's own default — unchanged behaviour for existing callers.
    assert est.stype_in == "raw_symbol"
    assert fake_client.metadata.calls[0][1]["stype_in"] == "raw_symbol"


def test_get_record_count_estimate():
    provider, fake_client = _make_provider(metadata_kwargs={"record_count": 4200})
    est = provider.get_record_count_estimate(
        dataset="GLBX.MDP3", schema="mbp-1", symbols=["GCZ26"], start="2026-01-01", end="2026-01-02",
    )
    assert isinstance(est, dbh.RecordCountEstimate)
    assert est.record_count == 4200
    assert est.stype_in == "raw_symbol"


def test_get_billable_size_estimate():
    provider, fake_client = _make_provider(metadata_kwargs={"billable_size": 999_999})
    est = provider.get_billable_size_estimate(
        dataset="GLBX.MDP3", schema="mbp-1", symbols=["GCZ26"], start="2026-01-01", end="2026-01-02",
    )
    assert isinstance(est, dbh.BillableSizeEstimate)
    assert est.billable_size_bytes == 999_999
    assert est.stype_in == "raw_symbol"


# ----------------------------------------------------------------------------------------------
# HMT-2B.1 extension: optional `stype_in` passthrough (continuous / parent symbology quotes)
# ----------------------------------------------------------------------------------------------

def test_get_cost_estimate_passes_through_continuous_stype_in():
    provider, fake_client = _make_provider(metadata_kwargs={"cost": 1.23})
    est = provider.get_cost_estimate(
        dataset="GLBX.MDP3",
        schema="ohlcv-1h",
        symbols=["GC.v.0"],
        start="2017-05-21",
        end="2026-09-18",
        stype_in="continuous",
    )
    assert est.stype_in == "continuous"
    assert fake_client.metadata.calls[0][1]["stype_in"] == "continuous"
    assert fake_client.metadata.calls[0][1]["symbols"] == ["GC.v.0"]


def test_get_record_count_estimate_passes_through_parent_stype_in():
    provider, fake_client = _make_provider(metadata_kwargs={"record_count": 7})
    est = provider.get_record_count_estimate(
        dataset="GLBX.MDP3",
        schema="definition",
        symbols=["GC.FUT"],
        start="2017-05-21",
        end="2026-09-18",
        stype_in="parent",
    )
    assert est.stype_in == "parent"
    assert fake_client.metadata.calls[0][1]["stype_in"] == "parent"


def test_get_billable_size_estimate_passes_through_parent_stype_in():
    provider, fake_client = _make_provider(metadata_kwargs={"billable_size": 42})
    est = provider.get_billable_size_estimate(
        dataset="GLBX.MDP3",
        schema="definition",
        symbols=["GC.FUT"],
        start="2017-05-21",
        end="2026-09-18",
        stype_in="parent",
    )
    assert est.stype_in == "parent"
    assert fake_client.metadata.calls[0][1]["stype_in"] == "parent"


def test_metadata_calls_wrap_and_scrub_vendor_errors():
    provider, _ = _make_provider(
        metadata_kwargs={"raise_error": RuntimeError("failure containing test-fixture-only-key")}
    )
    with pytest.raises(dbh.DatabentoAdapterError) as exc_info:
        provider.get_cost_estimate(
            dataset="GLBX.MDP3", schema="mbp-1", symbols=["GCZ26"], start="2026-01-01", end="2026-01-02",
        )
    assert "test-fixture-only-key" not in str(exc_info.value)


# ----------------------------------------------------------------------------------------------
# The deliberately-unimplemented capability
# ----------------------------------------------------------------------------------------------

def test_download_historical_range_always_raises_and_never_calls_the_client():
    provider, fake_client = _make_provider()
    with pytest.raises(dbh.DatabentoScopeError):
        provider.download_historical_range(dataset="GLBX.MDP3", schema="mbp-1", symbols=["GCZ26"])
    assert fake_client.symbology.calls == []
    assert fake_client.metadata.calls == []


def test_provider_has_no_live_streaming_method_at_all():
    provider, _ = _make_provider()
    assert not hasattr(provider, "open_live_stream")
    assert not hasattr(provider, "subscribe")
    assert not hasattr(provider, "live")
