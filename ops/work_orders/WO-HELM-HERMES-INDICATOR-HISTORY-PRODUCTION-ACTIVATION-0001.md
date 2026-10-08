# WO-HELM-HERMES-INDICATOR-HISTORY-PRODUCTION-ACTIVATION-0001 — governed production activation

**Persona:** Rogue (Delivery Controller), governance preparation, per direct Central Architecture dispatch.
**Operator (when dispatched, not by this document):** HELM — SYSOPS.
**Status:** PREPARATION ONLY — not yet accepted. No HELM dispatch has occurred. No production Redis, SQL, or
deployment mutation has occurred or is authorised by this document.
**Date:** 2026-10-08
**Repo:** `maff0000/hermes` (canonical checkout `/srv/rogue-hermes/canonical` on dell-debian)
**Parent governance:** `docs/governance/PID-HERMES-MVP-001.md` (§11 indicator-history decision, §12 H4
integrity defect, §13 weekend-bar ruling, §14 retention-normalisation finding, §8 delivery discipline) and
`ops/work_orders/WO-HERMES-INDICATOR-HISTORY-CONTRACT-0001.md` (the implementation WO this activates).
**Base SHA:** `origin/main = 7173c43e4c8f4a67a2199d9ed26d7251e808bacd` (canonical main after
`WO-HERMES-INDICATOR-HISTORY-CONTRACT-0001`'s implementation, PR #188, merged — code-in-main only).
**Production SHA (observed, separate from canonical base, to be re-verified at activation time, not assumed
current)**: `1c359067b0e00d3947fa314382bc125da398d662` (`hermes-signal:prod-1c359067b0e0`, host
`194.164.122.94`) — labelled `HERMES_BUILD_CLASSIFICATION=NON_PROMOTED_ENGINEERING_CANDIDATE`; see §9 below.

## 1. Governance chain (binding, copied forward)

```
Architecture decision → PID/Amendment → Git-tracked Work Order → Delivery Controller → Implementer/Operator → Independent Audit → PR → Architect Acceptance → Merge → Closure
```

**NO PID → NO WORK ORDER.** **NO WORK ORDER → NO IMPLEMENTATION.** **NO INDEPENDENT AUDIT + ARCHITECT
ACCEPTANCE → NO MERGE.** **NO GIT RECORD → NOT DURABLE PROJECT AUTHORITY.** Plus PID §8 (binding for every
HERMES WO): one WO/branch/worktree/PR; R2D2 GREEN before merge/deploy/activation; external configuration
only; no duplicate utilities; canonical/deployed/operational states recorded separately.

## 2. Governance assessment (the determination this document exists to make)

**Existing governance is INSUFFICIENT, by itself, to authorise production mutation.** `PID-HERMES-MVP-001`
already provides full architectural authority for *what* is wrong and *what* the correct repair looks like
(§12: the H4 integrity defect and its governed repair approach; §13: weekend bars are valid and must not be
excluded; §14: the retention-normalisation finding and its bounded design). `WO-HERMES-INDICATOR-HISTORY-
CONTRACT-0001` implements the *mechanism* (code-in-main only, per its own §17 disposition and the governed
3-state DoD in PID §5) but explicitly does not authorise executing that mechanism against live production —
its own §6/§6a state the repair/normalisation designs are "not executed by this WO document itself" and its
exclusions (§12) forbid any live Redis/SQL mutation. Neither document defines: exact build/deploy procedure
for this capability, pre-mutation evidence requirements, conflict/rollback behaviour for a live production
run, or runtime proof acceptance criteria tied to *this* capability specifically. Per the Architect's own
instruction, authority is not inferred merely because the implementation WO *discusses* runtime acceptance —
it must be explicit. **This document is that explicit, bounded authority, prepared under the existing
parent PID — no new PID is required; nothing here reopens PID architecture.**

## 3. Objective

Govern (not execute) the production activation of the already-merged indicator-history capability: deploy
the governed code, prove deployed-version identity, execute the governed H4 repair and retention
normalisation against *actual, freshly re-measured* production state (not the 7 October historical
snapshot), and prove the full runtime acceptance criteria — completing all three states of PID §5's
Indicator Definition of Done (code-in-main, already done; deployed runner; fresh versioned Redis payload).

## 4. Scope — what a future HELM dispatch against this WO is authorised to do

### A. Deploy

Build and deploy the already-accepted HERMES capability from governed canonical code at the exact SHA
Central Architecture accepts for this activation (currently `7173c43e...`, or a later canonical SHA if the
Architect re-confirms it at dispatch time — HELM must not deploy against a SHA this WO does not name).
Record exact artefact/version identity using the **existing, already-governed** deployment-identity
machinery (`docs/architecture/deployment-identity-and-config-binding.md`,
`utils/hermes_runtime_identity_v1.py`): baked `SOURCE_SHA` == OCI image revision == `/buildinfo`, verified
mechanically, not asserted by the operator. **No new identity/build mechanism may be invented** — reuse this
exactly.

**Truthful classification (§9 of the Architect's mandate, binding):** the deployed artefact will carry
`HERMES_BUILD_CLASSIFICATION=NON_PROMOTED_ENGINEERING_CANDIDATE` because, per PID §15, no `PROMOTED` build
path exists anywhere in this codebase. This WO does **not** invent a promotion mechanism and does **not**
relabel the artefact. The identifiability/reproducibility machinery above (`SOURCE_SHA`/`/buildinfo`
verification) is independent of and unaffected by the classification gap — the artefact is safely
identifiable by exact SHA even though it is not "promoted" in the classification-label sense. This is
recorded as known, pre-existing, out-of-scope debt (PID §15), not a blocker to this activation, and not
remediated here.

### B. Pre-mutation evidence (mandatory, before any repair/normalisation step)

Before touching anything, HELM must capture, freshly, against the *live, current* state (not reuse the 7
October figures as current fact):

- Running deployed version/build identity (via the mechanism in §4.A).
- H4 history index cardinality (`ZCARD` on `hermes:candles:XAU_USD:H4:history:v1:index`).
- H4 history object cardinality (count of index members with a corresponding existing history object).
- The exact set of currently-missing members (by epoch), not an assumed count.
- Current TTL state of H4 history objects relevant to the retention-normalisation step (§4.D).
- Current `hermes:indicators:XAU_USD:H4:v1` state, specifically `ema_200`.
- Relevant HERMES health/readiness signal.

This evidence must be recorded (Git-tracked evidence folder, per this repo's own `ops/evidence/<WO-ID>/`
convention) before step C/D proceed.

### C. H4 repair

Repair only legitimately missing governed H4 history, using:

- Authoritative durable SQL `candles_H1` (never stale/legacy `candles_H4`).
- The existing, already-governed, already-implemented, already-audited H4 derivation/backfill mechanism
  (`utils/candle_h4_history_seed_backfill_v1.py`, reused by `WO-HERMES-INDICATOR-HISTORY-CONTRACT-0001`'s
  implementation — not reimplemented here either).
- Exactly 4 complete H1 children per reconstructed bucket; never fabricate a missing child.
- Current governed weekend semantics (PID §13) — valid 4/4 flat carry-forward weekend bars are legitimate
  market truth and must be reconstructed identically to market-open buckets, never excluded or
  special-cased.
- Never overwrite an existing H4 survivor (check-before-write, exactly as the governed mechanism already
  does).
- Conflicting existing data halts/fails loudly per the governed mechanism's own existing behaviour — HELM
  does not invent a different conflict response.

**Binding instruction, not optional:** the previously-observed 250/185/65 split (and the 45-market-open/
20-weekend breakdown) is the *known, expected* defect shape — it is evidence of what repair *should* be
needed, not a blind mutation target. HELM must act on §4.B's *fresh* measurement. If fresh measurement
shows a materially different picture (different missing-member count/epochs, objects already repaired by
some other process, additional new gaps) and the difference is not obviously and fully explained by expected
forward operation (new bars published normally, expected TTL-driven aging within the governed policy), **STOP
→ CENTRAL ARCHITECTURE. Do not force the expected 65. Do not improvise a revised repair scope.**

### D. Retention normalisation

Apply only the bounded, existing canonical H4 retention policy (count-based `H4_HISTORY_RETAIN_COUNT=250` +
the current TTL cap, both already governed and already reused unmodified by the merged implementation) to
legitimately-retained-window H4 objects still carrying an obsolete shorter TTL — using the already-merged
`utils/candle_h4_retention_normalisation_v1.py` mechanism, which is `EXPIRE`-only by design (never
`SET`/`DEL`, never a market-value rewrite, never extends beyond the governed cap). Do not generalise this
into a broader TTL-rewriting operation. Do not touch any H4 object outside the governed retained window.

### E. Runtime proof (required before this stage can be considered closed)

After A-D, HELM must prove, with fresh evidence:

1. H4 index/object integrity within the governed retained window — zero dangling index members.
2. Sufficient H4 depth for the governed 210-bar deep window.
3. `hermes:indicators:XAU_USD:H4:v1`'s `ema_200` is non-null.
4. That value was produced by the existing, unmodified, governed HERMES indicator implementation (not by any
   manual computation or special-cased value).
5. Historical M5 `atr_14` is available via the new per-bar history contract.
6. Historical H4 `ema_50` is available via the new per-bar history contract.
7. Historical H4 `ema_200` is available via the new per-bar history contract.
8. Fresh, versioned Redis history payloads are being produced by the deployed runtime going forward (not
   merely a one-time backfilled set) — i.e. the live write-path extension in `indicator_step()` is
   genuinely active, not just the backfill.

## 5. Explicit exclusions (binding on any future HELM dispatch against this WO)

Do not: implement HELIOS's Redis-reader adapter or any HELIOS change of any kind; touch FALCON (including
PID-07) in any way; touch TRON or ARES; change weekend H4 semantics or the `H4_FROM_H1_NY1700_V1` anchor
policy (`22:00/02:00/06:00/10:00/14:00/18:00 UTC`); implement Redis ACL/credential/security changes (a
separate, later, HELM-gated deployment/security stage — this WO's deployment step uses whatever access the
existing production deployment already has, it does not change the access model); implement or invent a
build-promotion/classification gate (PID §15 remains open, separate debt); perform a general Redis
keyspace/retention redesign beyond the specific H4 class this WO's merged mechanism targets; rehabilitate
legacy SQL indicator values or `hermes:signals:*` as an interface; fabricate any missing H1 child or any H4
bucket lacking 4 complete children; overwrite any existing valid H4 history object; widen this activation to
any instrument/timeframe beyond XAU_USD H4 (repair/normalisation) and the already-merged M5/H4
indicator-history write path.

## 6. Rollback / safety model

This mechanism is fundamentally **additive and idempotent, not destructive**, and the rollback model must
reflect that rather than inventing a destructive revert that would itself damage newer valid market truth:

- **Pre-change evidence** (§4.B) is captured and Git/evidence-tracked before any mutation, establishing the
  exact starting state.
- **Exact deployed artefact identity** is recorded per §4.A before any repair/normalisation step runs against
  it.
- **Repair idempotence**: the governed backfill mechanism's own check-before-write behaviour means re-running
  it against an already-repaired state is a safe no-op for already-present objects — HELM may re-run the
  repair step if interrupted without risk of duplicate/conflicting writes.
- **Non-overwrite behaviour**: no existing valid H4 object (survivor or newly-repaired) is ever overwritten
  by a subsequent run of this same mechanism.
- **Conflict handling**: a write attempt against an object that already exists with *different* content than
  the deterministic recompute halts/fails loudly per the existing governed mechanism — this is itself the
  safety boundary, not a condition to work around.
- **No destructive rollback exists or is authorised**: because the repair only ever adds previously-missing,
  deterministically-reconstructed history objects and only ever shortens (never lengthens) TTL on specific
  already-identified stale-regime objects, there is no scenario in which "reverting" would be correct —
  deleting a freshly-repaired, correctly-reconstructed H4 object would re-create the exact defect this WO
  exists to fix. If something goes wrong mid-run, the correct response is to **stop, capture post-incident
  evidence, and return to Central Architecture** — not to attempt an automated revert.
- **Deployment rollback** (reverting to the prior running image if the new deployment itself is unhealthy)
  uses whatever standard redeploy-prior-image mechanism this project's existing deployment tooling already
  provides — this WO does not invent a new deployment-rollback mechanism, only requires that the prior
  image/SHA be recorded (§4.B) so a standard redeploy is possible if needed.
- **Post-change evidence** (§4.E) is captured and Git/evidence-tracked after mutation, in the same shape as
  the pre-change evidence, so the two can be directly compared.
- **Abort conditions** (binding STOP triggers, beyond the general governance-chain STOP rule): fresh
  pre-mutation measurement (§4.B) materially differs from the expected defect shape without obvious
  explanation; any conflict-halt fires during repair; any required H1 source data for an expected-repairable
  bucket is found incomplete at execution time; deployed-version identity verification fails; any runtime
  proof item in §4.E cannot be established after a reasonable, bounded number of attempts.

## 7. UTC doctrine

All platform timestamps throughout this activation — pre/post-mutation evidence, deployment identity,
repair/normalisation provenance, runtime proof — use governed aware UTC. No naive `datetime.now()`, no
host-local timestamps, no SQL `NOW()`/`UTC_TIMESTAMP()`, no implicit timezone assumptions. H4 anchoring
follows the governed `H4_FROM_H1_NY1700_V1` policy exactly. Europe/London is display-only, never a durable
timestamp source. Any future HELM dispatch prompt against this WO must restate this doctrine explicitly, per
this project's "do not assume an operator remembers governance from another session" discipline.

## 8. Required evidence (for the future dispatch, not produced by this document)

A Git-tracked evidence record under `ops/evidence/WO-HELM-HERMES-INDICATOR-HISTORY-PRODUCTION-ACTIVATION-0001/`
containing: pre-mutation evidence (§4.B); exact repair actions taken (bucket-by-bucket, with before/after
state); exact normalisation actions taken (objects identified, TTL before/after, byte-identity proof);
post-mutation evidence in the same shape as pre-mutation; the full §4.E runtime proof; and the three-state
DoD record (code-in-main/deployed/fresh-payload) per PID §5, recorded separately as that section requires.

## 9. Disposition

**Preparation only.** No HELM dispatch has occurred. No production Redis, SQL, or deployment mutation has
occurred. This document establishes the governed scope, exclusions, rollback/safety model, and evidence
requirements a future HELM dispatch must operate within — it does not itself authorise that dispatch.
Returned to Central Architecture for review and acceptance before any HELM dispatch.
