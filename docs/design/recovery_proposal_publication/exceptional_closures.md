# Exceptional-Closure Fail-Closed Ruling (§20)

Exceptional-closure truth remains unresolved (the planner blocks affected scope with `BLOCKED_UNCLASSIFIED_MARKET_STATE`
under Option A). This design does NOT invent exceptional closures and adds NO ARES dependency.

Publication ruling — **fail closed**: a proposal is ineligible (`PUB_UNRESOLVED_EXCEPTIONAL_CLOSURE`, no key) whenever its
scope intersects:
- an unresolved exceptional closure (gaps `MARKET_CLOSED` outside the governed weekend), or
- an unknown/unclassified closure interval, or
- conflicting closure sources.

Only proposals whose scope is fully explained by governed regular (weekend) closures are eligible. A **future** closure
contract (separately architected/authorised) may supply governed exceptional-closure truth; it would be consumed as an
additional eligibility input (a new digest in the tuple) **without changing** the fail-closed doctrine: unknown ⇒ refuse.
