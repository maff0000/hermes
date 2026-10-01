"""Tests for the F1 fix: evidence-publish asynchronous boundary (bounded queue + background
worker thread). WO-PID05-STAGE3B-EVIDENCE-PUBLISHER.

F1 (found during HELM's real DEV integration proof): the original Stage-3B design ran the
GELF TCP + mTLS transport SYNCHRONOUSLY, INLINE, on HERMES's single-threaded asyncio
signal-processing coroutine, so a hung/black-holed evidence sink could stall the ENTIRE
event loop for up to ~2 * timeout seconds per publication. This file proves the fix: no
network I/O to the evidence sink ever runs on the signal-processing path; a bounded
in-process queue + single background daemon thread owns the existing, unchanged, blocking
transport instead. See utils/evidence_publisher_v1.py's module docstring ("Asynchronous
publication boundary") for the full architecture/sizing rationale.

Pure-logic + synthetic-TLS-transport tests. NO real DB/Redis/FALCON, NO real PKI material —
reuses the throwaway self-signed test-only PKI and fake GELF-over-TLS listener already
established in tests/test_evidence_publisher_v1.py (imported, not duplicated).
"""
import json
import logging
import os
import socket
import sys
import threading
import time

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import utils.evidence_publisher_v1 as ep  # noqa: E402
from signal_builder import SignalPublisher  # noqa: E402
from tests.test_evidence_publisher_v1 import (  # noqa: E402
    make_signal, pki, listener, FakeGELFTLSListener, _wait_until, _bare_publisher,
)


# ============================================================================
# A "hung" sink: accepts the TCP connection but never completes the TLS handshake or sends
# any bytes — simulates a sink that is reachable at the TCP layer but completely
# unresponsive beyond that, deliberately for longer than the client's configured timeout.
# This is what case 3 of the required performance proof means by "accepts the TCP
# connection but then hangs/never responds".
# ============================================================================

class HungAcceptListener:
    def __init__(self):
        self._raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._raw_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._raw_sock.bind(("127.0.0.1", 0))
        self._raw_sock.listen(5)
        self.port = self._raw_sock.getsockname()[1]
        self._stop = False
        self._accepted = []
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        while not self._stop:
            self._raw_sock.settimeout(0.2)
            try:
                conn, _ = self._raw_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            self._accepted.append(conn)  # held open forever: never read, written, or closed

    def stop(self):
        self._stop = True
        try:
            self._raw_sock.close()
        except Exception:
            pass
        for c in self._accepted:
            try:
                c.close()
            except Exception:
                pass


@pytest.fixture
def hung():
    h = HungAcceptListener()
    yield h
    h.stop()


# ============================================================================
# 1. Signal-path non-blocking — THE key regression test proving F1 is fixed
# ============================================================================

def test_signal_path_never_blocks_on_hung_transport_key_regression_test(pki, hung):
    """SignalPublisher.publish_signal() must return in a bounded, short time regardless of a
    transport hang that is clearly longer than any acceptable signal-processing budget. Here
    the sink hangs for up to 10s (the EvidencePublisher's configured timeout) — under the OLD
    inline design this would have blocked the ENTIRE asyncio event loop for up to ~2x that.
    Measures the WHOLE publish_signal() call (SQL + Redis + evidence) since that is what the
    signal-processing coroutine actually awaits; SQL fails fast here (connection refused on
    an unused port) so this is a clean, honest measurement of the evidence-publish
    contribution specifically — the SQL failure path itself is sub-millisecond.
    """
    evidence = ep.EvidencePublisher(
        host="127.0.0.1", port=hung.port,
        client_cert_file=pki.client_cert, client_key_file=pki.client_key, ca_file=pki.ca_cert,
        timeout=10.0,
    )
    try:
        pub = SignalPublisher(
            db_config={"host": "127.0.0.1", "port": 1, "user": "x", "password": "x", "database": "x"},
            redis_publisher=None,
            evidence_publisher=evidence,
        )
        sig = make_signal()
        start = time.perf_counter()
        pub.publish_signal(sig)
        elapsed = time.perf_counter() - start

        assert elapsed < 0.1, (
            f"publish_signal() took {elapsed * 1000:.1f}ms with a 10s-hung evidence sink — "
            "the signal path touched the hung transport (F1 regression)"
        )
    finally:
        evidence.close()


# ============================================================================
# 2. Queue behavior
# ============================================================================

