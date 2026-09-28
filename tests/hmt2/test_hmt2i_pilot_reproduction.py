"""HMT-2 canonicalisation checkpoint — WO Part 6, the single most important check in this whole
checkpoint: the NEW durable, session-scoped canonicalisation pipeline must reproduce the
already-governed MBP-1 pilot's exact canonical-event-set hash, computed by combining the two
pilot sessions' OWN independently-canonicalised, durably-recorded canonical events — never
rationalised, never "fixed" if it disagrees.

Skips cleanly when the real retained native MBP-1 pilot artefacts are not present on disk (a bare
CI checkout never has them — real, gitignored vendor bytes under `research-source/`, per
`market_truth/acquisition/source_store.py`'s own documented layout and `.gitignore`'s blanket
`research-source/` rule). This mirrors this repository's OWN already-established convention
(see `tests/hmt2/test_mbp1_pilot_replay_compare.py`'s own docstring: "the real pipeline is
exercised deliberately outside pytest/CI") — the difference here is that THIS test genuinely
runs the real durable pipeline and asserts the real hash whenever the real retained data is
present (this worktree, right now, included), rather than being a purely manual/human-invoked
check.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_module(name: str, relative_path: str):
    """Dynamically load a `research/hmt2/*.py` script by file path, registering it into
    `sys.modules` first — see the identical, more fully-commented helper in
    `tests/hmt2/test_hmt2i_gc_corpus_canonicalise.py` for why this registration matters (it
    keeps a plain `import <name>` inside the dynamically-loaded driver script resolving to the
    SAME module object this test file holds, rather than silently re-executing a second,
    distinct copy)."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


hmt2i_canonicalise = _load_module("hmt2i_gc_corpus_canonicalise", "research/hmt2/hmt2i_gc_corpus_canonicalise.py")
source_ledger_mod = sys.modules.get("hmt2h_gc_corpus_ledger") or _load_module(
    "hmt2h_gc_corpus_ledger", "research/hmt2/hmt2h_gc_corpus_ledger.py"
)


def _pilot_native_artefacts_present() -> bool:
    try:
        acquisition_ledger = source_ledger_mod.load_ledger(hmt2i_canonicalise.SOURCE_LEDGER_STATE_PATH)
    except Exception:
        return False
    for session_id in hmt2i_canonicalise.PILOT_SESSION_IDS:
        entry = acquisition_ledger.get(session_id)
        if not entry or entry.get("state") != source_ledger_mod.STATE_COMPLETE:
            return False
        artefact = entry.get("artefact") or {}
        native_path = os.path.join(hmt2i_canonicalise.RESEARCH_SOURCE_ROOT, artefact.get("object_relative_path", ""))
        if not os.path.exists(native_path):
            return False
    return True


_SKIP_REASON = (
    "real retained native MBP-1 pilot artefacts are not present on disk (research-source/ is "
    "gitignored, real vendor bytes, never committed) -- this test only runs where the real "
    "retained pilot data actually lives, exactly mirroring this repo's own established "
    "'real pipeline exercised deliberately outside a bare CI checkout' convention"
)
_PILOT_DATA_PRESENT = _pilot_native_artefacts_present()


def test_expected_hash_constant_is_the_governed_pilot_value():
    """Regression pin — this literal must never be "corrected" to make a mismatch disappear."""
    assert hmt2i_canonicalise.EXPECTED_PILOT_CANONICAL_EVENT_SET_HASH == (
        "ffe0119a18c2b368fb6820a2c101fa889644cc796337d0edd64029d666acc60a"
    )


@pytest.mark.skipif(not _PILOT_DATA_PRESENT, reason=_SKIP_REASON)
def test_durable_pipeline_reproduces_the_governed_pilot_canonical_event_set_hash_exactly(tmp_path):
    result = hmt2i_canonicalise.run_pilot_reproduction_check(canonical_research_root_override=str(tmp_path))

    assert result["durable_combined_canonical_event_set_hash"] == hmt2i_canonicalise.EXPECTED_PILOT_CANONICAL_EVENT_SET_HASH, (
        "PILOT REPRODUCTION MISMATCH -- durable pipeline produced "
        f"{result['durable_combined_canonical_event_set_hash']!r}, expected "
        f"{hmt2i_canonicalise.EXPECTED_PILOT_CANONICAL_EVENT_SET_HASH!r}. Per WO Part 6: do not "
        "rationalise this, do not adjust the expected value -- report the exact discrepancy."
    )
    assert result["reproduces_exactly"] is True

    processed_ids = {p["session_id"] for p in result["batch_result"]["processed"]}
    assert processed_ids == set(hmt2i_canonicalise.PILOT_SESSION_IDS)
    for session_id in hmt2i_canonicalise.PILOT_SESSION_IDS:
        assert result["per_session_event_counts"][session_id] > 0


@pytest.mark.skipif(not _PILOT_DATA_PRESENT, reason=_SKIP_REASON)
def test_pilot_reproduction_check_is_idempotent_on_a_second_run(tmp_path):
    """Running the gate twice against the SAME durable store must reuse (verify-and-skip), not
    reprocess, and must report the identical hash both times."""
    first = hmt2i_canonicalise.run_pilot_reproduction_check(canonical_research_root_override=str(tmp_path))
    second = hmt2i_canonicalise.run_pilot_reproduction_check(canonical_research_root_override=str(tmp_path))

    assert first["durable_combined_canonical_event_set_hash"] == second["durable_combined_canonical_event_set_hash"]
    reused = {p["session_id"]: p["reused_existing_canonical_result"] for p in second["batch_result"]["processed"]}
    assert all(reused.values())
