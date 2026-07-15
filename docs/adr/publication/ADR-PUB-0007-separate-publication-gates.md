# ADR-PUB-0007 — Publication gates are separate from planner gates; fail closed; component-level
Status: Accepted. Decision: HERMES_RECOVERY_PUBLISHER_ENABLED/AUTHORISED distinct from planner gates; enabled-without-
authorised is a typed component fault (never SystemExit); missing/malformed ⇒ disabled. Planner enablement is not publication
authority.
