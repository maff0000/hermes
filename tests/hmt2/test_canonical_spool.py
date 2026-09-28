"""HMT-2 bounded-memory canonicalisation fix — tests for
`market_truth.acquisition.canonical_spool` (Gate A):

  1. `ExternalRowHasher` reproduces Python's own `sorted(bytes_list)` ordering exactly, across
     randomized fuzz cases (empty / single-element / tied / varying-length / spilled-across-many-
     runs), and reproduces `market_truth.replay.compute_event_set_hash`'s exact hash for the same
     row-bytes multiset.
  2. `PartitionSpool` round-trips arbitrary picklable objects through its partition-keyed scratch
     files, in insertion order per key, and fails closed (never guesses) on a corrupted/truncated
     scratch file.
"""
from __future__ import annotations

import hashlib
import pickle
import random
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from market_truth.acquisition.canonical_spool import (  # noqa: E402
    CanonicalSpoolError,
    ExternalRowHasher,
    PartitionSpool,
)


def _reference_event_set_hash(row_bytes_list):
    """The exact algorithm `market_truth.replay.compute_event_set_hash` implements, reproduced
    here so this test never depends on importing anything that itself imports pyarrow/HMT-1 —
    this file is a pure, isolated proof of `canonical_spool`'s own internal equivalence."""
    h = hashlib.sha256()
    for row_bytes in sorted(row_bytes_list):
        h.update(len(row_bytes).to_bytes(4, "big"))
        h.update(row_bytes)
    return h.hexdigest()


# ------------------------------------------------------------------------------------------------
# ExternalRowHasher <-> sorted() equivalence (dedicated property/fuzz proof)
# ------------------------------------------------------------------------------------------------

def _random_byte_strings(rng: random.Random, count: int, max_len: int = 64) -> list:
    out = []
    for _ in range(count):
        length = rng.randint(0, max_len)
        out.append(bytes(rng.randrange(256) for _ in range(length)))
    return out


@pytest.mark.parametrize("seed", range(25))
def test_external_row_hasher_matches_sorted_reference_random_sets(tmp_path, seed):
    rng = random.Random(seed)
    count = rng.randint(0, 500)
    rows = _random_byte_strings(rng, count)

    hasher = ExternalRowHasher(tmp_path / f"runs-{seed}", max_buffer_rows=37)  # small buffer forces spills
    for row in rows:
        hasher.add(row)
    got = hasher.finalize()
    hasher.cleanup()

    assert got == _reference_event_set_hash(rows)


def test_external_row_hasher_empty_input_matches_sha256_of_nothing(tmp_path):
    hasher = ExternalRowHasher(tmp_path / "runs-empty")
    got = hasher.finalize()
    hasher.cleanup()
    assert got == hashlib.sha256(b"").hexdigest()
    assert got == _reference_event_set_hash([])


def test_external_row_hasher_single_element(tmp_path):
    hasher = ExternalRowHasher(tmp_path / "runs-single")
    hasher.add(b"only-one-row")
    got = hasher.finalize()
    hasher.cleanup()
    assert got == _reference_event_set_hash([b"only-one-row"])


def test_external_row_hasher_all_tied_duplicate_rows(tmp_path):
    rows = [b"same-row-bytes"] * 5000
    hasher = ExternalRowHasher(tmp_path / "runs-ties", max_buffer_rows=100)
    for row in rows:
        hasher.add(row)
    got = hasher.finalize()
    hasher.cleanup()
    assert got == _reference_event_set_hash(rows)


def test_external_row_hasher_varying_lengths_including_empty_bytes(tmp_path):
    rows = [b"", b"\x00", b"\x00\x00", b"a", b"aa", b"\xff" * 10, b"", b"z" * 200]
    hasher = ExternalRowHasher(tmp_path / "runs-varylen", max_buffer_rows=3)
    for row in rows:
        hasher.add(row)
    got = hasher.finalize()
    hasher.cleanup()
    assert got == _reference_event_set_hash(rows)


def test_external_row_hasher_spills_multiple_runs_and_still_matches(tmp_path):
    rng = random.Random(12345)
    rows = _random_byte_strings(rng, 20_000, max_len=40)
    hasher = ExternalRowHasher(tmp_path / "runs-many", max_buffer_rows=1000)  # -> ~20 spilled runs
    for row in rows:
        hasher.add(row)
    got = hasher.finalize()
    hasher.cleanup()
    assert got == _reference_event_set_hash(rows)


def test_external_row_hasher_add_after_finalize_raises(tmp_path):
    hasher = ExternalRowHasher(tmp_path / "runs-x")
    hasher.add(b"row")
    hasher.finalize()
    with pytest.raises(CanonicalSpoolError):
        hasher.add(b"too-late")


