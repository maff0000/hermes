"""
market_truth.acquisition.canonical_spool — bounded-memory, attempt-scoped scratch mechanisms for
the HMT-2 canonical-worker orchestration layer (Stage 1 of the whole-session `events` accumulation
removal; see `canonical_worker.py`'s module docstring for the incident this responds to).

This module is HMT-2 orchestration code, never a change to any HMT-1 semantic (canonicaliser.py,
partition.py, identity.py, evidence.py, replay.py are all untouched, byte-for-byte, by this file).
It provides exactly two independent, bounded mechanisms:

  - `PartitionSpool` — as each canonical event is emitted, it is persisted (pickled, full
    Python-object fidelity — the same object shape `market_truth.partition.PartitionWriter.write()`
    needs, not merely its serialized row bytes) into one append-only scratch file per HMT-1
    partition key (`(partition_relative_dir(event), event_family_name(event))` — computed purely
    from the event's own fields, identical to the key `PartitionWriter.write()` groups on
    internally). Partition finalization then loads, reconstructs, and clears ONE partition's
    events at a time — at most one partition's events are ever resident in memory together.

    Pickled canonical events are heavier than they look (measured ~1.7KB/event on the real 2.39M-
    record session — every event repeats its own full `ProvenanceEnvelope`, most of whose fields
    are IDENTICAL across an entire session/partition: `provider_id`, `dataset_id`,
    `canonicaliser_version`, `schema_version`, `historical_provenance_era`, etc.). Writing that
    volume immediately, per-event, into many concurrently-open partition files generates enough
    disk write/dirty-page-cache traffic, under a memory-constrained cgroup with little swap, to
    itself become a `mem_cgroup_handle_over_high` driver — an empirically-confirmed, real
    contributor on the 2.39M-record session (see this fix's own dispatch report). `PartitionSpool`
    therefore buffers each key's framed pickle blobs in memory up to `flush_every` events, then
    zlib-compresses that whole batch as ONE block before writing — batch compression exploits the
    cross-event redundancy individual-event compression cannot (measured ~13-19x smaller than
    uncompressed per-event bytes on the same real data), directly cutting the disk-write/dirty-
    page-cache volume this scratch mechanism generates. The per-key in-memory buffer this requires
    is itself bounded by `flush_every`, never by total session/partition size.

  - `ExternalRowHasher` — reproduces `market_truth.replay.compute_event_set_hash`'s EXACT identity
    (SHA-256 chained over every event's `serialize_event_row()` bytes, in the GLOBAL sorted order
    Python's own `sorted()` gives raw byte strings, each row still individually 4-byte
    big-endian-length-prefixed before hashing) without ever holding the full row-byte collection in
    memory: bounded sorted runs are spilled to disk, then a deterministic k-way merge (via
    `heapq.merge`, which compares raw `bytes` with the same `<` Python's own `sorted()` uses) drives
    the hash accumulation in true global sorted order. `tests/hmt2/
    test_external_row_hasher_sort_equivalence.py` proves this reproduces `sorted(bytes_list)`
    exactly, across randomized fuzz cases (including empty/single/tied/varying-length inputs),
    before this is relied on for any governed identity.

Neither mechanism is ever itself authoritative evidence. Both are disposable/cleanable at any
point (a crash mid-attempt simply leaves scratch files behind under this session's own per-attempt
directory, which `canonical_worker.py`'s existing stale-attempt cleanup glob already discards at
the START of the next attempt — see `canonicalise_records()`), and both are deleted by the caller
on a successful attempt. Nothing in this module ever imports anything network-capable or
vendor-SDK-capable (see `tests/hmt2/test_no_network_guard.py`'s package-wide scan, which this
module — living under `market_truth/acquisition/` — is automatically subject to).
"""
from __future__ import annotations

import ctypes
import gc
import hashlib
import heapq
import pickle
import shutil
import zlib
from pathlib import Path
from typing import BinaryIO, Dict, Iterator, List, Tuple


