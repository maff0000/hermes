# HERMES GC Live Market Data — Source-Record Identity and Live/Historical Equivalence Proof

**Initiative:** `HERMES GC Historical + Live Market-Data Doctrine`
**Status:** DOCUMENTATION / ARCHITECTURE ONLY. See [`00-purpose-and-terminology.md`](00-purpose-and-terminology.md).

## §1 — Stable source-event identity: a known real architecture gap

This is a **known, real architecture issue**, recorded here deliberately rather than glossed over.

The current HMT-2 historical adapter constructs a source artifact/record identifier that **incorporates a
request/file-relative record index** — i.e., part of what identifies a given native record today is its
position within the specific request or file it was retrieved in.

- This is **acceptable within the currently-governed HMT-2 corpus semantics**. HMT-2 is a bounded,
  historical, PAYG-acquisition research corpus with a fixed, known set of acquisition requests; a
  request/file-relative index is a coherent identity scheme within that closed context.
- It is **NOT automatically suitable as a universal live↔historical exchange-event identity**. A record
  index of this kind can change across:
  - **Live capture** vs a historical request for the same interval (the live socket does not deliver
    records "as a file" with a stable relative index the way a historical request does).
  - **Different archive chunking** (if the durable archive is later re-chunked, re-partitioned, or
    re-compacted, a file-relative index computed against the old chunking will not match the new one).
  - **Different historical range downloads** (requesting the same underlying interval as two different
    range requests, e.g. one big pull vs several smaller pulls, can change which request/file a given
    record's index is relative to).
  - **Different replay segmentation** (replaying the corpus in different segment sizes changes what
    "relative index" even means for a given record).

**Explicit rule: do NOT silently change existing HMT-2 identities during the active corpus build.** The
HMT-2 corpus build is live, real engineering work happening right now
(`hmt-2/governed-gc-mbp1-historical-corpus`), and this pack does not touch it, propose changing it, or
imply that its current identity scheme should be modified in place. Any change to how HMT-2 identities are
computed — including migrating to whatever stable identity scheme results from this section — is its
**own, separately governed compatibility decision**, made deliberately and later, not an implicit
side-effect of writing this doctrine.

## §2 — What a future live GC CD must establish

A future live GC Change Decision (see
[`07-hmt-live-1-future-phase-not-authorised.md`](07-hmt-live-1-future-phase-not-authorised.md)) must
establish a **STABLE PROVIDER SOURCE-RECORD IDENTITY** for GC MBP-1 records that is **independent of**:

- File chunk boundary,
- Request boundary,
- Live connection epoch (i.e., which live session/reconnect delivered the record),
- Archive segmentation (however the durable archive happens to be physically laid out at any given time).

Such an identity would let HERMES recognise "this is the same underlying exchange event" regardless of
how, when, or via which acquisition mode it was captured — which is the prerequisite for the equivalence
proof in §3 below, and for clean gap-repair/reconciliation per
[`04-live-to-historical-evidence-doctrine.md`](04-live-to-historical-evidence-doctrine.md) §2.

## §3 — Required live-versus-historical identity proof

Before live GC can be declared authoritative market truth, the following **8-step real overlap
experiment** must be run and pass:

1. **Receive live MBP-1** for a bounded interval via the live canonical path.
2. **Retain the native records** from that live capture, unmodified, in the durable archive.
3. **Later, request the same interval historically** from Databento (via the standard historical
   retrieval path).
4. **Align equivalent provider-native records** between the live-captured set and the historically-
   retrieved set for that interval.
5. **Prove the proposed stable source-record identity** (§2) identifies the same underlying market
   records regardless of acquisition mode — i.e., the same real exchange event gets the same stable
   identity whether it was captured live or retrieved historically.
6. **Pass both sets through governed canonical semantics** (the same canonicalisation logic, per
   [`04-live-to-historical-evidence-doctrine.md`](04-live-to-historical-evidence-doctrine.md) §3).
7. **Require equivalent HERMES market facts** to result from both — the canonical output for a given
   stable-identity record must not differ depending on whether it arrived live or historically.
8. **Investigate every mismatch rather than normalising it away.** Any record present in one set and not
   the other, or any record that canonicalises differently depending on acquisition mode, must be
   individually investigated and explained — never silently dropped, deduplicated away, or treated as
   noise to average out.

## §4 — Genuine live-only / system records

Separately from the equivalence proof in §3, the future CD must document genuine **live-only / system
records** that Databento does not reproduce historically at all (e.g., session/heartbeat/system messages
specific to the live protocol that have no historical-request analogue). These are not failures of the
equivalence proof — they are known, documented asymmetries between the live and historical delivery
modes, and must be recorded as such rather than treated as unexplained mismatches.

**Do not assume perfect byte identity where the provider does not promise it.** The equivalence proof in
§3 is about the same underlying market records producing equivalent HERMES market facts under governed
canonical semantics — not about byte-for-byte identity of the raw wire/file representation, which
Databento itself does not guarantee across live and historical delivery.
