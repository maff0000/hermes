#!/usr/bin/env python3
"""HMT-2B.1 — deterministically (re)produces the frozen v2 scheduled macro-event snapshot.

Run from the repository root:

    python3 research/hmt2/generate_event_snapshot_v2.py

Pure stdlib, zero network calls, zero live provider API calls, zero spend. This is a
reproduction/audit tool, not a runtime service. It reads the frozen, already-committed
v1 snapshot (research/hmt2/scheduled-macro-event-snapshot-v1.json) and carries every one
of its FOMC_DECISION / CPI_RELEASE / EMPLOYMENT_SITUATION_NFP / PCE_RELEASE records forward
UNCHANGED (byte-identical dicts, read straight out of v1 rather than retyped, to remove any
chance of a transcription error), then appends the new checkpoint-verified dates below.

Every new date, source_ref and coverage_note in this file is taken verbatim from the
HMT-2B.1 coordinating-session verified-data brief (all primary-source lookups were already
performed and verified upstream of this script; this script performs zero re-research).

Running this script twice from the same repository state MUST reproduce byte-identical
output (same snapshot_sha256) -- that is the whole point of "frozen, committed,
deterministic".
"""
from __future__ import annotations

import json
import os
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_THIS_DIR, "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# Reuse the already-verified canonical-JSON/hash convention this repo established in HMT-2A
# for the selection manifest, rather than reinventing hashing logic here.
from market_truth.acquisition.corpus_manifest import canonical_json  # noqa: E402

V1_PATH = os.path.join(_THIS_DIR, "scheduled-macro-event-snapshot-v1.json")
V2_PATH = os.path.join(_THIS_DIR, "scheduled-macro-event-snapshot-v2.json")

CORPUS_START = "2017-05-21"  # metadata "corpus_start" -- the GC trading-session-universe start
# (unchanged from v1); it is NOT a floor on raw macro-event record dates -- CPI/NFP/PCE
# releases recur monthly all year regardless of when the trading session universe begins, and
# the checkpoint's own verified-data brief explicitly includes 2017 dates before 2017-05-21
# (e.g. 2017-01-06 NFP, 2017-01-18 CPI, 2017-01-30 PCE) with mandatory spot-check coverage.
# The sanity floor actually enforced on event-record dates below is EVENT_RECORD_LOWER_BOUND.
EVENT_RECORD_LOWER_BOUND = "2017-01-01"
CORPUS_CUTOFF_V2 = "2026-09-18"
EVENT_TAXONOMY_VERSION = "hmt2-event-taxonomy-v1"  # unchanged: record shape is identical to v1
RETRIEVAL_UTC_BATCH = "2026-09-21T00:00:00Z"  # representative timestamp for this whole batch

FOMC_COVERAGE_NOTE = (
    "regularly-scheduled meeting only; unscheduled/emergency FOMC actions (e.g. March 2020) "
    "are explicitly out of scope for this snapshot"
)
FOMC_SOURCE_REF_2026 = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"

BLS_SOURCE_REF = "https://www.bls.gov/schedule/{year}/home.htm"

PCE_SOURCE_REF_2017_2018 = (
    "BEA.gov Personal Income and Outlays release pages, 'EMBARGOED UNTIL' header dates "
    "(2017-2018 releases)"
)
PCE_SOURCE_REF_2019_2024 = (
    "BEA.gov annual Survey of Current Business (SCB) 'News Release Schedule' for {year}, "
    "spot-checked against individual Personal Income and Outlays release pages (2020-2024 "
    "spot-check)"
)
PCE_SOURCE_REF_2026 = (
    "BEA.gov Personal Income and Outlays release pages / BEA schedule-update announcements "
    "(2026 partial, reflecting Oct-Nov 2025 federal government shutdown rescheduling)"
)
PCE_2019_SHUTDOWN_NOTE = (
    "2019 series reflects the Dec-2018/Jan-2019 U.S. federal government shutdown: the "
    "December 2018 and January 2019 reference-month Personal Income and Outlays releases "
    "were combined into a single release on 2019-03-01"
)

