# HERMES evidence-publish boundary — PID05 / Stage 3B

**WO:** `WO-PID05-STAGE3B-EVIDENCE-PUBLISHER`
**Module:** `utils/evidence_publisher_v1.py`
**Insertion point:** `SignalPublisher.publish_signal()` in `signal_builder.py`

## Purpose

HERMES computes a trading signal and has always had two side effects on it: a SQL write
and a Redis write (both in `SignalPublisher.publish_signal()`). This WO adds a **third,
independent, best-effort side effect**: publishing the same signal as evidence to an
external evidence/telemetry sink over GELF TCP + mTLS.

```
SignalPublisher.publish_signal()
      |
      +-- existing SQL write        (own try/except, never raises)
      |
      +-- existing Redis write      (own try/except, never raises)
      |
      +-- EvidencePublisher.publish(signal)   <- independent, failure-isolated, NON-BLOCKING
              |                                   (builds + enqueues only — see "Asynchronous
              |                                    publication boundary" below for where the
              v                                    actual network send happens)
      queue.Queue(maxsize=200) -> ONE background daemon thread -> GELF TCP + mTLS send
```

This is deliberately small: one HERMES-owned module plus a handful of integration lines.
It is not a messaging framework — no brokers, no durable queues, no outbox tables, no new
SQL. The in-process bounded queue described below is an implementation detail of this one
module, not a general-purpose messaging layer.

## GOV-SE-SEVERED-001 — why this doesn't violate the severance doctrine

