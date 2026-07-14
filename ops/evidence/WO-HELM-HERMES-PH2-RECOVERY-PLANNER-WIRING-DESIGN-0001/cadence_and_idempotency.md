# Cadence & Idempotency (DESIGN-ONLY) — DECISION

Gaps key refresh cadence today: 60s (DEFAULT_INTERVAL_SECONDS). Planner deterministic proposal_id already excludes the clock.

## DECISION: hybrid, digest-gated
Runner ticks on the supervisor interval; recompute ONLY when the idempotency tuple changes:
  (gaps_payload_digest, coverage_snapshot_digest, closure_snapshot_digest, policy_digest, planner_version)
Unchanged tuple -> no work; return cached status. This prevents continuous recomputation of an identical proposal.

## Addressed
- duplicate proposals: prevented by digest short-circuit + deterministic proposal_id.
- unchanged source input: no work.
- CPU / Redis read load: bounded to one GET set per changed tick.
- startup delay: initial readiness wait for gaps key.
- market-open vs closed: closed + nothing-to-plan -> skip recompute.
- race/concurrency: single in-process mutex; no concurrent invocation; only one runner instance.
- long-running call: bounded by planner workload limits.
- restart recovery: recompute from live inputs (do not trust prior proposal).
- debounce/rate-limit: coalesce rapid gaps refreshes.

Idempotency tuple is the canonical dedup key; identical tuple => identical proposal (minus generated_at_utc).
