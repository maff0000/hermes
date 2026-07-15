# ADR-PUB-0004 — Publication is structurally separate from execution
Status: Accepted. Decision: const-locked safety flags (execution_authorised=false, publication_only=true, executor_bound=false,
consumer_live=false); publisher has no executor/job/queue/backfill/repair path. Executor is a separate WO + authorisation.
