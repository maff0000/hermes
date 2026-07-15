# HERMES PH2 Recovery-Planner — Recovery-Relevant Digest & Snapshot Recheck Hardening

WO-HELM-HERMES-PH2-RECOVERY-PLANNER-DIGEST-HARDENING-0001 · base `115a19c` · CODE-ONLY

> This is a narrowly scoped hardening of the PLAN-ONLY runtime wiring. The pure planner's recovery logic, the proposal and
> policy schemas, the retention doctrine, the caller model, the gate behaviour, the output disposition, the publication
> boundary and the executor boundary are **UNCHANGED**. No merge, no deploy, no policy install, no gate activation.

## Why

The first controlled in-memory invocation proved the planner deterministic and non-publishing, but R2D2 observed that the
planner recomputed roughly every 60 seconds. Cause: the invocation-idempotency tuple's coverage element was the **whole-window**
coverage presence digest. Every current-edge candle that sealed (and every retention-window advance) changed that digest, so
the tuple changed and the planner re-ran — even though the actual recovery workload was identical. Safe, but wasteful, and it
did not provide recovery-relevant idempotency.

## Invariant (D)

> The semantic invocation tuple `(gaps digest, RECOVERY-RELEVANT coverage digest, closure digest, policy digest, planner
> version)` changes **when and only when** a material input change can alter planner status, proposed / deferred / unclassified
> / intentionally-unavailable segments, workload cost, proposal identity, or a blocking fault.

> **Live candle advancement outside recovery-relevant gap scope must not trigger a new planner invocation.**

## Recovery-relevant coverage digest (A)

`recovery_relevant_scope(gaps, coverage, now, merge_threshold_seconds)` → `recovery_relevant_coverage_digest(...)`.

Per timeframe **that has a retained gap**:

- **retained gap intervals** — the gap's missing intervals clipped to the timeframe retention floor
  (`now − RETENTION_DAYS[tf]`); intervals wholly beyond retention are dropped (not recoverable → irrelevant).
- **relevant coverage** — coverage intervals **intersecting or adjacent** to those retained gaps. Adjacency half-width is
  `max(one period, merge_adjacent_threshold_seconds)` each side: one period captures a boundary candle that changes clipping;
  the merge threshold captures any covered candle that could change whether two gap segments merge. A wider window can never
  **hide** a material change, so the scope stays provably complete.
- **authority** — the coverage `provenance` (which encodes the retention classification) **and** `contract_version`, so a
  source-authority / interpretation / version change changes the digest.

### Included material fields
retained gap intervals · gap-relevant coverage intervals (intersecting/adjacent) · coverage completeness within those gaps ·
retention classification of those gaps · coverage provenance/authority · coverage contract version · planner version · policy
digest · closure digest · semantic gaps digest.

### Excluded volatile fields
gaps `generated_at_utc` / observed-at · coverage snapshot/observation timestamp · TTL · publisher refresh metadata ·
Redis retrieval / sorted-set iteration order · dictionary insertion order · current-edge candles outside every relevant window ·
sliding-window movement outside relevant gaps.

### Behaviour
| Change | Digest |
|---|---|
| New candle **outside** all relevant gap windows | unchanged |
| Sliding-window movement outside relevant gaps | unchanged |
| Volatile refresh timestamps only | unchanged |
| Candle that fills / partially fills a relevant gap | **changes** |
| Coverage removal / completeness change affecting a gap | **changes** |
| Retention-boundary movement that reclassifies a gap | **changes** |
| Coverage provenance / authority / contract-version change | **changes** |
| Any material gap state / interval / boundary change (via gaps digest) | **changes** |

The pure planner still receives the **full** retention-bounded coverage snapshots (its output is unchanged); only the
recompute trigger is scoped.

## Canonicalisation (B, C)
- **Gaps** (`_gaps_semantic_digest`): canonical timeframe keys (`sort_keys`), explicit `gap_state`, deterministically sorted +
  **deduplicated** missing-open epochs, UTC epoch endpoints; excludes generated/observed/TTL/refresh metadata; independent of
  dictionary and retrieval order.