`utils/structure_ingest_boundary.py` establishes `GOV-SE-SEVERED-001`: a producer never
pushes into a consumer's code; the consumer must pull instead. That doctrine was written
for a different situation (HERMES pushing ticks into `structure_engine`'s own Python code)
and remains **fully authoritative and unchanged**.

Central Architecture has clarified that it does not prohibit this WO, because:

> HERMES may publish its own governed evidence to an external evidence/telemetry sink,
> exactly like its existing Graylog SIEM GELF channel (`hermes_logging/gelf.py`). HERMES
> must not depend upon, import, call into, or require a consumer's implementation. The
> transport happening to terminate at an external evidence sink today does not make that
> sink part of HERMES's business-data execution path.

Concretely, `utils/evidence_publisher_v1.py`:

- **Carries no consumer-specific naming.** Classes/functions/the module itself are generic
  (`EvidencePublisher`, `DisabledEvidencePublisher`, `build_evidence_publisher_from_env`,
  `evidence_publisher_v1.py`). The real external system's name ("FALCON") appears **only**
  at the deployment/config boundary — environment variable names
  (`FALCON_PUBLISH_ENABLED`, `FALCON_HOST`, ...) — never as a code/architecture identifier.
- **Imports nothing from, and calls into nothing of, any external-sink repository or API.**
  It does not read the sink's Redis, OpenSearch, or any other storage, and HERMES is not
  added to any sink-owned network.
- **Never gates HERMES's own behaviour on the sink's availability or state.** A down,
  slow, or misconfigured-but-disabled sink has zero effect on signal generation, SQL, or
  Redis.
- **Is a sibling, not a wrapper or a gate,** of the existing SQL/Redis side effects — it has
  its own, separate `try`/`except` boundary in `publish_signal()` and cannot touch the
  `success` value those two side effects produce.

`GOV-SE-SEVERED-001`'s own test file (`tests/test_structure_ingest_boundary.py`) is
untouched and still passes unmodified — it is entirely about the `structure_engine`
severance (a different producer/consumer pair) and does not reference this module.

## Identity semantics

| Field | Definition | Lifecycle |
|---|---|---|
| `signal_natural_key` | `f"{instrument}:{timeframe}:{signal_timestamp_z}"` — **no** `hermes:` prefix | Mirrors HERMES's own SQL natural identity (`instrument`, `timeframe`, `timestamp`) exactly. Never unique by itself. |
| `falcon_event_id` | fresh `uuid4()` | Generated **once per `publish()` invocation**. Reused across that invocation's bounded transport retries. A **later**, separate invocation — including a backfill/recovery replay producing the identical `signal_natural_key` — always gets a **new** `uuid4`. Never derived from the natural key, content, or any hash. No dedup against the sink; no pre-publish query. |
| `produced_at_utc` | fresh `datetime.now(timezone.utc)` | Read **once** at the start of each `publish()` invocation; reused unchanged across that invocation's retries. Distinct from the signal's own business timestamp. |

Multiple sink-side events sharing one `signal_natural_key` is **expected and correct** — it
represents honest, separate HERMES emissions concerning the same natural signal identity,
not an error, a bug, or something to suppress.

## Timestamp handling

`Signal.timestamp` is HERMES's existing **naive** `datetime` which, by HERMES's own
established construction (`adapters/oanda.py`'s `datetime.now(timezone.utc)`, later
stripped of `tzinfo` for epoch-arithmetic boundary truncation in
`CandleAggregator._get_candle_start()`), already represents UTC wall-clock time.

This module attaches UTC **explicitly** — `signal.timestamp.replace(tzinfo=timezone.utc)`
— and **never** calls `.astimezone()` anywhere. On a naive value, `.astimezone()` silently
assumes the **local system timezone**, which would corrupt the value; this is exactly the
bug class this module must avoid, and a test
(`tests/test_evidence_publisher_v1.py::test_no_astimezone_call_anywhere_in_module`) greps
the module's actual code (not its docstrings) to enforce it.

This module does **not** read a new wall clock for the signal's own business timestamp,
and does **not** touch `Signal`, `CandleAggregator`, SQL, or Redis in any way. (A known,
separate, **dormant** defect exists in `CandleAggregator._get_candle_start()` — it does not
use the safer `astimezone(utc)`-then-strip helper already present in
`utils/candle_durable_sql_contract_v1.py`, so it would silently corrupt a result if ever
given non-UTC-aware input. Today's actual input is always UTC-sourced, so this is not
live. This WO does not touch `_get_candle_start()` or `CandleAggregator` — out of scope.)

## Asynchronous publication boundary — F1 fix (HELM DEV-integration defect)

**F1** (found during HELM's real DEV integration proof, fixed after this WO's original
audit-GREEN pass): the transport is **blocking** Python sockets (`socket`/`ssl`, not
`asyncio`-native). The original design called it **synchronously, inline, on HERMES's
single-threaded asyncio signal-processing coroutine** — so a hung/black-holed evidence sink
could stall the **entire event loop**, every instrument's signal processing, for up to
roughly `2 * timeout` seconds per publication. That violated the required doctrine: HERMES
must be independent of the sink's **latency**, not just its failures.

**Fix shape:** a bounded, thread-safe `queue.Queue` (`maxsize=200`) plus **one background
daemon thread** that owns the existing, byte-for-byte-unchanged, blocking transport. This
mirrors the established HERMES idiom in `utils/hermes_publisher_runtime_v1.py`
(`PublisherRunner`): a `threading.Thread` + `threading.Event`, bounded loops, graceful
start/stop, exception-isolated. Deliberately **not** `asyncio.Queue` + an asyncio task — the
transport itself is blocking I/O, and converting it to true asyncio-native sockets would be
a materially larger, riskier rewrite than this fix warrants.

```
EvidencePublisher.publish(signal)        <- runs on the SIGNAL-PROCESSING thread only
      |                                     builds + serializes the event (pure, in-memory,
      |                                     no I/O), then queue.put_nowait() — NEVER blocks,
      v                                     NEVER touches the network
queue.Queue(maxsize=200)
      |
      v
ONE background daemon thread (_worker_loop)
      |
      +-- blocking get() with a short poll timeout (observes stop() promptly)
      +-- _send_event_with_retry(): the SAME 2-attempt GELF/mTLS transport logic described
      |   below, now running entirely off the signal-processing path
      +-- exception-isolated per item — one malformed/failing event can never kill the
          worker thread (caught, logged `[EVIDENCE_PUBLISH_WORKER_ERROR]`, loop continues)
```

Event identity is fixed **before** enqueueing, not re-derived by the worker:
`EvidencePublisher.publish()` calls `build_evidence_event()` and serializes it to bytes
exactly once, on the caller's thread, and queues that already-built `(event, data)` pair.
The worker's own internal retry reuses those exact values/bytes for both attempts — this is
a relocation of **when/where** the transport send happens, not a change to **what** is sent
or to the identity semantics documented above.

**Queue sizing (`_EVIDENCE_QUEUE_MAX_SIZE = 200`)**, derived from real observed traffic:

- HELM's real DEV integration proof observed **~182 evidence-publish events over 14
  minutes**: an observed average rate of `182 / 14 ≈ 13.0 events/min` (~0.217 events/s).
- The dominant burst shape is a near-simultaneous cluster (up to 4 instruments' M1 signals
  landing within the same second, once per minute), not a sustained-rate spike. A generous
  **3x safety multiplier** on the observed average gives an assumed worst-case *sustained*
  rate of `13.0 * 3 ≈ 39 events/min`.
- Sized to absorb an evidence-sink outage of **~5 minutes** at that worst-case sustained
  rate: `39 * 5 ≈ 195`, rounded to **200**.
- At the actually-observed (non-multiplied) average rate, 200 slots absorb
  `200 / 13.0 ≈ 15.4 minutes` of outage before any drop — comfortably longer than a typical
  transient restart/blip — while each queued item (a JSON-sized dict + its pre-serialized
  bytes, a few KB) keeps total worst-case memory in the low single-digit MB, trivial against
  HERMES's budget.
- Deliberately **not** sized for hours of outage buffering: this channel is explicitly
  best-effort telemetry, so oversizing would only delay — never prevent — eventual drops
  during a genuinely prolonged outage, for zero behavioural benefit.

**Queue-full behaviour:** `queue.put_nowait()` only — never a blocking/timed `put()`. On
`queue.Full` the event is **dropped**: counted in `events_dropped_queue_full` and logged at
`warning` with the distinct tag `[EVIDENCE_PUBLISH_DROPPED_QUEUE_FULL]` — deliberately
different from the transport-level `[EVIDENCE_PUBLISH_FAIL]` tag, so an operator can tell
"dropped due to backpressure" apart from "tried to send and the network failed". This is
explicitly **best-effort, not guaranteed delivery** — no spill-to-disk, no durable queue,
nothing queued survives process shutdown.

**Startup:** enabling evidence publication never performs a blocking remote-availability
check — the worker thread starts eagerly (so the queue always has a consumer), but the
transport connection itself stays fully lazy (`_connect()` is only ever called from inside
the worker, on the first dequeued item), exactly as before this fix. HERMES boot never
waits on, or requires, the sink being reachable.

**Shutdown is bounded:** `close()` signals the worker to stop, then joins it for at most
`_WORKER_SHUTDOWN_MAX_WAIT_SECONDS` (**5.0s** — the same order of magnitude as the
transport's own per-attempt `timeout`, not tens of seconds). Within that window the worker
keeps draining whatever is already queued (a small, best-effort drain — not a guarantee).
After the deadline, `close()` stops waiting regardless of the worker's state, force-closes
the transport connection (using a non-blocking lock attempt, so a still-busy worker thread
can never make `close()` itself block past the bound), and logs exactly how many queued
events were discarded. No persistence is added for shutdown draining.

## Retry semantics

**Maximum 2 total send attempts per logical publication** (now performed by
`EvidencePublisher._send_event_with_retry()` on the background worker thread — see above;
unchanged from the original design apart from WHERE it runs) — not "2 retries": 2 attempts
total, counting the first. Both attempts send the **exact same already-serialized event**
(same `falcon_event_id`, `produced_at_utc`, `signal_natural_key`, payload — the event is
constructed and serialized once, in `publish()`, then sent up to twice by the worker). On
the first transport failure the broken connection is closed and a fresh one is attempted
for the second send. After a second failure: one structured `[EVIDENCE_PUBLISH_FAIL]` log
line, then return.

There is **no** delayed/background retry **beyond this single bounded 2-attempt pair**, no
persistent retry queue, and **no** guaranteed-delivery claim anywhere in this module, its
docs, or its log messages. (The in-process handoff queue described above is a latency/
backpressure buffer for when the event is SENT, not a delivery-retry mechanism — an event
that exhausts its 2 attempts is not re-queued.)

## Failure isolation

Every failure mode — connection refused, TLS handshake failure, certificate rejection,
timeout, reset, remote restart, a malformed local `Signal`-like object — is caught inside
`EvidencePublisher`'s own boundary (`publish()` for construction/enqueue failures;
`_send_event_with_retry()`, running on the background worker thread, for transport
failures). Neither ever raises. The integration point in `signal_builder.py` wraps the
`publish()` call in its **own, separate** `try`/`except` as a second, independent layer of
defence (so even an unexpected exception that somehow escaped the publisher's internal
handling still cannot reach `publish_signal()`'s caller or affect the SQL/Redis `success`
result computed above it). The background worker thread has its **own** third, independent
layer: `_worker_loop()` catches broadly around each dequeued item's processing, so a bad
event can never kill the worker thread itself.

Startup-time misconfiguration is a **different, deliberately separate** failure mode:
`FALCON_PUBLISH_ENABLED=true` with missing/malformed host/port/cert/key/ca **fails loud at
boot** (`GOV-CFG-001` — matching the existing "enabled gates mandatory validation" idiom
used throughout `main.py`'s `lifespan()`, e.g. the candle-forward and shadow-tick seams). A
runtime "sink unreachable" failure, by contrast, is always log-and-continue and is never
raised from a correctly-configured, already-running publisher — nor, per the fix above, does
it ever block the signal-processing path regardless of how long the sink takes to fail.

## Payload is a producer-owned extensibility boundary

HERMES's signal content is **not** treated as a fixed, enumerable schema by this module.
The raw/original-payload preservation field (`hermes_signal_raw_json`) is built via
**generic object serialization** (`dataclasses.asdict()` plus any extra instance attributes
present on the object — see `_serialize_signal_raw()`), never a hand-maintained allow-list
of "today's known field names". A new `Signal` field, or an attribute that exists only for
a particular instrument, flows through this module **unchanged with zero publisher code
changes**, and is never fabricated when genuinely absent.

The FALCON-facing **searchable envelope** (`payload_json`: `signal_natural_key` plus the
optional `instrument_id`/`timeframe`/`regime`/`session`/`signal_type`) is deliberately
**smaller and more stable** than the HERMES payload. New or instrument-specific HERMES
information may be preserved in the raw payload without requiring a FALCON contract
revision. **Promotion** of information from the raw payload into the searchable envelope
is deliberate and explicitly governed, not automatic — adding a new envelope field is a
conscious code change to `build_evidence_event()`, never a side effect of `Signal` growing.

A dedicated test
(`test_unknown_nested_field_survives_in_raw_payload_only_not_in_envelope`) proves a
synthetic, previously-unmodeled, **nested** field survives serialization into the raw
preservation field untouched, while confirming it is absent from both the governed payload
and `payload_json`.

## Replay / backfill behaviour

Replaying this code path (e.g. a recovery/backfill re-traversal) is **intentional and not
suppressed**: each independent `publish()` invocation gets a fresh `falcon_event_id` and
`produced_at_utc`, even for the identical `signal_natural_key`. There is no dedup, no
pre-publish query, and no idempotency key beyond the natural key itself — multiple sink-side
events for one natural key are the expected, honest representation of HERMES having emitted
that signal more than once.

**`scripts/backfill_signals.py` is explicitly and permanently excluded** from this WO's
coverage: it does not traverse `SignalPublisher.publish_signal()` at all, so it never reaches
this evidence-publish boundary. This is a deliberate scope boundary, not an oversight — if
backfilled signals ever need evidence coverage, that is separate, future work.

## Configuration

```
FALCON_PUBLISH_ENABLED=false
FALCON_HOST=
FALCON_PORT=
FALCON_CLIENT_CERT_FILE=
FALCON_CLIENT_KEY_FILE=
FALCON_CA_FILE=
```

Loaded via `env_config.get_env()` / `get_env_bool()`. `FALCON_PUBLISH_ENABLED` unset/false
(the default) produces a `DisabledEvidencePublisher` — **zero** connection, credential, or
TLS-context overhead; no socket, no SSL context, no file reads. `FALCON_PUBLISH_ENABLED=true`
makes all five of the remaining variables mandatory, validated fail-loud at startup
(`GOV-CFG-001`) by `build_evidence_publisher_from_env()` in `main.py`'s `lifespan()`
startup sequence, alongside the existing shadow-tick/live-tick/candle-forward seam
initializers.

**Why `FALCON_CLIENT_CERT_FILE`/`FALCON_CLIENT_KEY_FILE`/`FALCON_CA_FILE` use
`get_env()`, not `get_secret()`:** `env_config.get_secret()`'s `_FILE` convention (used for
`DISCORD_WEBHOOK_PROD_FILE`) reads a referenced file's **content** as the secret *value*.
A TLS certificate/key/CA bundle is consumed by `ssl.SSLContext` as a **file path** — the
`ssl` module reads the file itself — not as inline content handed to HERMES. The `_FILE`
suffix is kept purely for naming-convention consistency (it signals "this names a file");
it deliberately does not invoke `get_secret()`'s content-loading/secret-root allow-list
machinery, which is designed for small inline secret values, not PKI material consumed by
path.

## Credential ownership

The container mounts a **HERMES-owned** host path read-only:

```
/srv-dev/hermes-deploy-secrets/falcon-evidence  ->  /app/secrets/falcon-evidence  (ro)
```

Expected layout once provisioned (by HELM, **separately and not by this change**):
`client.crt` (0644), `client.key` (0600), `ca.crt` (0644), directory itself `0700`. This WO
adds the `docker-compose.yml` mount declaration only — it does **not** create the host
directory or any file inside it, and the material is never copied from the evidence sink's
own filesystem; it is provisioned independently into this HERMES-owned path.

## Testing

`tests/test_evidence_publisher_v1.py` (infra-free, no real DB/Redis/FALCON) covers
identity, UTC, payload, forward-compatibility, transport (mTLS handshake, send/retry/
reconnect/failure-isolation against a synthetic fake GELF-over-TLS listener), runtime
lifecycle (enable/disable, fail-loud config, bounded shutdown), and the
`SignalPublisher.publish_signal()` integration boundary itself. All PKI material used in
tests is generated fresh, per test session, by
`tests/fixtures/evidence_publisher_test_certs/` (throwaway self-signed test-only
certificates — never real HERMES/FALCON PKI, never committed to the repository).

`tests/test_evidence_publisher_async_boundary_v1.py` (same infra-free PKI/fixtures, reused
via import rather than duplicated) covers the F1 fix specifically: the signal path never
blocking regardless of a deliberately-hung fake sink (the key regression proof), bounded
queue capacity + non-blocking overflow/drop behaviour and its distinct log tag, event
identity being fixed before enqueue and staying distinct across two events sitting in the
queue at once, worker-thread resilience to a malformed queued item and to a transport
exception escaping the retry loop, bounded shutdown (clean/fast when idle, bounded-not-
indefinite when the transport is hung, with the discard count logged), and the required
empirical latency proof (healthy / connection-refused / hung-sink, contrasted against a real
measurement of the old inline call pattern).

`tests/test_structure_ingest_boundary.py` (`GOV-SE-SEVERED-001`'s own test file) is
unmodified and passes unchanged, confirming this WO does not weaken that doctrine's guard.
