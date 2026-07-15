# HERMES PH2 — Recovery-Proposal Publication Eligibility & Fail-Closed Contract

WO-HELM-HERMES-PH2-RECOVERY-PROPOSAL-PUBLICATION-CONTRACT-DESIGN-0001 · base `a798fbc4` · **DESIGN-ONLY**

> This document designs *when* and *how* a bounded HERMES recovery proposal may be published from the in-memory planner
> holder to an external HERMES-owned contract, and — more importantly — *when it must be refused*. It authorises **no**
> runtime publication, planner activation, policy install, execution, deploy or live-state mutation. No publisher runner is
> added; no Redis application key is written; no gate is installed.

## 0. Position in the programme

| Stage | State |
|---|---|
| Pure planner (PR#93) | deployed, deterministic DRY-RUN-ONLY |
| Wiring + digest hardening (PR#95/#96) | merged `a798fbc4`, deployed **dark/inert** |
| Controlled runtime invocation | proven READY→HELD, rolled back, R2D2-audited |
| **This WO** | design the *publication contract* only |
| Future | eligibility validator → schema → dark publisher → audits → controlled publish → activation |

The planner today retains **one proposal in process memory** and publishes nothing (frozen decision 8). This WO defines the
*external contract* that a future, separately-audited publisher runner would emit — and the fail-closed law governing it.

## 1. Core doctrine (the five laws)

1. **Publication is advisory market-data-recovery truth, never execution authority.** A consumer may read a proposal to
   *understand* what recovery HERMES would need; it may never treat publication as approval to act. (§8, executor stays a
   separate WO.)
2. **Eligibility is a single fail-closed predicate re-evaluated every cycle.** A proposal is publishable only when *every*
   condition in the eligibility matrix is true against *live* inputs. Absence, staleness, drift, or doubt → **refuse**.
   Existence of a proposal object is *never* sufficient. (§5.)
3. **The current pointer is TTL-bounded and self-neutralising.** Live truth carries a TTL; if the publisher stops
   revalidating, the pointer expires and consumers see "no current proposal" — which is *correct*, not a fault. No permanent
   key holds live truth. (§12.)
4. **A new planner result creates a new immutable generation; historical truth is never mutated in place.** Supersession and
   revocation are explicit, monotonic, and atomic. A failed/stale proposal can never silently transition back to current. (§7, §15.)
5. **Restart re-establishes eligibility from scratch.** No in-memory proposal survives a process restart; no external key is
   trusted as current after a publisher restart without full revalidation. (§14.)

## 2. Ownership & boundary (§4)

HERMES owns the recovery-proposal contract end to end: writer, schema, envelope, keys, TTL, supersession. It consumes only
existing HERMES-owned truth (planner holder, gaps, coverage index, policy, closures). It imports **no** code from ARES,
Falcon, HELIOS, NEO, SOLO, PLUTUS, ARGUS or legacy recovery modules. No shared cross-application wrapper. There is **no
currently-authorised consumer** (confirmed by source search); the contract is designed consumer-agnostic and Falcon is **not**
named a consumer. A future consumer must be granted explicitly in the HERMES blueprint.

## 3. Layered separation (never combine)

```
  ┌───────────────────────┐   pure planner (PR#93) — deterministic proposal object (in-memory)
  │  planner holder        │   [existing, unchanged]
  └──────────┬─────────────┘
             │  read-only snapshot of holder + live inputs
  ┌──────────▼─────────────┐   PURE ELIGIBILITY VALIDATOR  (future WO-1)
  │  eligibility validator  │   deterministic (proposal, live inputs, config) -> ELIGIBLE | REFUSE(code)
  └──────────┬─────────────┘   NO I/O. NO Redis. NO clock sampling beyond an injected cycle clock.
             │  verdict + bounded envelope
  ┌──────────▼─────────────┐   PUBLISHER RUNTIME ADAPTER   (future WO-3, dark)
  │  publication adapter    │   gate-first; atomic current-pointer write; TTL; supersession; refusal handling
  └──────────┬─────────────┘   the ONLY component that touches Redis. Writes only publication keys.
             │  bounded envelope (Redis)                 (durable audit -> SQL, future WO)
  ┌──────────▼─────────────┐
  │  consumer (future)      │   validates version/schema/instrument/status/eligibility/expiry/flags; fails closed
  └────────────────────────┘
```

Four concerns are kept **structurally distinct** and are **never** merged for convenience:
(a) the **proposal contract** (this design); (b) **publication-component health** (minimal, §19); (c) **planner health /
held-counter** (deferred to a separate WO — R2D2's process-local finding); (d) the **executor** (separate WO, separate
authorisation). Publication never implies execution.

## 4. Eligibility predicate (summary — full matrix in `eligibility_matrix.md`, §5)

`publish(proposal) ⟺ AND(all conditions)`, evaluated **fresh each cycle** against **live** inputs:

- **Gate layer**: publisher feature enabled ∧ publication authorised ∧ contract version authorised (publication gates are
  *separate* from planner gates — §17).
- **Provenance layer**: planner version authorised ∧ policy present/valid/version-matched/**digest-matched to the proposal** ∧
  gaps contract present/version-authorised/**fresh**/**semantic-digest-matched** ∧ coverage present/**fresh**/
  **recovery-relevant-digest-matched** ∧ closure model complete for scope.
- **Integrity layer**: snapshot pre/post consistency passed ∧ no `BLOCKED_INPUT_INCONSISTENCY` ∧ no failed/exhausted retry ∧
  canonical instrument `XAU_USD` with no alias ambiguity (no `XAUUSD`) ∧ within retention ∧ gap end=start+period invariant
  holds (§21) ∧ no unresolved exceptional closure in scope (§20) ∧ (no unclassified segment unless governed).
- **Currency layer**: proposal is the *current holder* ∧ not superseded ∧ not stale ∧ not expired ∧ no newer proposal or
  revocation exists ∧ publication generation monotonic ∧ envelope schema validates.

### 4.1 Is a `HELD` holder publishable? (§5 ruling)

**Ruling:** `READY` is an *internal planner transition*, not by itself sufficient to publish. A held proposal (the planner's
`PROPOSAL_HELD`) MAY remain **externally current** — but ONLY through `HELD_CURRENT`, which requires the publisher to
**re-run the full eligibility predicate against live inputs on every revalidation cycle**. The holder existing is necessary,
never sufficient. TTL renewal is *gated on* successful revalidation (§12). This directly encodes law 2.

## 5. Publication vs execution separation (§8)

The published envelope carries explicit, machine-checkable safety flags that a consumer MUST verify:

```
"execution_authorised": false,   "execution_started": false,
"publication_only": true,        "executor_bound": false,
"consumer_live": false,          "backfill_executed": false,   "repair_executed": false
```

The contract structurally cannot trigger execution: the publisher adapter has **no** import path to any executor / recovery
job / queue / progress / vendor / candle-writer / D1-seed / repair, and never changes PH2 gaps or backfill status to an
executing state. Any consumer that reads `publication_only=true` and still acts is in violation; the contract makes that
violation explicit and auditable. A future executor requires a *separate* contract + authorisation.

## 6. What is published vs never published (§11)

**Published (bounded Redis summary):** the versioned envelope (`contract_envelope.md` / JSON Schema) + a **bounded proposal
summary** — proposal id, generation, per-class counts (segments, deferred, unclassified, intentionally-unavailable),
estimated request units, and a **capped** list of segment descriptors (timeframe + half-open UTC interval + class), capped at
`max_published_segments` with an overflow marker; never the unbounded segment set. Detail beyond the cap belongs in durable
SQL audit (future, §22), referenced by digest — never inlined unboundedly.

**Never published:** secrets, credentials, API tokens, private keys, raw environment, unbounded logs, exception traces, raw
vendor/market datasets, or anything that would let a reader reconstruct execution authority. Payload is size-capped;
`PUB_PAYLOAD_TOO_LARGE` refuses oversize rather than truncating silently.

## 7. Redis contract (summary — full spec in `redis_contract.md`, §9)

**Smallest safe surface = two keys**, both HERMES-owned, TTL-governed, `XAU_USD`-scoped, `v1`:

| Key | Role | Type | TTL | Writer |
|---|---|---|---|---|
| `hermes:recovery_proposal:XAU_USD:v1` | **current pointer** — the single externally-current envelope (or absent) | string(JSON) | **TTL-bounded** (`proposal_ttl_seconds`) | publisher (single-writer) |
| `hermes:recovery_proposal:status:XAU_USD:v1` | **status/revocation record** — last publication decision incl. refusals + tombstones | string(JSON) | short TTL | publisher (single-writer) |

Rejected for the initial surface: a separate immutable per-generation key (unbounded history risk → SQL instead) and a
publication-health key (defer to publication-health boundary §19 unless the consumer contract strictly requires it — it does
not for the initial dark stage). Rationale + envelopes in `redis_contract.md`. **No unbounded proposal history in Redis.**

## 8. Fail-closed everywhere (restart, drift, publisher failure) — §12/§14/§18

- **Missing/expired current pointer** ⇒ consumer sees "no current proposal" ⇒ treats as *nothing to consume* (never as
  current). A stale holder is never silently republished to renew TTL; TTL renewal *requires* full revalidation.
- **Input drift** (policy/gaps/coverage/closure/planner-version change, or digest mismatch) ⇒ the current pointer is either
  superseded by a fresh generation or actively revoked (tombstone) — never left apparently-current.
- **Publisher failure** (Redis unavailable, atomic write failed, schema invalid, oversize, duplicate/stale writer) ⇒
  component-level fault only (typed, **never** `SystemExit`, never process-wide shutdown); the pointer expires by TTL so the
  external truth self-neutralises. Faults are enumerated with stable codes and a defined effect (`fault_code_catalogue.md`).
- **Restart** ⇒ no in-memory proposal survives; a retained external pointer is **not** trusted — the publisher must re-derive
  a fresh generation from a live holder + live inputs, and until it does, the stale pointer expires by TTL (short enough that
  cold-start staleness is bounded — `restart_cold_start.md`).

## 9. Artefact index

| # | Artefact | File |
|---|---|---|
| 1 | Architecture (this) | `architecture.md` |
| 2 | Eligibility matrix | `eligibility_matrix.md` |
| 3 | State-transition table | `state_transition_table.md` |
| 4 | Fault-code catalogue | `fault_code_catalogue.md` |
| 5 | Redis contract spec | `redis_contract.md` |
| 6 | JSON Schema | `../../../schemas/recovery_proposal/recovery_proposal_publication.v1.schema.json` |
| 7-10 | Fixtures (eligible/stale/revocation/invalid) | `../../../fixtures/recovery_proposal/` |
| 11 | Gate truth table | `gate_truth_table.md` |
| 12 | TTL & freshness | `ttl_freshness.md` |
| 13 | Restart / cold-start | `restart_cold_start.md` |
| 14 | Supersession & atomicity | `supersession_atomicity.md` |
| 15 | Threat model | `threat_model.md` |
| 16 | Consumer validation | `consumer_validation.md` |
| 17 | SQL audit design / deferral | `sql_audit_design.md` |
| 18 | Implementation decomposition | `implementation_decomposition.md` |
| 19 | Activation / rollback / audit plan | `activation_rollback_audit_plan.md` |
| — | Publication-health boundary | `publication_health_boundary.md` |
| — | Configuration doctrine | `configuration_doctrine.md` |
| — | Test matrix | `test_matrix.md` |
| 20 | ADRs | `../../adr/publication/ADR-PUB-0001..0010.md` |

## 10. Explicit non-goals (kept separate)

Planner-health / held-counter publication; exceptional-closure resolution; gap end-epoch invariant *fix*; executor / recovery
job / backfill / repair; SQL DDL execution; any live gate or configuration install. Each is a distinct future WO.
