# HERMES GC Live Market Data — Slow-Reader Doctrine and PROD/DEV Boundary

**Initiative:** `HERMES GC Historical + Live Market-Data Doctrine`
**Status:** DOCUMENTATION / ARCHITECTURE ONLY. See [`00-purpose-and-terminology.md`](00-purpose-and-terminology.md).

## §1 — Live slow-reader doctrine

Databento Live supports `skip` / `warn` slow-reader behaviours, and **defaults to `skip` for MBP-1** if
the client falls behind the live feed.

**`skip` is NOT acceptable as implicit HERMES market-truth behaviour.** Silently skipping records to let
a slow client catch back up to realtime means HERMES would silently lose GC market records — exactly the
kind of gap this pack exists to prevent. HERMES must not inherit Databento's default silently; the
slow-reader behaviour actually used, and what HERMES does when it is invoked, must be an explicit,
governed decision, not an accident of client defaults.

**Consequences for design:**

- **Favour a very thin receive path**: receive → durable bounded spool/archive → downstream processing.
  This is the same "very-thin acquisition/spool boundary" from
  [`03-target-architecture-and-transport-boundary.md`](03-target-architecture-and-transport-boundary.md)
  §2. The receive hop's only job is to get bytes off the wire and into durable storage as fast and as
  reliably as possible.
- **Do not do expensive feature computation in the network reader.** Any processing heavy enough to risk
  making the reader fall behind the live feed does not belong in the same hop that owns the vendor
  socket. Push it downstream, after the durable spool, where a slowdown causes backlog rather than silent
  data loss.
- **If HERMES cannot retain the authoritative source stream without loss, GC market truth must become
  explicitly DEGRADED / GAP-DETECTED.** There is no acceptable state in which an incomplete feed is
  represented as complete market truth. A gap is a fact to be recorded and surfaced, not a fact to be
  hidden by continuing to report data as if nothing had been missed.
- **Use provider replay/recovery semantics where valid, and record their limits.** Databento's own
  reconnect/replay/recovery mechanisms should be used where they genuinely apply (e.g., recovering a short
  gap after a reconnect within whatever window the provider supports) — but the limits of what those
  mechanisms can actually recover must be documented, not assumed to cover every possible gap.

## §2 — PROD/DEV doctrine

**HERMES PROD:**

- Is a **standalone live operational stack** consuming the governed live GC market-truth delivery path
  (the Live Canonical Path in
  [`03-target-architecture-and-transport-boundary.md`](03-target-architecture-and-transport-boundary.md)
  §1).
- Its retention is **bounded by PROD-specific housekeeping policy** — PROD does not need to, and should
  not, retain the full depth of GC history; it retains what its own operational housekeeping policy
  requires for live operation.

**HERMES DEV:**

- **Remains the deep-history/replay environment.** DEV is where full historical depth and replay live.
- **Must NOT open a redundant live Databento connection merely to reproduce PROD behaviour.** If DEV needs
  to observe or test against "what PROD would be seeing right now," it does so by consuming the same
  governed corpus/replay path that DEV already uses — never by standing up its own second live vendor
  connection. This is a direct instance of the "one governed acquisition authority" invariant in
  [`00-purpose-and-terminology.md`](00-purpose-and-terminology.md) §1.
- **Consumes retained governed history and replay** — DEV's job is served entirely by the durable corpus
  and the replay/canonical path, never by an independent live feed.

**Do not truncate DEV historical authority to match PROD retention.** PROD's bounded, housekeeping-driven
retention policy is a PROD-specific operational decision; it must never be used as a reason to shorten or
constrain how much history DEV (or DARWIN-research) is able to see. DEV's depth of access is governed by
its own deep-history/replay role, independent of whatever PROD currently chooses to keep online.
