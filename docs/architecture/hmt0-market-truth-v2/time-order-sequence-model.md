# HMT-0 — Time / order sequence model

**Initiative:** `HMT-0 — Market Truth v2 Architecture Closure`
**Status:** DOCUMENTATION / ARCHITECTURE ONLY. See [`hmt0-closure-report.md`](hmt0-closure-report.md).

## Purpose

Time and ordering are the single easiest place for a market-data system to quietly lie to itself — by
collapsing several genuinely distinct clocks into one field, or by inventing a cross-source ordering that
no two systems actually agreed on. This document specifies the fields this programme keeps **permanently
separate**, and states explicitly, by name, the one existing HERMES field this pack must not let a future
implementer reuse for a purpose it was never designed for.

## §1 — The fields, kept permanently separate

Every canonical GC event/fact must be able to carry all of the following, distinctly, never collapsed
into a single "timestamp" field:

1. **Market/exchange event time** — the time the exchange itself attributes to the event (trade time,
   quote time), as reported in the provider's feed. This is market truth.
2. **Provider receive/capture time** — the time the data provider (e.g. Databento) captured or received
   the event, **only where the provider genuinely exposes this**. Never fabricated or backfilled from
   another timestamp if the provider does not actually supply it — an absent provider-capture time is
   recorded as absent, not approximated from event time or arrival time.