def test_event_enqueued_successfully_under_normal_conditions(pki, listener):
    pub = ep.EvidencePublisher(
        host="127.0.0.1", port=listener.port,
        client_cert_file=pki.client_cert, client_key_file=pki.client_key, ca_file=pki.ca_cert,
        timeout=2.0,
    )
    try:
        pub.publish(make_signal())
        _wait_until(lambda: pub.get_stats()["events_queued"] >= 1)
    finally:
        pub.close()


def test_queue_bounded_capacity_enforced_full_drop_nonblocking_and_logged(pki, hung, caplog):
    """Makes the worker permanently busy (stuck inside a hung TLS handshake) so the queue
    genuinely fills to its bound, then proves: (a) exactly maxsize items queue successfully,
    (b) the NEXT publish() hits queue.Full and is dropped WITHOUT blocking, (c) the drop is
    counted and logged with the distinct DROPPED_QUEUE_FULL tag at warning level."""
    pub = ep.EvidencePublisher(
        host="127.0.0.1", port=hung.port,
        client_cert_file=pki.client_cert, client_key_file=pki.client_key, ca_file=pki.ca_cert,
        timeout=30.0,  # long enough it will not resolve during this test
    )
    try:
        # First event gets dequeued almost immediately and blocks the worker in the hung
        # handshake for (up to) the whole 30s timeout window.
        pub.publish(make_signal())
        _wait_until(lambda: pub._queue.qsize() == 0, timeout=2.0)

        maxsize = ep._EVIDENCE_QUEUE_MAX_SIZE
        for _ in range(maxsize):
            pub.publish(make_signal())
        assert pub._queue.full()
        assert pub.get_stats()["events_queued"] == maxsize + 1  # +1 for the one already dequeued
        assert pub.get_stats()["events_dropped_queue_full"] == 0

        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="utils.evidence_publisher_v1"):
            start = time.perf_counter()
            pub.publish(make_signal())  # must overflow: queue is completely full
            elapsed = time.perf_counter() - start

        assert elapsed < 0.1, "queue-full path must never block"
        assert pub.get_stats()["events_dropped_queue_full"] == 1
        drop_records = [r for r in caplog.records if "EVIDENCE_PUBLISH_DROPPED_QUEUE_FULL" in r.message]
        assert drop_records, "expected a distinct [EVIDENCE_PUBLISH_DROPPED_QUEUE_FULL] log line"
        assert all(r.levelno == logging.WARNING for r in drop_records)
        # Distinct from the transport-level failure tag.
        assert not any("EVIDENCE_PUBLISH_FAIL" in r.message for r in drop_records)
    finally:
        pub.close()


# ============================================================================
# 3. Identity across the hop
# ============================================================================

def test_identity_fixed_before_enqueue_distinct_across_concurrently_queued_events(pki, hung):
    """Proves: (1) falcon_event_id/produced_at_utc/natural_key are already fixed on the
    queued item itself — inspected directly, not merely inferred; (2) two SEPARATE publish()
    calls get different event ids even while BOTH are sitting in the queue at the same time,
    despite sharing the same instrument/timeframe (natural-key) family."""
    pub = ep.EvidencePublisher(
        host="127.0.0.1", port=hung.port,
        client_cert_file=pki.client_cert, client_key_file=pki.client_key, ca_file=pki.ca_cert,
        timeout=30.0,
    )
    try:
        pub.publish(make_signal(instrument="EUR_USD"))
        _wait_until(lambda: pub._queue.qsize() == 0, timeout=2.0)  # dequeued; worker now stuck

        pub.publish(make_signal(instrument="EUR_USD"))
        pub.publish(make_signal(instrument="EUR_USD"))
        assert pub._queue.qsize() == 2  # both genuinely sitting in the queue together, right now

        item_b = pub._queue.get_nowait()
        item_c = pub._queue.get_nowait()

        for item in (item_b, item_c):
            assert item["event"]["falcon_event_id"]
            assert item["event"]["produced_at_utc"]
            assert item["event"]["signal_natural_key"] == item["event"]["payload"]["signal_natural_key"]
            # The already-serialized wire bytes carry this exact identity too — fixed before
            # enqueue, nothing left to regenerate later.
            wire = json.loads(item["data"].rstrip(b"\x00").decode("utf-8"))
            assert wire["_falcon_event_id"] == item["event"]["falcon_event_id"]
            assert wire["_produced_at_utc"] == item["event"]["produced_at_utc"]

        assert item_b["event"]["falcon_event_id"] != item_c["event"]["falcon_event_id"]
        assert item_b["event"]["signal_natural_key"] == item_c["event"]["signal_natural_key"]
    finally:
        pub.close()


