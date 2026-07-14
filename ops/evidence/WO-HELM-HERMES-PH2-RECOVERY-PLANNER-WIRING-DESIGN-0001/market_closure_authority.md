# Market-Closure Authority (DESIGN-ONLY)

## Taxonomy
1. Regular instrument-session closure — HERMES-owned deterministic schedule (policy regular_weekend_closure_utc).
2. Exceptional holiday — separately governed EXTERNAL evidence only; never guessed from a date.
3. Ad-hoc market closure — governed external evidence.
4. Broker-specific maintenance outage — governed external evidence; distinct provenance.
5. Unknown / unverified — remains UNCLASSIFIED_MARKET_STATE (warn), never silently excluded.

## Preserved doctrine
- XAU_USD regular closure: Friday 22:00 UTC -> Sunday 22:00 UTC.
- D1 anchor 22:00 / NY5PM; 00:00 is a boundary defect (BLOCKED_D1_BOUNDARY), never a recovery segment.
- No guessed holiday; uncertain intervals unclassified; no vendor call from the pure planner; no ARES runtime dependency;
  no cross-application code import.

## Authority model
- HERMES OWNS the deterministic regular schedule.
- Exceptional closures are governed external evidence with `authority` in the policy's accepted set; below-threshold authority
  is NOT excluded (warn + UNCLASSIFIED).
- If ARES (or another app) later publishes a market-event contract, HERMES may consume ONLY an explicit external contract after
  cross-application governance — NEVER by importing ARES code and NEVER by assuming ARES behaviour. (ADR-0004.)

## Contract fields
source, authority, provenance, contract_version, classification, closure_interval, effective_at_utc, staleness.
Conflict handling: governed authority wins; ties -> warn + UNCLASSIFIED. Fallback: regular schedule only.
Blocking vs warning: missing regular schedule -> BLOCKED_POLICY; conflicting governed evidence -> BLOCKED_CLOSURE_TRUTH;
unverified -> warning + UNCLASSIFIED.
