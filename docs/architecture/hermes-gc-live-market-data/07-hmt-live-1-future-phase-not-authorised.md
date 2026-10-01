# HERMES GC Live Market Data — HMT-LIVE-1 Future Implementation Phase

**Initiative:** `HERMES GC Historical + Live Market-Data Doctrine`
**Status:** DOCUMENTATION / ARCHITECTURE ONLY. See [`00-purpose-and-terminology.md`](00-purpose-and-terminology.md).

> ## STATUS: NOT AUTHORISED FOR IMPLEMENTATION
>
> `HMT-LIVE-1` is recorded below as a **future possible implementation phase only**. Nothing in this
> document, or in any other document in this pack, authorises starting any part of it. No code,
> infrastructure change, subscription activation, or configuration change should be made on the basis of
> this section. This is a record of scope for a decision that has not been made.

## §1 — Name and nature

**`HMT-LIVE-1` — GOVERNED GC CONTINUOUS MARKET-TRUTH INTAKE**

This is **infrastructure / market-truth acquisition work**. It is **explicitly NOT an HMT-3-derived
feature family** — it is not about deriving new features, signals, or facts from GC; it is about how GC
market truth itself is continuously acquired, retained, and delivered live. Any future feature/derived-fact
work that consumes GC live data is downstream of, and out of scope for, `HMT-LIVE-1` itself.

## §2 — Full possible future acceptance scope (recorded, not started)

The following is the full list of scope items that a future, properly authorised `HMT-LIVE-1` Change
Decision might reasonably need to cover. It is recorded here **exactly as given**, for completeness, not
as a commitment to build all or any of it:

1. **One Databento GLBX.MDP3 GC MBP-1 live authority** — the single acquisition point described in
   [`03-target-architecture-and-transport-boundary.md`](03-target-architecture-and-transport-boundary.md).
2. **External secrets/config** — vendor credentials and connection configuration held externally, not
   embedded in code or committed to the repository.
3. **Contract/symbology handling** — correct handling of GC contract identity and roll (consistent with
   the existing `gc-futures-identity-and-roll.md` doctrine in the HMT-0 pack).
4. **Immutable native retention** — the durable, append-only native archive described in
   [`03-target-architecture-and-transport-boundary.md`](03-target-architecture-and-transport-boundary.md)
   §1.
5. **Rolling archival boundaries** — how the durable archive's physical retention/chunking evolves over
   time (implementation-authority detail, per §1 of that same document).
6. **Hashes/manifests/provenance** — integrity and provenance tracking for what was acquired, when, and
   from which acquisition mode.
7. **Stable source-record identity** — the identity scheme required by
   [`05-source-record-identity-and-equivalence-proof.md`](05-source-record-identity-and-equivalence-proof.md)
   §2.
8. **Live/historical equivalence proof** — the 8-step overlap experiment in
   [`05-source-record-identity-and-equivalence-proof.md`](05-source-record-identity-and-equivalence-proof.md)
   §3, run and passed before live GC is declared authoritative.
9. **Reconnect/replay/gap handling** — explicit, governed handling of live disconnects and reconnects,
   using provider replay/recovery semantics where valid (see
   [`06-slow-reader-doctrine-and-prod-dev-boundary.md`](06-slow-reader-doctrine-and-prod-dev-boundary.md)
   §1).
10. **No silent slow-reader skipping** — explicit rejection of Databento's default `skip` slow-reader
    behaviour as implicit HERMES doctrine (same section as above).
11. **Canonical HERMES live output** — live GC market facts expressed under the same governed canonical
    semantics as historical GC market facts (see
    [`04-live-to-historical-evidence-doctrine.md`](04-live-to-historical-evidence-doctrine.md) §3).
12. **Service health/lag/gap observability** — visibility into whether the live intake is healthy,
    how far behind realtime it is, and whether any gap has been detected.
13. **PROD consumer decoupled from vendor socket lifecycle** — per
    [`03-target-architecture-and-transport-boundary.md`](03-target-architecture-and-transport-boundary.md)
    §2.
14. **DEV/research replay from retained evidence** — DEV and DARWIN-research consume the durable corpus
    and replay path, never a live vendor connection of their own (see
    [`06-slow-reader-doctrine-and-prod-dev-boundary.md`](06-slow-reader-doctrine-and-prod-dev-boundary.md)
    §2).
15. **No duplicate vendor feed for DEV and PROD** — restating the single-acquisition-authority invariant
    from [`00-purpose-and-terminology.md`](00-purpose-and-terminology.md) §1 as an explicit acceptance
    criterion.

## §3 — What this section is, and is not

This section is a **scope record for a future decision**. It does not:

- Commit to a start date, work-order number, or budget for `HMT-LIVE-1`.
- Authorise activation of the Databento live subscription described in
  [`01-databento-commercial-facts.md`](01-databento-commercial-facts.md).
- Authorise any code or infrastructure change.
- Override or supersede any existing HMT-0/HMT-1/HMT-2 ruling, authorisation state, or budget.

Starting `HMT-LIVE-1` requires its own separate authorisation, exactly as HMT-1 and HMT-2 each required
their own separate authorisation before work began on them.