# ============================================================================
# 4. Worker resilience
# ============================================================================

def test_worker_survives_malformed_queued_item_and_processes_next_normal_event(pki, listener):
    pub = ep.EvidencePublisher(
        host="127.0.0.1", port=listener.port,
        client_cert_file=pki.client_cert, client_key_file=pki.client_key, ca_file=pki.ca_cert,
        timeout=2.0,
    )
    try:
        # Directly inject a malformed item — defends against anything ever reaching the
        # queue in a shape other than publish()'s own well-formed {"event":..,"data":..}.
        pub._queue.put_nowait({"event": None, "data": None})
        pub.publish(make_signal())  # a normal, well-formed event right behind it

        msgs = listener.wait_for_messages(1, timeout=3.0)
        assert len(msgs) == 1
        assert pub.get_stats()["events_sent"] == 1
        assert pub._worker_thread.is_alive()
    finally:
        pub.close()


def test_worker_survives_transport_exception_and_processes_next_normal_event(pki, listener):
    """Feed the worker one event whose transport call raises an UNEXPECTED exception
    (simulating a failure mode escaping the transport's own internal try/except — exactly
    the scenario _send_event_with_retry's own outer boundary exists for: one raise aborts
    that event's retry loop entirely and is caught, logged, without crashing the worker),
    then a separate, different event that must still succeed — proving the worker thread
    survives and continues. Keyed off the serialized payload's subject_id (not a shared call
    counter) so the SECOND, unrelated event is never affected by the first event's failure."""
    pub = ep.EvidencePublisher(
        host="127.0.0.1", port=listener.port,
        client_cert_file=pki.client_cert, client_key_file=pki.client_key, ca_file=pki.ca_cert,
        timeout=2.0,
    )
    try:
        real_attempt = pub._attempt_send
        calls = {"n": 0, "failing_one_attempts": 0}

        def flaky(data):
            calls["n"] += 1
            msg = json.loads(data.rstrip(b"\x00").decode("utf-8"))
            if msg.get("_subject_id") == "FAILING_ONE":
                calls["failing_one_attempts"] += 1
                raise RuntimeError("simulated transport explosion")
            return real_attempt(data)

        pub._attempt_send = flaky
        pub.publish(make_signal(instrument="FAILING_ONE"))
        _wait_until(lambda: calls["failing_one_attempts"] >= 1, timeout=3.0)
        assert pub._worker_thread.is_alive()  # survived the unexpected exception

        pub.publish(make_signal(instrument="NORMAL_TWO"))  # a separate, different event
        msgs = listener.wait_for_messages(1, timeout=3.0)
        assert len(msgs) == 1
        assert pub.get_stats()["events_sent"] == 1
        assert pub._worker_thread.is_alive()
    finally:
        pub.close()


# ============================================================================
# 5. Shutdown
# ============================================================================

def test_shutdown_clean_and_fast_when_queue_empty(pki, listener):
    pub = ep.EvidencePublisher(
        host="127.0.0.1", port=listener.port,
        client_cert_file=pki.client_cert, client_key_file=pki.client_key, ca_file=pki.ca_cert,
        timeout=2.0,
    )
    start = time.perf_counter()
    pub.close()
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0


def test_shutdown_bounded_when_transport_hung_and_discard_is_logged(pki, hung, caplog):
    pub = ep.EvidencePublisher(
        host="127.0.0.1", port=hung.port,
        client_cert_file=pki.client_cert, client_key_file=pki.client_key, ca_file=pki.ca_cert,
        timeout=30.0,
    )
    pub.publish(make_signal())  # dequeued immediately; blocks the worker in the hung handshake
    _wait_until(lambda: pub._queue.qsize() == 0, timeout=2.0)
    pub.publish(make_signal())  # these two will NOT be processed before the shutdown deadline
    pub.publish(make_signal())
    assert pub._queue.qsize() == 2

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="utils.evidence_publisher_v1"):
        start = time.perf_counter()
        pub.close()
        elapsed = time.perf_counter() - start

    # Bounded: close() must return close to _WORKER_SHUTDOWN_MAX_WAIT_SECONDS (5.0s),
    # nowhere near the 30s the transport is actually configured to hang for.
    assert elapsed < ep._WORKER_SHUTDOWN_MAX_WAIT_SECONDS + 1.0, (
        f"close() took {elapsed:.2f}s — shutdown is not bounded"
    )
    messages = [r.message for r in caplog.records]
    assert any("EVIDENCE_PUBLISH_SHUTDOWN_TIMEOUT" in m for m in messages)
    assert any("EVIDENCE_PUBLISH_SHUTDOWN_DISCARD" in m for m in messages)


