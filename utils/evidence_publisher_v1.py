"""HERMES-owned, independent, best-effort evidence-publish boundary (PID05 / Stage 3B).
WO-PID05-STAGE3B-EVIDENCE-PUBLISHER.

HERMES currently has two side effects on every computed signal: a SQL write and a Redis
write (both in ``signal_builder.SignalPublisher.publish_signal()``). This module adds a
THIRD, independent, best-effort side effect: publishing the same signal as evidence to an
external evidence/telemetry sink over GELF TCP + mTLS. It never gates, wraps, or replaces
the existing SQL/Redis side effects, and the sink's availability or state can never affect
HERMES's own behaviour.

Architecture / GOV-SE-SEVERED-001 doctrine extension
-----------------------------------------------------
GOV-SE-SEVERED-001 (``utils/structure_ingest_boundary.py``) establishes HERMES's severance
doctrine: a producer never pushes into a consumer's code; a consumer must pull instead.
Central Architecture has ruled this remains fully authoritative, with this clarifying
extension:

    HERMES may publish its own governed evidence to an external evidence/telemetry sink,
    exactly like its existing Graylog SIEM GELF channel (``hermes_logging/gelf.py``).
    HERMES must not depend upon, import, call into, or require a consumer's implementation.
    The transport happening to terminate at an external evidence sink today does not make
    that sink part of HERMES's business-data execution path.

Accordingly, this module:
  - Carries NO consumer-specific naming in code/architecture (class/function/module names
    are generic: ``EvidencePublisher`` / ``evidence_publisher_v1.py``). The external sink's
    real-world name ("FALCON") is legitimate ONLY at the deployment/config boundary
    (environment variable names such as ``FALCON_PUBLISH_ENABLED``) — never in code
    identifiers.
  - Imports nothing from, and calls into nothing of, any external-sink repository or API.
    Reads none of the external sink's storage. Never gates HERMES's own behaviour on the
    sink's availability or state.
  - Is a sibling of the existing SQL and Redis side effects in
    ``SignalPublisher.publish_signal()`` — not a replacement, wrapper, or gate for either.

Identity semantics (FINAL, binding)
------------------------------------
  - ``signal_natural_key`` = ``f"{instrument}:{timeframe}:{signal_timestamp_z}"`` — no
    ``hermes:`` prefix. Mirrors HERMES's own SQL natural identity (instrument, timeframe,
    timestamp) exactly. It is NOT unique across invocations by itself — see
    ``falcon_event_id`` below.
  - ``falcon_event_id``: a fresh ``uuid4`` generated ONCE per logical publication
    invocation (i.e. once per ``EvidencePublisher.publish()`` call). Reused across that
    invocation's own bounded transport retries. A LATER, separate invocation — including
    one producing the identical ``signal_natural_key`` (e.g. a backfill/recovery replay
    re-traversing this code path) — always gets a NEW ``uuid4``. Never derived from
    ``signal_natural_key``, content, or any hash. No dedup against the sink; no pre-publish
    query. Multiple sink-side events sharing one ``signal_natural_key`` is expected and
    represents honest, separate HERMES emissions — not an error.
  - ``produced_at_utc``: a fresh, aware, UTC ``datetime.now(timezone.utc)`` read ONCE at the
    start of each ``publish()`` invocation. Reused unchanged across that invocation's
    retries. Distinct from the signal's own business timestamp.

Timestamp handling (FINAL, binding)
-------------------------------------
``Signal.timestamp`` is HERMES's existing naive ``datetime`` which, by HERMES's own
established construction, already represents UTC wall-clock time. This module treats it
EXPLICITLY as UTC (``.replace(tzinfo=timezone.utc)``) — never ``.astimezone()``, which on a
naive value silently assumes local system time (the bug class this module must avoid). This
module does not read a new wall clock for the signal's own business timestamp, and does not
touch ``Signal``, ``CandleAggregator``, SQL, or Redis in any way.

Retry semantics (FINAL, binding)
-----------------------------------
Maximum 2 total send attempts per ``publish()`` invocation (not "2 retries" — 2 attempts
total, counting the first), both using the exact same already-serialized GELF message
(constructed once). On the first transport failure the connection is closed and a fresh
connection is attempted for the second send. After a second failure: a single structured
``[EVIDENCE_PUBLISH_FAIL]`` log line, then return. Never raises. No delayed/background
retry, no persistent retry queue, no guaranteed-delivery claim anywhere in this module.

Failure isolation
-------------------
Every failure mode (connection refused, TLS handshake failure, certificate rejection,
timeout, reset, remote restart, malformed local object) is caught inside this module's own
boundary. ``EvidencePublisher.publish()`` NEVER raises — callers get a best-effort
fire-and-forget call that cannot affect their own return value or exception flow.

Asynchronous publication boundary (FINAL, binding) — HELM DEV-integration defect F1 fix
------------------------------------------------------------------------------------------
F1 (found during HELM's real DEV integration proof): the original Stage-3B design ran the
GELF TCP + mTLS transport SYNCHRONOUSLY, INLINE, on HERMES's single-threaded asyncio
signal-processing coroutine. Because the transport is blocking (``socket``/``ssl``, not
``asyncio``-native) with up to ``_MAX_SEND_ATTEMPTS`` (2) attempts each bounded by
``timeout`` (default 5s), a hung/black-holed evidence sink could stall the ENTIRE event
loop — every instrument's signal processing, not just one — for up to roughly
``2 * timeout`` seconds per publication. That violates the required doctrine: HERMES must
be independent of the evidence sink's LATENCY, not just its failures.

Fix shape (Central Architecture pre-approved, intentionally small): a bounded,
thread-safe ``queue.Queue`` plus ONE background daemon thread that owns the existing,
unchanged, blocking transport. This mirrors the established HERMES idiom in
``utils/hermes_publisher_runtime_v1.py`` (``PublisherRunner``: ``threading.Thread`` +
``threading.Event``, bounded loops, graceful start/stop, exception-isolated). Deliberately
NOT ``asyncio.Queue`` + an asyncio task — the transport itself is blocking I/O, and
converting it to true asyncio-native sockets would be a materially larger, riskier rewrite
than this fix warrants; a background OS thread consuming a thread-safe queue keeps the
already-audited transport code completely unchanged.

    SignalPublisher.publish_signal()
          |
          +-- SQL (unchanged)
          |
          +-- Redis (unchanged)
          |
          +-- EvidencePublisher.publish(signal)   <-- runs ONLY on the signal-processing
                  |                                    thread: builds + serializes the
                  |                                    event (pure, in-memory, no I/O),
                  |                                    then queue.put_nowait() — NEVER
                  |                                    blocks, NEVER touches the network.
                  v
          queue.Queue(maxsize=_EVIDENCE_QUEUE_MAX_SIZE)
                  |
                  v
          ONE background daemon thread (_worker_loop)
                  |
                  +-- blocking get() with a short poll timeout (observes the stop signal
                  |   promptly — standard producer/consumer idiom)
                  +-- _send_event_with_retry(): the EXISTING, UNCHANGED 2-attempt GELF/mTLS
                  |   transport logic (previously inline in publish()), now running off the
                  |   signal-processing path entirely
                  +-- exception-isolated per item: one malformed/failing event can never
                      kill the worker thread — caught, logged, loop continues

Event identity is fixed BEFORE enqueueing, not re-derived by the worker: ``publish()``
calls ``build_evidence_event()`` and serializes it to bytes exactly once, on the caller's
thread, and queues that already-built ``(event, data)`` pair. The worker's own internal
retry loop (``_send_event_with_retry``) reuses those exact same values/bytes for both send
attempts — it never regenerates ``falcon_event_id``/``produced_at_utc`` because the worker
reconnected, and never regenerates them because the item sat in the queue for a while. This
is a relocation of WHEN/WHERE the transport send happens, not a change to WHAT is sent or
to the identity semantics documented above.

Queue sizing — ``_EVIDENCE_QUEUE_MAX_SIZE`` = 200, derived from real observed traffic, not
guessed:
  - HELM's real DEV integration proof observed ~182 evidence-publish events over 14
    minutes: an observed AVERAGE rate of ``182 / 14 ≈ 13.0 events/min`` (~0.217 events/s).
  - The dominant burst shape is NOT a sustained-rate spike but a near-simultaneous cluster:
    up to 4 instruments' M1 signals landing within the same second, once per minute (plus
    smaller, less frequent M5/M15 contributions). To size for sustained backpressure
    (not just one instant's burst), a generous 3x safety multiplier is applied to the
    observed average, giving an assumed worst-case SUSTAINED rate of
    ``13.0 * 3 ≈ 39 events/min``.
  - The queue is sized to absorb an evidence-sink outage of ~5 minutes at that worst-case
    sustained rate: ``39 * 5 ≈ 195``, rounded to ``200``.
  - At the actually-OBSERVED (non-multiplied) average rate, 200 slots absorb
    ``200 / 13.0 ≈ 15.4 minutes`` of outage before any drop — comfortably longer than a
    typical transient restart/network blip — while staying small: each queued item is a
    JSON-sized dict plus its pre-serialized bytes (a few KB), so 200 items is at most a
    low single-digit number of MB resident, trivial against HERMES's memory budget.
  - This is deliberately NOT sized for "hours of outage buffering": this channel is
    explicitly best-effort telemetry, not guaranteed delivery (see below), so oversizing
    the queue would only delay — never prevent — eventual drops during a genuinely
    prolonged outage, while permanently costing memory headroom for zero behavioural
    benefit.

Queue-full behaviour: ``queue.put_nowait()`` only — NEVER ``queue.put()`` with a timeout or
blocking wait, NEVER any wait at all. On ``queue.Full`` the event is dropped: counted in
``events_dropped_queue_full`` and logged at ``warning`` with the distinct tag
``[EVIDENCE_PUBLISH_DROPPED_QUEUE_FULL]`` — deliberately different from the transport-level
``[EVIDENCE_PUBLISH_FAIL]`` tag, so an operator can tell "we dropped evidence because of
backpressure" apart from "we tried to send and the network failed". This is explicitly
best-effort, NOT guaranteed delivery — there is no spill-to-disk, no durable/persistent
queue, and nothing queued survives process shutdown.

Startup: enabling evidence publication never performs a blocking remote-availability check
against the sink — the worker thread is started eagerly (so the queue always has a
consumer), but the transport connection itself is still lazy (``_connect()`` is only ever
called from inside the worker, on the first dequeued item) exactly as before. HERMES boot
never waits on, or requires, the sink being reachable, even momentarially.

Shutdown is BOUNDED: ``close()`` signals the worker to stop, then joins it for at most
``_WORKER_SHUTDOWN_MAX_WAIT_SECONDS`` (5.0s — deliberately small, on the same order as the
transport's own per-attempt ``timeout``, not tens of seconds). Within that window the
worker keeps draining whatever is already queued (a small, best-effort drain, not a
guarantee). After the deadline, ``close()`` stops waiting regardless of whether the worker
has finished, force-closes the transport connection, and logs exactly how many queued
events were discarded (via ``queue.qsize()``). No persistence is added for shutdown
draining — this is explicitly out of scope, matching the best-effort nature of this whole
channel.

Worker resilience: a malformed/corrupt queued item, an exception raised while processing
one, or any transport exception propagating out of the existing send/retry logic are all
caught broadly — with a structured, logged ``[EVIDENCE_PUBLISH_WORKER_ERROR]`` line — and
the worker loop continues to the next item. The worker thread is never silently killed by
a single bad event.

Replay / backfill
--------------------
Intentional: replaying this code path produces a fresh ``falcon_event_id`` and
``produced_at_utc`` per invocation. Not suppressed, not deduplicated.
``scripts/backfill_signals.py`` does not traverse ``SignalPublisher.publish_signal()`` at
all and is explicitly, permanently out of this module's coverage — see
``docs/architecture/evidence-publisher.md``.

Payload is a producer-owned extensibility boundary (Central Architecture addendum)
-------------------------------------------------------------------------------------
HERMES's signal content is NOT treated as a fixed, enumerable schema here. The raw/original
payload preservation field (``hermes_signal_raw_json``) is built via GENERIC object
serialization (``dataclasses.asdict()`` plus any extra instance attributes present on the
object — see ``_serialize_signal_raw()``), never a hand-maintained allow-list of "today's
known field names". A new ``Signal`` field, or an attribute that exists only for a
particular instrument, flows through this module unchanged with ZERO publisher code
changes, and is never fabricated when genuinely absent. This is deliberately distinct from
the small, governed, searchable envelope (``instrument_id``/``timeframe``/``regime``/
``session``/``signal_type``), which stays exactly as scoped — promoted only when genuinely
present, never auto-expanded to mirror whatever happens to be in the raw payload.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import os
import queue
import socket
import ssl
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# TCP GELF messages are null-terminated (same convention as hermes_logging/gelf.py).
_TCP_NULL_TERMINATOR = b"\x00"

# Maximum total send attempts per publish() invocation (binding: exactly 2, not "2 retries").
_MAX_SEND_ATTEMPTS = 2

# F1 fix (HELM DEV-integration defect): bounded in-process async handoff so no network I/O
# to the evidence sink ever runs on the signal-processing coroutine. See the module
# docstring's "Asynchronous publication boundary" section for the full queue-sizing and
# shutdown-bound rationale.
_EVIDENCE_QUEUE_MAX_SIZE = 200
# How often the background worker wakes from a blocking queue.get() to re-check the stop
# signal when the queue is empty. Short enough to observe stop() promptly; irrelevant to
# normal-operation latency (queue.put() wakes a blocked get() immediately, it never waits
# out this poll interval).
_WORKER_POLL_SECONDS = 0.2
# Bounded shutdown wait: deliberately small, same order of magnitude as the transport's own
# per-attempt `timeout` (default 5.0s) — not tens of seconds. See module docstring.
_WORKER_SHUTDOWN_MAX_WAIT_SECONDS = 5.0

# Fixed envelope metadata for this evidence family (producer-declared, not Signal-derived —
# every evidence event emitted by this module belongs to the same registered family/type).
_ENVELOPE_VERSION = "v1"
_PAYLOAD_SCHEMA_VERSION = "v1"
_PRODUCER_SYSTEM_ID = "hermes"
_PRODUCER_COMPONENT_ID = "hermes.signal_engine"
_EVIDENCE_FAMILY = "hermes.signal_state"
_EVIDENCE_TYPE = "indicator_regime_snapshot"
_EVIDENCE_CLASS = "DETERMINISTIC_DERIVATION"
_SIGNAL_TYPE = "indicator_regime_snapshot"
_RETENTION_CLASS = "standard"
_SENSITIVITY_CLASS = "internal"


def _utc_z(dt: datetime) -> str:
    """Format an already-UTC-aware datetime as an explicit ...Z string (no astimezone)."""
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _signal_timestamp_utc(signal: Any) -> datetime:
    """Attach UTC to Signal.timestamp EXPLICITLY (it is naive-but-UTC by construction).

    Deliberately never calls `.astimezone()` anywhere in this module — on a naive datetime
    that silently assumes local system time, which would corrupt the value (exactly the bug
    class this module must avoid). `Signal.timestamp` is, by HERMES's own established
    construction, always naive-but-UTC, so a direct `.replace(tzinfo=...)` is the correct and
    only transformation needed. This function does not read a new wall clock and does not
    mutate the Signal object.
    """
    return signal.timestamp.replace(tzinfo=timezone.utc)


def _optional_field(signal: Any, attr: str) -> Optional[Any]:
    """Return the attribute's value only when genuinely present (never fabricate/default)."""
    value = getattr(signal, attr, None)
    return value if value is not None else None