# --- New FOMC_DECISION dates: 2026, federalreserve.gov/monetarypolicy/fomccalendars.htm,
# retrieved 2026-09-21. ---
NEW_FOMC_DATES = [
    "2026-01-28",
    "2026-03-18",
    "2026-04-29",
    "2026-06-17",
    "2026-07-29",
    "2026-09-16",
]

# --- New CPI_RELEASE dates: 2017-2023, bls.gov/schedule/{year}/home.htm, retrieved 2026-09-21. ---
NEW_CPI_DATES_BY_YEAR = {
    2017: ["01-18", "02-15", "03-15", "04-14", "05-12", "06-14", "07-14", "08-11", "09-14", "10-13", "11-15", "12-13"],
    2018: ["01-12", "02-14", "03-13", "04-11", "05-10", "06-12", "07-12", "08-10", "09-13", "10-11", "11-14", "12-12"],
    2019: ["01-11", "02-13", "03-12", "04-10", "05-10", "06-12", "07-11", "08-13", "09-12", "10-10", "11-13", "12-11"],
    2020: ["01-14", "02-13", "03-11", "04-10", "05-12", "06-10", "07-14", "08-12", "09-11", "10-13", "11-12", "12-10"],
    2021: ["01-13", "02-10", "03-10", "04-13", "05-12", "06-10", "07-13", "08-11", "09-14", "10-13", "11-10", "12-10"],
    2022: ["01-12", "02-10", "03-10", "04-12", "05-11", "06-10", "07-13", "08-10", "09-13", "10-13", "11-10", "12-13"],
    2023: ["01-12", "02-14", "03-14", "04-12", "05-10", "06-13", "07-12", "08-10", "09-13", "10-12", "11-14", "12-12"],
}

# --- New EMPLOYMENT_SITUATION_NFP dates: 2017-2023, bls.gov/schedule/{year}/home.htm,
# retrieved 2026-09-21. ---
NEW_NFP_DATES_BY_YEAR = {
    2017: ["01-06", "02-03", "03-10", "04-07", "05-05", "06-02", "07-07", "08-04", "09-01", "10-06", "11-03", "12-08"],
    2018: ["01-05", "02-02", "03-09", "04-06", "05-04", "06-01", "07-06", "08-03", "09-07", "10-05", "11-02", "12-07"],
    2019: ["01-04", "02-01", "03-08", "04-05", "05-03", "06-07", "07-05", "08-02", "09-06", "10-04", "11-01", "12-06"],
    2020: ["01-10", "02-07", "03-06", "04-03", "05-08", "06-05", "07-02", "08-07", "09-04", "10-02", "11-06", "12-04"],
    2021: ["01-08", "02-05", "03-05", "04-02", "05-07", "06-04", "07-02", "08-06", "09-03", "10-08", "11-05", "12-03"],
    2022: ["01-07", "02-04", "03-04", "04-01", "05-06", "06-03", "07-08", "08-05", "09-02", "10-07", "11-04", "12-02"],
    2023: ["01-06", "02-03", "03-10", "04-07", "05-05", "06-02", "07-07", "08-04", "09-01", "10-06", "11-03", "12-08"],
}

