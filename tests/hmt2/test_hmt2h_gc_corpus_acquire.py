"""HMT-2 (real-money checkpoint) — tests for `research/hmt2/hmt2h_gc_corpus_acquire.py`'s
`process_planned_sessions()` orchestration loop: sequential, bounded, no-duplicate-billing,
no-auto-retry-on-ambiguous-failure, stop-before-ceiling-exceeded.

Every test uses a fake vendor-facing provider (exposing the free quote methods PLUS
`acquire_mbp1_pilot_session_data`) and a real `NativeSourceStore`/plain dict catalogue rooted at
`tmp_path` — never a real network call, mirroring `test_mbp1_pilot_acquisition.py`'s own
established fake-provider discipline exactly.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from market_truth.acquisition.source_store import (  # noqa: E402
    NativeSourceStore,
    artefact_relative_path,
    compute_request_identity,
)


def _load_module(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ledger_mod = _load_module("hmt2h_gc_corpus_ledger", "research/hmt2/hmt2h_gc_corpus_ledger.py")
hmt2h_acquire = _load_module("hmt2h_gc_corpus_acquire", "research/hmt2/hmt2h_gc_corpus_acquire.py")


# ----------------------------------------------------------------------------------------------
# Fake provider — quote methods + acquire_mbp1_pilot_session_data, no vendor SDK, no network.
# ----------------------------------------------------------------------------------------------

class _FakeCostEstimate:
    def __init__(self, quoted_cost_usd):
        self.quoted_cost_usd = quoted_cost_usd


class _FakeCountEstimate:
    def __init__(self, record_count):
        self.record_count = record_count


class _FakeSizeEstimate:
    def __init__(self, billable_size_bytes):
        self.billable_size_bytes = billable_size_bytes


class _FakeAcquireResult:
    def __init__(self, record_count, nbytes):
        self.record_count = record_count
        self.nbytes = nbytes
        self.adapter_version = "test-adapter-version"


class _FakeProvider:
    """Deterministic per-symbol-count pricing (`0.01 * len(symbols)`), so ceiling-check tests
    can precisely predict quoted cost. `fail_quote_for`/`fail_acquire_for` let a test inject an
    ambiguous failure at an exact session_id."""

    def __init__(self, *, fail_quote_for=frozenset(), fail_acquire_for=frozenset(), price_per_symbol=0.01):
        self.fail_quote_for = fail_quote_for
        self.fail_acquire_for = fail_acquire_for
        self.price_per_symbol = price_per_symbol
        self.quote_calls = []
        self.acquire_calls = []

    def _price(self, symbols):
        return round(self.price_per_symbol * len(symbols), 10)

    def get_cost_estimate(self, *, dataset, schema, symbols, start, end, stype_in):
        self.quote_calls.append(("cost", symbols, start, end))
        return _FakeCostEstimate(self._price(symbols))

    def get_record_count_estimate(self, *, dataset, schema, symbols, start, end, stype_in):
        return _FakeCountEstimate(len(symbols) * 100)

    def get_billable_size_estimate(self, *, dataset, schema, symbols, start, end, stype_in):
        return _FakeSizeEstimate(len(symbols) * 1000)

    def acquire_mbp1_pilot_session_data(self, *, session_id, symbols, start, end, path):
        self.acquire_calls.append(session_id)
        if session_id in self.fail_acquire_for:
            raise RuntimeError(f"simulated ambiguous vendor failure for {session_id}")
        with open(path, "wb") as f:
            f.write(b"X" * (len(symbols) * 10))
        return _FakeAcquireResult(record_count=len(symbols) * 100, nbytes=len(symbols) * 10)


def _session(session_id, trade_date, symbols):
    return {
        "session_id": session_id, "trade_date": trade_date, "active_raw_symbols": symbols,
        "request_start_utc": f"{trade_date}T22:00:00+00:00", "request_end_utc": f"{trade_date}T23:00:00+00:00",
    }


def _build_ledger(sessions):
    return ledger_mod.build_initial_ledger(
        activity_sessions=sessions, pilot_session_ids=frozenset(), pilot_catalogue={},
        compute_request_identity_fn=compute_request_identity,
    )


# ----------------------------------------------------------------------------------------------
# Happy path — sequential completion in trade_date order, ledger persisted, catalogue updated.
# ----------------------------------------------------------------------------------------------

def test_completes_all_planned_sessions_in_trade_date_order(tmp_path):
    sessions = [
        _session("GC-2020-01-03", "2020-01-03", ["GCG0"]),
        _session("GC-2020-01-01", "2020-01-01", ["GCG0"]),
        _session("GC-2020-01-02", "2020-01-02", ["GCG0"]),
    ]
    ledger = _build_ledger(sessions)
    store = NativeSourceStore(tmp_path)
    provider = _FakeProvider()
    catalogue = {}
    ledger_path = str(tmp_path / "ledger.json")

    result = hmt2h_acquire.process_planned_sessions(
        ledger=ledger, provider=provider, store=store, catalogue=catalogue,
        prior_total_usd=2.415042329108, ledger_state_path=ledger_path, max_sessions=10,
    )

    assert result["completed_this_run"] == ["GC-2020-01-01", "GC-2020-01-02", "GC-2020-01-03"]
    assert result["stopped_reason"] is None
    assert result["ledger_summary"]["by_state"][ledger_mod.STATE_COMPLETE] == 3
    assert result["ledger_summary"]["by_state"][ledger_mod.STATE_PLANNED] == 0
    for sid in ("GC-2020-01-01", "GC-2020-01-02", "GC-2020-01-03"):
        assert ledger[sid]["state"] == ledger_mod.STATE_COMPLETE
        assert ledger[sid]["artefact"]["sha256"]
        assert ledger[sid]["actual_cost_usd"] == pytest.approx(0.01)
    assert len(catalogue) == 3
    reloaded = ledger_mod.load_ledger(ledger_path)
    assert reloaded == ledger


def test_never_re_quotes_or_re_acquires_a_non_planned_entry(tmp_path):
    sessions = [_session("GC-2020-01-01", "2020-01-01", ["GCG0"])]
    ledger = _build_ledger(sessions)
    ledger["GC-2020-01-01"]["state"] = ledger_mod.STATE_COMPLETE  # simulate already-done
    ledger["GC-2020-01-01"]["pilot_reused"] = True
    provider = _FakeProvider()
    result = hmt2h_acquire.process_planned_sessions(
        ledger=ledger, provider=provider, store=NativeSourceStore(tmp_path), catalogue={},
        prior_total_usd=2.415042329108, ledger_state_path=str(tmp_path / "l.json"), max_sessions=10,
    )
    assert result["completed_this_run"] == []
    assert provider.quote_calls == []
    assert provider.acquire_calls == []


# ----------------------------------------------------------------------------------------------
# --max-sessions bound — stop cleanly, remainder stays PLANNED.
# ----------------------------------------------------------------------------------------------

def test_stops_at_max_sessions_leaving_remainder_planned(tmp_path):
    sessions = [_session(f"GC-2020-01-0{i}", f"2020-01-0{i}", ["GCG0"]) for i in range(1, 5)]
    ledger = _build_ledger(sessions)
    result = hmt2h_acquire.process_planned_sessions(
        ledger=ledger, provider=_FakeProvider(), store=NativeSourceStore(tmp_path), catalogue={},
        prior_total_usd=0.0, ledger_state_path=str(tmp_path / "l.json"), max_sessions=2,
    )
    assert result["completed_this_run_count"] == 2
    assert "max-sessions=2" in result["stopped_reason"]
    assert result["ledger_summary"]["by_state"][ledger_mod.STATE_PLANNED] == 2


# ----------------------------------------------------------------------------------------------
# Cost ceiling — stop BEFORE the specific request that would exceed it, never skip past it.
# ----------------------------------------------------------------------------------------------

def test_stops_before_the_specific_request_that_would_exceed_the_ceiling(tmp_path):
    # price_per_symbol=40 -> 1-symbol session quotes $40. prior_total_usd=25 -> after 1 session
    # completes (spend=65), the 2nd session's projected 65+40=105 > 100 -- must stop BEFORE it.
    sessions = [
        _session("GC-2020-01-01", "2020-01-01", ["GCG0"]),
        _session("GC-2020-01-02", "2020-01-02", ["GCH0"]),
    ]
    ledger = _build_ledger(sessions)
    provider = _FakeProvider(price_per_symbol=40.0)
    result = hmt2h_acquire.process_planned_sessions(
        ledger=ledger, provider=provider, store=NativeSourceStore(tmp_path), catalogue={},
        prior_total_usd=25.0, ledger_state_path=str(tmp_path / "l.json"), max_sessions=10,
    )
    assert result["completed_this_run"] == ["GC-2020-01-01"]
    assert "cost ceiling" in result["stopped_reason"]
    assert ledger["GC-2020-01-02"]["state"] == ledger_mod.STATE_PLANNED  # left PLANNED, not skipped-silently
    assert provider.acquire_calls == ["GC-2020-01-01"]  # 2nd session's acquire was never even attempted


# ----------------------------------------------------------------------------------------------
# Ambiguous failure — FAILED_AMBIGUOUS, batch stops, no auto-retry, no further sessions touched.
# ----------------------------------------------------------------------------------------------

def test_ambiguous_acquisition_failure_marks_failed_ambiguous_and_stops_whole_batch(tmp_path):
    sessions = [
        _session("GC-2020-01-01", "2020-01-01", ["GCG0"]),
        _session("GC-2020-01-02", "2020-01-02", ["GCH0"]),
        _session("GC-2020-01-03", "2020-01-03", ["GCJ0"]),
    ]
    ledger = _build_ledger(sessions)
    provider = _FakeProvider(fail_acquire_for={"GC-2020-01-02"})
    result = hmt2h_acquire.process_planned_sessions(
        ledger=ledger, provider=provider, store=NativeSourceStore(tmp_path), catalogue={},
        prior_total_usd=0.0, ledger_state_path=str(tmp_path / "l.json"), max_sessions=10,
    )
    assert result["completed_this_run"] == ["GC-2020-01-01"]
    assert ledger["GC-2020-01-02"]["state"] == ledger_mod.STATE_FAILED_AMBIGUOUS
    assert "simulated ambiguous vendor failure" in ledger["GC-2020-01-02"]["failure_reason"]
    assert ledger["GC-2020-01-03"]["state"] == ledger_mod.STATE_PLANNED  # never attempted
    assert "GC-2020-01-03" not in provider.acquire_calls
    assert "FAILED_AMBIGUOUS" in result["stopped_reason"]


def test_quote_step_failure_leaves_row_planned_and_never_calls_acquire(tmp_path):
    class _RaisingQuoteProvider(_FakeProvider):
        def get_cost_estimate(self, **kwargs):
            raise RuntimeError("simulated free-quote network failure")

    sessions = [_session("GC-2020-01-01", "2020-01-01", ["GCG0"])]
    ledger = _build_ledger(sessions)
    provider = _RaisingQuoteProvider()
    result = hmt2h_acquire.process_planned_sessions(
        ledger=ledger, provider=provider, store=NativeSourceStore(tmp_path), catalogue={},
        prior_total_usd=0.0, ledger_state_path=str(tmp_path / "l.json"), max_sessions=10,
    )
    assert result["completed_this_run"] == []
    assert ledger["GC-2020-01-01"]["state"] == ledger_mod.STATE_PLANNED
    assert provider.acquire_calls == []
    assert "quote step failed" in result["stopped_reason"]


# ----------------------------------------------------------------------------------------------
# compute_prior_actual_spend_usd — pure arithmetic over injected paths, using SYNTHETIC fixture
# files carrying the checkpoint's own real, independently-confirmed figures (never a dependency
# on `research-source/` -- that tree is real local acquisition state, gitignored, and absent on
# a fresh CI checkout; a unit test must never require it to exist).
# ----------------------------------------------------------------------------------------------

def test_compute_prior_actual_spend_usd_sums_the_three_real_baseline_figures(tmp_path):
    reference_series_path = tmp_path / "reference_series_acquisition_evidence.json"
    reference_series_path.write_text(json.dumps({"actual_cost_usd": 0.546575635672}))
    definitions_path = tmp_path / "gc_definitions_acquisition_evidence.json"
    definitions_path.write_text(json.dumps({"actual_cost_usd": 1.692707203329}))
    pilot_quote_path = tmp_path / "hmt2d-mbp1-pilot-selection-and-quote-evidence-v1.json"
    pilot_quote_path.write_text(json.dumps({"gate_5_dollars": {"quoted_cost_usd": 0.175759524107}}))

    prior = hmt2h_acquire.compute_prior_actual_spend_usd(
        reference_series_evidence_path=str(reference_series_path),
        gc_definitions_evidence_path=str(definitions_path),
        pilot_quote_evidence_path=str(pilot_quote_path),
    )
    assert prior["reference_series_usd"] == pytest.approx(0.546575635672, abs=1e-9)
    assert prior["definitions_usd"] == pytest.approx(1.692707203329, abs=1e-9)
    assert prior["pilot_usd"] == pytest.approx(0.175759524107, abs=1e-9)
    assert prior["total_usd"] == pytest.approx(2.415042363108, abs=1e-6)  # matches the checkpoint's own confirmed ~$2.415042329108 (floating-point summation-order, ~3e-8, immaterial)


@pytest.mark.skipif(
    not (os.path.exists(hmt2h_acquire.REFERENCE_SERIES_EVIDENCE_PATH) and os.path.exists(hmt2h_acquire.GC_DEFINITIONS_EVIDENCE_PATH)),
    reason="research-source/ is real, gitignored local acquisition state -- absent on a fresh checkout (e.g. CI); this is an integration smoke-check, not a unit test.",
)
def test_compute_prior_actual_spend_usd_against_the_real_local_evidence_files_when_present():
    prior = hmt2h_acquire.compute_prior_actual_spend_usd()
    assert prior["total_usd"] == pytest.approx(2.415042329108, abs=1e-6)
