"""HMT-2B.1 checkpoint -- scheduled-macro-event-snapshot-v2.json tests.

Covers: schema validity, exact per-class counts, date-range bounds, hash reproducibility
(the generator run twice must produce byte-identical output), and known-present /
known-absent date spot checks. Zero network calls; reads only the already-committed
frozen snapshot files and runs the pure-stdlib generator script as a subprocess.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys

import pytest

from market_truth.acquisition.corpus_manifest import canonical_json
from market_truth.acquisition.corpus_selection import load_event_records

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_HMT2_DIR = os.path.join(_REPO_ROOT, "research", "hmt2")
V1_PATH = os.path.join(_HMT2_DIR, "scheduled-macro-event-snapshot-v1.json")
V2_PATH = os.path.join(_HMT2_DIR, "scheduled-macro-event-snapshot-v2.json")
GENERATOR_PATH = os.path.join(_HMT2_DIR, "generate_event_snapshot_v2.py")

EXPECTED_COUNTS = {
    "FOMC_DECISION": 75,
    "CPI_RELEASE": 107,
    "EMPLOYMENT_SITUATION_NFP": 107,
    "PCE_RELEASE": 114,
}
EXPECTED_TOTAL = 403

# Upper bound on any event-record date in this snapshot (the checkpoint cutoff).
EVENT_RECORD_UPPER_BOUND = "2026-09-18"
# Lower sanity bound actually enforced on raw event-record dates. NOTE: this is
# deliberately NOT the trading corpus_start (2017-05-21) -- CPI/NFP/PCE releases recur
# monthly all year and this checkpoint's verified-data brief mandates 2017 dates before
# 2017-05-21 (e.g. 2017-01-06 NFP / 2017-01-18 CPI / 2017-01-30 PCE), each covered by a
# spot-check below. See generate_event_snapshot_v2.py's own comment for the same note.
EVENT_RECORD_LOWER_BOUND = "2017-01-01"


@pytest.fixture(scope="module")
def v2_document() -> dict:
    with open(V2_PATH, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def v1_document() -> dict:
    with open(V1_PATH, encoding="utf-8") as f:
        return json.load(f)


# --- schema validity -------------------------------------------------------------------

def test_v2_top_level_shape_matches_v1_convention(v2_document, v1_document):
    assert set(v2_document.keys()) == set(v1_document.keys()) == {"metadata", "records"}
    assert set(v2_document["metadata"].keys()) == set(v1_document["metadata"].keys())


def test_v2_every_record_has_the_v1_field_set(v2_document, v1_document):
    v1_keys = set(v1_document["records"][0].keys())
    for r in v2_document["records"]:
        assert set(r.keys()) == v1_keys


def test_v2_records_sorted_by_date_then_event_class(v2_document):
    keys = [(r["date"], r["event_class"]) for r in v2_document["records"]]
    assert keys == sorted(keys)


def test_v2_records_load_via_existing_corpus_selection_loader(v2_document):
    records = load_event_records(v2_document)
    assert len(records) == EXPECTED_TOTAL


# --- exact per-class counts -------------------------------------------------------------

def test_v2_per_class_counts_are_exact(v2_document):
    from collections import Counter

    counts = Counter(r["event_class"] for r in v2_document["records"])
    assert dict(counts) == EXPECTED_COUNTS


def test_v2_grand_total_is_exact(v2_document):
    assert len(v2_document["records"]) == EXPECTED_TOTAL


def test_v2_no_duplicate_date_within_a_class(v2_document):
    from collections import Counter

    for event_class in EXPECTED_COUNTS:
        dates = [r["date"] for r in v2_document["records"] if r["event_class"] == event_class]
        dupes = [d for d, n in Counter(dates).items() if n > 1]
        assert dupes == [], f"{event_class} has duplicate dates: {dupes}"


def test_v2_carries_forward_every_v1_record_unchanged(v2_document, v1_document):
    v2_by_key = {(r["event_class"], r["date"]): r for r in v2_document["records"]}
    for r in v1_document["records"]:
        key = (r["event_class"], r["date"])
        assert key in v2_by_key, f"v1 record missing from v2: {key}"
        assert v2_by_key[key] == r, f"v1 record altered in v2: {key}"


# --- date-range bounds -------------------------------------------------------------------

def test_v2_no_date_outside_the_eligible_range(v2_document):
    for r in v2_document["records"]:
        assert EVENT_RECORD_LOWER_BOUND <= r["date"] <= EVENT_RECORD_UPPER_BOUND, (
            f"date out of range: {r['event_class']} {r['date']}"
        )


def test_v2_metadata_corpus_cutoff_is_extended(v2_document):
    assert v2_document["metadata"]["corpus_cutoff"] == "2026-09-18"


# --- known-present / known-absent spot checks --------------------------------------------

def _has(records, event_class, date):
    return any(r["event_class"] == event_class and r["date"] == date for r in records)


@pytest.mark.parametrize(
    "event_class,date",
    [
        ("FOMC_DECISION", "2026-09-16"),
        ("CPI_RELEASE", "2017-01-18"),
        ("EMPLOYMENT_SITUATION_NFP", "2017-01-06"),
        ("PCE_RELEASE", "2017-01-30"),
    ],
)
def test_v2_known_dates_present(v2_document, event_class, date):
    assert _has(v2_document["records"], event_class, date)


@pytest.mark.parametrize(
    "event_class,date",
    [
        ("PCE_RELEASE", "2026-09-30"),  # Aug-2026-data release; correctly after the cutoff
        ("FOMC_DECISION", "2026-10-28"),  # not a real regularly-scheduled FOMC meeting date
    ],
)
def test_v2_known_dates_absent(v2_document, event_class, date):
    assert not _has(v2_document["records"], event_class, date)


# --- hash correctness and reproducibility -------------------------------------------------

def test_v2_snapshot_hash_matches_recomputed_hash(v2_document):
    metadata = dict(v2_document["metadata"])
    recorded = metadata.pop("snapshot_sha256")
    payload = {"metadata": metadata, "records": v2_document["records"]}
    recomputed = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    assert recomputed == recorded
    assert len(recorded) == 64


def test_generator_is_byte_reproducible(tmp_path):
    """Running the generator twice must produce byte-identical output, and must match the
    already-committed v2 file exactly (i.e. the committed file IS the generator's output,
    not something hand-edited afterward)."""
    with open(V2_PATH, "rb") as f:
        committed_bytes = f.read()

    env = dict(os.environ)
    outputs = []
    for _ in range(2):
        result = subprocess.run(
            [sys.executable, GENERATOR_PATH],
            cwd=_REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
        with open(V2_PATH, "rb") as f:
            outputs.append(f.read())

    assert outputs[0] == outputs[1], "generator output not byte-identical across two runs"
    assert outputs[0] == committed_bytes, "generator output does not match the committed v2 file"
