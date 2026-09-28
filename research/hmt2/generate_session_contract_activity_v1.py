#!/usr/bin/env python3
"""HMT-2 (real-money checkpoint, MBP-1 quote resolution) — for every one of the 448 governed GC
MBP-1 corpus-selection manifest v2 sessions, determines which of the 93 clean outright contract
mappings were actually live/listed on that session's trade date (a session may involve more
than one concurrently-active outright contract near a roll date).

Pure stdlib logic only (no `databento` import anywhere in this file) — reads two already-
committed evidence files (`corpus-selection-manifest-v2.json`, `gc-outright-active-windows-
v1.json`) and reuses `market_truth.acquisition.gc_active_windows.determine_active_contracts_
for_sessions` (the actual filter). Deterministic and reproducible: run twice, byte-identical
output (see `tests/hmt2/test_session_contract_activity.py`).

Run from the repository root:

    python3 research/hmt2/generate_session_contract_activity_v1.py
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_THIS_DIR, "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from market_truth.acquisition import gc_active_windows as gaw  # noqa: E402

MANIFEST_PATH = os.path.join(_THIS_DIR, "corpus-selection-manifest-v2.json")
ACTIVE_WINDOWS_PATH = os.path.join(_THIS_DIR, "gc-outright-active-windows-v1.json")
OUT_PATH = os.path.join(_THIS_DIR, "gc-session-contract-activity-v1.json")

SESSION_CONTRACT_ACTIVITY_SCHEMA_VERSION = "hmt2-gc-session-contract-activity-v1"


def _sha256_of_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    manifest = json.load(open(MANIFEST_PATH, encoding="utf-8"))
    active_windows_doc = json.load(open(ACTIVE_WINDOWS_PATH, encoding="utf-8"))

    windows = {
        entry["provider_symbol"]: gaw.ActiveWindow(
            raw_symbol=entry["provider_symbol"],
            activation_utc=entry["activation_utc"],
            expiration_utc=entry["expiration_utc"],
            source_ts_event=entry["source_ts_event"],
        )
        for entry in active_windows_doc["entries"]
    }

    rows = manifest["rows"]
    if len(rows) != 448:
        raise SystemExit(f"expected exactly 448 manifest rows, got {len(rows)}")

    active_by_session = gaw.determine_active_contracts_for_sessions(rows, windows)

    sessions_out = []
    contract_session_counts: dict[str, int] = {symbol: 0 for symbol in windows}
    multi_active_count = 0
    zero_active_count = 0
    for row in sorted(rows, key=lambda r: r["gc_trade_date"]):
        session_id = row["session_id"]
        active = active_by_session[session_id]
        if len(active) == 0:
            zero_active_count += 1
        if len(active) > 1:
            multi_active_count += 1
        for symbol in active:
            contract_session_counts[symbol] += 1
        sessions_out.append(
            {
                "session_id": session_id,
                "trade_date": row["gc_trade_date"],
                "request_start_utc": row["request_start_utc"],
                "request_end_utc": row["request_end_utc"],
                "active_raw_symbols": list(active),
            }
        )

    relevant_contract_count = sum(1 for count in contract_session_counts.values() if count > 0)

    doc = {
        "version": SESSION_CONTRACT_ACTIVITY_SCHEMA_VERSION,
        "source": {
            "manifest_file": os.path.relpath(MANIFEST_PATH, _REPO_ROOT),
            "manifest_sha256": manifest["manifest_sha256"],
            "active_windows_file": os.path.relpath(ACTIVE_WINDOWS_PATH, _REPO_ROOT),
            "active_windows_content_sha256": _sha256_of_file(ACTIVE_WINDOWS_PATH),
        },
        "total_sessions": len(rows),
        "clean_outright_contract_count_in_mapping_table": len(windows),
        "relevant_outright_contract_count_for_this_manifest": relevant_contract_count,
        "sessions_with_zero_active_contracts": zero_active_count,
        "sessions_with_multiple_active_contracts": multi_active_count,
        "contract_session_counts": contract_session_counts,
        "sessions": sessions_out,
    }

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, sort_keys=True)
        f.write("\n")

    print(
        json.dumps(
            {
                "total_sessions": len(rows),
                "clean_outright_contract_count_in_mapping_table": len(windows),
                "relevant_outright_contract_count_for_this_manifest": relevant_contract_count,
                "sessions_with_zero_active_contracts": zero_active_count,
                "sessions_with_multiple_active_contracts": multi_active_count,
                "wrote": os.path.relpath(OUT_PATH, _REPO_ROOT),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
