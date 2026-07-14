# ADR-0009: Legacy recovery subsystem separation
Context: pre-existing utils/recovery_planner.py + recovery_executor.py must not entangle the new planner.
Decision: strict separation (fully-qualified imports, no shared classes/gates/keys/config/executor, no fallback/handoff);
later isolate the legacy subsystem into a legacy package + deprecate via a SEPARATE discovery/removal WO.
Alternatives: reuse legacy classes (rejected); remove now (rejected: still referenced by main.py startup path).
Consequences: zero coupling; clear future cleanup path.
Risks: naming confusion -> mitigated by fully-qualified imports + this ADR. Rollback: n/a. Unresolved: legacy live-usage audit.