# --- New PCE_RELEASE dates: 2017-2024 (bea.gov, see source refs above) plus 9 partial 2026
# dates through the 2026-09-18 cutoff, retrieved 2026-09-21. ---
NEW_PCE_DATES_BY_YEAR = {
    2017: ["01-30", "03-01", "03-31", "05-01", "05-30", "06-30", "08-01", "08-31", "09-29", "10-30", "11-30", "12-22"],
    2018: ["01-29", "03-01", "03-29", "04-30", "05-31", "06-29", "07-31", "08-30", "09-28", "10-29", "11-29", "12-21"],
    2019: ["03-01", "03-29", "04-29", "05-31", "06-28", "07-30", "08-30", "09-27", "10-31", "11-27", "12-20"],
    2020: ["01-31", "02-28", "03-27", "04-30", "05-29", "06-26", "07-31", "08-28", "10-01", "10-30", "11-25", "12-23"],
    2021: ["01-29", "02-26", "03-26", "04-30", "05-28", "06-25", "07-30", "08-27", "10-01", "10-29", "11-24", "12-23"],
    2022: ["01-28", "02-25", "03-31", "04-29", "05-27", "06-30", "07-29", "08-26", "09-30", "10-28", "12-01", "12-23"],
    2023: ["01-27", "02-24", "03-31", "04-28", "05-26", "06-30", "07-28", "08-31", "09-29", "10-27", "11-30", "12-22"],
    2024: ["01-26", "02-29", "03-29", "04-26", "05-31", "06-28", "07-26", "08-30", "09-27", "10-31", "11-27", "12-20"],
}

# 2026 partial PCE dates -> (mm-dd, coverage_note describing which reference-month data it carries)
NEW_PCE_2026_PARTIAL = [
    ("01-22", "combined Oct+Nov 2025 reference-month data, delayed/combined by the Oct-Nov 2025 federal government shutdown"),
    ("02-20", "Dec 2025 reference-month data"),
    ("03-13", "Jan 2026 reference-month data"),
    ("04-09", "Feb 2026 reference-month data"),
    ("04-30", "Mar 2026 reference-month data"),
    ("05-28", "Apr 2026 reference-month data"),
    ("06-25", "May 2026 reference-month data"),
    ("07-30", "Jun 2026 reference-month data"),
    ("08-26", "Jul 2026 reference-month data"),
]

EXCLUDED_PCE_DATE_2026_09_30 = "2026-09-30"  # Aug-2026-data release; correctly excluded, after cutoff


def _load_v1_records_by_class():
    with open(V1_PATH, encoding="utf-8") as f:
        v1 = json.load(f)
    by_class: dict[str, list[dict]] = {}
    for r in v1["records"]:
        by_class.setdefault(r["event_class"], []).append(dict(r))  # copy, do not mutate v1's data
    return by_class


def _new_fomc_records() -> list[dict]:
    return [
        {
            "authority": "FEDERAL_RESERVE",
            "coverage_note": FOMC_COVERAGE_NOTE,
            "date": d,
            "event_class": "FOMC_DECISION",
            "event_taxonomy_version": EVENT_TAXONOMY_VERSION,
            "retrieval_utc": RETRIEVAL_UTC_BATCH,
            "source_ref": FOMC_SOURCE_REF_2026,
        }
        for d in NEW_FOMC_DATES
    ]


def _new_bls_records(dates_by_year: dict[int, list[str]], event_class: str) -> list[dict]:
    records = []
    for year, mmdd_list in dates_by_year.items():
        source_ref = BLS_SOURCE_REF.format(year=year)
        for mmdd in mmdd_list:
            records.append(
                {
                    "authority": "BLS",
                    "coverage_note": None,
                    "date": f"{year}-{mmdd}",
                    "event_class": event_class,
                    "event_taxonomy_version": EVENT_TAXONOMY_VERSION,
                    "retrieval_utc": RETRIEVAL_UTC_BATCH,
                    "source_ref": source_ref,
                }
            )
    return records