def _serialize_signal_raw(signal: Any) -> Dict[str, Any]:
    """Generic, forward-compatible serialization of the FULL, authoritative Signal object for
    raw/original-payload preservation.

    Deliberately NOT a hand-maintained allow-list of "today's known field names" (unlike
    Signal.to_dict(), which enumerates fields explicitly and would silently drop any field
    added to Signal later without also updating that method). This captures:
      - every declared dataclass field (dataclasses.asdict(), which also recurses through any
        nested dataclasses/lists/dicts), and
      - any EXTRA instance attribute present on the object that is not a declared dataclass
        field (forward compatibility with a possible future per-instrument
        extra-attributes mechanism — e.g. one instrument's signal carrying an attribute no
        other instrument has).

    A field or attribute that is genuinely absent is simply absent from the output — nothing
    here fabricates, defaults, or infers a value. This function is used ONLY for the raw
    preservation field; it has no bearing on the small, governed, searchable envelope, whose
    optional fields are promoted individually and explicitly (see build_evidence_event()).
    """
    if dataclasses.is_dataclass(signal) and not isinstance(signal, type):
        data: Dict[str, Any] = dataclasses.asdict(signal)
    else:
        data = dict(vars(signal))
    # Merge in any extra instance attributes not already captured as declared dataclass
    # fields — e.g. an ad-hoc attribute set on one instance that isn't part of the dataclass
    # definition at all.
    for key, value in vars(signal).items():
        if key not in data:
            data[key] = value
    return data


