"""Tests for the HERMES evidence-publish boundary (PID05/Stage-3B).
WO-PID05-STAGE3B-EVIDENCE-PUBLISHER.

Pure-logic + synthetic-TLS-transport tests. NO real DB/Redis, NO real FALCON, NO real PKI
material anywhere — all certificates are generated fresh, per test-session, into pytest's
tmp_path via tests/fixtures/evidence_publisher_test_certs (throwaway self-signed test-only
PKI; see that module's docstring).
"""
import json
import os
import re
import socket
import ssl
import sys
import threading
import time
import uuid
from datetime import datetime, timezone

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import utils.evidence_publisher_v1 as ep  # noqa: E402
from signal_builder import Signal, SignalPublisher  # noqa: E402
from tests.fixtures.evidence_publisher_test_certs import generate_test_pki  # noqa: E402


# ============================================================================
# Helpers
# ============================================================================

def make_signal(**overrides) -> Signal:
    """Build a real Signal dataclass instance with sane defaults, override-able per test."""
    base = dict(
        instrument="WTICO_USD",
        timestamp=datetime(2026, 10, 1, 8, 47, 0),  # naive, UTC-by-construction (no tzinfo)
        timeframe="M1",
        price_open=82.10,
        price_high=82.20,
        price_low=82.05,
        price_close=82.15,
        volume=123,
        volume_ratio=1.1,
        rsi_14=55.5,
        ema_9=82.1,
        ema_21=82.0,
        ema_12=82.05,
        ema_26=81.9,
        ema_20=82.0,
        ema_50=81.5,
        ema_200=80.0,
        ema_9_21_state="BULLISH",
        ema_12_26_state="BULLISH",
        ema_20_50_state="BULLISH",
        ema_50_200_state="BULLISH",
        atr_14=0.35,
        regime="BULL_TREND",
        session="london",
    )
    base.update(overrides)
    return Signal(**base)


class FakeGELFTLSListener:
    """A minimal mTLS GELF TCP listener for tests — accepts connections requiring a client
    certificate signed by the test CA, reads null-terminated messages, and records them.
    Not a real FALCON; just enough transport to prove EvidencePublisher's wire behaviour.
    """

    def __init__(self, pki, require_client_cert=True):
        self._ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self._ctx.load_cert_chain(certfile=pki.server_cert, keyfile=pki.server_key)
        if require_client_cert:
            self._ctx.verify_mode = ssl.CERT_REQUIRED
            self._ctx.load_verify_locations(cafile=pki.ca_cert)
        else:
            self._ctx.verify_mode = ssl.CERT_NONE

        self._raw_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._raw_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._raw_sock.bind(("127.0.0.1", 0))
        self._raw_sock.listen(5)
        self.port = self._raw_sock.getsockname()[1]

        self.messages = []
        self._lock = threading.Lock()
        self._stop = False
        self._accept_failures = []
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        while not self._stop:
            self._raw_sock.settimeout(0.2)
            try:
                raw_conn, _ = self._raw_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                conn = self._ctx.wrap_socket(raw_conn, server_side=True)
            except Exception as e:
                with self._lock:
                    self._accept_failures.append(repr(e))
                try:
                    raw_conn.close()
                except Exception:
                    pass
                continue
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn):
        buf = b""
        conn.settimeout(2.0)
        try:
            while not self._stop:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                buf += chunk
                while b"\x00" in buf:
                    raw, buf = buf.split(b"\x00", 1)
                    with self._lock:
                        self.messages.append(json.loads(raw.decode("utf-8")))
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def wait_for_messages(self, count, timeout=5.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if len(self.messages) >= count:
                    return list(self.messages)
            time.sleep(0.02)
        with self._lock:
            return list(self.messages)

    def stop(self):
        self._stop = True
        try:
            self._raw_sock.close()
        except Exception:
            pass


@pytest.fixture(scope="module")
def pki(tmp_path_factory):
    return generate_test_pki(tmp_path_factory.mktemp("evidence_publisher_pki"))


@pytest.fixture
def listener(pki):
    srv = FakeGELFTLSListener(pki)
    yield srv
    srv.stop()


def _env(monkeypatch, **kv):
    for k, v in kv.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, str(v))


