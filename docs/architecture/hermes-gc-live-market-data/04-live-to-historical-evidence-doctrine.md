# HERMES GC Live Market Data — Live-to-Historical Evidence Doctrine

**Initiative:** `HERMES GC Historical + Live Market-Data Doctrine`
**Status:** DOCUMENTATION / ARCHITECTURE ONLY. See [`00-purpose-and-terminology.md`](00-purpose-and-terminology.md).

## §1 — Live data must become historical evidence

```
   LIVE TODAY                                  REPLAY TOMORROW
   ----------                                  ---------------

   Databento live GC MBP-1                     Immutable Native Archive
          |                                     (today's live capture,
          v                                      now historical evidence)
   HERMES GC Ingestor                                     |
          |                                               v
          v                                     GC MARKET-TRUTH CORPUS
   Immutable Native Archive                               |
   (append-only, written                                  v
    as the live feed arrives)                   Replay / Canonical
                                                            |
                                                  --------------------
                                                  |                  |
                                                  v                  v
                                            HERMES DEV        DARWIN-research
                                          (gap-repair,        (deep-history
                                           reconciliation,     replay-based
                                           recent research,    discovery)
                                           validation)
```

The record written today, while GC MBP-1 is arriving live, is the **same durable evidence** that
tomorrow's replay, gap-repair, reconciliation, or research consumer reads back. There is no separate
"live-only" record and "historical-only" record for the same underlying market event — the live capture
*becomes* the historical evidence, on the same corpus, under the same governed canonical semantics.

## §2 — Reducing dependence on repeated PAYG retrieval

The programme should **progressively build its own continuous GC history from the live subscription**.
Every day the live subscription runs and durably archives its native capture, HERMES accumulates more of
its own governed GC history without needing to go back to the vendor for it.

This progressively reduces — but does not eliminate — dependence on repeated future historical PAYG
retrieval (see [`01-databento-commercial-facts.md`](01-databento-commercial-facts.md) §2). The rolling
12-month historical MBP-1 entitlement included in the Standard subscription **remains valuable** even
after live accumulation is underway, specifically for:

- **Gap repair** — filling a hole left by a live outage, reconnect, or slow-reader event (see
  [`06-slow-reader-doctrine-and-prod-dev-boundary.md`](06-slow-reader-doctrine-and-prod-dev-boundary.md)).
- **Reconciliation** — cross-checking the live-accumulated archive against the vendor's own historical
  record for the same interval.
- **Recent research** — DARWIN-research or other analysis that needs recent GC history that predates when
  live accumulation started, or that needs to re-pull a recent window independent of what was actually
  captured live.
- **Validation** — proving live/historical equivalence (see
  [`05-source-record-identity-and-equivalence-proof.md`](05-source-record-identity-and-equivalence-proof.md)).
- **Controlled backfill** — deliberately extending the corpus backward within the rolling 12-month window,
  or (via PAYG) beyond it, under governance.

The rolling-window entitlement and the growing live-accumulated archive are complementary, not
redundant: one is a vendor-side safety net and validation source; the other is HERMES's own accumulating
authority.

## §3 — Live/historical semantic equivalence is mandatory

Databento documents shared schemas and normalised structures across its live and historical products —
the same MBP-1 schema describes a record whether it arrived over the live socket or was retrieved via a
historical request. HERMES doctrine mirrors this: HERMES must use the **same governed, provider-neutral
semantic boundary** for:

- Retained historical MBP-1 (already in the corpus from a prior PAYG/backfill acquisition),
- Live MBP-1 (arriving now over the live socket), and
- Live replay/backfill (a later re-request of an interval that was, or should have been, captured live).

**HERMES must never maintain separate interpretations of GC merely because the delivery mode differs.** A
GC MBP-1 record's canonical meaning inside HERMES market truth is a function of the record itself and the
governed canonical semantics applied to it — not a function of whether it arrived live or historically.
Any implementation that branches canonicalisation logic on "was this live or historical" (beyond the
narrow, explicitly-governed differences required by §1 of
[`05-source-record-identity-and-equivalence-proof.md`](05-source-record-identity-and-equivalence-proof.md))
is out of doctrine.