def release_process_memory() -> None:
    """Best-effort memory-hygiene call: a garbage-collection pass, then ask glibc to return freed
    heap arenas to the OS (`malloc_trim`). CPython frees a Python object's memory back to its own
    allocator (pymalloc) immediately on refcount-zero, but the underlying glibc heap does NOT
    always give that memory back to the OS afterward (it retains freed arenas for reuse) unless
    asked — this is orthogonal to `gc.collect()`, which only handles cyclic garbage, not arena
    retention. Reconstructing one, extremely large partition (measured on the real 2.39M-record
    session: one partition held 89.3% of all canonical events — see canonical_worker.py's module
    docstring) can otherwise leave resident memory elevated well past that partition's own logical
    release, compounding across this checkpoint's finalization loop and the retained (PR #172)
    reload-verification loop that follows it — an empirically-confirmed real contributor to the
    2.39M-record session's `mem_cgroup_handle_over_high` stall (see this fix's own dispatch
    report). A PURE housekeeping call: it never affects program behaviour or output, only how
    promptly the OS is told freed memory is free — silently a no-op wherever `malloc_trim` isn't
    available (e.g. a non-glibc libc), never required for correctness."""
    gc.collect()
    try:
        libc = ctypes.CDLL("libc.so.6")
        libc.malloc_trim(0)
    except (OSError, AttributeError):
        pass


class CanonicalSpoolError(RuntimeError):
    """Fails closed on any structurally invalid or unrecoverable scratch-spool state (a corrupt or
    truncated spool/run file, an operation performed out of order, etc.) — this module never
    silently guesses at a partial/corrupted scratch read."""


PartitionKey = Tuple[str, str]  # (partition_relative_dir, event_family_name)

_LENGTH_PREFIX_BYTES = 8  # generous vs. partition.py/identity.py's own 4-byte convention — this
# module's framing is purely an internal scratch-file detail, never a governed identity input, so
# it deliberately uses its own, wider convention rather than reusing identity.py's `encode_field`
# (which is reserved for identity/row-serialization bytes).


def _write_framed(handle: BinaryIO, blob: bytes) -> None:
    handle.write(len(blob).to_bytes(_LENGTH_PREFIX_BYTES, "big"))
    handle.write(blob)


def _iter_framed(path: Path) -> Iterator[bytes]:
    with open(path, "rb") as fh:
        while True:
            length_prefix = fh.read(_LENGTH_PREFIX_BYTES)
            if not length_prefix:
                return
            if len(length_prefix) != _LENGTH_PREFIX_BYTES:
                raise CanonicalSpoolError(
                    f"corrupt scratch file {path}: truncated length prefix ({len(length_prefix)} of "
                    f"{_LENGTH_PREFIX_BYTES} bytes) — refusing to guess at a partial scratch read"
                )
            length = int.from_bytes(length_prefix, "big")
            blob = fh.read(length)
            if len(blob) != length:
                raise CanonicalSpoolError(
                    f"corrupt scratch file {path}: truncated payload ({len(blob)} of {length} bytes "
                    f"expected) — refusing to guess at a partial scratch read"
                )
            yield blob


def _iter_framed_bytes(buf: bytes, *, source_description: str) -> Iterator[bytes]:
    """Like `_iter_framed`, but over an in-memory buffer (e.g. one already-decompressed batch)
    rather than a file on disk."""
    offset = 0
    total = len(buf)
    while offset < total:
        if offset + _LENGTH_PREFIX_BYTES > total:
            raise CanonicalSpoolError(
                f"corrupt {source_description}: truncated length prefix at offset {offset} — "
                f"refusing to guess at a partial scratch read"
            )
        length = int.from_bytes(buf[offset:offset + _LENGTH_PREFIX_BYTES], "big")
        offset += _LENGTH_PREFIX_BYTES
        if offset + length > total:
            raise CanonicalSpoolError(
                f"corrupt {source_description}: truncated payload at offset {offset} (wanted "
                f"{length} bytes, only {total - offset} available) — refusing to guess at a "
                f"partial scratch read"
            )
        yield buf[offset:offset + length]
        offset += length


