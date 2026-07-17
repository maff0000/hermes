# Phase-2 acceptance + Phase-3 readiness

## Phase-2 acceptance (architecture_phase2_v1 §26) — DESIGN-satisfied
1 read-only; current authority unchanged. 2 no shadow reconnect emittable (mechanical §16). 3 July-16-equiv ->
proposal-only / SHADOW_DENIES_CURRENT_RECONNECT. 4 WTICO/SPX fail-loud, no vote. 5 genuine faults ->
RECONNECT_AUTHORISED. 6 callback dups bounded. 7 heartbeat/shared-progress/parser provenance-backed. 8 missing
evidence -> INCOMPLETE/fail-closed. 9 deterministic comparison. 10 snapshot offline-replayable. 11 perf within
budget. 12 no false GREEN. 13 no Redis/SQL unless separately approved (JSONL chosen). 14 no new runner (none added).
15 consumer_live=false (validated constant).

## Phase-3 readiness (§27) — NOT pre-authorised
Soak >= N daily transitions; >= M evaluations; evidence-complete % >= threshold; divergence taxonomy counts
reviewed; ZERO shadow side effects; July-16 closure success; genuine-fault fixture success; latency within budget;
callback-bound proof; no regression; rollback proof; R2D2 cold audit. Phase 3 executor cutover is a SEPARATE
signed-off WO behind a rollback flag. This design does NOT authorise Phase 3.
