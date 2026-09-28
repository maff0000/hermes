"""HMT-2 (real-money checkpoint, MBP-1 pilot) — Part 3 tests:
  - `acquire_mbp1_pilot_session_data`'s fail-closed dataset/schema/session_id/symbols/range
    validation (never touches the fake vendor client on a rejected input).
  - A successful call sends exactly the real, caller-supplied symbols/start/end/path plus the
    hardcoded dataset="GLBX.MDP3"/schema="mbp-1"/stype_in="raw_symbol" to the vendor client.
  - `compute_request_identity` determinism/order-independence/change-sensitivity.
  - The duplicate-request-reuse check (`hmt2e_mbp1_pilot_acquire.acquire_one_session`): a second
    call with the same request identity must reuse the catalogue entry and never call the
    provider's acquisition method a second time (no duplicate billing).

Every test uses a stub/fake vendor client, exactly mirroring
`test_databento_historical_provider.py`'s own established fake-client discipline — never a real
network call, never the real `databento` package's client class.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from market_truth.acquisition.providers import databento_historical as dbh  # noqa: E402
from market_truth.acquisition.source_store import NativeSourceStore, compute_request_identity  # noqa: E402


def _load_module(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hmt2e = _load_module("hmt2e_mbp1_pilot_acquire", "research/hmt2/hmt2e_mbp1_pilot_acquire.py")


# ----------------------------------------------------------------------------------------------
# Fake vendor client — mirrors test_databento_historical_provider.py's own fixtures exactly.
# ----------------------------------------------------------------------------------------------

class _FakeDBNStore:
    def __init__(self, *, nbytes=0, fake_rows=0):
        self.nbytes = nbytes
        self._fake_rows = fake_rows

    def to_df(self):
        return [None] * self._fake_rows


class _FakeTimeseries:
    def __init__(self, *, nbytes=100, fake_rows=10, raise_error=None):
        self._nbytes = nbytes
        self._fake_rows = fake_rows
        self._raise_error = raise_error
        self.calls = []

    def get_range(self, **kwargs):
        self.calls.append(kwargs)
        if self._raise_error:
            raise self._raise_error
        path = kwargs.get("path")
        if path:
            with open(path, "wb") as f:
                f.write(b"__FAKE_DBN_ZST_PLACEHOLDER_NOT_REAL_VENDOR_BYTES__")
        return _FakeDBNStore(nbytes=self._nbytes, fake_rows=self._fake_rows)


class _FakeHistoricalClient:
    def __init__(self, **timeseries_kwargs):
        self.timeseries = _FakeTimeseries(**timeseries_kwargs)


def _make_provider(**timeseries_kwargs):
    fake_client = _FakeHistoricalClient(**timeseries_kwargs)
    provider = dbh.DatabentoHistoricalProvider(api_key="test-fixture-only-key", client_factory=lambda _key: fake_client)
    return provider, fake_client


# ----------------------------------------------------------------------------------------------
# Fail-closed validation — never touches the vendor client on a rejected input.
# ----------------------------------------------------------------------------------------------

_VALID_SYMBOLS = ["GCG0", "GCH9"]
_VALID_START = "2019-03-21T22:00:00+00:00"
_VALID_END = "2019-03-22T21:00:00+00:00"


def test_rejects_bad_session_id_shape_before_any_network_call(tmp_path):
    provider, fake_client = _make_provider()
    for bad_id in ("GC2019-03-22", "2019-03-22", "", "GC-19-03-22", "GC-2019-3-22"):
        with pytest.raises(dbh.DatabentoScopeError):
            provider.acquire_mbp1_pilot_session_data(
                session_id=bad_id, symbols=_VALID_SYMBOLS, start=_VALID_START, end=_VALID_END,
                path=str(tmp_path / "out.dbn.zst"),
            )
    assert fake_client.timeseries.calls == []


def test_rejects_empty_symbols_before_any_network_call(tmp_path):
    provider, fake_client = _make_provider()
    with pytest.raises(dbh.DatabentoScopeError):
        provider.acquire_mbp1_pilot_session_data(
            session_id="GC-2019-03-22", symbols=[], start=_VALID_START, end=_VALID_END,
            path=str(tmp_path / "out.dbn.zst"),
        )
    assert fake_client.timeseries.calls == []


@pytest.mark.parametrize(
    "bad_symbol", ["GC.v.0", "GC.FUT", "GCZ26-GCH27", "XAU_USD", "", "GC", "gc.fut"]
)
def test_rejects_non_outright_shaped_symbol_before_any_network_call(tmp_path, bad_symbol):
    provider, fake_client = _make_provider()
    with pytest.raises(dbh.DatabentoScopeError):
        provider.acquire_mbp1_pilot_session_data(
            session_id="GC-2019-03-22", symbols=["GCG0", bad_symbol], start=_VALID_START, end=_VALID_END,
            path=str(tmp_path / "out.dbn.zst"),
        )
    assert fake_client.timeseries.calls == []


def test_rejects_start_not_before_end_before_any_network_call(tmp_path):
    provider, fake_client = _make_provider()
    with pytest.raises(dbh.DatabentoScopeError):
        provider.acquire_mbp1_pilot_session_data(
            session_id="GC-2019-03-22", symbols=_VALID_SYMBOLS, start=_VALID_END, end=_VALID_START,
            path=str(tmp_path / "out.dbn.zst"),
        )
    with pytest.raises(dbh.DatabentoScopeError):
        provider.acquire_mbp1_pilot_session_data(
            session_id="GC-2019-03-22", symbols=_VALID_SYMBOLS, start=_VALID_START, end=_VALID_START,
            path=str(tmp_path / "out.dbn.zst"),
        )
    assert fake_client.timeseries.calls == []


def test_successful_call_sends_exactly_the_real_caller_supplied_shape_plus_hardcoded_fields(tmp_path):
    provider, fake_client = _make_provider(nbytes=12345, fake_rows=678)
    out_path = str(tmp_path / "out.dbn.zst")
    result = provider.acquire_mbp1_pilot_session_data(
        session_id="GC-2019-03-22", symbols=_VALID_SYMBOLS, start=_VALID_START, end=_VALID_END, path=out_path,
    )
    assert len(fake_client.timeseries.calls) == 1
    call = fake_client.timeseries.calls[0]
    assert call["dataset"] == "GLBX.MDP3"
    assert call["schema"] == "mbp-1"
    assert call["stype_in"] == "raw_symbol"
    assert call["symbols"] == _VALID_SYMBOLS
    assert call["start"] == _VALID_START
    assert call["end"] == _VALID_END
    assert call["path"] == out_path
    assert result.dataset == "GLBX.MDP3"
    assert result.schema == "mbp-1"
    assert result.nbytes == 12345
    assert result.record_count == 678


# ----------------------------------------------------------------------------------------------
# compute_request_identity — deterministic, order-independent in symbols, change-sensitive.
# ----------------------------------------------------------------------------------------------

def test_request_identity_is_deterministic_and_symbol_order_independent():
    a = compute_request_identity(
        dataset="GLBX.MDP3", schema="mbp-1", symbols=["GCG0", "GCH9"], stype_in="raw_symbol",
        start=_VALID_START, end=_VALID_END,
    )
    b = compute_request_identity(
        dataset="GLBX.MDP3", schema="mbp-1", symbols=["GCH9", "GCG0"], stype_in="raw_symbol",
        start=_VALID_START, end=_VALID_END,
    )
    assert a == b


@pytest.mark.parametrize(
    "changed_kwargs",
    [
        {"dataset": "OTHER.MDP3"},
        {"schema": "trades"},
        {"symbols": ["GCG0"]},
        {"stype_in": "continuous"},
        {"start": "2019-01-01T00:00:00+00:00"},
        {"end": "2019-01-02T00:00:00+00:00"},
    ],
)
def test_request_identity_changes_when_any_field_changes(changed_kwargs):
    base = dict(dataset="GLBX.MDP3", schema="mbp-1", symbols=["GCG0", "GCH9"], stype_in="raw_symbol", start=_VALID_START, end=_VALID_END)
    changed = dict(base, **changed_kwargs)
    assert compute_request_identity(**base) != compute_request_identity(**changed)


# ----------------------------------------------------------------------------------------------
# Duplicate-request-reuse check — no re-request, no duplicate billing.
# ----------------------------------------------------------------------------------------------

class _FakeAcquisitionProvider:
    """Stands in for `DatabentoHistoricalProvider` at the `acquire_one_session` call boundary —
    only `acquire_mbp1_pilot_session_data` is exercised there."""

    def __init__(self, *, nbytes=100, record_count=10):
        self.call_count = 0
        self._nbytes = nbytes
        self._record_count = record_count

    def acquire_mbp1_pilot_session_data(self, *, session_id, symbols, start, end, path):
        self.call_count += 1
        with open(path, "wb") as f:
            f.write(b"X" * self._nbytes)

        class _Result:
            pass

        r = _Result()
        r.record_count = self._record_count
        r.nbytes = self._nbytes
        r.adapter_version = "test-adapter-version"
        return r


def test_second_acquisition_with_same_request_identity_reuses_and_never_recalls_the_provider(tmp_path):
    store = NativeSourceStore(tmp_path)
    fake_provider = _FakeAcquisitionProvider()
    catalogue: dict = {}

    kwargs = dict(
        session_id="GC-2019-03-22", trade_date="2019-03-22", active_raw_symbols=("GCG0", "GCH9"),
        start=_VALID_START, end=_VALID_END, provider=fake_provider, store=store, catalogue=catalogue,
    )

    first = hmt2e.acquire_one_session(**kwargs)
    assert first["reused_existing_artefact"] is False
    assert fake_provider.call_count == 1

    second = hmt2e.acquire_one_session(**kwargs)
    assert second["reused_existing_artefact"] is True
    assert fake_provider.call_count == 1, "must NOT call the (billable) acquisition method a second time"
    assert second["request_identity"] == first["request_identity"]


def test_acquisition_refuses_to_proceed_if_a_file_exists_with_no_matching_catalogue_entry(tmp_path):
    store = NativeSourceStore(tmp_path)
    fake_provider = _FakeAcquisitionProvider()
    catalogue: dict = {}

    # Simulate a stray/partial file from an earlier ambiguous failure, with no catalogue entry.
    from market_truth.acquisition.source_store import artefact_relative_path

    stray_path = tmp_path / artefact_relative_path("GC-2019-03-22", "source", "gc_mbp1_GC-2019-03-22.dbn.zst")
    stray_path.parent.mkdir(parents=True, exist_ok=True)
    stray_path.write_bytes(b"stray-partial-bytes")

    with pytest.raises(RuntimeError):
        hmt2e.acquire_one_session(
            session_id="GC-2019-03-22", trade_date="2019-03-22", active_raw_symbols=("GCG0", "GCH9"),
            start=_VALID_START, end=_VALID_END, provider=fake_provider, store=store, catalogue=catalogue,
        )
    assert fake_provider.call_count == 0, "must never call the acquisition method over an ambiguous stray file"
