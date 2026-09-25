#!/usr/bin/env python3
"""HMT-2 (real-money checkpoint, MBP-1 quote resolution) — extracts the real, authoritative
activation/expiration listing window for each of the 93 clean GC outright contract mappings
(`research/hmt2/gc-contract-mapping-table-v2.json`) from the SAME already-acquired, already-
committed, already-audited-GREEN `GC.FUT` parent `definition`-schema native artefact
`generate_gc_definitions_v2.py` used — verified below by re-hashing the file and comparing
against that checkpoint's own recorded SHA-256, so this script provably decodes the identical
bytes and makes NO new acquisition of any kind (no `timeseries.get_range` call anywhere in this
file — only a local, read-only `DBNStore.from_file` decode of a file already on disk).

Legitimately imports `databento` (read-only, local file decode only) — lives in
`research/hmt2/`, not `market_truth/acquisition/`, mirroring `generate_gc_definitions_v2.py`'s
own placement rationale exactly.

All filtering / activation-window-resolution logic lives in the pure, tested, network-free
`market_truth.acquisition.gc_active_windows` module — this script only does the one-time
DBN-to-plain-Python translation and writes the resulting evidence file.

Run from the repository root:

    python3 research/hmt2/generate_gc_outright_active_windows_v1.py
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

import databento as db  # noqa: E402  # read-only local decode of the already-retained file only

from market_truth.acquisition import gc_active_windows as gaw  # noqa: E402
from market_truth.futures import ContractMappingTable  # noqa: E402

NATIVE_ARTEFACT_PATH = os.path.join(
    _REPO_ROOT,
    "research-source", "hmt2-gc-mbp1-v1", "sessions",
    "HMT2-REAL-ACQ-GC-FUT-DEFINITIONS", "definitions",
    "gc_fut_definitions_2017-05-21_2026-09-19.dbn.zst",
)

MAPPING_TABLE_PATH = os.path.join(_THIS_DIR, "gc-contract-mapping-table-v2.json")
DEFINITIONS_SUMMARY_PATH = os.path.join(_THIS_DIR, "gc-definitions-processing-summary-v2.json")
OUT_PATH = os.path.join(_THIS_DIR, "gc-outright-active-windows-v1.json")

ACTIVE_WINDOWS_SCHEMA_VERSION = "hmt2-gc-outright-active-windows-v1"


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
    if not os.path.exists(NATIVE_ARTEFACT_PATH):
        raise SystemExit(f"native artefact not found at {NATIVE_ARTEFACT_PATH}")

    artefact_sha256 = _sha256_of_file(NATIVE_ARTEFACT_PATH)
    definitions_summary = json.load(open(DEFINITIONS_SUMMARY_PATH, encoding="utf-8"))
    expected_sha256 = definitions_summary["frozen_source"]["native_artefact_sha256"]
    if artefact_sha256 != expected_sha256:
        raise SystemExit(
            "native artefact SHA-256 mismatch against the already-audited GC.FUT definitions "
            f"processing summary — refusing to proceed (expected {expected_sha256}, got "
            f"{artefact_sha256}). This script must decode the SAME already-acquired file, "
            "never a re-download or a different artefact."
        )

    mapping_table = ContractMappingTable.from_json_file(MAPPING_TABLE_PATH)
    clean_raw_symbols = sorted(mapping_table.entries)

    store = db.DBNStore.from_file(NATIVE_ARTEFACT_PATH)
    df = store.to_df()
    outrights = df[df["instrument_class"] == "F"]
    clean_set = set(clean_raw_symbols)
    relevant = outrights[outrights["raw_symbol"].isin(clean_set)]

    records = [
        gaw.ActivationRecord(
            raw_symbol=str(row.raw_symbol),
            ts_event=row.ts_event.isoformat(),
            activation_utc=row.activation.isoformat(),
            expiration_utc=row.expiration.isoformat(),
        )
        for row in relevant.itertuples(index=False)
    ]

    windows = gaw.latest_activation_window_per_symbol(records, clean_raw_symbols=clean_raw_symbols)

    # Disclosure cross-check (see gc_active_windows module docstring): count how many clean
    # raw_symbols have more than one distinct (activation, expiration) pair across all their
    # own rows — expected exactly 1 (GCX1), from a real, disclosed vendor expiration correction.
    multi_valued = 0
    for symbol in clean_raw_symbols:
        pairs = {
            (r.activation_utc, r.expiration_utc) for r in records if r.raw_symbol == symbol
        }
        if len(pairs) > 1:
            multi_valued += 1

    entries = []
    for symbol in clean_raw_symbols:
        contract = mapping_table.entries[symbol]
        window = windows[symbol]
        entries.append(
            {
                "provider_symbol": symbol,
                "venue": contract.venue,
                "product_root": contract.product_root,
                "delivery_year": contract.delivery_year,
                "delivery_month": contract.delivery_month,
                "activation_utc": window.activation_utc,
                "expiration_utc": window.expiration_utc,
                "source_ts_event": window.source_ts_event,
            }
        )
    entries.sort(key=lambda e: e["provider_symbol"])

    doc = {
        "version": ACTIVE_WINDOWS_SCHEMA_VERSION,
        "source": {
            "native_artefact_relative_path": os.path.relpath(NATIVE_ARTEFACT_PATH, _REPO_ROOT),
            "native_artefact_sha256": artefact_sha256,
            "contract_mapping_table_file": os.path.relpath(MAPPING_TABLE_PATH, _REPO_ROOT),
            "contract_mapping_table_content_sha256": mapping_table.content_sha256(),
        },
        "resolution_method": (
            "latest ts_event (last-write-wins) per clean raw_symbol, over that symbol's own "
            "real outright (instrument_class='F') definition-schema rows"
        ),
        "clean_raw_symbol_count": len(clean_raw_symbols),
        "clean_raw_symbols_with_multiple_distinct_activation_or_expiration_values": multi_valued,
        "entries": entries,
    }

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, sort_keys=True)
        f.write("\n")

    print(
        json.dumps(
            {
                "clean_raw_symbol_count": len(clean_raw_symbols),
                "multi_valued_activation_or_expiration_count": multi_valued,
                "wrote": os.path.relpath(OUT_PATH, _REPO_ROOT),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