def _new_pce_records() -> list[dict]:
    records = []
    for year, mmdd_list in NEW_PCE_DATES_BY_YEAR.items():
        source_ref = (
            PCE_SOURCE_REF_2017_2018 if year in (2017, 2018) else PCE_SOURCE_REF_2019_2024.format(year=year)
        )
        for mmdd in mmdd_list:
            coverage_note = PCE_2019_SHUTDOWN_NOTE if year == 2019 else None
            records.append(
                {
                    "authority": "BEA",
                    "coverage_note": coverage_note,
                    "date": f"{year}-{mmdd}",
                    "event_class": "PCE_RELEASE",
                    "event_taxonomy_version": EVENT_TAXONOMY_VERSION,
                    "retrieval_utc": RETRIEVAL_UTC_BATCH,
                    "source_ref": source_ref,
                }
            )
    for mmdd, note in NEW_PCE_2026_PARTIAL:
        records.append(
            {
                "authority": "BEA",
                "coverage_note": (
                    f"2026 reference-month release carries {note}; released on this actual "
                    "as-published date under the Oct-Nov 2025 federal government shutdown "
                    "rescheduling, not the originally-scheduled date"
                ),
                "date": f"2026-{mmdd}",
                "event_class": "PCE_RELEASE",
                "event_taxonomy_version": EVENT_TAXONOMY_VERSION,
                "retrieval_utc": RETRIEVAL_UTC_BATCH,
                "source_ref": PCE_SOURCE_REF_2026,
            }
        )
    return records


def build_records() -> list[dict]:
    v1_by_class = _load_v1_records_by_class()

    fomc = v1_by_class["FOMC_DECISION"] + _new_fomc_records()
    cpi = v1_by_class["CPI_RELEASE"] + _new_bls_records(NEW_CPI_DATES_BY_YEAR, "CPI_RELEASE")
    nfp = v1_by_class["EMPLOYMENT_SITUATION_NFP"] + _new_bls_records(NEW_NFP_DATES_BY_YEAR, "EMPLOYMENT_SITUATION_NFP")
    pce = v1_by_class["PCE_RELEASE"] + _new_pce_records()

    assert len(fomc) == 75, f"FOMC_DECISION count expected 75, got {len(fomc)}"
    assert len(cpi) == 107, f"CPI_RELEASE count expected 107, got {len(cpi)}"
    assert len(nfp) == 107, f"EMPLOYMENT_SITUATION_NFP count expected 107, got {len(nfp)}"
    assert len(pce) == 114, f"PCE_RELEASE count expected 114, got {len(pce)}"

    all_records = fomc + cpi + nfp + pce
    assert len(all_records) == 403, f"grand total expected 403, got {len(all_records)}"

    # no accidental exact (event_class, date) duplicate within a class
    for cls, recs in (("FOMC_DECISION", fomc), ("CPI_RELEASE", cpi), ("EMPLOYMENT_SITUATION_NFP", nfp), ("PCE_RELEASE", pce)):
        dates = [r["date"] for r in recs]
        assert len(dates) == len(set(dates)), f"duplicate date within {cls}: {sorted(dates)}"

    # every date within the eligible range (see EVENT_RECORD_LOWER_BOUND note above re:
    # why this is not gated at the trading-corpus_start of 2017-05-21)
    for r in all_records:
        assert EVENT_RECORD_LOWER_BOUND <= r["date"] <= CORPUS_CUTOFF_V2, f"date out of range: {r}"

    # explicitly confirm the correctly-excluded post-cutoff PCE date is NOT present
    assert not any(r["date"] == EXCLUDED_PCE_DATE_2026_09_30 and r["event_class"] == "PCE_RELEASE" for r in all_records)

    # deterministic final order: sorted by (date, event_class), matching v1's own convention
    all_records.sort(key=lambda r: (r["date"], r["event_class"]))
    return all_records


