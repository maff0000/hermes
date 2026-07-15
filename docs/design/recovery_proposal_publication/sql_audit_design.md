# SQL Audit Model (§22) — DESIGN + DEFERRAL RULING

**No DDL/DML executed by this WO.** Ruling: **durable SQL audit is DEFERRED to a later WO and is NOT required for the initial
dark/isolated publication stage.** Rationale: at the dark stage there is no consumer and no live publication; the K2 status
record + structured logs provide sufficient short-horizon auditability. SQL becomes **mandatory before production activation**
(§27 step 11), so publication/refusal/supersession events are durably auditable beyond Redis TTL.

Proposed (future) append-only HERMES-owned table `hermes_recovery_proposal_audit` (HERMES SQL only; no cross-application write):

| Column | Notes |
|---|---|
| `audit_id` | PK, surrogate |
| `event_type` | enum: GENERATED, VALIDATED, PUBLISHED, REFUSED, SUPERSEDED, REVOKED, EXPIRED, WITHDRAWN |
| `proposal_id`, `proposal_generation` | |
| `instrument` | `XAU_USD` |
| `status`, `publication_eligibility`, `publication_reason_code` | |
| `policy_version`, `policy_digest` | |
| `gaps_semantic_digest`, `coverage_semantic_digest`, `closure_digest` | source identity |
| `contract_payload_digest` | sha256 of the published envelope (not the full payload) |
| `snapshot_consistency_status` | |
| `publisher_instance_id`, `source_commit`, `source_image` | |
| `generated_at_utc`, `validated_at_utc`, `published_at_utc`, `event_at_utc` | all UTC |

Doctrine: **append-only** (no UPDATE/UPSERT); idempotency via unique `(proposal_id, proposal_generation, event_type)`;
UTC-only; retention per governance; migrations append-only and fail-loud on re-apply; **no cross-application writes**.
When SQL becomes mandatory it is added as its own WO **before** any controlled publication that a consumer could act on.