def _bare_publisher():
    """Construct an EvidencePublisher via __new__ (no real SSL/socket/thread init) with every
    attribute the F1 async-boundary refactor now expects to exist, pre-populated, so tests
    that drive internals directly (bypassing the real queue/worker-thread machinery) keep
    working. Used only for tests that exercise a single internal method in isolation
    (e.g. `_send_event_with_retry`) — never starts a background thread."""
    publisher = ep.EvidencePublisher.__new__(ep.EvidencePublisher)
    publisher.host, publisher.port = "127.0.0.1", 0
    publisher._lock = threading.Lock()
    publisher._stats_lock = threading.Lock()
    publisher._stats = {
        "events_sent": 0, "events_failed": 0, "reconnections": 0,
        "events_queued": 0, "events_dropped_queue_full": 0,
    }
    publisher._queue = ep.queue.Queue(maxsize=ep._EVIDENCE_QUEUE_MAX_SIZE)
    publisher._stop_event = threading.Event()
    publisher._worker_thread = None  # no real worker thread for these isolated-method tests
    return publisher


def _wait_until(predicate, timeout=3.0, interval=0.01):
    """Poll `predicate()` until truthy or timeout; raises AssertionError on timeout. Used to
    deterministically wait for the background worker thread to finish processing an item
    that was just enqueued via publish() (the handoff is async by design — see F1 fix)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError(f"timed out after {timeout}s waiting for condition to become true")


# ============================================================================
# Identity semantics
# ============================================================================

def test_natural_key_exact_format():
    sig = make_signal(instrument="XAUUSD", timeframe="M5",
                       timestamp=datetime(2026, 9, 30, 8, 5, 0))
    event = ep.build_evidence_event(sig)
    assert event["signal_natural_key"] == "XAUUSD:M5:2026-09-30T08:05:00Z"
    assert not event["signal_natural_key"].startswith("hermes:")


def test_event_id_fresh_per_invocation_even_for_identical_natural_key():
    sig = make_signal()
    e1 = ep.build_evidence_event(sig)
    e2 = ep.build_evidence_event(sig)
    assert e1["signal_natural_key"] == e2["signal_natural_key"]
    assert e1["falcon_event_id"] != e2["falcon_event_id"]
    # both are real UUID4 strings
    uuid.UUID(e1["falcon_event_id"], version=4)
    uuid.UUID(e2["falcon_event_id"], version=4)


def test_event_id_and_produced_at_reused_across_retries_within_one_invocation(pki, listener, monkeypatch):
    """Simulate a transport retry: force the FIRST send attempt to fail, let the SECOND
    succeed, and prove the wire message is byte-identical (same event id / produced_at) on
    both attempts, not a freshly-built event."""
    publisher = ep.EvidencePublisher(
        host="127.0.0.1", port=listener.port,
        client_cert_file=pki.client_cert, client_key_file=pki.client_key, ca_file=pki.ca_cert,
        timeout=2.0,
    )
    attempts = []
    real_attempt = publisher._attempt_send

    def flaky_attempt(data):
        attempts.append(data)
        if len(attempts) == 1:
            return False  # force first attempt to "fail" without touching the real socket
        return real_attempt(data)

    monkeypatch.setattr(publisher, "_attempt_send", flaky_attempt)
    sig = make_signal()
    publisher.publish(sig)
    publisher.close()

    assert len(attempts) == 2
    assert attempts[0] == attempts[1]  # exact same already-serialized event both times
    msgs = listener.wait_for_messages(1)
    assert len(msgs) == 1


def test_replay_two_separate_invocations_same_natural_key_get_distinct_event_ids():
    """Mirrors how backfill_gap()/recovery_executor.py would re-traverse this code path: two
    independent publish() calls for the identical natural key must not be suppressed or
    deduplicated, and must carry different event ids."""
    sig = make_signal()
    seen_ids = set()
    seen_keys = set()
    for _ in range(2):
        event = ep.build_evidence_event(sig)
        seen_ids.add(event["falcon_event_id"])
        seen_keys.add(event["signal_natural_key"])
    assert len(seen_ids) == 2
    assert len(seen_keys) == 1  # same natural key both times — intentional, not an error


# ============================================================================
# UTC timestamp semantics
# ============================================================================

def test_naive_signal_timestamp_becomes_explicit_z_suffixed_utc_with_no_shift():
    sig = make_signal(timestamp=datetime(2026, 10, 1, 23, 59, 59))
    event = ep.build_evidence_event(sig)
    assert event["signal_natural_key"].endswith("2026-10-01T23:59:59Z")
    assert event["payload"]["signal_natural_key"].endswith("2026-10-01T23:59:59Z")


def test_naive_signal_timestamp_unaffected_by_local_timezone(monkeypatch):
    """The historical bug class: calling .astimezone() on a naive datetime silently assumes
    the PROCESS's local timezone. Prove the natural key value is identical regardless of
    the OS TZ env var (this module must never read local time for the signal's own
    timestamp)."""
    sig = make_signal(timestamp=datetime(2026, 3, 1, 12, 0, 0))
    monkeypatch.setenv("TZ", "Pacific/Kiritimati")  # UTC+14 — would shift the date if misused
    try:
        time.tzset()
    except AttributeError:
        pass  # not available on this platform; the assertion below still proves the point
    event = ep.build_evidence_event(sig)
    assert event["signal_natural_key"] == "WTICO_USD:M1:2026-03-01T12:00:00Z"
    monkeypatch.undo()
    try:
        time.tzset()
    except AttributeError:
        pass


def test_no_astimezone_call_anywhere_in_module():
    """Grep-level rigor: the specific bug class this module must avoid is calling
    `.astimezone()` on a naive datetime (which silently assumes local system time). Checked
    against the actual CODE only — module/function docstrings are allowed to discuss the rule
    in prose (and do)."""
    src = open(ep.__file__, encoding="utf-8").read()
    code_only = re.sub(r'""".*?"""', "", src, flags=re.DOTALL)
    assert ".astimezone(" not in code_only


def test_produced_at_utc_is_separate_fresh_aware_value_from_signal_timestamp():
    from datetime import timedelta

    sig = make_signal(timestamp=datetime(2020, 1, 1, 0, 0, 0))
    before = datetime.now(timezone.utc)
    event = ep.build_evidence_event(sig)
    after = datetime.now(timezone.utc)
    produced_at = datetime.strptime(
        event["produced_at_utc"], "%Y-%m-%dT%H:%M:%SZ"
    ).replace(tzinfo=timezone.utc)
    # produced_at_utc is a fresh wall-clock read, bounded by [before, after] (±1s for the
    # whole-second truncation in the "...Z" format) — NOT the signal's own business timestamp.
    assert before.replace(microsecond=0) - timedelta(seconds=1) <= produced_at
    assert produced_at <= after.replace(microsecond=0) + timedelta(seconds=1)
    assert event["produced_at_utc"] != event["signal_natural_key"].split(":", 2)[-1]


def test_produced_at_utc_reused_unchanged_across_retries():
    """F1 refactor: the 2-attempt retry loop now lives in _send_event_with_retry(), called by
    the background worker thread — exercise it directly (bypassing the real queue/thread) to
    prove the SAME already-built event/bytes are reused across both attempts, unmodified."""
    publisher = _bare_publisher()  # avoid real SSL/socket/thread init
    sig = make_signal()
    event = ep.build_evidence_event(sig)
    data = json.dumps(event["gelf_message"], default=str).encode("utf-8") + ep._TCP_NULL_TERMINATOR
    captured = []

    def fake_attempt_send(d):
        captured.append(json.loads(d.rstrip(b"\x00").decode("utf-8")))
        return len(captured) >= 2

    publisher._attempt_send = fake_attempt_send
    publisher._send_event_with_retry(event, data)

    assert len(captured) == 2
    assert captured[0]["_produced_at_utc"] == captured[1]["_produced_at_utc"]
    assert captured[0]["_falcon_event_id"] == captured[1]["_falcon_event_id"]


# ============================================================================
# Payload semantics
# ============================================================================

def test_required_signal_natural_key_present_in_payload():
    event = ep.build_evidence_event(make_signal())
    assert event["payload"]["signal_natural_key"] == event["signal_natural_key"]


def test_optional_fields_included_only_when_present():
    sig = make_signal(instrument="EUR_USD", timeframe="M5", regime="RANGING", session="asia")
    event = ep.build_evidence_event(sig)
    payload = event["payload"]
    assert payload["instrument_id"] == "EUR_USD"
    assert payload["timeframe"] == "M5"
    assert payload["regime"] == "RANGING"
    assert payload["session"] == "asia"
    assert payload["signal_type"] == "indicator_regime_snapshot"


def test_optional_fields_omitted_not_defaulted_when_genuinely_absent():
    """A bare object missing `regime`/`session` entirely must OMIT those keys — never send
    null/empty-string/a fabricated default."""
    class Bare:
        instrument = "XAU_USD"
        timeframe = "M1"
        timestamp = datetime(2026, 1, 1, 0, 0, 0)

    event = ep.build_evidence_event(Bare())
    payload = event["payload"]
    assert "regime" not in payload
    assert "session" not in payload
    assert payload.get("regime", "ABSENT") == "ABSENT"  # never None/""/a default
    assert payload["instrument_id"] == "XAU_USD"
    assert payload["signal_type"] == "indicator_regime_snapshot"


def test_full_original_signal_json_preserved_exactly():
    sig = make_signal(rsi_14=71.25, break_level_id=42)
    event = ep.build_evidence_event(sig)
    raw = json.loads(event["gelf_message"]["_hermes_signal_raw_json"])
    assert raw["rsi_14"] == 71.25
    assert raw["break_level_id"] == 42
    assert raw["instrument"] == sig.instrument
    assert raw["regime"] == sig.regime
    # every declared dataclass field round-trips
    import dataclasses
    for f in dataclasses.fields(Signal):
        assert f.name in raw


def test_payload_hash_is_genuine_sha256_over_canonical_payload():
    import hashlib
    sig = make_signal()
    event = ep.build_evidence_event(sig)
    expected = "sha256:" + hashlib.sha256(
        json.dumps(event["payload"], sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert event["gelf_message"]["_payload_hash"] == expected


def test_envelope_fixed_fields_match_registered_family():
    event = ep.build_evidence_event(make_signal())
    msg = event["gelf_message"]
    assert msg["_envelope_version"] == "v1"
    assert msg["_payload_schema_version"] == "v1"
    assert msg["_producer_system_id"] == "hermes"
    assert msg["_producer_component_id"] == "hermes.signal_engine"
    assert msg["_evidence_family"] == "hermes.signal_state"
    assert msg["_evidence_type"] == "indicator_regime_snapshot"
    assert msg["_evidence_class"] == "DETERMINISTIC_DERIVATION"
    assert msg["_retention_class"] == "standard"
    assert msg["_sensitivity_class"] == "internal"
    assert msg["_falcon_ingested_at_utc"] == msg["_produced_at_utc"]
    assert json.loads(msg["_instrument_ids"]) == [make_signal().instrument]
    prov = json.loads(msg["_provenance_ref_json"])
    assert prov["producer_system_id"] == "hermes"
    assert prov["upstream_ref"].startswith("hermes:signals:")


def test_no_falcon_naming_leakage_in_code_identifiers():
    """Code/architecture identifiers must stay generic; 'FALCON' is legitimate only as a
    config/env-var-boundary string, never a class/function/module name."""
    assert ep.EvidencePublisher.__name__ == "EvidencePublisher"
    assert ep.DisabledEvidencePublisher.__name__ == "DisabledEvidencePublisher"
    assert ep.build_evidence_publisher_from_env.__name__ == "build_evidence_publisher_from_env"
    src = open(ep.__file__, encoding="utf-8").read()
    for ln in src.splitlines():
        stripped = ln.strip()
        if stripped.startswith(("class ", "def ")) and "falcon" in stripped.lower():
            pytest.fail(f"consumer-specific naming leaked into a code identifier: {ln!r}")


# ============================================================================
# Forward-compatibility: payload is a producer-owned extensibility boundary
# ============================================================================

def test_unknown_nested_field_survives_in_raw_payload_only_not_in_envelope():
    """A field HERMES doesn't model today (e.g. per-instrument-only data) must flow through
    the raw/original preservation field UNCHANGED, with NESTED structure intact, via purely
    generic serialization — with zero publisher code changes — and must NOT be auto-promoted
    into the small, governed, searchable envelope/payload_json."""
    sig = make_signal()
    sig.future_instrument_feature = {
        "nested": {"deeply": {"value": 42, "tags": ["a", "b"]}},
        "flag": True,
    }
    event = ep.build_evidence_event(sig)

    raw = json.loads(event["gelf_message"]["_hermes_signal_raw_json"])
    assert raw["future_instrument_feature"] == {
        "nested": {"deeply": {"value": 42, "tags": ["a", "b"]}},
        "flag": True,
    }

    # Never auto-promoted into the governed searchable envelope.
    assert "future_instrument_feature" not in event["payload"]
    payload_json_str = event["gelf_message"]["_payload_json"]
    assert "future_instrument_feature" not in payload_json_str
    full_wire_json = json.dumps(event["gelf_message"])
    # It legitimately exists somewhere on the wire (inside the raw field) ...
    assert "future_instrument_feature" in full_wire_json
    # ... but not inside the payload_json sub-string specifically.
    assert "future_instrument_feature" not in event["gelf_message"]["_payload_json"]


def test_serialize_signal_raw_is_generic_not_a_hand_maintained_allowlist():
    """_serialize_signal_raw must not be a fixed field-name allow-list: adding a brand-new
    dataclass field to a Signal-shaped object must appear automatically with zero changes to
    evidence_publisher_v1.py."""
    import dataclasses

    @dataclasses.dataclass
    class FutureSignal:
        instrument: str
        timeframe: str
        timestamp: datetime
        regime: str
        session: str
        brand_new_field_nobody_coded_for: dict

    fs = FutureSignal(
        instrument="XAU_USD", timeframe="M1", timestamp=datetime(2026, 1, 1),
        regime="BULL_TREND", session="london",
        brand_new_field_nobody_coded_for={"x": [1, 2, 3]},
    )
    raw = ep._serialize_signal_raw(fs)
    assert raw["brand_new_field_nobody_coded_for"] == {"x": [1, 2, 3]}


# ============================================================================
# Transport: mTLS, send/retry/reconnect, failure isolation
# ============================================================================

def test_mtls_context_construction_with_synthetic_certs(pki, listener):
    publisher = ep.EvidencePublisher(
        host="127.0.0.1", port=listener.port,
        client_cert_file=pki.client_cert, client_key_file=pki.client_key, ca_file=pki.ca_cert,
        timeout=2.0,
    )
    try:
        assert publisher._ssl_context.verify_mode == ssl.CERT_REQUIRED
    finally:
        publisher.close()


def test_successful_send_received_by_fake_listener_with_correct_fields(pki, listener):
    publisher = ep.EvidencePublisher(
        host="127.0.0.1", port=listener.port,
        client_cert_file=pki.client_cert, client_key_file=pki.client_key, ca_file=pki.ca_cert,
        timeout=2.0,
    )
    sig = make_signal(instrument="XAUUSD", timeframe="M5", regime="BULL_TREND", session="london")
    publisher.publish(sig)
    publisher.close()

    msgs = listener.wait_for_messages(1)
    assert len(msgs) == 1
    msg = msgs[0]
    assert msg["version"] == "1.1"
    assert msg["_subject_id"] == "XAUUSD"
    assert msg["short_message"] == "HERMES signal evidence XAUUSD/M5"
    payload = json.loads(msg["_payload_json"])
    assert payload["signal_natural_key"].startswith("XAUUSD:M5:")


def test_connection_reused_across_multiple_sends_not_one_handshake_per_signal(pki, listener):
    """F1 refactor: publish() only enqueues (async handoff to the background worker thread),
    so the actual send/connect happens slightly later than the publish() call returns — wait
    for each event to be observed by the fake listener before inspecting `_sock`/stats."""
    publisher = ep.EvidencePublisher(
        host="127.0.0.1", port=listener.port,
        client_cert_file=pki.client_cert, client_key_file=pki.client_key, ca_file=pki.ca_cert,
        timeout=2.0,
    )
    publisher.publish(make_signal())
    listener.wait_for_messages(1)
    _wait_until(lambda: publisher.get_stats()["events_sent"] >= 1)
    sock_after_first = publisher._sock

    publisher.publish(make_signal())
    listener.wait_for_messages(2)
    _wait_until(lambda: publisher.get_stats()["events_sent"] >= 2)
    sock_after_second = publisher._sock
    publisher.close()

    assert sock_after_first is not None
    assert sock_after_first is sock_after_second  # same connection object — not re-handshaked
    assert publisher.get_stats()["reconnections"] == 1
    msgs = listener.wait_for_messages(2)
    assert len(msgs) == 2


def test_reconnect_after_failure_establishes_a_new_connection(pki, listener):
    """F1 refactor: publish() only enqueues; wait for the background worker thread to have
    actually processed each event before asserting on connection/reconnection state."""
    publisher = ep.EvidencePublisher(
        host="127.0.0.1", port=listener.port,
        client_cert_file=pki.client_cert, client_key_file=pki.client_key, ca_file=pki.ca_cert,
        timeout=2.0,
    )
    publisher.publish(make_signal())
    _wait_until(lambda: publisher.get_stats()["reconnections"] == 1)

    # Simulate a broken connection (remote restart/reset) by forcibly closing the socket
    # out from under the publisher, without going through its own close().
    publisher._sock.close()
    try:
        publisher._sock.send(b"x")
    except Exception:
        pass  # confirmed broken; the next publish() must detect this and reconnect

    publisher.publish(make_signal())
    _wait_until(lambda: publisher.get_stats()["reconnections"] == 2)
    publisher.close()


def test_bounded_retry_exactly_two_total_attempts_not_more():
    """F1 refactor: the retry loop now lives in _send_event_with_retry(); exercise it
    directly (bypassing the real queue/thread) — same assertion strength as before."""
    publisher = _bare_publisher()
    publisher.host, publisher.port = "127.0.0.1", 1  # nothing listens here

    call_count = {"n": 0}

    def always_fail(data):
        call_count["n"] += 1
        return False

    publisher._attempt_send = always_fail
    event = ep.build_evidence_event(make_signal())
    data = json.dumps(event["gelf_message"], default=str).encode("utf-8") + ep._TCP_NULL_TERMINATOR
    publisher._send_event_with_retry(event, data)

    assert call_count["n"] == 2
    assert publisher.get_stats()["events_failed"] == 1
    assert publisher.get_stats()["events_sent"] == 0


def test_final_failure_is_isolated_raises_nothing(pki):
    """Connecting to a port nothing listens on must never raise out of publish() — and,
    since F1, publish() only enqueues, so the actual (failing) send happens asynchronously
    on the background worker thread; wait for it to complete before asserting the outcome."""
    publisher = ep.EvidencePublisher(
        host="127.0.0.1", port=1,  # privileged/unused port — connection refused
        client_cert_file=pki.client_cert, client_key_file=pki.client_key, ca_file=pki.ca_cert,
        timeout=1.0,
    )
    publisher.publish(make_signal())  # must not raise
    _wait_until(lambda: publisher.get_stats()["events_failed"] == 1)
    publisher.close()  # must not raise / hang either


def test_malformed_local_signal_object_does_not_raise():
    publisher = _bare_publisher()
    publisher._attempt_send = lambda data: True

    class Broken:
        pass  # no .instrument/.timestamp/.timeframe at all

    publisher.publish(Broken())  # must not raise; fails inside build_evidence_event(), before
    # ever touching the queue — so no thread/queue interaction is needed for this to hold.
    assert publisher.get_stats()["events_sent"] == 0
    assert publisher.get_stats()["events_failed"] == 0  # never even attempted a send
    assert publisher.get_stats()["events_queued"] == 0  # never reached the enqueue step either


# ============================================================================
# Runtime / lifecycle: enable/disable, fail-loud config, shutdown
# ============================================================================

def test_disabled_by_default_zero_connection_attempts(monkeypatch):
    _env(monkeypatch, FALCON_PUBLISH_ENABLED=None, ENVIRONMENT="DEV")

    def boom(*a, **kw):
        raise AssertionError("socket.create_connection must never be called when disabled")

    monkeypatch.setattr(ep.socket, "create_connection", boom)
    publisher = ep.build_evidence_publisher_from_env()
    assert isinstance(publisher, ep.DisabledEvidencePublisher)
    assert publisher.enabled is False
    publisher.publish(make_signal())  # no-op, must not raise or touch the network
    publisher.close()


def test_disabled_reads_no_credential_files(monkeypatch, tmp_path):
    _env(monkeypatch, FALCON_PUBLISH_ENABLED="false", ENVIRONMENT="DEV")
    reads = []
    real_isfile = os.path.isfile

    def spy_isfile(path):
        if "falcon" in str(path).lower() or "evidence" in str(path).lower():
            reads.append(path)
        return real_isfile(path)

    monkeypatch.setattr(ep.os.path, "isfile", spy_isfile)
    publisher = ep.build_evidence_publisher_from_env()
    assert isinstance(publisher, ep.DisabledEvidencePublisher)
    assert reads == []


def test_enabled_but_missing_config_fails_loud_at_startup(monkeypatch):
    _env(
        monkeypatch, FALCON_PUBLISH_ENABLED="true", ENVIRONMENT="DEV",
        FALCON_HOST=None, FALCON_PORT=None, FALCON_CLIENT_CERT_FILE=None,
        FALCON_CLIENT_KEY_FILE=None, FALCON_CA_FILE=None,
    )
    with pytest.raises(ValueError, match="GOV-CFG-001"):
        ep.build_evidence_publisher_from_env()


def test_enabled_with_nonexistent_cert_files_fails_loud(monkeypatch, pki):
    _env(
        monkeypatch, FALCON_PUBLISH_ENABLED="true", ENVIRONMENT="DEV",
        FALCON_HOST="127.0.0.1", FALCON_PORT="12345",
        FALCON_CLIENT_CERT_FILE="/nonexistent/client.crt",
        FALCON_CLIENT_KEY_FILE="/nonexistent/client.key",
        FALCON_CA_FILE="/nonexistent/ca.crt",
    )
    with pytest.raises(ValueError, match="GOV-CFG-001"):
        ep.build_evidence_publisher_from_env()


def test_enabled_with_malformed_pem_fails_loud(monkeypatch, tmp_path):
    bogus = tmp_path / "bogus.pem"
    bogus.write_text("not a real certificate\n")
    _env(
        monkeypatch, FALCON_PUBLISH_ENABLED="true", ENVIRONMENT="DEV",
        FALCON_HOST="127.0.0.1", FALCON_PORT="12345",
        FALCON_CLIENT_CERT_FILE=str(bogus), FALCON_CLIENT_KEY_FILE=str(bogus),
        FALCON_CA_FILE=str(bogus),
    )
    with pytest.raises(ssl.SSLError):
        ep.build_evidence_publisher_from_env()


def test_enabled_invalid_port_fails_loud(monkeypatch, pki):
    _env(
        monkeypatch, FALCON_PUBLISH_ENABLED="true", ENVIRONMENT="DEV",
        FALCON_HOST="127.0.0.1", FALCON_PORT="not-a-number",
        FALCON_CLIENT_CERT_FILE=pki.client_cert, FALCON_CLIENT_KEY_FILE=pki.client_key,
        FALCON_CA_FILE=pki.ca_cert,
    )
    with pytest.raises(ValueError, match="GOV-CFG-001"):
        ep.build_evidence_publisher_from_env()


def test_enabled_fully_configured_builds_real_publisher(monkeypatch, pki, listener):
    _env(
        monkeypatch, FALCON_PUBLISH_ENABLED="true", ENVIRONMENT="DEV",
        FALCON_HOST="127.0.0.1", FALCON_PORT=str(listener.port),
        FALCON_CLIENT_CERT_FILE=pki.client_cert, FALCON_CLIENT_KEY_FILE=pki.client_key,
        FALCON_CA_FILE=pki.ca_cert,
    )
    publisher = ep.build_evidence_publisher_from_env()
    try:
        assert isinstance(publisher, ep.EvidencePublisher)
        assert publisher.enabled is True
    finally:
        publisher.close()


def test_runtime_unreachable_sink_does_not_propagate_hermes_continues():
    """Simulates the call site: FALCON unreachable at runtime must never raise, regardless
    of which layer (publisher internals, or an unexpected exception escaping them) fails.

    F1 refactor: the retry loop (and thus _attempt_send) now runs inside
    _send_event_with_retry() on the background worker thread, not inside publish() — so this
    exercises THAT method directly (bypassing the real queue/thread) to prove its own outer
    defensive boundary holds even when an attempt helper raises unexpectedly instead of
    returning False. This is also exactly the scenario the worker's own per-item exception
    isolation (_worker_loop) backstops in production."""
    publisher = _bare_publisher()
    publisher.host, publisher.port = "127.0.0.1", 1
    publisher._attempt_send = lambda data: (_ for _ in ()).throw(ConnectionRefusedError("refused"))

    event = ep.build_evidence_event(make_signal())
    data = json.dumps(event["gelf_message"], default=str).encode("utf-8") + ep._TCP_NULL_TERMINATOR

    # _send_event_with_retry() has its own outer defensive boundary — must not raise even if
    # an attempt helper itself raises unexpectedly rather than returning False.
    publisher._send_event_with_retry(event, data)  # must not raise

    # Also prove publish() itself (the real producer-side entry point) is entirely unaffected
    # by this failure mode — it never even reaches _attempt_send.
    publisher.publish(make_signal())  # must not raise either


def test_shutdown_close_does_not_hang(pki, listener):
    publisher = ep.EvidencePublisher(
        host="127.0.0.1", port=listener.port,
        client_cert_file=pki.client_cert, client_key_file=pki.client_key, ca_file=pki.ca_cert,
        timeout=2.0,
    )
    publisher.publish(make_signal())
    start = time.time()
    publisher.close()
    elapsed = time.time() - start
    assert elapsed < 2.0


def test_disabled_publisher_close_is_instant_and_safe():
    publisher = ep.DisabledEvidencePublisher()
    start = time.time()
    publisher.close()
    assert time.time() - start < 0.1


# ============================================================================
# SignalPublisher integration (signal_builder.py insertion point)
# ============================================================================

class _RaisingEvidencePublisher:
    """A hostile double proving SignalPublisher.publish_signal()'s OWN outer try/except
    around the evidence-publish call is load-bearing, independent of EvidencePublisher's own
    internal never-raise contract."""

    def __init__(self):
        self.calls = 0

    def publish(self, signal):
        self.calls += 1
        raise RuntimeError("simulated FALCON-side catastrophe")


def test_publish_signal_never_raises_even_if_evidence_publisher_raises():
    evidence = _RaisingEvidencePublisher()
    # host/port chosen so the SQL write fails FAST (connection refused) and deterministically,
    # with no live DB required — this test is about the evidence-publish boundary, not SQL.
    pub = SignalPublisher(
        db_config={"host": "127.0.0.1", "port": 1, "user": "x", "password": "x", "database": "x"},
        redis_publisher=None,
        evidence_publisher=evidence,
    )
    sig = make_signal()
    result = pub.publish_signal(sig)  # must not raise
    assert evidence.calls == 1
    assert result is False  # SQL failed (no listener on port 1) — expected, unrelated to evidence


def test_publish_signal_calls_evidence_publisher_after_sql_and_redis_regardless_of_outcome():
    class RecordingEvidencePublisher:
        def __init__(self):
            self.received = []

        def publish(self, signal):
            self.received.append(signal)

    evidence = RecordingEvidencePublisher()
    pub = SignalPublisher(
        db_config={"host": "127.0.0.1", "port": 1, "user": "x", "password": "x", "database": "x"},
        redis_publisher=None,
        evidence_publisher=evidence,
    )
    sig = make_signal()
    pub.publish_signal(sig)
    assert len(evidence.received) == 1
    assert evidence.received[0] is sig


def test_signal_publisher_with_no_evidence_publisher_is_unaffected():
    """evidence_publisher=None (the pre-PID05 default) must behave exactly as before."""
    pub = SignalPublisher(
        db_config={"host": "127.0.0.1", "port": 1, "user": "x", "password": "x", "database": "x"},
        redis_publisher=None,
        evidence_publisher=None,
    )
    result = pub.publish_signal(make_signal())  # must not raise
    assert result is False  # SQL failed, as expected; no evidence publisher involved