class PartitionSpool:
    """Attempt-scoped, git-untracked scratch area: one append-only file per HMT-1 partition key.
    Never authoritative — cleaned up by the caller after a successful attempt, and structurally
    incapable of being mistaken for a durable canonical artifact (it never lives under the durable
    `canonical-v2/`, `evidence/`, `lineage/`, or `quality/` trees)."""

    _COMPRESSION_LEVEL = 6

    def __init__(self, root: Path, *, flush_every: int = 2000):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.flush_every = flush_every
        self._paths_by_key: Dict[PartitionKey, Path] = {}
        self._handles: Dict[PartitionKey, BinaryIO] = {}
        self._order: List[PartitionKey] = []
        self._seen_keys: set = set()
        # Per-key buffer of already-framed (length-prefixed), still-uncompressed pickle blobs,
        # pending a batch flush. Bounded by `flush_every` events per key, never by session/
        # partition size — see module docstring for why batching-then-compressing matters.
        self._buffers: Dict[PartitionKey, List[bytes]] = {}
        self._buffer_counts: Dict[PartitionKey, int] = {}

    def _path_for_key(self, key: PartitionKey) -> Path:
        relative_dir, family = key
        # A short, filesystem-safe, deterministic-within-this-process name. The partition key
        # itself is kept in `_paths_by_key`/`_order` (in memory — one small tuple of strings per
        # DISTINCT partition, never per event), so this file name never needs to be decoded back
        # into the key; a hash keeps this immune to path-length/character-set limits regardless of
        # how long a real `partition_relative_dir()` output gets.
        digest = hashlib.sha256(f"{relative_dir}\x00{family}".encode("utf-8")).hexdigest()
        return self.root / f"{digest}.partition"

    def _handle_for_key(self, key: PartitionKey) -> BinaryIO:
        # NOTE: `_order`/`_seen_keys` bookkeeping lives solely in `add()` — this method only
        # lazily creates the on-disk path/handle the first time a key is actually flushed, and
        # must never ALSO append to `_order` (a key can be add()-ed, and therefore already
        # recorded in `_order`, long before its first flush-to-disk).
        handle = self._handles.get(key)
        if handle is not None:
            return handle
        path = self._paths_by_key.get(key)
        if path is None:
            path = self._path_for_key(key)
            self._paths_by_key[key] = path
        handle = open(path, "ab")
        self._handles[key] = handle
        return handle

    def add(self, key: PartitionKey, event: object) -> None:
        """Persist ONE canonical event under its own partition key. Full pickle fidelity — the
        exact Python object `PartitionWriter.write()` will later reconstruct and operate on,
        never a lossy/partial row-bytes-only projection. Buffered and batch-compressed, not
        written individually — see module docstring."""
        if key not in self._seen_keys:
            self._seen_keys.add(key)
            self._order.append(key)
        blob = pickle.dumps(event, protocol=pickle.HIGHEST_PROTOCOL)
        framed = len(blob).to_bytes(_LENGTH_PREFIX_BYTES, "big") + blob
        buf = self._buffers.setdefault(key, [])
        buf.append(framed)
        count = self._buffer_counts.get(key, 0) + 1
        self._buffer_counts[key] = count
        if count >= self.flush_every:
            self._flush_key(key)

    def _flush_key(self, key: PartitionKey) -> None:
        """Compress this key's currently-buffered batch as ONE block and append it to this key's
        scratch file. A no-op if nothing is buffered. Batch compression (rather than per-event
        compression) is what actually shrinks the on-disk/dirty-page-cache footprint — see module
        docstring for the measured ratio."""
        buf = self._buffers.get(key)
        if not buf:
            return
        raw = b"".join(buf)
        compressed = zlib.compress(raw, self._COMPRESSION_LEVEL)
        _write_framed(self._handle_for_key(key), compressed)
        self._buffers[key] = []
        self._buffer_counts[key] = 0

    def partition_keys(self) -> List[PartitionKey]:
        """Every distinct partition key observed so far (via `add()`), in first-seen order — an
        incidental, non-identity-bearing detail (`PartitionWriter.write()` itself always re-sorts
        groups by key before producing results, so the ORDER partitions are finalized in here
        never affects any governed output). Includes keys whose events are still only in the
        in-memory buffer and have never touched disk."""
        return list(self._order)

    def load_and_clear(self, key: PartitionKey) -> List[object]:
        """Reconstruct and return ONE partition's events (and only that partition's events),
        releasing this partition's scratch file and buffer immediately. The caller must not call
        this twice for the same key."""
        if key not in self._seen_keys:
            raise CanonicalSpoolError(f"partition key {key!r} was already loaded/cleared, or never spooled")
        self._seen_keys.discard(key)
        self._flush_key(key)  # flush any residual buffered events for this key to disk first

        handle = self._handles.pop(key, None)
        if handle is not None:
            handle.close()
        path = self._paths_by_key.pop(key, None)
        self._buffers.pop(key, None)
        self._buffer_counts.pop(key, None)
        if path is None:
            # This key had events added but never actually persisted anywhere (buffer was empty
            # at flush time -- structurally unreachable given `_flush_key` above only skips an
            # EMPTY buffer, and an empty buffer for a key that was ever add()-ed to would mean it
            # was already flushed by the automatic flush_every threshold and its path recorded).
            return []

        events: List[object] = []
        for compressed_block in _iter_framed(path):
            try:
                raw = zlib.decompress(compressed_block)
            except zlib.error as exc:
                raise CanonicalSpoolError(
                    f"corrupt partition spool block at {path}: could not decompress "
                    f"({exc.__class__.__name__}: {exc}) — refusing to guess at a partial/corrupt "
                    f"scratch read"
                ) from exc
            for blob in _iter_framed_bytes(raw, source_description=f"decompressed block from {path}"):
                try:
                    events.append(pickle.loads(blob))
                except Exception as exc:  # pickle can raise many exception types on corrupt input
                    raise CanonicalSpoolError(
                        f"corrupt partition spool entry at {path}: could not unpickle a spooled "
                        f"event ({exc.__class__.__name__}: {exc}) — refusing to guess at a "
                        f"partial/corrupt scratch read"
                    ) from exc
        path.unlink(missing_ok=True)
        return events

    def close_all(self) -> None:
        for handle in self._handles.values():
            handle.close()
        self._handles.clear()

    def cleanup(self) -> None:
        """Disposable-scratch discard — safe to call at any time, including after a partial
        failure; never touches any durable output tree."""
        self.close_all()
        self._buffers.clear()
        self._buffer_counts.clear()
        shutil.rmtree(self.root, ignore_errors=True)

    # Context-manager use closes every open partition-scratch-file HANDLE on the way out —
    # success or failure — so a caller that processes many sessions in one long-lived process
    # (e.g. a corpus regression driver) never leaks file descriptors across a failed session. It
    # deliberately does NOT `rmtree` on exit: a failed attempt's scratch directory is left in
    # place, cleanable/recoverable exactly like every other disposable scratch state this module
    # produces (see module docstring) — only an explicit `cleanup()` call (the caller's own
    # success path) ever deletes it.
    def __enter__(self) -> "PartitionSpool":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close_all()


