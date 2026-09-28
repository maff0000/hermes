#!/usr/bin/env python3
"""HMT-2 (real-money checkpoint) — reproduces the real GC outright/spread filtering and
provider-symbol -> canonical-contract mapping-table construction from the retained, real
`GC.FUT` parent `definition`-schema native artefact.

Legitimately imports `databento` (read-only, local file decode — `DBNStore.from_file`, never a
network call, never re-requests the real data) — lives in `research/hmt2/`, not
`market_truth/acquisition/`, mirroring `generate_reference_series_v2.py`'s own placement
rationale exactly.

All filtering / symbol-parsing / mapping-table-construction logic lives in the pure, tested,
network-free `market_truth.acquisition.gc_definitions` module — this script only does the
one-time DBN-to-plain-Python translation and writes the resulting evidence files.

Run from the repository root:

    python3 research/hmt2/generate_gc_definitions_v2.py
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

from market_truth.acquisition import gc_definitions as gd  # noqa: E402
from market_truth.futures import ContractMappingTable  # noqa: E402

NATIVE_ARTEFACT_PATH = os.path.join(
    _REPO_ROOT,
    "research-source", "hmt2-gc-mbp1-v1", "sessions",
    "HMT2-REAL-ACQ-GC-FUT-DEFINITIONS", "definitions",
    "gc_fut_definitions_2017-05-21_2026-09-19.dbn.zst",
)

OUT_SUMMARY_PATH = os.path.join(_THIS_DIR, "gc-definitions-processing-summary-v2.json")
OUT_MAPPING_TABLE_PATH = os.path.join(_THIS_DIR, "gc-contract-mapping-table-v2.json")
MAPPING_TABLE_VERSION = "hmt2-gc-contract-mapping-v2"


def _sha256_of_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def load_definition_records(path: str) -> list[gd.DefinitionRecord]:
    store = db.DBNStore.from_file(path)
    df = store.to_df()
    records = []
    has_expiration = "expiration" in df.columns
    has_maturity_year = "maturity_year" in df.columns
    has_maturity_month = "maturity_month" in df.columns
    for row in df.itertuples(index=False):
        expiration_utc = None
        if has_expiration:
            exp_val = getattr(row, "expiration", None)
            if exp_val is not None:
                try:
                    expiration_utc = exp_val.isoformat()
                except AttributeError:
                    expiration_utc = str(exp_val)
        # Real-data correction (this checkpoint) — the authoritative delivery-year/month source;
        # see market_truth.acquisition.gc_definitions module docstring for why the wire
        # raw_symbol string alone (one-digit real year code, e.g. "GCG8") is NOT reliable.
        maturity_year = None
        if has_maturity_year:
            my_val = getattr(row, "maturity_year", None)
            try:
                if my_val is not None and my_val == my_val and int(my_val) != 0:  # NaN-safe (NaN != NaN)
                    maturity_year = int(my_val)
            except (TypeError, ValueError):
                maturity_year = None
        maturity_month = None
        if has_maturity_month:
            mm_val = getattr(row, "maturity_month", None)
            try:
                if mm_val is not None and mm_val == mm_val and int(mm_val) != 0:
                    maturity_month = int(mm_val)
            except (TypeError, ValueError):
                maturity_month = None
        records.append(
            gd.DefinitionRecord(
                raw_symbol=str(row.raw_symbol),
                instrument_class=str(row.instrument_class),
                instrument_id=int(row.instrument_id),
                expiration_utc=expiration_utc,
                maturity_year=maturity_year,
                maturity_month=maturity_month,
            )
        )
    return records


def main() -> None:
    if not os.path.exists(NATIVE_ARTEFACT_PATH):
        raise SystemExit(f"native artefact not found at {NATIVE_ARTEFACT_PATH}")

    artefact_sha256 = _sha256_of_file(NATIVE_ARTEFACT_PATH)
    records = load_definition_records(NATIVE_ARTEFACT_PATH)

    outrights, others = gd.partition_outrights_and_others(records)
    mapping_result = gd.build_contract_mapping_entries(outrights)

    instrument_class_counts: dict = {}
    for r in records:
        instrument_class_counts[r.instrument_class] = instrument_class_counts.get(r.instrument_class, 0) + 1

    anomaly_reason_counts: dict = {}
    for a in mapping_result.anomalies:
        anomaly_reason_counts[a["reason"]] = anomaly_reason_counts.get(a["reason"], 0) + 1

    unique_outright_raw_symbols = len({r.raw_symbol for r in outrights})

    summary = {
        "processing_version": gd.GC_DEFINITIONS_PROCESSING_VERSION,
        "frozen_source": {
            "provider": "databento",
            "dataset": "GLBX.MDP3",
            "parent_symbol": "GC.FUT",
            "stype_in": "parent",
            "schema": "definition",
            "native_artefact_relative_path": os.path.relpath(NATIVE_ARTEFACT_PATH, _REPO_ROOT),
            "native_artefact_sha256": artefact_sha256,
        },
        "total_definition_records": len(records),
        "instrument_class_counts": instrument_class_counts,
        "outright_count": len(outrights),
        "unique_outright_raw_symbol_count": unique_outright_raw_symbols,
        "non_outright_count": len(others),
        "contract_mapping_entry_count": len(mapping_result.entries),
        "contract_mapping_anomaly_count": len(mapping_result.anomalies),
        "contract_mapping_anomaly_reason_counts": anomaly_reason_counts,
        "contract_mapping_anomalies": mapping_result.anomalies[:200],  # capped for file size
        "contract_mapping_table_file": os.path.relpath(OUT_MAPPING_TABLE_PATH, _REPO_ROOT),
        "real_data_correction_disclosure": (
            "The real Databento raw_symbol wire form for a GC outright is one-digit-year "
            "(e.g. GCG8), genuinely ambiguous across decades; this run uses the authoritative "
            "maturity_year/maturity_month fields, not symbol-string parsing, for delivery "
            "identity — see market_truth.acquisition.gc_definitions module docstring. "
            "CONFLICTING_DUPLICATE_MAPPING anomalies below are the real, expected consequence "
            "of raw_symbol strings that recur across the corpus's ~9.3-year window."
        ),
    }
    with open(OUT_SUMMARY_PATH, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, sort_keys=True)
        f.write("\n")

    mappings_doc = {
        "version": MAPPING_TABLE_VERSION,
        "mappings": [
            {
                "provider_symbol": symbol,
                "venue": contract.venue,
                "product_root": contract.product_root,
                "delivery_year": contract.delivery_year,
                "delivery_month": contract.delivery_month,
            }
            for symbol, contract in sorted(mapping_result.entries.items())
        ],
    }
    with open(OUT_MAPPING_TABLE_PATH, "w", encoding="utf-8") as f:
        json.dump(mappings_doc, f, indent=2, sort_keys=True)
        f.write("\n")

    # Round-trip sanity: the mapping table this script just wrote must load cleanly via the
    # REAL, existing market_truth.futures.ContractMappingTable.from_json_file() reader.
    table = ContractMappingTable.from_json_file(OUT_MAPPING_TABLE_PATH)
    table_content_sha256 = table.content_sha256()

    print(json.dumps({
        "total_definition_records": len(records),
        "instrument_class_counts": instrument_class_counts,
        "outright_count": len(outrights),
        "non_outright_count": len(others),
        "contract_mapping_entry_count": len(mapping_result.entries),
        "contract_mapping_anomaly_count": len(mapping_result.anomalies),
        "contract_mapping_table_content_sha256": table_content_sha256,
    }, indent=2))
    print(f"wrote {OUT_SUMMARY_PATH}")
    print(f"wrote {OUT_MAPPING_TABLE_PATH}")


if __name__ == "__main__":
    main()