def test_external_row_hasher_finalize_twice_raises(tmp_path):
    hasher = ExternalRowHasher(tmp_path / "runs-y")
    hasher.add(b"row")
    hasher.finalize()
    with pytest.raises(CanonicalSpoolError):
        hasher.finalize()


def test_external_row_hasher_corrupt_run_file_fails_closed(tmp_path):
    spill_root = tmp_path / "runs-corrupt"
    hasher = ExternalRowHasher(spill_root, max_buffer_rows=2)
    hasher.add(b"row-one")
    hasher.add(b"row-two")
    hasher.add(b"row-three")  # forces at least one spill (buffer size 2)

    # Truncate whatever run file(s) got written -- simulate a crash mid-write / disk corruption.
    run_files = list(spill_root.glob("run-*.bin"))
    assert run_files, "expected at least one spilled run file"
    with open(run_files[0], "r+b") as fh:
        fh.truncate(3)  # chop off most of the length-prefix -- unrecoverably corrupt

    with pytest.raises(CanonicalSpoolError):
        hasher.finalize()


# ------------------------------------------------------------------------------------------------
# PartitionSpool
# ------------------------------------------------------------------------------------------------

def test_partition_spool_round_trips_arbitrary_picklable_objects_in_order(tmp_path):
    spool = PartitionSpool(tmp_path / "spool")
    key_a = ("dir/a", "market_trade")
    key_b = ("dir/b", "top_of_book")

    for i in range(50):
        spool.add(key_a, {"n": i, "tag": "a"})
    for i in range(30):
        spool.add(key_b, {"n": i, "tag": "b"})

    assert set(spool.partition_keys()) == {key_a, key_b}

    got_a = spool.load_and_clear(key_a)
    assert got_a == [{"n": i, "tag": "a"} for i in range(50)]

    got_b = spool.load_and_clear(key_b)
    assert got_b == [{"n": i, "tag": "b"} for i in range(30)]

    spool.cleanup()


def test_partition_spool_load_and_clear_releases_the_scratch_file(tmp_path):
    root = tmp_path / "spool"
    spool = PartitionSpool(root)
    key = ("dir/a", "market_trade")
    spool.add(key, "hello")
    spool.load_and_clear(key)
    # no partition scratch files left for this key
    assert not any(root.glob("*.partition"))


def test_partition_spool_load_and_clear_twice_for_same_key_raises(tmp_path):
    spool = PartitionSpool(tmp_path / "spool")
    key = ("dir/a", "market_trade")
    spool.add(key, "hello")
    spool.load_and_clear(key)
    with pytest.raises(CanonicalSpoolError):
        spool.load_and_clear(key)


def test_partition_spool_corrupt_entry_fails_closed(tmp_path):
    root = tmp_path / "spool"
    spool = PartitionSpool(root, flush_every=1)  # force an immediate on-disk flush
    key = ("dir/a", "market_trade")
    spool.add(key, {"n": 1})
    spool.close_all()

    path = spool._paths_by_key[key]  # test-only introspection of the scratch file location
    with open(path, "r+b") as fh:
        fh.truncate(4)  # length prefix present but payload missing -- unrecoverably corrupt

    with pytest.raises(CanonicalSpoolError):
        spool.load_and_clear(key)


def test_partition_spool_corrupt_compressed_block_fails_closed(tmp_path):
    root = tmp_path / "spool"
    spool = PartitionSpool(root, flush_every=1)  # force an immediate on-disk flush
    key = ("dir/a", "market_trade")
    spool.add(key, {"n": 1})
    spool.close_all()

    path = spool._paths_by_key[key]
    with open(path, "rb") as fh:
        original = fh.read()
    # keep the same overall length (so the outer length-prefix framing still parses) but corrupt
    # the compressed block's bytes themselves -- zlib.decompress() must raise, and PartitionSpool
    # must turn that into a fail-closed CanonicalSpoolError rather than propagating an ambiguous
    # raw exception type.
    garbage_payload = bytes((b + 1) % 256 for b in original[8:])
    with open(path, "wb") as fh:
        fh.write(original[:8] + garbage_payload)

    with pytest.raises(CanonicalSpoolError):
        spool.load_and_clear(key)


def test_partition_spool_cleanup_is_safe_after_partial_use(tmp_path):
    root = tmp_path / "spool"
    spool = PartitionSpool(root)
    spool.add(("dir/a", "market_trade"), 1)
    spool.add(("dir/b", "top_of_book"), 2)
    spool.cleanup()
    assert not root.exists()


def test_partition_spool_context_manager_closes_handles_on_exception(tmp_path):
    root = tmp_path / "spool"
    spool = PartitionSpool(root, flush_every=1)  # force an immediate on-disk flush
    try:
        with spool:
            spool.add(("dir/a", "market_trade"), 1)
            raise RuntimeError("simulated mid-attempt failure")
    except RuntimeError:
        pass
    # handles closed (no lingering open fds), but scratch directory/files are still on disk --
    # disposable/cleanable, never deleted just because an exception propagated.
    assert spool._handles == {}
    assert any(root.glob("*.partition"))
    spool.cleanup()
    assert not root.exists()


