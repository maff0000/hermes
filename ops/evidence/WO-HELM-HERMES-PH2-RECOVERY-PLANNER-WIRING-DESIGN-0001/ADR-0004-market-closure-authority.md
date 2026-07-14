# ADR-0004: Market-closure authority (Option A — FROZEN)
Context: closures must not become recovery targets; holidays must not be guessed; ARES must not be imported.
Decision: the FIRST wiring implementation consumes ONLY the HERMES-owned deterministic regular schedule (Fri22->Sun22 UTC).
Exceptional/suspected closures without accepted governed evidence -> UNCLASSIFIED_MARKET_STATE; an unresolved exceptional
intersection blocks the affected scope (BLOCKED_UNCLASSIFIED_MARKET_STATE) and cannot become PROPOSAL_READY.
Rejected alternatives: hardcode holiday calendar; vendor calendar; import ARES; assume an ARES contract in the first impl.
Consequences: deterministic weekend handling; honest blocking of unresolved exceptional periods; zero ARES/vendor dependency.
Failure behaviour: unresolved exceptional -> blocking status, preserved for review; never silently discarded or guessed.
Reversal path: exceptional automatic classification only after a separate governed contract exists.
Follow-on WO: WO-HERMES-PH2-EXCEPTIONAL-MARKET-CLOSURE-CONTRACT-DESIGN-0001.
