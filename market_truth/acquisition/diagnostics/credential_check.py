#!/usr/bin/env python3
"""market_truth.acquisition.diagnostics.credential_check -- HMT-2 acquisition credential check.

Ports the CONCEPT of Trinity's one-off `hmt2_acquire_credential_check.py` (sealed HELM handoff
bundle) as a real, reusable ops diagnostic, generalised over session id and ledger path instead
of hardcoding either.

Ops diagnostic only -- reuses the existing, unmodified `DatabentoHistoricalProvider` free
metadata call (`get_cost_estimate`, documented there as free/informational/never-billable) both
as the non-billable authentication proof required before any paid request, and to obtain a real
prequote for a named session. Never prints, logs, or returns the API key itself -- only whether
it loaded, and its length (a coarse sanity check, not a partial disclosure).

Intended to be run through the acquisition launcher (`hmt2-acquire-run.sh` /
`ENV_ACQUIRE_LAUNCHER_PATH`), exactly like the real acquisition driver, so it exercises the SAME
credential-access code path (acquisition identity + `SupplementaryGroups=`) it is meant to prove
works -- running it as any other identity would prove nothing about the real path.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from typing import Any, Optional


class CredentialCheckError(RuntimeError):
    """Raised when a required input (ledger row, session id) is missing/invalid. Never raised
    for a credential-load failure itself -- that is reported in the result dict, not raised,
    since "credential did not load" is exactly the failure mode this tool exists to diagnose."""


@dataclass(frozen=True)
class CredentialCheckResult:
    session_id: str
    key_path: Optional[str]
    key_loaded: bool
    key_length: Optional[int]
    key_load_error: Optional[str]
    authentication_succeeded: Optional[bool]
    quoted_cost_usd: Optional[float]
    quoted_record_count: Optional[int]
    quoted_billable_size_bytes: Optional[int]
    auth_error: Optional[str]

    def as_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "key_path": self.key_path,
            "key_loaded": self.key_loaded,
            "key_length": self.key_length,
            "key_load_error": self.key_load_error,
            "authentication_succeeded": self.authentication_succeeded,
            "quoted_cost_usd": self.quoted_cost_usd,
            "quoted_record_count": self.quoted_record_count,
            "quoted_billable_size_bytes": self.quoted_billable_size_bytes,
            "auth_error": self.auth_error,
        }


def run_credential_check(
    *,
    session_id: str,
    ledger: dict,
    key_path_env_value: Optional[str],
    load_api_key_fn,
    provider_factory,
) -> CredentialCheckResult:
    """Pure(ish) core, fully injectable (`load_api_key_fn`, `provider_factory`) so this can be
    unit-tested without a real credential file or real network access. `provider_factory(api_key)`
    must return an object exposing `get_cost_estimate` / `get_record_count_estimate` /
    `get_billable_size_estimate`, matching `DatabentoHistoricalProvider`'s own surface exactly --
    production callers pass the real class; tests pass a double.
    """
    if session_id not in ledger:
        raise CredentialCheckError(f"session_id not present in ledger: {session_id!r}")
    rec = ledger[session_id]

    try:
        api_key = load_api_key_fn()
        key_loaded = True
        key_length: Optional[int] = len(api_key)
        key_load_error = None
    except Exception as exc:  # noqa: BLE001 - intentionally broad; reported, not raised (see class docstring)
        return CredentialCheckResult(
            session_id=session_id,
            key_path=key_path_env_value,
            key_loaded=False,
            key_length=None,
            key_load_error=f"{type(exc).__name__}: {exc}",
            authentication_succeeded=None,
            quoted_cost_usd=None,
            quoted_record_count=None,
            quoted_billable_size_bytes=None,
            auth_error=None,
        )

    provider = provider_factory(api_key)
    kwargs = dict(
        dataset=rec["dataset"], schema=rec["schema"], symbols=rec["symbols"],
        start=rec["start_utc"], end=rec["end_utc"], stype_in=rec["stype_in"],
    )
    try:
        cost_est = provider.get_cost_estimate(**kwargs)
        count_est = provider.get_record_count_estimate(**kwargs)
        size_est = provider.get_billable_size_estimate(**kwargs)
        return CredentialCheckResult(
            session_id=session_id,
            key_path=key_path_env_value,
            key_loaded=key_loaded,
            key_length=key_length,
            key_load_error=key_load_error,
            authentication_succeeded=True,
            quoted_cost_usd=cost_est.quoted_cost_usd,
            quoted_record_count=count_est.record_count,
            quoted_billable_size_bytes=size_est.billable_size_bytes,
            auth_error=None,
        )
    except Exception as exc:  # noqa: BLE001 - reported, not raised; see class docstring
        return CredentialCheckResult(
            session_id=session_id,
            key_path=key_path_env_value,
            key_loaded=key_loaded,
            key_length=key_length,
            key_load_error=key_load_error,
            authentication_succeeded=False,
            quoted_cost_usd=None,
            quoted_record_count=None,
            quoted_billable_size_bytes=None,
            auth_error=f"{type(exc).__name__}: {exc}",
        )


def main(argv: Optional[list] = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--session-id", required=True)
    ap.add_argument("--ledger-path", required=True)
    args = ap.parse_args(argv)

    from market_truth.acquisition.providers.databento_historical import (
        DatabentoHistoricalProvider,
        load_databento_api_key,
    )

    with open(args.ledger_path, "r", encoding="utf-8") as fh:
        ledger = json.load(fh)

    result = run_credential_check(
        session_id=args.session_id,
        ledger=ledger,
        key_path_env_value=os.environ.get("DATABENTO_HISTORICAL_API_KEY_PATH"),
        load_api_key_fn=load_databento_api_key,
        provider_factory=lambda api_key: DatabentoHistoricalProvider(api_key=api_key),
    )
    print(json.dumps(result.as_dict(), indent=2))
    return 0 if result.key_loaded and result.authentication_succeeded else 1


if __name__ == "__main__":
    sys.exit(main())