3. **HERMES receive time** — the time HERMES's own ingestion path observed the event. This is always
   available (it is a fact about HERMES's own process), and is distinct from both of the above.
4. **Native precision per source** — the actual timestamp resolution the source provides (e.g.
   millisecond for the legacy CME era, nanosecond for MDP 3.0 — see §3 below). A fact must never claim a
   precision finer than its source actually delivered.
5. **Sequence source/domain** — which system's sequence numbering a given `seq`-shaped field belongs to.
   A sequence number is meaningless without knowing whose counting scheme produced it; this field makes
   that explicit on every record that carries one, rather than leaving it implicit.
6. **Connection/acquisition epoch** — which specific connection/session/acquisition run observed this
   event (a reconnect, a backfill run, a live session are all distinct epochs). This is provenance, not
   market truth, and is never conflated with either.

## §2 — Explicit prohibition: HERMES's existing stored-row `seq` is NOT an exchange sequence number

`docs/hermes_tick_seq_semantic.md` (read in full while preparing this pack) ratifies exactly what
HERMES's existing `ticks.seq` column means today: a **per-instrument monotonic sequence assigned over
stored tick rows**, ordered by `(timestamp, id)`. Quoting that document directly: *"`seq` is NOT a
guarantee that every market-time tick exists. It guarantees only that the ticks HERMES stored for an
instrument are densely, monotonically numbered 1..N with no duplicates."* It was defined this way
specifically because the 2026-06-10/11 outage produced unrecoverable raw-tick gaps that could never be
reinserted, making a stored-row (rather than market-time-complete) sequence the only stable option for
that legacy OANDA tick pipeline.

**This is a reasonable, ratified semantic for its own purpose — and it must never be reused for the new
GC programme's sequencing needs.** Concretely:

- HERMES's existing `seq` counts *rows HERMES happened to keep*, not events the exchange actually
  emitted in order. It has no relationship whatsoever to a COMEX/Databento sequence number.
- Any GC canonical event or derived fact that needs a genuine exchange-ordering guarantee must use the
  **provider's own sequence field** (§1 item 5, "sequence source/domain" — tagged explicitly as
  `DATABENTO` or the specific feed's own sequence domain, never `HERMES_STORED_ROW`), not a HERMES-side
  counter modelled on the legacy tick semantic.
- If a future implementer is tempted to give a new GC table a `seq BIGINT AUTO_INCREMENT`-style column
  "because that's how `ticks.seq` works" — that is exactly the error this section exists to block. The
  legacy `ticks.seq` pattern is precedent for *stored-row bookkeeping*, not for *exchange event ordering*,
  and the two must never be presented under the same field name or the same informal mental model.

## §3 — Historical provenance eras and quality propagation

As established in `source-taxonomy.md` §1.3, GC provenance quality is not uniform across history. Three
eras are recognised. The table below states each era's final, resolved timestamp/provenance semantics.
The legacy-era facts below are attributed explicitly to source documentation, not to this pack's own
measurement or inference:

| Era | Timestamp resolution | Genuine provider capture time? | `ts_recv` semantics | Quality flag |
|---|---|---|---|---|
| `PRE_2015_11_20_LEGACY` | Millisecond | **No** — legacy MDP2/FIX flat-file provenance (pre-2017-05-21) | Synthetically equated to `ts_event`; **never** an independent capture-time measurement | `F_BAD_TS_RECV` (per Databento's official CME GLBX.MDP3 documentation) |
| `2015_11_20_TO_2017_05_20_LEGACY` | Nanosecond (from CME's 2015-11-20 nanosecond-resolution introduction) | **No** — still pre-2017-05-21 legacy MDP2/FIX flat-file provenance; the 2015-11-20 boundary improves timestamp *resolution* only, not capture-time genuineness | Synthetically equated to `ts_event`, same as the prior era; **never** an independent capture-time measurement | `F_BAD_TS_RECV` (per Databento's official CME GLBX.MDP3 documentation) — still propagates |
| `MDP3_FROM_2017_05_21` | Nanosecond | **Yes** — modern MDP3 capture provenance, available subject to per-record quality flags confirmed at real-ingestion time (not asserted in advance for records not yet ingested) | Genuine, independent of `ts_event` | Per-record, confirmed at ingestion |

**Item 2 of §1 above** ("provider receive/capture time... never fabricated or backfilled from another
timestamp if the provider does not actually supply it") applies with full force to both legacy eras: per
Databento's official CME GLBX.MDP3 documentation, `ts_recv` is set for records from both legacy eras to
the same underlying legacy time basis as `ts_event` — this is a **synthetic equation, not an independent
capture-time measurement**, and must never be read or presented as one downstream, in any derived fact or
canonical record. The `F_BAD_TS_RECV` quality flag that Databento's official CME GLBX.MDP3 documentation
attaches to records from both legacy eras must propagate as this era's quality/condition flag into every
derived fact that consumes it, per this document's own quality-propagation rule below.

This resolves the `PRE_2015_11_20_LEGACY` timestamp/provenance cell that in a prior revision of this
table read `PENDING_EVIDENCE` — the source for the resolution above is Databento's own official CME
GLBX.MDP3 documentation, cited explicitly here rather than inferred or estimated by this pack, and no
stronger precision claim is made than what that documentation states.

**Era-boundary corroboration:** the `MDP3_FROM_2017_05_21` era boundary is independently corroborated by
Databento's own `mbo`/`cmbp-1`/`cbbo-*` schema-availability boundary (those schemas only exist from
2017-05-21 onward), consistent with genuine modern full-book capture beginning exactly there — this is the
same boundary at which genuine, independent provider capture time first becomes available (table above).
The `2015-11-20` boundary, by contrast, is a timestamp-*resolution* improvement only (millisecond →
nanosecond, per CME's own nanosecond-resolution introduction on that date) and does **not** correspond to
any change in capture-time genuineness — both legacy eras share the same synthetic `ts_recv`-equated-to-
`ts_event` semantic and the same `F_BAD_TS_RECV` quality flag. This document does not conflate a
resolution improvement with genuine capture-time availability: only the 2017-05-21 MDP3 boundary carries
the latter.

A quality/condition flag carrying this era classification must propagate from the raw event into every
derived fact that consumes it (cross-referenced from `derived-fact-taxonomy-and-ownership.md`'s
"evidence lineage" field). A derived fact spanning an era boundary (e.g. a rolling statistic that
straddles 2017-05-21) must record **all** contributing eras — it may never claim the strongest
contributing era's guarantee if any input came from a weaker one.

## §4 — No fabricated cross-source global ordering

HERMES already has a real, honest precedent for refusing to fabricate provenance it does not have: the
`canonical_candles_m15` view (migration 027) leaves `derivation_generated_at_utc` genuinely `NULL` because
the underlying table has no `created_at` column, rather than deriving a fake value from the candle's own
market-open time (see `current-state-reconciliation.md` §2.3). This document generalises that discipline
to cross-source ordering:

- If two events originate from genuinely different sources (e.g. an OANDA XAU_USD quote and a COMEX GC
  trade) and there is no shared, trustworthy clock/sequence domain that actually orders them, **HERMES
  does not invent an ordering**. Any comparison between them is stated as approximate/best-effort
  wall-clock proximity, explicitly labelled as such, never presented as a genuine causal or exchange-level
  ordering.
- Within a single source's own native sequence domain (e.g. Databento's own MBP-1 sequence numbers for
  GC), genuine ordering is preserved and trusted as that source's own truth.
- HERMES receive time (§1 item 3) can always be used to approximate arrival order across sources, but this
  document requires that any such use be labelled `HERMES_RECEIVE_ORDER_APPROXIMATION`, never presented as
  `MARKET_EVENT_ORDER`.

## §5 — What this document does not do

Does not specify a concrete schema (column names/types) — that is implementation, gated behind HMT-1
authorisation (`hmt1-provisional-scope.md`). The TBBO-vs-MBP-1 native-corpus decision
(`canonical-market-events.md` §3) is now resolved (MBP-1) — that decision affects which sequence domain
is actually available in practice, but this document's field-separation requirement was always stated to
hold regardless of which level was ultimately chosen, and remains unchanged by the resolution.
