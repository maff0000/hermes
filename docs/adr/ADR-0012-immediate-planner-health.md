# ADR-0012: Immediate planner health (Option A — FROZEN)
Context: planner runtime health must be observable without overloading existing contracts.
Decision: the FIRST wiring implementation uses ONLY structured UTC logs + existing supervisor runner state + process-local
component status. NO Redis planner-health key; NO write into hermes:backfill:status:XAU_USD:v1; NO overload of feed_health/gaps/
backfill_status/instrument_catalogue. Logs are SUMMARY-ONLY (ids/counts), never full segment dumps.
Rejected alternatives: dedicated Redis health key now; control-plane telemetry now (both deferred).
Consequences: zero new Redis surface; safe observability. Failure behaviour: component fault visible in logs + supervisor state.
Reversal path: n/a. Follow-on WO: WO-HERMES-PH2-RECOVERY-PLANNER-HEALTH-CONTRACT-DESIGN-0001 (any future Redis health key).