# ============================================================================
# Required empirical performance proof (STOP GATE item)
# ============================================================================

def test_performance_proof_healthy_refused_hung_before_after_contrast(pki, listener, capsys):
    """Measures publish()'s wall-clock latency under three conditions and reports the actual
    numbers. The point to prove: a hung remote end (case 3), which under the OLD inline
    design would have blocked the signal-processing coroutine for ~2x its configured
    timeout, must no longer produce anything close to that under the NEW design — contrasted
    directly against a real (not merely reasoned) measurement of the old call pattern
    (_send_event_with_retry called directly, exactly as publish() used to call it inline).
    """
    results = {}

    # --- 1. Healthy sender ---
    pub_healthy = ep.EvidencePublisher(
        host="127.0.0.1", port=listener.port,
        client_cert_file=pki.client_cert, client_key_file=pki.client_key, ca_file=pki.ca_cert,
        timeout=2.0,
    )
    start = time.perf_counter()
    pub_healthy.publish(make_signal())
    results["healthy_ms"] = (time.perf_counter() - start) * 1000
    _wait_until(lambda: pub_healthy.get_stats()["events_sent"] == 1)
    pub_healthy.close()

    # --- 2. Connection refused (nothing listening) ---
    pub_refused = ep.EvidencePublisher(
        host="127.0.0.1", port=1,
        client_cert_file=pki.client_cert, client_key_file=pki.client_key, ca_file=pki.ca_cert,
        timeout=2.0,
    )
    start = time.perf_counter()
    pub_refused.publish(make_signal())
    results["refused_ms"] = (time.perf_counter() - start) * 1000
    _wait_until(lambda: pub_refused.get_stats()["events_failed"] == 1)
    pub_refused.close()

    # --- 3. Hung sender (accepts TCP, never completes the handshake), NEW design ---
    hung1 = HungAcceptListener()
    try:
        pub_hung = ep.EvidencePublisher(
            host="127.0.0.1", port=hung1.port,
            client_cert_file=pki.client_cert, client_key_file=pki.client_key, ca_file=pki.ca_cert,
            timeout=8.0,
        )
        start = time.perf_counter()
        pub_hung.publish(make_signal())  # NEW design: must return almost instantly regardless
        results["hung_new_design_ms"] = (time.perf_counter() - start) * 1000
        pub_hung.close()
    finally:
        hung1.stop()

    # --- "Before" contrast: a REAL measurement of the old inline call pattern ---
    # The OLD (pre-F1) design ran exactly this retry loop INLINE on publish()'s caller.
    # Reproduce that call pattern directly (bypassing the async queue) with a short timeout
    # (1.0s, purely to keep this proof fast) to get an ACTUAL measured number, not just
    # reasoned timeout math.
    hung2 = HungAcceptListener()
    try:
        old_style = ep.EvidencePublisher(
            host="127.0.0.1", port=hung2.port,
            client_cert_file=pki.client_cert, client_key_file=pki.client_key, ca_file=pki.ca_cert,
            timeout=1.0,
        )
        event = ep.build_evidence_event(make_signal())
        data = json.dumps(event["gelf_message"], default=str).encode("utf-8") + ep._TCP_NULL_TERMINATOR
        start = time.perf_counter()
        old_style._send_event_with_retry(event, data)  # the exact old inline call pattern
        results["hung_old_design_equivalent_ms"] = (time.perf_counter() - start) * 1000
        old_style.close()
    finally:
        hung2.stop()

    with capsys.disabled():
        print("\n[EVIDENCE_PUBLISH_PERF_PROOF] measured latencies (ms):")
        for k, v in results.items():
            print(f"    {k} = {v:.2f}ms")
        print(
            "    reasoned old-design worst case at the 8.0s timeout used for case 3: "
            f"~{2 * 8000:.0f}ms (2 attempts * timeout, inline on the event loop)"
        )

    assert results["healthy_ms"] < 100
    assert results["refused_ms"] < 100
    assert results["hung_new_design_ms"] < 100, (
        "F1 regression: the signal path must never block on a hung evidence sink"
    )
    # Real measured old-design-equivalent: ~2 * timeout(1.0s) = ~2000ms. Confirms the old
    # inline design really would have blocked for roughly this long (and scales linearly with
    # the configured timeout — ~16s at the 8.0s timeout used for case 3 above).
    assert results["hung_old_design_equivalent_ms"] > 1500
