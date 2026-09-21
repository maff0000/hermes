#!/usr/bin/env python3
"""HMT-2A — reproduces the frozen corpus-selection manifest from scratch.

Run from the repository root:

    python3 research/hmt2/generate_selection_manifest.py

This is a reproduction/audit tool, not a runtime service. It is pure stdlib, makes zero
network calls, and depends only on market_truth.acquisition.* (this checkpoint's own new
code) plus the two frozen JSON reference files alongside it in this directory. Running it
again from the same repository state MUST reproduce the exact same manifest bytes (same
manifest_sha256) — that is the whole point of "frozen, committed, deterministic".
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_THIS_DIR, "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from market_truth.acquisition.corpus_manifest import (  # noqa: E402
    ManifestMetadata,
    ManifestRow,
    write_manifest,
)
from market_truth.acquisition.corpus_selection import (  # noqa: E402
    BASE_SHA,
    SELECTION_ALGORITHM_VERSION,
    CORPUS_VERSION,
    STRATUM_PRECEDENCE,
    domain_seed_hex,
    SEED_DOMAIN_DEVELOPMENT_RANDOM,
    SEED_DOMAIN_PROTECTED_HOLDOUT,
    load_event_records,
    select_corpus,
)
from market_truth.acquisition.session_calendar import CALENDAR_VERSION, build_session_universe

CORPUS_START = _dt.date(2017, 5, 21)
CORPUS_CUTOFF = _dt.date(2025, 12, 31)


def main() -> None:
    with open(os.path.join(_THIS_DIR, "scheduled-macro-event-snapshot-v1.json"), encoding="utf-8") as f:
        event_snapshot = json.load(f)
    event_records = load_event_records(event_snapshot)

    session_universe = build_session_universe(CORPUS_START, CORPUS_CUTOFF)

    result = select_corpus(session_universe, event_records)

    rows: list[ManifestRow] = []
    for selected in result.all_rows():
        rows.append(
            ManifestRow(
                corpus_version=CORPUS_VERSION,
                session_id=selected.session.session_id,
                gc_trade_date=selected.session.session_date.isoformat(),
                request_start_utc=selected.session.window_start_utc.isoformat(),
                request_end_utc=selected.session.window_end_utc.isoformat(),
                primary_stratum=selected.primary_stratum.value,
                protected_holdout=selected.primary_stratum.value == "PROTECTED_HOLDOUT",
                selection_algorithm_version=SELECTION_ALGORITHM_VERSION,
                calendar_version=selected.session.calendar_version,
                inclusion_reason=selected.inclusion_reason,
                event_class=selected.event_class,
                event_source_ref=selected.event_source_ref,
                matched_parent_session=selected.matched_parent_session,
                volatility_compression_metric=selected.volatility_compression_metric,
                random_seed_domain=selected.random_seed_domain,
                random_seed_version=selected.random_seed_version,
                exclusion_or_replacement_lineage=selected.exclusion_or_replacement_lineage,
            )
        )
    # Deterministic final row order: by session date, then by a fixed stratum tie-break for
    # any (should-be-impossible, since every session has exactly one primary stratum) same-date
    # collision — sort key is simply (date, session_id) since session_id already encodes date.
    rows.sort(key=lambda r: r.session_id)

    for u in result.unmatched_events:
        print(f"UNMATCHED EVENT (documented, not fabricated): {u}", file=sys.stderr)

    metadata = ManifestMetadata(
        corpus_version=CORPUS_VERSION,
        manifest_schema_version="hmt2-corpus-selection-manifest-v1",
        selection_algorithm_version=SELECTION_ALGORITHM_VERSION,
        calendar_version=CALENDAR_VERSION,
        session_universe_start=CORPUS_START.isoformat(),
        session_universe_end=CORPUS_CUTOFF.isoformat(),
        seed_development_random_hex=domain_seed_hex(SEED_DOMAIN_DEVELOPMENT_RANDOM),
        seed_protected_holdout_hex=domain_seed_hex(SEED_DOMAIN_PROTECTED_HOLDOUT),
        stratum_precedence_order=tuple(s.value for s in STRATUM_PRECEDENCE),
        pending_strata=("HIGH_VOL_NON_EVENT", "COMPRESSION"),
        generated_by="research/hmt2/generate_selection_manifest.py",
        base_sha=BASE_SHA,
        notes=(
            "HMT-2A checkpoint. Zero real market-data acquisition; zero spend; zero live "
            "provider API calls. HIGH_VOL_NON_EVENT and COMPRESSION strata are "
            "PENDING_REFERENCE_SERIES_DATA (see "
            "research/hmt2/volatility-compression-reference-series-v1.json) and contribute "
            "zero sessions in this manifest. Scheduled-event coverage is honestly partial: "
            "FOMC full 2017-05-21..2025-12-31; CPI/NFP 2024-01-01..2025-12-31 only; PCE "
            "2025-01-01..2025-12-31 only (see research/hmt2/scheduled-macro-event-snapshot-v1.json "
            "for the full per-class disclosure)."
        ),
    )

    out_path = os.path.join(_THIS_DIR, "corpus-selection-manifest-v1.json")
    digest = write_manifest(out_path, metadata, rows)

    counts = result.counts()
    print(f"Wrote manifest: {out_path}")
    print(f"manifest_sha256: {digest}")
    print(f"counts: {counts}")


if __name__ == "__main__":
    main()
