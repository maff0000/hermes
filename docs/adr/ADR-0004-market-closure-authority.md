# ADR-0004: Market-closure authority
Context: closures must not become recovery targets; holidays must not be guessed; ARES must not be imported.
Decision: HERMES owns the deterministic regular schedule (policy); exceptional closures are governed external evidence only;
unverified -> UNCLASSIFIED (warn); if ARES publishes a market-event contract, consume ONLY via explicit cross-app governance.
Alternatives: hardcode holiday calendar (rejected); vendor calendar (rejected: vendor call); import ARES (rejected).
Consequences: deterministic weekend + governed exceptional evidence; honest uncertainty.
Risks: missing exceptional evidence -> a closed period may appear as a gap -> surfaced honestly, not auto-recovered.
Rollback: n/a. Unresolved: exceptional-closure evidence source (ARES contract vs HERMES-owned).
