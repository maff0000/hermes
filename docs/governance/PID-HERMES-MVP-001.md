# PID-HERMES-MVP-001 — HERMES MVP Closure Mandate

**Project Initiation Document · binding · Project Owner mandate**
WO-HELM-HERMES-PID-MVP-CLOSURE-001 · documentation-only · base canonical main `f69df68`

> This PID records the Project Owner's binding MVP-closure mandate for HERMES. It is governance, not code. Every HERMES
> work order — and the agent instructions that drive them — must conform to it.

## 1. What HERMES is

HERMES is a **pure market-data fact spine**. It produces and publishes deterministic facts only:

- **deterministic candles and indicators** (moving averages, RSI, ATR, deterministic bands/ADX, etc.);
- **gap and completeness transparency** (gap truth, backfill-readiness, freshness);
- **publication validation and governance** (fail-closed eligibility, versioned contracts, provenance).

## 2. What HERMES is NOT (hard boundary)

HERMES has **no strategy, recommendation, risk or trading authority**. It emits no regime, no risk score, no trade
recommendation, no order, no execution. Those belong to other applications (ARES/Falcon/etc.) and are out of scope forever.

## 3. Binding correction — a gap key is EVIDENCE, not execution authority

A published gap/recovery fact is **evidence of a market-data condition**, never authority to act. Any recovery action must
traverse the full binding chain, each link separately governed and authorised:

```
gap truth → planner → eligibility validator → proposal publisher
          → separate job authorisation → durable job → bounded executor → verification → reconciliation
```

No link may be skipped or short-circuited. Publication of a proposal is advisory planning truth only; it never authorises a
job, and a job never authorises an unbounded executor.

## 4. Binding correction — EMA 50 and EMA 200 are separate lanes

- **EMA 50** belongs to the **bounded existing-indicator wiring** (reuses the existing calc lib, contract, runner, history).
- **EMA 200** is a **separate WO**: it requires its own history-depth, retention and warm-start design (deeper history than
  the current bounded window supports). EMA 200 must not be smuggled into the EMA-50 wiring lane.

## 5. Indicator Definition of Done (DoD)

An indicator is **DONE** only when ALL three hold — and each state is recorded **separately**:

1. **code in main** (merged, R2D2-audited);
2. **deployed container runner active** (the runtime runner is live on the deployed image);
3. **fresh payload on the explicit versioned Redis contract** (`hermes:indicators:XAU_USD:{TF}:v1` carrying the new fields,
   freshly published).

Code-in-main alone is **not** done. Merged ≠ deployed ≠ operational. **Canonical, deployed and operational states must always
be recorded separately** (in evidence and fabric).

## 6. Publication programme deliverables

- **DEL-PUB-01 — pure eligibility validator** (`utils/hermes_proposal_validator_v1.py`): deterministic, side-effect-free
  fail-closed eligibility decision. **Merged via PR #98; NOT deployed.**
- **DEL-PUB-02 — dark publisher adapter**: applies validator verdicts; atomic current-pointer + status/revocation keys; TTL;
  supersession. **Blocked pending its own WO.**
- **DEL-PUB-03 — append-only SQL audit sink**: durable publication/refusal/supersession/revocation audit. **Blocked**;
  mandatory before production activation.

## 7. Layer discipline

**Layer 5 remains BLOCKED until Layer 4 is complete.** (Publication/consumer activation cannot begin while the
validation/publisher/audit layer is incomplete.) No layer may be activated ahead of the one beneath it.

## 8. Delivery discipline (binding for every HERMES WO)

- **one WO / one branch / one worktree / one PR.**
- **R2D2 GREEN before merge, deploy or activation** — no exceptions.
- **external configuration only** — no operational config in code; no hidden defaults.
- **no duplicate utilities or one-off repair scripts** — reuse the governed module; extend, don't fork.
- **canonical, deployed and operational states recorded separately** in evidence + `helm:*` fabric.

## 9. Current status (truth as of this PID)

| Item | State |
|---|---|
| DEL-PUB-01 eligibility validator | **MERGED** (PR #98) → canonical main; **NOT deployed** |
| EMA 50 / Bollinger(20,2) / ADX(14,+DI/-DI) wiring | **IMPLEMENTED** through the PR #99 merge sequence; **NOT deployed** until separately proven (DoD §5) |
| EMA 200 | **SEPARATE** lane (own history-depth/retention/warm-start WO); not started |
| MACD | **SEPARATE**; not started |
| VWAP | **SEPARATE**; not started |
| Sweep detection | **UNRESOLVED**; not in scope |
| DEL-PUB-02 dark publisher | **BLOCKED** pending its own WO |
| DEL-PUB-03 SQL audit sink | **BLOCKED**; mandatory before production activation |
| Layer 5 (publication/consumer activation) | **BLOCKED** until Layer 4 complete |

## 10. State-separation reminder

At the time of writing: **canonical main** contains DEL-PUB-01 + the EMA50/Bollinger/ADX wiring; the **deployed runtime**
remains the earlier dark image (indicator payload still carries only the prior field set); **operational** activation of the new
indicators and of any publisher is a later, separately-audited gate. Do not conflate the three.
