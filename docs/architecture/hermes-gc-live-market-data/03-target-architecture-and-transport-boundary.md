# HERMES GC Live Market Data — Target Architecture and Transport Boundary

**Initiative:** `HERMES GC Historical + Live Market-Data Doctrine`
**Status:** DOCUMENTATION / ARCHITECTURE ONLY. See [`00-purpose-and-terminology.md`](00-purpose-and-terminology.md).

## §1 — Target GC architecture

```
                       Databento GLBX.MDP3 — GC MBP-1
                     (ONE live connection, single vendor
                          acquisition authority)
                                    |
                                    v
                    HERMES GC Ingestor
              (single acquisition authority — the only
               component that talks to the vendor for GC)
                                    |
                  ------------------------------------
                  |                                  |
                  v                                  v
     Immutable Native Archive                 Live Canonical Path
   (durable, append-only, replay              (low-latency delivery,
        authority for GC)                      governed canonical
                  |                             semantics applied)
                  v                                  |
         GC MARKET-TRUTH CORPUS                       |
      (governed canonical semantics,                  |
       historical + progressively-                    |
         accumulated live records)                    |
                  |                                    |
        --------------------                           |
        |                  |                           |
        v                  v                           v
   Replay/Canonical     HERMES DEV /            HERMES PROD
   (backfill, gap-      DARWIN-research      (fed exclusively from
    repair, validation, (deep-history,           the live canonical
    controlled          replay-based)                  path)
    research access)
```

**Invariant:** one vendor acquisition stream (the HERMES GC Ingestor's single Databento connection) must
be capable of feeding **both** the durable replay authority (Immutable Native Archive → GC Market-Truth
Corpus) **and** live market-truth delivery (Live Canonical Path → HERMES PROD). There is no scenario in
which a second, independently-configured GC acquisition point is stood up to serve either side of this
diagram.

**Implementation authority note:** the exact transport and storage technology (message bus, file format,
database engine, streaming framework, etc.) used to realise this diagram is **implementation authority
for a later Change Decision (CD)**. This pack fixes the architecture and its invariants; it does not fix,
constrain, or presuppose any specific technology choice for realising them.

## §2 — The archive/corpus is NOT the live transport

The durable GC market-truth corpus is **archive and replay authority** — it is not, and must never be
treated as, HERMES PROD's low-latency live feed. Reading live GC ticks by querying the durable corpus
directly, in the live-latency path, would conflate two different jobs the architecture deliberately keeps
separate.

```
        Databento live socket
                |
                v
   Very-thin acquisition/spool boundary
   (receive -> durable bounded spool;
    minimal processing in this hop —
    no feature computation here)
                |
      --------------------
      |                  |
      v                  v
  Durable native      Live governed
  archival path       canonical path
                             |
                             v
                        HERMES PROD
```

Two invariants follow from this diagram:

1. **A HERMES restart, or a HERMES PROD deployment, must never require creating a second external GC
   market-data source.** The vendor connection belongs to the HERMES GC Ingestor, not to any individual
   HERMES PROD process instance. Restarting or redeploying HERMES PROD reconnects to the existing live
   canonical path; it does not, and must not, cause a new Databento live session to be opened as a side
   effect of application lifecycle.
2. **Provider connectivity and HERMES application lifecycle must be decoupled.** The vendor
   acquisition/spool boundary's uptime is managed independently of whether HERMES PROD (or any other
   consumer) is currently up, restarting, or being deployed. This is what makes it possible for the
   durable archive to keep receiving and retaining market truth even while a downstream consumer cycles.

## §3 — Why the thin boundary matters here

The "very-thin acquisition/spool boundary" language in §2 is deliberate: the component that owns the
vendor socket should do as little as possible beyond receiving and durably retaining the native stream.
Pushing feature computation, canonicalisation, or business logic into that same hop increases the risk
that a slow or failing downstream step causes the acquisition point itself to fall behind or drop data —
see [`06-slow-reader-doctrine-and-prod-dev-boundary.md`](06-slow-reader-doctrine-and-prod-dev-boundary.md)
for the full doctrine on why HERMES must not silently lose GC market records under load.