def build_metadata() -> dict:
    return {
        "corpus_cutoff": CORPUS_CUTOFF_V2,
        "corpus_start": CORPUS_START,
        "coverage_disclosure": {
            "CPI_RELEASE": (
                "FULL coverage 2017-01-01 through 2025-12-31 (107 dates); 2017-2023 added this "
                "checkpoint (84 dates) from BLS.gov per-year schedule archive pages, 2024-2025 "
                "(23 dates) carried forward unchanged from v1. 2026 CPI release dates are "
                "intentionally out of scope for this snapshot (not attempted; not a disclosed "
                "gap within the 2017-2025 target universe of this checkpoint)."
            ),
            "EMPLOYMENT_SITUATION_NFP": (
                "FULL coverage 2017-01-01 through 2025-12-31 (107 dates); 2017-2023 added this "
                "checkpoint (84 dates) from BLS.gov per-year schedule archive pages, 2024-2025 "
                "(23 dates) carried forward unchanged from v1. 2026 Employment Situation dates "
                "are intentionally out of scope for this snapshot (not attempted; not a "
                "disclosed gap within the 2017-2025 target universe of this checkpoint)."
            ),
            "FOMC_DECISION": (
                "FULL coverage 2017-05-21 through 2026-09-18 (75 dates); primary-source "
                "verified against federalreserve.gov FOMC calendar/historical pages "
                "(2019-2026 meeting calendar; 2017/2018 historical archive pages). "
                "Regularly-scheduled meetings only. No remaining gap within the eligible "
                "universe (2026-09-16 is the final regularly-scheduled meeting on or before "
                "the 2026-09-18 cutoff)."
            ),
            "PCE_RELEASE": (
                "FULL coverage 2017-01-01 through 2026-09-18 (114 dates); 2017-2024 (95 dates) "
                "added this checkpoint from BEA.gov release-page 'EMBARGOED UNTIL' headers "
                "(2017-2018) and annual SCB 'News Release Schedule' pages spot-checked against "
                "individual release pages (2019-2024), 2025 (10 dates) carried forward "
                "unchanged from v1, and 9 partial-2026 dates added reflecting the actual "
                "(post Oct-Nov-2025-shutdown) release calendar through the 2026-09-18 cutoff. "
                "The Aug-2026-reference-month release (2026-09-30) correctly falls after the "
                "cutoff and is excluded. No remaining gap within the eligible universe."
            ),
        },
        "event_taxonomy_version": EVENT_TAXONOMY_VERSION,
        "known_real_world_anomaly_disclosed": (
            "The Oct-Nov 2025 U.S. federal government shutdown caused BLS to skip/delay "
            "several 2025 CPI and Employment Situation releases and caused BEA to delay/"
            "combine several 2025 and 2026 Personal Income and Outlays (PCE) releases (the "
            "Oct+Nov 2025 PCE reference-month data were combined into a single release on "
            "2026-01-22, and subsequent 2026 PCE releases ran on a compressed, roughly "
            "one-month-delayed cadence through the 2026-09-18 cutoff). Separately, the "
            "Dec-2018/Jan-2019 U.S. federal government shutdown caused BEA to combine the "
            "Dec-2018 and Jan-2019 reference-month PCE releases into a single release on "
            "2019-03-01. This snapshot records the ACTUAL release calendar (including the "
            "resulting gaps/combinations), not the originally-scheduled-but-never-published "
            "dates, except where explicitly noted in a record's coverage_note."
        ),
        "snapshot_version": "hmt2-scheduled-macro-event-snapshot-v2",
    }


def build_document() -> dict:
    metadata = build_metadata()
    records = build_records()
    payload = {"metadata": metadata, "records": records}
    digest = __import__("hashlib").sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    metadata_with_hash = dict(metadata)
    metadata_with_hash["snapshot_sha256"] = digest
    return {"metadata": metadata_with_hash, "records": records}


def main() -> None:
    document = build_document()
    with open(V2_PATH, "w", encoding="utf-8") as f:
        json.dump(document, f, indent=2, sort_keys=True)
        f.write("\n")

    from collections import Counter

    counts = Counter(r["event_class"] for r in document["records"])
    print(f"Wrote snapshot: {V2_PATH}")
    print(f"snapshot_sha256: {document['metadata']['snapshot_sha256']}")
    print(f"counts: {dict(counts)}")
    print(f"grand total: {sum(counts.values())}")


if __name__ == "__main__":
    main()
