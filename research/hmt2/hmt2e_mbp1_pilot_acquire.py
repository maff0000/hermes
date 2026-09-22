#!/usr/bin/env python3
"""HMT-2 (real-money checkpoint) — Part 3: the real, bounded, once-only MBP-1 pilot
acquisition for the two sessions `hmt2d_mbp1_pilot_selection_and_quote.py` deterministically
selected (Part 1) and free-quoted (Part 2, already reconfirmed <= the governed $5.00 pilot cap).

Explicit-invocation only (never auto-triggered by any other code path):

    /tmp/hmt2-work-venv/bin/python3 research/hmt2/hmt2e_mbp1_pilot_acquire.py

For EACH of the two pilot sessions:
    1. Re-derive the pilot selection deterministically (never trusts a stale hardcoded id).
    2. Look up the session's own already-governed active outright raw_symbol set from
       `gc-session-contract-activity-v1.json` (Part 2 lookup — reused, never recomputed) and
       its own governed UTC session window.
    3. Compute this exact request's `request_identity`
       (`market_truth.acquisition.source_store.compute_request_identity`) and check the local
       acquisition catalogue for an already-completed artefact with that SAME identity — if
       found, reuse it (never re-request, never double-bill) and skip straight to reporting.
    4. Otherwise: call `DatabentoHistoricalProvider.acquire_mbp1_pilot_session_data()` exactly
       once for this session (the vendor bytes stream straight to the session's own `source/`
       file — never held fully in memory).
    5. On success: verify the file is fully and correctly written (non-empty, exists), THEN
       compute its sha256/byte-size, THEN build and write the `NativeArtefactRecord` evidence
       JSON, THEN update the catalogue — evidence is only ever written after the artefact bytes
       it describes are confirmed complete on disk (WO Part 3 atomicity requirement).
    6. On any ambiguous failure (partial write, decode error, network timeout): this script
       does NOT catch and retry — it lets the exception propagate, prints the on-disk state of
       any partial artefact for the session in progress, and exits non-zero. The caller (a
       human) must inspect and decide next steps; this preserves the no-duplicate-billing
       invariant for whoever investigates next.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_THIS_DIR, "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from hmt2d_mbp1_pilot_selection_and_quote import (  # noqa: E402
    select_pilot_pair,
)
from market_truth.acquisition.corpus_manifest_v2 import read_manifest_v2  # noqa: E402
from market_truth.acquisition.providers.databento_historical import (  # noqa: E402
    DatabentoHistoricalProvider,
    load_databento_api_key,
)
from market_truth.acquisition.source_store import (  # noqa: E402
    NativeArtefactRecord,
    NativeSourceStore,
    artefact_relative_path,
    compute_request_identity,
    manifest_relative_path,
)

DATASET = "GLBX.MDP3"
SCHEMA = "mbp-1"
STYPE_IN = "raw_symbol"
CORPUS_VERSION = "hmt2-mbp1-pilot-v1"
PROVIDER_LIBRARY_VERSION = "0.86.0"  # the now-governed pin; confirmed installed this checkpoint

MANIFEST_PATH = os.path.join(_THIS_DIR, "corpus-selection-manifest-v2.json")
SESSION_CONTRACT_ACTIVITY_PATH = os.path.join(_THIS_DIR, "gc-session-contract-activity-v1.json")
RESEARCH_SOURCE_ROOT = os.path.abspath(os.path.join(_THIS_DIR, "..", "..", "research-source"))
CATALOGUE_RELATIVE_PATH = manifest_relative_path("mbp1_pilot_acquisition_catalogue.json")


def _utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _load_catalogue(store_root) -> dict:
    path = os.path.join(store_root, CATALOGUE_RELATIVE_PATH)
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_catalogue_atomic(store_root, catalogue: dict) -> None:
    path = os.path.join(store_root, CATALOGUE_RELATIVE_PATH)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(catalogue, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp_path, path)  # atomic rename on POSIX


def acquire_one_session(
    *, session_id: str, trade_date: str, active_raw_symbols: tuple, start: str, end: str,
    provider: DatabentoHistoricalProvider, store: NativeSourceStore, catalogue: dict,
) -> dict:
    request_identity = compute_request_identity(
        dataset=DATASET, schema=SCHEMA, symbols=active_raw_symbols, stype_in=STYPE_IN, start=start, end=end,
    )
    existing = catalogue.get(request_identity)
    if existing is not None:
        return {"session_id": session_id, "reused_existing_artefact": True, "request_identity": request_identity, "record": existing}

    source_relative_path = artefact_relative_path(session_id, "source", f"gc_mbp1_{session_id}.dbn.zst")
    evidence_relative_path = artefact_relative_path(session_id, "evidence", "mbp1_pilot_acquisition_evidence.json")
    object_path = os.path.join(store.root_dir, source_relative_path)
    os.makedirs(os.path.dirname(object_path), exist_ok=True)

    if os.path.exists(object_path):
        raise RuntimeError(
            f"REFUSING TO PROCEED: a file already exists at {object_path!r} but no matching "
            f"catalogue entry (request_identity={request_identity}) was found — this is an "
            f"ambiguous state (possibly a prior partial/failed write). Investigate manually; "
            f"this script never overwrites an existing path."
        )

    acquisition_started_utc = _utc_now_iso()
    result = provider.acquire_mbp1_pilot_session_data(
        session_id=session_id, symbols=list(active_raw_symbols), start=start, end=end, path=object_path,
    )
    acquisition_utc = _utc_now_iso()

    # Verify the artefact is fully and correctly written BEFORE computing/recording anything.
    if not os.path.exists(object_path):
        raise RuntimeError(f"acquisition for {session_id} returned success but no file exists at {object_path!r}")
    byte_size = os.path.getsize(object_path)
    if byte_size <= 0:
        raise RuntimeError(f"acquisition for {session_id} produced an empty file at {object_path!r}")

    with open(object_path, "rb") as f:
        sha256 = hashlib.sha256(f.read()).hexdigest()

    record = NativeArtefactRecord(
        corpus_version=CORPUS_VERSION,
        session_id=session_id,
        provider_id="databento",
        dataset_id=DATASET,
        schema=SCHEMA,
        request_identity=request_identity,
        requested_start_utc=start,
        requested_end_utc=end,
        provider_raw_symbols=tuple(active_raw_symbols),
        canonical_contract_mapping_ref="research/hmt2/gc-session-contract-activity-v1.json",
        object_relative_path=source_relative_path,
        byte_size=byte_size,
        sha256=sha256,
        source_condition="None reported by the vendor SDK for this request.",
        acquisition_utc=acquisition_utc,
        synthetic=False,
    )
    evidence_doc = record.to_dict()
    evidence_doc["trade_date"] = trade_date
    evidence_doc["acquisition_started_utc"] = acquisition_started_utc
    evidence_doc["stype_in"] = STYPE_IN
    evidence_doc["provider_library_version_at_transfer"] = PROVIDER_LIBRARY_VERSION
    evidence_doc["provider_library_version_at_evidence_derivation"] = PROVIDER_LIBRARY_VERSION
    evidence_doc["reported_record_count"] = result.record_count
    evidence_doc["reported_nbytes"] = result.nbytes
    evidence_doc["adapter_version"] = result.adapter_version

    # Evidence is written ONLY after the artefact bytes are confirmed complete (above).
    evidence_path = os.path.join(store.root_dir, evidence_relative_path)
    os.makedirs(os.path.dirname(evidence_path), exist_ok=True)
    tmp_evidence_path = evidence_path + ".tmp"
    with open(tmp_evidence_path, "w", encoding="utf-8") as f:
        json.dump(evidence_doc, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp_evidence_path, evidence_path)

    catalogue[request_identity] = evidence_doc
    _write_catalogue_atomic(store.root_dir, catalogue)

    return {"session_id": session_id, "reused_existing_artefact": False, "request_identity": request_identity, "record": evidence_doc}


def main() -> None:
    manifest_doc = read_manifest_v2(MANIFEST_PATH)
    selection = select_pilot_pair(manifest_doc)

    activity = json.load(open(SESSION_CONTRACT_ACTIVITY_PATH, encoding="utf-8"))
    by_id = {s["session_id"]: s for s in activity["sessions"]}

    pilot_sessions = [
        {"role": "SCHEDULED_EVENT", "session_id": selection["selected_event_session_id"]},
        {"role": "MATCHED_CONTROL", "session_id": selection["selected_control_session_id"]},
    ]

    api_key = load_databento_api_key()
    provider = DatabentoHistoricalProvider(api_key=api_key)
    store = NativeSourceStore(RESEARCH_SOURCE_ROOT)
    catalogue = _load_catalogue(RESEARCH_SOURCE_ROOT)

    results = []
    for entry in pilot_sessions:
        session_id = entry["session_id"]
        s = by_id[session_id]
        outcome = acquire_one_session(
            session_id=session_id,
            trade_date=s["trade_date"],
            active_raw_symbols=tuple(s["active_raw_symbols"]),
            start=s["request_start_utc"],
            end=s["request_end_utc"],
            provider=provider,
            store=store,
            catalogue=catalogue,
        )
        outcome["role"] = entry["role"]
        results.append(outcome)

    print(json.dumps({"selection": selection, "acquisitions": results}, indent=2))


if __name__ == "__main__":
    main()
