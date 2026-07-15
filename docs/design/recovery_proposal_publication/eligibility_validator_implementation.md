# Pure Eligibility Validator — Implementation (WO-PUB-1)

WO-HELM-HERMES-PH2-RECOVERY-PROPOSAL-PUBLICATION-ELIGIBILITY-VALIDATOR-0001 · base `0ef84a6` · **pure, non-operational**.

> This module determines ELIGIBILITY ONLY. It does not publish, persist, revoke, execute or mutate runtime state.

## Module
`utils/hermes_recovery_proposal_publication_eligibility_v1.py`

## Public API
- `validate_publication_eligibility(candidate, context, config, now_utc) -> EligibilityDecision` — the pure entry point.
- Immutable models: `CandidateProposal`, `ValidationContext`, `PublicationConfig`, `GapInterval`, `EligibilityDecision`.
- `ModelConstructionError` — raised ONLY at construction for structurally impossible input (naive/non-UTC datetime,
  expires ≤ generated, unknown enum). Never used for governed refusals.
- 33 `PUB_*` constants + `ALL_PUB_CODES` + `CODE_ORDER`; recommendation constants `REC_ELIGIBLE`,
  `REC_REFUSE_NO_CURRENT_KEY`, `REC_REFUSE_AND_NEUTRALISE_PRIOR_CURRENT`.

## Pure boundary
Standard library only. No Redis/SQL/HTTP/socket/subprocess/shell/file-write/env-read/policy-load/logging/singleton/thread.
`now_utc` is injected — the validator never samples the clock. Deterministic: identical inputs ⇒ identical decision.

## Fail-closed AND-predicate (layers, in evaluation/priority order = refusal ordering §10)
1. contract + envelope structure (`PUB_UNSUPPORTED_CONTRACT_VERSION`, `PUB_SCHEMA_VALIDATION_FAILED` incl. const-locked
   safety flags) → 2. gates (`PUB_GATES_DISABLED`/`PUB_GATE_MISMATCH`) → 3. canonical identity
   (`PUB_NON_CANONICAL_INSTRUMENT`, `XAUUSD` always refuses) → 4. holder/state (`PUB_NO_CURRENT_PROPOSAL`,
   `PUB_PROPOSAL_STATUS_INELIGIBLE`, `PUB_BLOCKED`, `PUB_REVOKED`, `PUB_SUPERSEDED`, `PUB_PROPOSAL_EXPIRED`,
   `PUB_PROPOSAL_STALE`) → 5. policy (`PUB_POLICY_ABSENT/INVALID/CHANGED`, `PUB_UNSUPPORTED_PLANNER_VERSION`) →
   6. source presence+freshness (`PUB_GAPS_MISSING/STALE`, `PUB_COVERAGE_MISSING/STALE`) → 7. digest identity
   (`PUB_GAPS_DIGEST_MISMATCH`, `PUB_COVERAGE_DIGEST_MISMATCH`) → 8. closure (`PUB_CLOSURE_INCOMPLETE`,
   `PUB_UNRESOLVED_EXCEPTIONAL_CLOSURE`) → 9. snapshot/integrity (`PUB_INCONSISTENT_SNAPSHOT`) → 10. retention
   (`PUB_OUT_OF_RETENTION`) → 11. payload/content (`PUB_UNCLASSIFIED_PRESENT`, `PUB_GAP_INVARIANT_VIOLATION`,
   `PUB_PAYLOAD_TOO_LARGE`) → 12. generation/fencing (`PUB_STALE_WRITER`, `PUB_DUPLICATE_PUBLISHER`,
   `PUB_REDIS_UNAVAILABLE`, `PUB_ATOMIC_PUBLICATION_FAILED`).
All failing codes are collected; the lowest-rank present is `primary_refusal_code`; `refusal_codes` is deterministically ordered.

## Key semantics
- **READY / HELD_CURRENT**: neither is auto-eligible; both require the full predicate. A held proposal is stale
  (`PUB_PROPOSAL_STALE`) if `now − last_validated_at > revalidation_max_age_seconds`.
- **Freshness ≠ sameness**: matching gaps/coverage digests do NOT waive freshness — stale `gaps_generated_at`/coverage refuses
  even when digests are identical.
- **UTC**: all datetimes tz-aware UTC (offset 0) or `ModelConstructionError`. Boundaries: age `≤ max` fresh, `> max` stale;
  `now ≥ expires` expired.
- **Gap invariant (§16/§21)**: if raw `scoped_intervals` supplied, assert `end − start == period(tf)`; else the caller's
  `gap_invariant_ok` must be explicitly `True` (None/False fail closed) ⇒ `PUB_GAP_INVARIANT_VIOLATION`.
- **Exceptional closure (§17)**: only `NONE_IN_SCOPE`/`RESOLVED` pass; `UNRESOLVED`/`UNKNOWN`/`CONFLICTING` ⇒
  `PUB_UNRESOLVED_EXCEPTIONAL_CLOSURE` (fail closed; no ARES dependency).
- **Generation/currency (§18)**: `proposal_generation` must be strictly greater than `current_generation` and
  `latest_published_generation`; equal/lower ⇒ `PUB_STALE_WRITER`. `superseded`/`newer_proposal_exists` ⇒ `PUB_SUPERSEDED`.
  The validator does NOT allocate generations or perform CAS — the adapter does.
- **Safety flags (§19)**: candidate must carry the const-locked safe values; any unsafe flag ⇒ `PUB_SCHEMA_VALIDATION_FAILED`.
  The validator confers no execution authority (`execution_authority=False` always).

## Output recommendation
`ELIGIBLE` (+ logical `expires_at_utc = now + proposal_ttl_seconds`) · `REFUSE_AND_NEUTRALISE_PRIOR_CURRENT` (input drift on a
current holder) · `REFUSE_NO_CURRENT_KEY` (nothing to neutralise). Plus `retryable`/`alertable` classification.

## Explicit non-capabilities
No Redis key, no envelope write, no generation increment, no persistence, no revocation, no publisher call, no TTL renewal.
The publisher adapter (separate audited WO) applies the recommendation.