def build_evidence_event(signal: Any) -> Dict[str, Any]:
    """Build one GELF message (dict, pre-JSON) for `signal`. Pure function — no I/O.

    Called exactly once per publish() invocation; the caller is responsible for reusing the
    SAME constructed/serialized event across any transport retries within that invocation.
    """
    event_id = str(uuid.uuid4())
    produced_at = datetime.now(timezone.utc)
    produced_at_str = _utc_z(produced_at)

    ts_utc = _signal_timestamp_utc(signal)
    ts_str = _utc_z(ts_utc)

    instrument = signal.instrument
    timeframe = signal.timeframe
    natural_key = f"{instrument}:{timeframe}:{ts_str}"

    payload: Dict[str, Any] = {"signal_natural_key": natural_key}
    instrument_id = _optional_field(signal, "instrument")
    if instrument_id is not None:
        payload["instrument_id"] = instrument_id
    timeframe_val = _optional_field(signal, "timeframe")
    if timeframe_val is not None:
        payload["timeframe"] = timeframe_val
    regime_val = _optional_field(signal, "regime")
    if regime_val is not None:
        payload["regime"] = regime_val
    session_val = _optional_field(signal, "session")
    if session_val is not None:
        payload["session"] = session_val
    # signal_type is fixed, producer-declared metadata about THIS emitter's output family —
    # not inferred/defaulted from the Signal object — so it is always included.
    payload["signal_type"] = _SIGNAL_TYPE

    payload_canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    payload_hash = "sha256:" + hashlib.sha256(payload_canonical.encode("utf-8")).hexdigest()

    provenance_ref = {
        "producer_system_id": _PRODUCER_SYSTEM_ID,
        "upstream_ref": f"hermes:signals:{instrument}:{timeframe}:{ts_str}",
    }

    # Full, real, original Signal object preserved verbatim (opaque, producer-owned JSON) —
    # generic serialization (see _serialize_signal_raw()), not a hand-maintained field list,
    # so new/per-instrument-only Signal fields flow through with zero code changes here.
    raw_signal_json = json.dumps(_serialize_signal_raw(signal), default=str)

    gelf_additional: Dict[str, Any] = {
        "falcon_event_id": event_id,
        "envelope_version": _ENVELOPE_VERSION,
        "payload_schema_version": _PAYLOAD_SCHEMA_VERSION,
        "producer_system_id": _PRODUCER_SYSTEM_ID,
        "producer_component_id": _PRODUCER_COMPONENT_ID,
        "evidence_family": _EVIDENCE_FAMILY,
        "evidence_type": _EVIDENCE_TYPE,
        "evidence_class": _EVIDENCE_CLASS,
        "produced_at_utc": produced_at_str,
        # Schema-shape-valid placeholder only; the receiving pipeline's own "Set
        # falcon_ingested_at_utc" rule overwrites this with the real ingest time. The
        # producer's value here is never trusted/used downstream.
        "falcon_ingested_at_utc": produced_at_str,
        "subject_id": instrument,
        # GELF additional fields are flat scalars; arrays/objects are sent JSON-encoded as
        # strings (same treatment as the explicit *_json fields below).
        "instrument_ids": json.dumps([instrument]),
        "payload_hash": payload_hash,
        "retention_class": _RETENTION_CLASS,
        "sensitivity_class": _SENSITIVITY_CLASS,
        # Nested fields: the receiving pipeline's stage-0 rule parses these FROM a
        # JSON-string additional field into flat sub-fields server-side.
        "provenance_ref_json": json.dumps(provenance_ref, sort_keys=True),
        "payload_json": payload_canonical,
        # Raw preservation field — separate from payload_json; carries the FULL original
        # Signal object, not the reduced searchable payload.
        "hermes_signal_raw_json": raw_signal_json,
    }

    gelf_message: Dict[str, Any] = {
        "version": "1.1",
        "host": socket.gethostname(),
        "short_message": f"HERMES signal evidence {instrument}/{timeframe}",
        "timestamp": produced_at.timestamp(),
    }
    for key, value in gelf_additional.items():
        gelf_message[f"_{key}"] = value

    return {
        "gelf_message": gelf_message,
        "falcon_event_id": event_id,
        "produced_at_utc": produced_at_str,
        "signal_natural_key": natural_key,
        "payload": payload,
    }


