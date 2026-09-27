"""Controlled retry (one-time ledger repair): uses only a load_ledger/save_ledger_atomic/
ledger_content_sha256 test double with the SAME surface as the real, unmodified
hmt2h_gc_corpus_ledger.py; never hand-edits ledger JSON; mandatory post-mutation diff proves only
the target row changed."""
import hashlib
import json

import pytest

from market_truth.acquisition.orchestration import retry as rt

FAILED = "FAILED_AMBIGUOUS"
PLANNED = "PLANNED"


class FakeLedgerModule:
    """Minimal double with EXACTLY the surface `retry.LedgerModule` documents -- proves
    `reset_session_to_planned()` depends on nothing beyond `load_ledger`/`save_ledger_atomic`/
    `ledger_content_sha256`, so it never needs the real, heavier acquisition module tree."""

    def __init__(self, initial: dict):
        self._store = json.dumps(initial)
        self.save_calls = []

    def load_ledger(self, state_path):
        return json.loads(self._store)

    def save_ledger_atomic(self, state_path, ledger):
        self.save_calls.append(dict(ledger))
        self._store = json.dumps(ledger)

    def ledger_content_sha256(self, ledger):
        return hashlib.sha256(json.dumps(ledger, sort_keys=True).encode()).hexdigest()


def _auth(**overrides):
    defaults = dict(
        session_id="GC-2022-09-13", authorised_by="ops", authorised_utc="2026-09-26T00:00:00Z",
        reason="controlled retry after transient DatabentoAdapterError",
    )
    defaults.update(overrides)
    return rt.RetryAuthorization(**defaults)


def _fresh_entry_builder(existing_entry):
    fresh = dict(existing_entry)
    fresh["state"] = PLANNED
    fresh["quote"] = None
    fresh["artefact"] = None
    fresh["actual_cost_usd"] = None
    fresh["failure_reason"] = None
    fresh["ledger_updated_utc"] = None
    return fresh


def test_validate_authorization_requires_grammar_and_named_authoriser():
    with pytest.raises(rt.RetryError, match="grammar"):
        rt.validate_authorization(_auth(session_id="bad"))
    with pytest.raises(rt.RetryError, match="authorised_by"):
        rt.validate_authorization(_auth(authorised_by=""))
    with pytest.raises(rt.RetryError, match="authorised_utc"):
        rt.validate_authorization(_auth(authorised_utc=""))


def test_diff_ledger_rows_detects_changed_added_and_removed():
    before = {"a": {"state": "X"}, "b": {"state": "Y"}}
    after = {"a": {"state": "X"}, "b": {"state": "Z"}, "c": {"state": "NEW"}}
    assert rt.diff_ledger_rows(before, after) == ["b", "c"]


def test_reset_session_to_planned_happy_path():
    initial = {
        "GC-2022-09-13": {"state": FAILED, "request_identity": "req-xyz", "failure_reason": "boom"},
        "GC-2022-09-14": {"state": PLANNED, "request_identity": "req-other"},
    }
    ledger_module = FakeLedgerModule(initial)
    result = rt.reset_session_to_planned(
        ledger_state_path="/fake/ledger.json", authorization=_auth(), ledger_module=ledger_module,
        fresh_entry_state=PLANNED, failed_ambiguous_state=FAILED, build_fresh_entry_fn=_fresh_entry_builder,
    )
    assert result["changed_rows"] == ["GC-2022-09-13"]
    assert result["session_id"] == "GC-2022-09-13"
    assert result["before_sha256"] != result["after_sha256"]

    reloaded = ledger_module.load_ledger("/fake/ledger.json")
    assert reloaded["GC-2022-09-13"]["state"] == PLANNED
    assert reloaded["GC-2022-09-13"]["failure_reason"] is None
    assert reloaded["GC-2022-09-13"]["request_identity"] == "req-xyz"  # preserved unchanged
    # The neighbouring row was never touched.
    assert reloaded["GC-2022-09-14"] == initial["GC-2022-09-14"]


def test_reset_session_to_planned_refuses_unknown_session():
    ledger_module = FakeLedgerModule({})
    with pytest.raises(rt.RetryError, match="unknown session_id"):
        rt.reset_session_to_planned(
            ledger_state_path="/fake/ledger.json", authorization=_auth(), ledger_module=ledger_module,
            fresh_entry_state=PLANNED, failed_ambiguous_state=FAILED, build_fresh_entry_fn=_fresh_entry_builder,
        )


def test_reset_session_to_planned_refuses_non_failed_ambiguous_starting_state():
    initial = {"GC-2022-09-13": {"state": "COMPLETE"}}
    ledger_module = FakeLedgerModule(initial)
    with pytest.raises(rt.RetryError, match="only ever authorised for a"):
        rt.reset_session_to_planned(
            ledger_state_path="/fake/ledger.json", authorization=_auth(), ledger_module=ledger_module,
            fresh_entry_state=PLANNED, failed_ambiguous_state=FAILED, build_fresh_entry_fn=_fresh_entry_builder,
        )
    assert ledger_module.save_calls == []  # never even attempted a write


def test_reset_session_to_planned_raises_if_builder_returns_wrong_state():
    initial = {"GC-2022-09-13": {"state": FAILED}}
    ledger_module = FakeLedgerModule(initial)
    with pytest.raises(rt.RetryError, match="unexpected shape"):
        rt.reset_session_to_planned(
            ledger_state_path="/fake/ledger.json", authorization=_auth(), ledger_module=ledger_module,
            fresh_entry_state=PLANNED, failed_ambiguous_state=FAILED,
            build_fresh_entry_fn=lambda e: {**e, "state": "SOMETHING_ELSE"},
        )


def test_reset_session_to_planned_detects_a_wider_mutation_and_raises():
    """Adversarial: simulates a ledger_module double whose save_ledger_atomic corrupts a
    NEIGHBOURING row too -- the mandatory post-mutation diff must catch this and raise, proving
    the safety net is real and not just decorative."""
    initial = {
        "GC-2022-09-13": {"state": FAILED},
        "GC-2022-09-14": {"state": PLANNED},
    }

    class CorruptingLedgerModule(FakeLedgerModule):
        def save_ledger_atomic(self, state_path, ledger):
            corrupted = dict(ledger)
            corrupted["GC-2022-09-14"] = {"state": "CORRUPTED"}
            super().save_ledger_atomic(state_path, corrupted)

    ledger_module = CorruptingLedgerModule(initial)
    with pytest.raises(rt.RetryError, match="more than the target row changed"):
        rt.reset_session_to_planned(
            ledger_state_path="/fake/ledger.json", authorization=_auth(), ledger_module=ledger_module,
            fresh_entry_state=PLANNED, failed_ambiguous_state=FAILED, build_fresh_entry_fn=_fresh_entry_builder,
        )
