# HERMES Governance Index

Canonical index of binding HERMES governance documents. **Agents and operators must load the applicable PID before acting
on any HERMES work order.**

| Document | Scope |
|---|---|
| [PID-HERMES-MVP-001](PID-HERMES-MVP-001.md) | **Binding MVP-closure mandate** — HERMES is a pure market-data fact spine (deterministic candles/indicators, gap & completeness transparency, publication validation/governance); **no strategy/recommendation/risk/trading authority**. Indicator DoD = code-in-main + deployed runner + fresh versioned Redis payload. Binding chain (gap truth → planner → eligibility validator → proposal publisher → separate job authorisation → durable job → bounded executor → verification → reconciliation). EMA 50 and EMA 200 are separate lanes. Layer 5 blocked until Layer 4 complete. One WO/branch/worktree/PR; R2D2 GREEN before merge/deploy/activation; external config only; no duplicate utilities; canonical/deployed/operational recorded separately. |

## Standing rules (from PID-HERMES-MVP-001)
- A **gap key is evidence, not execution authority.**
- **Merged ≠ deployed ≠ operational** — record the three states separately.
- DEL-PUB-01 (eligibility validator) merged, not deployed · DEL-PUB-02 (dark publisher) blocked · DEL-PUB-03 (SQL audit sink) blocked.
