# Coverage Completeness Analysis (DESIGN-ONLY) — the central honesty risk

## Problem
The governed candle history is a ROLLING RETENTION WINDOW (M1-H4 = 35 days, D1 = 120 days). It is NOT a complete historical
record. If used raw as ExistingCoverageSnapshot, the planner would compute residual "gaps" for pre-retention periods that are
merely aged out — inventing recovery work that is neither needed nor recoverable.

## Decision
1. BOUND the planning window to the governed retention window. Plan only within retention, where the governed history index
   (maintained by the forward writer + governed backfill) is authoritative-enough for PRESENCE.
2. Represent coverage with an explicit `completeness="RETENTION_BOUNDED"` marker + `retention_window` + provenance so no
   downstream consumer can mistake it for full-history completeness.
3. Anything older than retention is OUT_OF_RETENTION — already the gaps surface doctrine — and is EXCLUDED, never planned.
4. D1 history floor comes from policy `d1_history_floor_seconds` (governed, ~120d). Within floor: recovery candidate. Beyond:
   excluded.
5. M1-H4: within 35d retention the index is sufficiently complete for safe within-window planning; beyond it, insufficient —
   DO NOT PLAN and DO NOT claim completeness.

## Do NOT
- Do not let a partial retention window masquerade as complete coverage.
- Do not silently treat aged-out candles as recoverable gaps.
- Do not invent completeness.

## Recommendation for beyond-retention
If planning beyond retention is ever required, author a SEPARATE coverage-truth surface WO that establishes historical
completeness from an authoritative store (e.g. a governed archive), with its own contract, provenance and freshness. This
design explicitly refuses to fabricate it. (ADR-0003.)
