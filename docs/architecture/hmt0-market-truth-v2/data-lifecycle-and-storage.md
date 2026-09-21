# HMT-0 — Data lifecycle and storage

**Initiative:** `HMT-0 — Market Truth v2 Architecture Closure`
**Status:** DOCUMENTATION / ARCHITECTURE ONLY. See [`hmt0-closure-report.md`](hmt0-closure-report.md).

## Purpose

This document defines three semantic storage classes for the GC/HMT-0 programme, kept permanently
distinct. As established in `current-state-reconciliation.md` §3, HERMES today has exactly **one** of
these three tiers in production; the other two are new architecture, described here as target design.

## §1 — The three tiers

### 1.1 OPERATIONAL TRUTH

Roughly what HERMES already has today (`current-state-reconciliation.md` §2): operational SQL
(MariaDB — the real `candles_*`/`canonical_candles_*`/`ticks`/`hermes_*` tables) plus operational Redis
(the real `hermes:*` published consumer interface, per
`docs/architecture/redis-consumer-integrity-boundary.md`). This tier holds **live/current state** — what
is true now, and recent durable history needed for live operation. It is not the programme's long-horizon
research archive.

For the GC programme, this tier would hold: current/recent canonical GC events and derived facts needed
for live operation, in the same spirit as the existing OANDA operational tables — bounded depth, live
freshness, existing HERMES SQL/Redis operational discipline (deployment identity, environment model,
Redis consumer-integrity boundary — all unchanged, all inherited, none of it re-litigated by this
document).

### 1.2 MARKET RESEARCH STORE

New. An **append-oriented columnar research representation** — object/file storage suitable for
Parquet/Zstd-style partitioning (by instrument/contract/date, following the natural partition boundaries
established by `gc-futures-identity-and-roll.md`'s actual-contract identity), with SQL metadata/catalogue/
provenance sitting alongside it (not instead of it — the catalogue is metadata, the object store holds the
actual columnar research data).

This tier exists specifically because the P0 microstructure fact classes and the native GC corpus (TBBO
or MBP-1, per the still-pending decision in `canonical-market-events.md` §3) represent a materially larger
and differently-shaped dataset than anything the existing operational MariaDB/Redis tier was designed to
hold at research-horizon depth. It is where deterministic replay (`deterministic-replay-and-evidence.md`)
reads its inputs from for anything beyond the operational tier's live-state window.

### 1.3 EVIDENCE VAULT

New. **Immutable, promoted evidence.** Once a derived fact (per
`derived-fact-taxonomy-and-ownership.md`) or a research finding has been verified and is being relied upon
for a governed decision, it is promoted into this tier and becomes immutable — no further silent
revision. This is the tier `deterministic-replay-and-evidence.md`'s replay guarantee is ultimately in
service of: evidence that can be independently reproduced from a known input set is evidence worth
promoting here.

## §2 — Explicit boundary: do not deepen dependency on deprecated/shared Proteus architecture

This is stated as a hard rule, grounded in real, existing repository references (not an abstract
admonition). Confirmed by repo-wide search: `tradingProteus` already appears as a **separate, legacy
database path** referenced across several existing HERMES documents and code comments —
`docs/contracts/hermes_redis_tick_contract_v1.md` explicitly lists `/srv-dev/tradingProteus` as something
a consumer must **not** read; `docs/design/build_security_and_canary_doctrine_v1.md` documents a real
shared read-only credential (`trinity@'%'`) that already spans `tradingRisk`/`tradingSignals`/
`tradingProteus` as "a shared estate credential reused beyond HERMES"; and
`docs/design/container_mvp/WP2_CANONICAL_BUILD_AND_EXTERNALISED_CONFIG.md` treats any config path
referencing `tradingProteus` as a rejected cross-application reference
(`DISCORD-CROSS-APP-REFERENCE-REJECTED`, `SECRET-ROOT-CROSS-APPLICATION`).

The existing HERMES codebase already treats `tradingProteus` as **out-of-bounds, shared, legacy
infrastructure it must not lean on** — this document extends that same existing discipline to the new
GC programme explicitly: **none of the three tiers in §1 (operational, research store, evidence vault)
may be implemented as a deepened dependency on `tradingProteus` or any other deprecated/shared Proteus
architecture.** New storage for this programme is either genuinely HERMES-owned (operational tier, exactly
as today) or genuinely new, purpose-built infrastructure (research store, evidence vault) — never a
convenience extension bolted onto an already-legacy, already-shared, already-flagged-for-avoidance
database.

## §3 — Tier relationships

```
raw/canonical GC events (native corpus, level TBD)
        │
        ├──► OPERATIONAL TRUTH (live/current state; bounded depth; existing HERMES SQL+Redis discipline)
        │
        └──► MARKET RESEARCH STORE (full-depth, columnar, partitioned by contract/date; SQL catalogue)
                        │
                        └──► promoted, reproducible findings ──► EVIDENCE VAULT (immutable)
```

Nothing flows backward: the evidence vault never feeds back into operational truth as a live dependency,
and operational truth is never treated as a substitute for the research store's full-depth retention.

## §4 — What this document does not do

Does not specify a concrete object-storage provider, a concrete Parquet partition scheme, or a concrete
SQL catalogue schema — those are HMT-1 (or later) implementation decisions, explicitly gated behind
separate authorisation (`hmt1-provisional-scope.md`). Does not commit to any retention horizon or cost
figure for the research store or evidence vault — see `gc-data-volume-and-cost-study.md` for the
`PENDING_EVIDENCE` treatment of anything cost/volume-related.
