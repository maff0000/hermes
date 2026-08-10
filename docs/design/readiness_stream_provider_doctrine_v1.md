# Readiness stream-provider doctrine v1

WO-HELM-HERMES-READINESS-LIVE-OANDA-STREAM-PROVIDER-WIRING-CORRECTION-0001

## Principle

`/readiness` is an external, authoritative **projection of existing HERMES runtime truth**. It must consume the
same canonical live-stream authority used by the operational health/state surfaces, and must **never** maintain an
independent, synthetic stream truth of its own.

The single authoritative source for the one governed pricing stream is the **watchdog** (`state.watchdog`). Its
`get_health_snapshot()` is what `/health` reports. In it:

- `stream_state` is promoted to `FLOWING` **only on a real price tick** (`record_tick`), never on a heartbeat
  (`record_stream_message` updates only the last stream-message time and does not promote).
- `last_tick_utc` and the per-instrument `instruments[<sym>].last_tick_utc` advance **only on a real tick**.

Therefore readiness derives its stream observation from that snapshot, via a **read-only projection**
(`hermes_readiness_observers_v1.project_watchdog_stream`) fed into the unchanged `observe_stream`. The governed
active-stream semantics (`_classify_one_stream`: connected **and** a fresh real tick) are preserved — only the
*source of truth* is corrected.

## Health and readiness may differ in policy, never in fact

`/health` and `/readiness` intentionally apply **different acceptance policies** (health is a liveness/operational
surface; readiness is an external go/no-go gate with the full scope/expansion/boundary contract). But they must
**not disagree about underlying facts** such as:

- whether the authoritative OANDA adapter is **actually flowing**, and
- what the **latest genuine tick timestamp** is.

If `/health` reports the single stream FLOWING with fresh XAU ticks, `/readiness` must observe the same stream as
active — and vice-versa. A contradiction there is a provider-wiring defect, not a data-plane fact.

## The defect this doctrine closes

The base pricing adapter's own `AdapterHealth.state` is a lower-level connection flag that is **not** maintained
`CONNECTED` while the runtime streams via `adapter.stream()`, and its `last_tick_at` is refreshed by **heartbeats**.
Reading it made `/readiness` report `active_stream_count=0`, `stream_health=DISCONNECTED`, `last_xau_tick_utc=None`
and `RDY-STREAM-COUNT-NOT-ONE` **during genuine sustained market flow** while `/health` correctly showed FLOWING.
The correction points readiness at the watchdog authority instead of the base adapter's flag.

## Invariants (preserved)

- **No second stream / no parallel heartbeat / no new tracker.** The projection holds no connection and does no I/O.
- **No redefinition of "active".** A projected stream is active only when the watchdog says FLOWING **and** the
  authoritative last real tick is fresh. `active = configured` and `active = heartbeat-exists` remain forbidden.
- **Fail-closed.** No authoritative state → no stream slot → not ready (never an assumed healthy stream).
- **Truthful market-closed.** Weekend/stale (STALE/DISCONNECTED, no fresh real tick) stays not-active → RED. The
  fix does not make market-closed readiness falsely GREEN because the adapter process exists.
- **`last_xau_tick_utc`** is the authoritative per-instrument real tick from the watchdog — never a heartbeat,
  candle, wall-clock, or synthetic value.
