# ADR-0011: External planning-policy transport (mounted JSON — FROZEN)
Context: RecoveryPlanningPolicy must be governed, external, versioned, secret-free; no config in code.
Decision: load from a READ-ONLY JSON file mounted into the container at the exact canonical path
/app/config/recovery_planner_policy.v1.json. JSON Schema + contract_version="1" + instrument XAU_USD + digest. Default-deny on
missing/invalid/unsupported-version/wrong-instrument/invalid-anchor (fault codes POLICY_*). No code-default fallback; no stale
cached policy unless separately governed; present-but-invalid replacement blocks new planning; only fully valid replacement
becomes active; atomic replacement expected.
Rejected alternatives: /etc; Redis; SQL; env-embedded JSON; control-plane API; vendor API; shared/ARES/host-global config.
Consequences: reproducible dev/prod parity; auditable; fail-closed. Failure behaviour: planner component blocked, reported in
health. Reversal path: none needed (design). Follow-on WO: none (frozen); implementation may not invent another transport.