- **Coverage**: deterministic timeframe order, deduplicated + sorted intervals, no Redis retrieval / sorted-set order
  dependence, no volatile observation timestamp / TTL; `now` enters only through retention clipping, using the one cycle clock.

## Pre/post consistency (F, G) and acquisition order (H)
One exact order per candidate cycle:

1. evaluate gates · 2. read+validate policy, capture identity (`_content_sha` of the exact bytes) · 3. gaps A ·
4. derive retained gap scope · 5. recovery-relevant coverage A · 6. closures · 7. gaps B · 8. **policy recheck B** ·
9. recovery-relevant coverage B · 10. compare all material identities · 11. bounded retry or block · 12. build tuple ·
13. hold or invoke.

- **Policy** (F): identity captured **before** input coordination and re-read **before** planner invocation. Atomic
  replacement, content-digest change, version/instrument change, invalid replacement and file removal are all detected →
  `BLOCKED_INPUT_INCONSISTENCY`. **No stale last-known-good fallback.**
- **Coverage** (G): compared on the **recovery-relevant** digest, so an unrelated current-edge advance does **not** force a
  retry; a gap-relevant change causes a **bounded** retry (`SNAPSHOT_MAX_RETRIES + 1` attempts); a perpetual relevant change
  ends in `BLOCKED_INPUT_INCONSISTENCY`. The re-read is scoped — never an unbounded history re-read.

## One cycle clock (I)
A single injected UTC `now` drives freshness, retention boundaries, closures, relevant-scope derivation, the semantic digest
and the pure-planner call. Wall-clock is never independently re-sampled within a cycle; monotonic time is used only for
durations. Unrelated wall-clock progression does not destabilise the digest; a retention-boundary change that reclassifies a
gap does.

## Proposal-ID stability (J)
Unchanged and re-tested: the pure planner derives `proposal_id` from proposal semantics (instrument, policy digest, status,
segments) — never from cycle number, invocation/retrieval/refresh/runner time. Semantically identical inputs across separate
cycle times produce the same `proposal_id`.

## Idempotency & accounting (E, K)
- First valid tuple invokes the planner; an identical material tuple returns `PROPOSAL_HELD` with **no** pure-planner call.
- A material gap / coverage / policy / closure / planner-version change invokes exactly once.
- A failed invocation does **not** poison a prior good hold and is never marked successful (`PLANNER_INVOCATION_FAILED`).
- A blocked cycle clears the holder; restart recomputes (in-memory state is intentionally not durable); concurrent identical
  cycles are suppressed (`SKIPPED_CONCURRENT`).
- Process-local counters, written in structured summary logs (not published): `runner_cycle_count`, `snapshot_attempt_count`,
  `planner_invocation_count`, `proposal_ready_count`, `proposal_held_count`, `blocked_count`, `failure_count`. "Cycle" is no
  longer used ambiguously — a runner cycle is one supervisor tick; a planner invocation is one pure-planner call.

## Performance expectation (N)
Given ≥5 cycles where unrelated current-edge candles advance and generated timestamps refresh but gap-relevant gaps and
coverage are unchanged: **1** planner invocation, **≥4** held cycles.

## Historical cycle-count correction (K)
The first controlled invocation: the HELM summary said "four cycles"; the captured logs proved **three** `PROPOSAL_READY`
planner invocations, and R2D2 accepted **three**. The accepted historical count is **three**. Historical evidence is not
rewritten; the counter semantics above remove the ambiguity going forward.

## Unchanged boundaries (L, M)
Process-local proposal holder only · no Redis proposal/health key · no SQL / disk / message-bus / external consumer · no
executor / backfill / repair · no new write path · strict separation from `utils.recovery_planner`, `utils.recovery_executor`,
`RecoveryLibrary` · `consumer_live=false`.
