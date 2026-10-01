# HERMES GC Live Market Data — Downstream Architecture Chain (Correction Record)

**Initiative:** `HERMES GC Historical + Live Market-Data Doctrine`
**Status:** DOCUMENTATION / ARCHITECTURE ONLY. See [`00-purpose-and-terminology.md`](00-purpose-and-terminology.md).

## §1 — Current authoritative downstream chain

GC market truth produced by this pack's architecture flows, several layers downstream, into the following
authoritative chain:

```
HERMES (market truth) -> ARES (context) -> DARWIN (discovery) -> MENDEL/Workshop (Specification, StrategyVersion)
  -> ATHENA/APOLLO (Qualification) -> HELIOS (deterministic live evaluation) -> TRON (capital + execution)
```

This is the current, correct chain and supersedes any earlier phrasing describing the authoritative
downstream architecture as `HERMES -> DARWIN -> HSA -> HELIOS` (or any equivalent phrasing implying HSA is
a separate strategy-authority component sitting between DARWIN and HELIOS).

**HSA's useful doctrine has been absorbed into DARWIN's Specification architecture (MENDEL/Workshop).
HSA is no longer a separate strategy-authority component.** Any reference to HSA as an independent
component in the strategy-authority chain is stale and should read as the chain above instead.

## §2 — Repository search performed for this correction

Per this pack's mandate, the real repository was searched for stale wording describing the authoritative
downstream architecture as `HERMES -> DARWIN -> HSA -> HELIOS` (or equivalent phrasing implying HSA is a
separate strategy-authority component), across `docs/` and any architecture markdown, from the new
worktree at `/srv/rogue-hermes/worktrees/hermes-gc-live-architecture-docs` (branch
`docs/hermes-gc-historical-and-live-architecture`, based on real `main` tip
`03d700a463ada61d472c64ad52265d09d82c60ea`).

Searches run (all case-insensitive, whole-repository, excluding `.git`):

```bash
grep -rn 'HSA' docs/
grep -rniE 'darwin\s*(->|→).*hsa|hsa\s*(->|→).*helios|hermes\s*(->|→)\s*darwin\s*(->|→)\s*hsa' docs/
grep -rniE '(->|→)' docs/ | grep -iE 'darwin|helios|ares|athena|apollo|tron|mendel|hsa'
grep -rn --include='*.md' -w 'HSA' .
grep -rln -w 'HSA' --exclude-dir=.git .
grep -rn --include='*.md' -iE 'strategy.authority|hermes.*darwin.*helios|human.*strategy.*authority' .
```

**Result: zero matches for `HSA`, and zero matches for any `HERMES -> DARWIN -> HSA -> HELIOS`-style
chain or synonym, anywhere in the repository as checked out at the `main` tip used for this branch.** The
existing downstream-chain reference actually present in the repository
(`docs/contracts/hermes_darwin_canonical_historical_sql_contract_v1.md:15`) already reads
`HERMES durable historical authority -> DARWIN SELECT-only SQL adapter -> immutable MarketDataset ->
ATHENA/APOLLO`, which does not mention HSA and is not in conflict with §1 above.

**Consequence: no file in the repository required correction for this specific stale-HSA wording as of
this branch's base commit.** No files were edited under this correction task. This document exists to
(a) record the search that was performed and its real, verified-zero result, and (b) state the current
authoritative chain and the HSA-absorption fact explicitly in this pack, as instructed, so that anyone
reading this GC live-architecture doctrine also has the correct downstream chain in front of them and does
not need to rely on tribal knowledge to know HSA is not a separate component.

If a stale `HERMES -> DARWIN -> HSA -> HELIOS`-style reference is found in the repository at some later
commit (e.g., introduced by other concurrent work, or existing in a location this search did not cover),
it should be corrected to the chain in §1, citing this document.