class EvidencePublisher:
    """GELF TCP + mTLS evidence publisher. Lazy-connect, long-lived connection reused across
    signals, bounded 2-total-attempt send, send()-never-raises contract (modelled closely on
    ``hermes_logging.gelf.GELFHandler``, with mTLS added — there is no existing TLS/mTLS code
    anywhere else in this codebase).

    Constructed only when ``FALCON_PUBLISH_ENABLED=true`` and fully configured — see
    ``build_evidence_publisher_from_env()``. The SSL context (and the cert/key/CA file reads
    it performs) is built once, at construction, not per-signal.
    """

    enabled = True

    def __init__(
        self,
        host: str,
        port: int,
        client_cert_file: str,
        client_key_file: str,
        ca_file: str,
        timeout: float = 5.0,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.timeout = timeout

        self._ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        self._ssl_context.load_verify_locations(cafile=ca_file)
        self._ssl_context.load_cert_chain(certfile=client_cert_file, keyfile=client_key_file)

        self._sock: Optional[ssl.SSLSocket] = None
        # Guards the transport connection (`_sock`) only — shared between the background
        # worker thread (sole caller of _attempt_send/_connect/_reset_connection during
        # normal operation) and close() (called from the signal-processing thread at
        # shutdown).
        self._lock = threading.Lock()

        # Stats are mutated from TWO different threads (producer-side counters from the
        # signal-processing thread via publish(); consumer-side counters from the background
        # worker thread) — guarded by their own lock, deliberately separate from `_lock` so
        # a stats read/update is never blocked behind a slow/hung transport operation.
        self._stats_lock = threading.Lock()
        self._stats = {
            "events_sent": 0,
            "events_failed": 0,
            "reconnections": 0,
            "events_queued": 0,
            "events_dropped_queue_full": 0,
        }

        # F1 fix: bounded handoff queue + single background worker thread. The queue and
        # stop event are created BEFORE the thread starts so the thread's first loop
        # iteration always finds them in place. No network I/O happens here — only a thread
        # object is created and started; the actual transport connection stays fully lazy
        # (see _connect()), so constructing/enabling this publisher never performs a
        # blocking remote-availability check.
        self._queue: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=_EVIDENCE_QUEUE_MAX_SIZE)
        self._stop_event = threading.Event()
        self._worker_thread = threading.Thread(
            target=self._worker_loop, name="hermes-evidence-publisher", daemon=True,
        )
        self._worker_thread.start()

    # -- connection management -------------------------------------------------------------

    def _connect(self) -> ssl.SSLSocket:
        raw = socket.create_connection((self.host, self.port), timeout=self.timeout)
        try:
            wrapped = self._ssl_context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            try:
                raw.close()
            except Exception:
                pass
            raise
        wrapped.settimeout(self.timeout)
        self._bump_stat("reconnections")
        return wrapped

    def _get_connection(self) -> ssl.SSLSocket:
        if self._sock is None:
            self._sock = self._connect()
        return self._sock

    def _reset_connection(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    # -- send ---------------------------------------------------------------------------------

    def _attempt_send(self, data: bytes) -> bool:
        try:
            sock = self._get_connection()
        except Exception as e:
            logger.warning("[EVIDENCE_PUBLISH_RETRY] connect failed: %r", e)
            self._reset_connection()
            return False
        try:
            sock.sendall(data)
            return True
        except Exception as e:
            logger.warning("[EVIDENCE_PUBLISH_RETRY] send failed: %r", e)
            self._reset_connection()
            return False

    def _bump_stat(self, key: str, amount: int = 1) -> None:
        with self._stats_lock:
            self._stats[key] = self._stats.get(key, 0) + amount

    def _send_event_with_retry(self, event: Dict[str, Any], data: bytes) -> None:
        """The EXISTING, UNCHANGED transport retry logic (previously inline in publish()):
        up to `_MAX_SEND_ATTEMPTS` (2) total attempts of the SAME already-serialized `data`,
        reconnecting between attempts. Runs exclusively on the background worker thread.
        Never raises — the worker's own exception-isolation wrapper is a second, independent
        layer of defence on top of this, matching the original design's layered boundaries.
        """
        try:
            with self._lock:
                for attempt in range(1, _MAX_SEND_ATTEMPTS + 1):
                    if self._attempt_send(data):
                        self._bump_stat("events_sent")
                        logger.debug(
                            "[EVIDENCE_PUBLISH_OK] natural_key=%s event_id=%s attempt=%d",
                            event["signal_natural_key"], event["falcon_event_id"], attempt,
                        )
                        return
                self._bump_stat("events_failed")
                logger.error(
                    "[EVIDENCE_PUBLISH_FAIL] exhausted %d/%d send attempts; natural_key=%s "
                    "event_id=%s host=%s:%s (best-effort, no retry queue, HERMES unaffected)",
                    _MAX_SEND_ATTEMPTS, _MAX_SEND_ATTEMPTS, event["signal_natural_key"],
                    event["falcon_event_id"], self.host, self.port,
                )
        except Exception as e:
            # Final defensive boundary: no failure mode of this method may ever propagate,
            # and it must never be able to kill the worker thread.
            logger.error("[EVIDENCE_PUBLISH_FAIL] unexpected exception inside publisher boundary: %r", e)

    def _worker_loop(self) -> None:
        """Background daemon thread: the ONLY code in this module that ever performs
        transport I/O. Blocking `get()` with a short poll timeout so the stop signal is
        observed promptly once the queue drains; while the queue has items, keeps draining
        them even after stop() has been called (the bounded "best-effort drain" — the actual
        bound on how long a CALLER waits for this is close()'s `join(timeout=...)`, not a
        limit enforced inside this loop). Exception-isolated per item: nothing dequeued here
        may ever kill this thread.
        """
        while True:
            try:
                item = self._queue.get(timeout=_WORKER_POLL_SECONDS)
            except queue.Empty:
                if self._stop_event.is_set():
                    return
                continue
            try:
                self._send_event_with_retry(item.get("event"), item.get("data"))
            except Exception as e:  # noqa: BLE001 - worker must survive ANY per-item failure
                logger.error(
                    "[EVIDENCE_PUBLISH_WORKER_ERROR] unexpected exception processing a queued "
                    "evidence event; worker continues, event dropped: %r", e,
                )
            finally:
                try:
                    self._queue.task_done()
                except Exception:
                    pass

    def publish(self, signal: Any) -> None:
        """Publish `signal` as evidence. Best-effort, fire-and-forget — NEVER raises and
        NEVER performs network I/O itself (F1 fix). Builds and serializes the event ONCE,
        here, on the caller's (signal-processing) thread — this is pure, in-memory work with
        no I/O — then hands the already-built, already-identity-fixed event to the bounded
        queue via a non-blocking `put_nowait()`. On backpressure (queue full) the event is
        dropped; this is logged distinctly from a transport-level failure and never raises.
        The actual GELF/mTLS send always happens later, on the background worker thread.
        """
        try:
            event = build_evidence_event(signal)
        except Exception as e:
            logger.error(
                "[EVIDENCE_PUBLISH_FAIL] failed to construct evidence event (local/malformed "
                "object; nothing sent): %r", e,
            )
            return

        try:
            data = json.dumps(event["gelf_message"], default=str).encode("utf-8") + _TCP_NULL_TERMINATOR
        except Exception as e:
            logger.error("[EVIDENCE_PUBLISH_FAIL] failed to serialize evidence event: %r", e)
            return

        try:
            self._queue.put_nowait({"event": event, "data": data})
            self._bump_stat("events_queued")
        except queue.Full:
            self._bump_stat("events_dropped_queue_full")
            logger.warning(
                "[EVIDENCE_PUBLISH_DROPPED_QUEUE_FULL] queue_size=%d natural_key=%s "
                "event_id=%s (best-effort evidence telemetry dropped under backpressure; "
                "HERMES SQL/Redis unaffected)",
                self._queue.maxsize, event["signal_natural_key"], event["falcon_event_id"],
            )
        except Exception as e:
            # Final defensive boundary: enqueueing itself must never be able to propagate.
            logger.error("[EVIDENCE_PUBLISH_FAIL] unexpected exception while enqueueing evidence event: %r", e)

    def close(self) -> None:
        """Bounded shutdown (F1 fix). Signals the worker to stop, gives it a small,
        best-effort chance to drain whatever is already queued/in-flight, but never waits
        longer than `_WORKER_SHUTDOWN_MAX_WAIT_SECONDS` regardless of the worker's state.
        After that bound, whatever remains queued is discarded (logged, counted via
        queue.qsize()) and the transport connection is force-closed on a best-effort basis.

        Deliberately uses a NON-BLOCKING attempt to acquire `_lock` for the final close: if
        the join() above timed out, the worker thread may still be mid-operation and
        holding `_lock` for up to its own remaining `timeout` budget — blocking here too
        would silently let total shutdown time drift well past
        `_WORKER_SHUTDOWN_MAX_WAIT_SECONDS` (up to ~2x the transport timeout), defeating the
        whole point of a bounded shutdown. If the lock is busy, the stale connection is left
        for the (daemon) worker thread to close itself when it eventually unblocks, or for
        the OS to reclaim at process exit — never for this method to wait on. No
        persistence is added across shutdown; this channel is explicitly best-effort, not
        guaranteed-delivery.
        """
        self._stop_event.set()
        if self._worker_thread is not None:
            self._worker_thread.join(timeout=_WORKER_SHUTDOWN_MAX_WAIT_SECONDS)
            if self._worker_thread.is_alive():
                logger.warning(
                    "[EVIDENCE_PUBLISH_SHUTDOWN_TIMEOUT] worker thread still running after "
                    "the %.1fs bounded shutdown wait; proceeding without waiting further. "
                    "The worker is a daemon thread and will not block process exit.",
                    _WORKER_SHUTDOWN_MAX_WAIT_SECONDS,
                )
        discarded = self._queue.qsize()
        if discarded:
            logger.warning(
                "[EVIDENCE_PUBLISH_SHUTDOWN_DISCARD] discarding %d queued evidence event(s) "
                "at shutdown (best-effort telemetry; no persistence across shutdown)",
                discarded,
            )
        if self._lock.acquire(blocking=False):
            try:
                self._reset_connection()
            finally:
                self._lock.release()
        else:
            logger.warning(
                "[EVIDENCE_PUBLISH_SHUTDOWN_LOCK_BUSY] transport lock still held by the "
                "worker thread after the bounded shutdown wait; skipping explicit close to "
                "keep shutdown bounded (the OS reclaims the socket at process exit)."
            )

    def get_stats(self) -> Dict[str, int]:
        with self._stats_lock:
            stats = dict(self._stats)
        # Approximation under concurrency (queue.qsize() is documented as such by the stdlib)
        # — acceptable for observability purposes; never used for a correctness decision.
        stats["queue_depth_approx"] = self._queue.qsize()
        return stats


class DisabledEvidencePublisher:
    """No-op evidence publisher. Zero connection/credential/TLS overhead — no socket, no SSL
    context, no file reads. Used whenever FALCON_PUBLISH_ENABLED is unset/false."""

    enabled = False

    def publish(self, signal: Any) -> None:  # noqa: ARG002 - signal intentionally unused
        return

    def close(self) -> None:
        return

    def get_stats(self) -> Dict[str, int]:
        return {
            "events_sent": 0, "events_failed": 0, "reconnections": 0,
            "events_queued": 0, "events_dropped_queue_full": 0, "queue_depth_approx": 0,
        }


def build_evidence_publisher_from_env():
    """Build the evidence publisher from environment configuration, fail-loud when enabled.

    FALCON_PUBLISH_ENABLED unset/false -> DisabledEvidencePublisher (default; zero overhead).
    FALCON_PUBLISH_ENABLED=true -> ALL of FALCON_HOST / FALCON_PORT / FALCON_CLIENT_CERT_FILE /
    FALCON_CLIENT_KEY_FILE / FALCON_CA_FILE are mandatory and validated here (GOV-CFG-001:
    enabled-but-misconfigured fails loud at startup — this is a DIFFERENT code path/doctrine
    from a runtime "sink unreachable" failure, which is always log-and-continue, never raised).

    Note on `_FILE` naming: these three variables hold FILE PATHS consumed directly by
    ssl.SSLContext (load_cert_chain/load_verify_locations read the files themselves) — they
    are deliberately read via env_config.get_env(), not env_config.get_secret(). get_secret()'s
    `_FILE` convention reads a referenced file's CONTENT as the secret VALUE (e.g.
    DISCORD_WEBHOOK_PROD_FILE); a TLS cert/key/CA is consumed as a path, not as inline file
    content, so that content-loading semantics does not apply here. The `_FILE` suffix is kept
    purely for naming-convention consistency (it signals "this names a file").
    """
    from env_config import get_env, get_env_bool  # HERMES-owned config at repo root

    enabled = get_env_bool("FALCON_PUBLISH_ENABLED", False)
    if not enabled:
        return DisabledEvidencePublisher()

    host = get_env("FALCON_HOST")
    port_raw = get_env("FALCON_PORT")
    cert_file = get_env("FALCON_CLIENT_CERT_FILE")
    key_file = get_env("FALCON_CLIENT_KEY_FILE")
    ca_file = get_env("FALCON_CA_FILE")

    missing = [
        name for name, value in (
            ("FALCON_HOST", host),
            ("FALCON_PORT", port_raw),
            ("FALCON_CLIENT_CERT_FILE", cert_file),
            ("FALCON_CLIENT_KEY_FILE", key_file),
            ("FALCON_CA_FILE", ca_file),
        ) if not value
    ]
    if missing:
        raise ValueError(
            "GOV-CFG-001: FALCON_PUBLISH_ENABLED=true but required evidence-publish "
            f"configuration is missing: {', '.join(missing)} (fail-loud; no silent no-op)"
        )

    try:
        port = int(port_raw)
    except (TypeError, ValueError):
        raise ValueError(f"GOV-CFG-001: FALCON_PORT is not a valid integer: {port_raw!r}")

    for label, path in (
        ("FALCON_CLIENT_CERT_FILE", cert_file),
        ("FALCON_CLIENT_KEY_FILE", key_file),
        ("FALCON_CA_FILE", ca_file),
    ):
        if not os.path.isfile(path):
            raise ValueError(
                f"GOV-CFG-001: {label} does not reference a readable regular file: {path!r}"
            )

    # Constructing EvidencePublisher here builds the SSLContext (load_cert_chain /
    # load_verify_locations) eagerly, so a cert/key mismatch or malformed PEM also fails loud
    # at startup rather than silently at first publish.
    return EvidencePublisher(
        host=host,
        port=port,
        client_cert_file=cert_file,
        client_key_file=key_file,
        ca_file=ca_file,
    )