def test_partition_spool_batched_flush_round_trips_across_multiple_flushes(tmp_path):
    """flush_every=10 with 47 events forces 4 automatic flushes plus a residual in-memory buffer
    of 7 events at load_and_clear time -- both paths (already-flushed compressed blocks on disk,
    and the still-buffered residual) must reconstruct in the exact original order."""
    spool = PartitionSpool(tmp_path / "spool", flush_every=10)
    key = ("dir/a", "market_trade")
    for i in range(47):
        spool.add(key, {"n": i})
    got = spool.load_and_clear(key)
    assert got == [{"n": i} for i in range(47)]
    spool.cleanup()


def test_partition_spool_small_partition_never_touches_disk_until_load(tmp_path):
    """A partition with fewer events than `flush_every` is never written to disk until
    `load_and_clear()` is called (an efficiency detail, not a correctness one) -- and still
    round-trips correctly."""
    root = tmp_path / "spool"
    spool = PartitionSpool(root, flush_every=10_000)
    key = ("dir/a", "market_trade")
    for i in range(5):
        spool.add(key, {"n": i})
    assert not any(root.glob("*.partition")), "small partition must not touch disk before load"
    got = spool.load_and_clear(key)
    assert got == [{"n": i} for i in range(5)]
    spool.cleanup()


def test_partition_spool_batch_compression_shrinks_redundant_data_on_disk(tmp_path):
    """Sanity/regression check that batch compression actually engages: many events sharing a
    long, highly-repetitive string field must compress far smaller than the raw pickled bytes --
    this is the mechanism the bounded-memory fix depends on to keep disk/dirty-page-cache volume
    down for the largest real sessions (see canonical_worker.py's module docstring)."""
    spool = PartitionSpool(tmp_path / "spool", flush_every=500)
    key = ("dir/a", "market_trade")
    shared_padding = "x" * 2000  # stands in for a real event's highly-repetitive provenance text
    for i in range(500):
        spool.add(key, {"n": i, "padding": shared_padding})
    spool._flush_key(key)  # force the flush explicitly (also exercised implicitly at 500 already)
    path = spool._paths_by_key[key]
    on_disk_size = path.stat().st_size
    approx_raw_pickle_size = 500 * (len(shared_padding) + 50)  # rough per-event lower bound
    assert on_disk_size < approx_raw_pickle_size / 3, (
        f"expected batch compression to shrink highly-redundant data by >3x, got "
        f"on_disk={on_disk_size} vs approx_raw={approx_raw_pickle_size}"
    )
    got = spool.load_and_clear(key)
    assert got == [{"n": i, "padding": shared_padding} for i in range(500)]
    spool.cleanup()


def test_partition_spool_partition_keys_never_duplicated_across_multiple_flushes(tmp_path):
    """Regression: a key that crosses the flush_every threshold multiple times (triggering
    several automatic on-disk flushes) must still appear EXACTLY ONCE in partition_keys() -- a
    caller (canonical_worker.py) iterates partition_keys() once and calls load_and_clear() exactly
    once per key; a duplicate would make the second load_and_clear() call for the same key fail
    with 'already loaded/cleared'."""
    spool = PartitionSpool(tmp_path / "spool", flush_every=5)
    key = ("dir/a", "market_trade")
    for i in range(23):  # crosses the flush_every=5 threshold 4 times, plus a residual buffer
        spool.add(key, {"n": i})
    keys = spool.partition_keys()
    assert keys.count(key) == 1
    got = spool.load_and_clear(key)
    assert got == [{"n": i} for i in range(23)]
    spool.cleanup()


def test_partition_spool_distinct_keys_get_distinct_files_even_with_similar_names(tmp_path):
    spool = PartitionSpool(tmp_path / "spool", flush_every=1)  # force an immediate on-disk flush
    keys = [
        ("schema=v1/venue=COMEX/product=GC/contract=COMEX:GC:2020-04/date=2020-02-27/event_type=market_trade", "market_trade"),
        ("schema=v1/venue=COMEX/product=GC/contract=COMEX:GC:2020-04/date=2020-02-27/event_type=top_of_book", "top_of_book"),
        ("schema=v1/venue=COMEX/product=GC/contract=COMEX:GC:2020-06/date=2020-02-27/event_type=market_trade", "market_trade"),
    ]
    for i, key in enumerate(keys):
        spool.add(key, i)
    paths = {spool._paths_by_key[k] for k in keys}
    assert len(paths) == 3
    for i, key in enumerate(keys):
        assert spool.load_and_clear(key) == [i]
    spool.cleanup()
