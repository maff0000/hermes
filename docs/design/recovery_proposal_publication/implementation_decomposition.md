# Implementation Decomposition into Future WOs (§27)

Bounded, independently-auditable WOs (each its own branch/PR/cold-audit; nothing here implements runtime publication):

1. **WO-PUB-1 — Pure eligibility validator** (code-only) — IMPLEMENTED in PR (WO-...-ELIGIBILITY-VALIDATOR-0001); NOT merged, NOT operational: deterministic `(proposal, live-input snapshots, config) -> ELIGIBLE
   | REFUSED(code)`; no I/O; unit-tested against the full test matrix; the eligibility matrix encoded exactly.
2. **WO-PUB-2 — Contract schema + fixtures hardening** (code-only): promote this schema/fixtures; consumer-side validator lib
   (HERMES-owned); additive-evolution rules.
3. **WO-PUB-3 — Publisher runtime adapter, DARK + INERT** (code-only): gate-first (publication gates), single-writer fence,
   atomic K1+K2 CAS, TTL, supersession/revocation, refusal handling; append-on-ENABLED; NO gate installed; NO key written live.
4. **WO-PUB-4 — R2D2 cold audit** of PUB-1..3 PR.
5. **WO-PUB-5 — Merge** (merge-only, exact head).
6. **WO-PUB-6 — Deploy dark** (publication gates absent; zero-read; zero-publication).
7. **WO-PUB-7 — R2D2 deploy-dark audit**.
8. **WO-PUB-8 — Controlled publication to an ISOLATED test Redis namespace** (separately authorised; never the live contract
   key); prove eligible-publishes / refusals / supersession / TTL expiry / revocation; then roll back.
9. **WO-PUB-9 — Rollback** to dark.
10. **WO-PUB-10 — R2D2 controlled-publication audit**.
11. **WO-PUB-11 — SQL audit sink** (append-only) — MANDATORY before production activation; may land between PUB-8 and PUB-12.
12. **WO-PUB-12 — Production contract activation** (only after a consumer is authorised in the blueprint).
13. **WO-PUB-13 — Independent post-activation audit**.

**SQL placement ruling:** the SQL audit sink (PUB-11) is NOT required for the isolated publication proof (PUB-8) but IS
mandatory before production activation (PUB-12). Kept separate from planner-health, exceptional-closure and executor WOs.
