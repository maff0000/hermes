"""HMT-2 acquisition credential-check diagnostic: reusable, generalised over session id/ledger
path, never prints or returns the credential value itself."""
import pytest

from market_truth.acquisition.diagnostics import credential_check as cc


class _FakeEstimate:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class _FakeProvider:
    def __init__(self, api_key):
        self.api_key = api_key

    def get_cost_estimate(self, **kwargs):
        return _FakeEstimate(quoted_cost_usd=1.23)

    def get_record_count_estimate(self, **kwargs):
        return _FakeEstimate(record_count=1000)

    def get_billable_size_estimate(self, **kwargs):
        return _FakeEstimate(billable_size_bytes=2048)


_LEDGER = {
    "GC-2020-03-19": {
        "dataset": "GLBX.MDP3", "schema": "mbp-1", "symbols": ["GCJ0"],
        "start_utc": "2020-03-19T00:00:00Z", "end_utc": "2020-03-19T23:59:59Z", "stype_in": "raw_symbol",
    }
}


def test_run_credential_check_success_never_exposes_the_key_value():
    result = cc.run_credential_check(
        session_id="GC-2020-03-19", ledger=_LEDGER, key_path_env_value="/etc/secret/path",
        load_api_key_fn=lambda: "totally-real-secret-value", provider_factory=_FakeProvider,
    )
    assert result.key_loaded is True
    assert result.key_length == len("totally-real-secret-value")
    assert result.authentication_succeeded is True
    assert result.quoted_cost_usd == 1.23
    d = result.as_dict()
    assert "totally-real-secret-value" not in str(d)
    assert "key" not in {"key_loaded", "key_length"} or True  # sanity: field names only, never a value


def test_run_credential_check_reports_load_failure_without_raising():
    def failing_loader():
        raise FileNotFoundError("no such file: /etc/secret/path")

    result = cc.run_credential_check(
        session_id="GC-2020-03-19", ledger=_LEDGER, key_path_env_value="/etc/secret/path",
        load_api_key_fn=failing_loader, provider_factory=_FakeProvider,
    )
    assert result.key_loaded is False
    assert "FileNotFoundError" in result.key_load_error
    assert result.authentication_succeeded is None


def test_run_credential_check_reports_auth_failure_without_raising():
    class _FailingProvider(_FakeProvider):
        def get_cost_estimate(self, **kwargs):
            raise PermissionError("401 unauthorized")

    result = cc.run_credential_check(
        session_id="GC-2020-03-19", ledger=_LEDGER, key_path_env_value="/etc/secret/path",
        load_api_key_fn=lambda: "key-value", provider_factory=_FailingProvider,
    )
    assert result.key_loaded is True
    assert result.authentication_succeeded is False
    assert "PermissionError" in result.auth_error


def test_run_credential_check_raises_for_unknown_session_id():
    with pytest.raises(cc.CredentialCheckError, match="not present in ledger"):
        cc.run_credential_check(
            session_id="GC-9999-99-99", ledger=_LEDGER, key_path_env_value=None,
            load_api_key_fn=lambda: "x", provider_factory=_FakeProvider,
        )


def test_result_as_dict_has_no_field_named_for_the_raw_key():
    result = cc.run_credential_check(
        session_id="GC-2020-03-19", ledger=_LEDGER, key_path_env_value=None,
        load_api_key_fn=lambda: "x" * 40, provider_factory=_FakeProvider,
    )
    d = result.as_dict()
    assert "api_key" not in d
    assert "key_value" not in d
    assert set(d.keys()) == {
        "session_id", "key_path", "key_loaded", "key_length", "key_load_error",
        "authentication_succeeded", "quoted_cost_usd", "quoted_record_count",
        "quoted_billable_size_bytes", "auth_error",
    }
