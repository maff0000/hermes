# Market-Closure Authority (DESIGN-ONLY) — Option A FROZEN

## Binding decision (first wiring implementation)
Consume ONLY the HERMES-owned deterministic regular XAU_USD schedule: Friday 22:00 UTC -> Sunday 22:00 UTC.
NO ARES exceptional-closure data, NO vendor holiday calendar, NO broker maintenance calendar, NO manually inferred holiday,
NO ungoverned exceptional source. NO ARES import, NO ARES contract assumption, NO vendor call.

## Taxonomy
1. Regular instrument-session closure — HERMES-owned deterministic schedule (policy regular_market_schedule) -> INTENTIONALLY_UNAVAILABLE.
2. Exceptional holiday / 3. Ad-hoc closure / 4. Broker maintenance / 5. Unknown-unverified — WITHOUT accepted governed evidence
   -> UNCLASSIFIED_MARKET_STATE. Not guessed, not silently discarded, preserved for review.

## Blocking behaviour
An unresolved exceptional-closure intersection with proposed segments -> status BLOCKED_UNCLASSIFIED_MARKET_STATE; the affected
planning scope CANNOT become PROPOSAL_READY. Regular closure is deterministic and excluded as INTENTIONALLY_UNAVAILABLE.

## Preserved doctrine
XAU_USD regular Fri22->Sun22 UTC; D1 anchor 22:00/NY5PM; 00:00 boundary defect; no guessed holiday; no vendor; no ARES.

## Future exceptional-closure transport (separate WO)
WO-HERMES-PH2-EXCEPTIONAL-MARKET-CLOSURE-CONTRACT-DESIGN-0001 decides: contract ownership; HERMES vs external publisher
responsibility; schema; authority; provenance; freshness; conflict resolution; transport; fallback; cross-application governance.
NOT decided in the wiring implementation WO. (ADR-0004.)