class ExternalRowHasher:
    """Bounded-memory equivalent of `sorted(row_bytes_for_every_event)` chained into
    `market_truth.replay.compute_event_set_hash`'s exact SHA-256 accumulation. See module
    docstring for the equivalence this depends on, and
    `tests/hmt2/test_external_row_hasher_sort_equivalence.py` for the fuzz proof."""

    def __init__(self, spill_root: Path, *, max_buffer_rows: int = 200_000):
        self.spill_root = Path(spill_root)
        self.spill_root.mkdir(parents=True, exist_ok=True)
        self._max_buffer_rows = max_buffer_rows
        self._buffer: List[bytes] = []
        self._run_paths: List[Path] = []
        self._finalized = False

    def add(self, row_bytes: bytes) -> None:
        if self._finalized:
            raise CanonicalSpoolError("ExternalRowHasher.add() called after finalize()")
        self._buffer.append(row_bytes)
        if len(self._buffer) >= self._max_buffer_rows:
            self._spill()

    def _spill(self) -> None:
        if not self._buffer:
            return
        self._buffer.sort()
        run_path = self.spill_root / f"run-{len(self._run_paths):06d}.bin"
        with open(run_path, "wb") as fh:
            for row_bytes in self._buffer:
                _write_framed(fh, row_bytes)
        self._run_paths.append(run_path)
        self._buffer = []

    def finalize(self) -> str:
        """Consume every buffered/spilled row exactly once, in true global sorted order, and
        return the same hex digest `compute_event_set_hash()` would over the identical row-bytes
        multiset. May be called at most once."""
        if self._finalized:
            raise CanonicalSpoolError("ExternalRowHasher.finalize() called twice")
        self._finalized = True

        if not self._run_paths:
            # Small enough to never have spilled at all — sort in memory directly, exactly
            # matching `sorted()` (this IS `sorted()`), no disk round-trip needed.
            self._buffer.sort()
            hasher = hashlib.sha256()
            for row_bytes in self._buffer:
                hasher.update(len(row_bytes).to_bytes(4, "big"))
                hasher.update(row_bytes)
            self._buffer = []
            return hasher.hexdigest()

        self._spill()  # flush any residual buffer as one final run
        hasher = hashlib.sha256()
        for row_bytes in heapq.merge(*(_iter_framed(p) for p in self._run_paths)):
            hasher.update(len(row_bytes).to_bytes(4, "big"))
            hasher.update(row_bytes)
        return hasher.hexdigest()

    def cleanup(self) -> None:
        """Disposable-scratch discard — safe to call at any time."""
        self._buffer = []
        shutil.rmtree(self.spill_root, ignore_errors=True)
