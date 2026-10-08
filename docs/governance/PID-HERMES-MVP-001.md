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

## 11. Binding correction — Indicator History Contract (the EMA 200 lane, §4 realised)

Realises §4's EMA 200 lane. **Governed by `ops/work_orders/WO-HERMES-INDICATOR-HISTORY-CONTRACT-0001.md`.**
That WO's implementation is complete and merged into canonical main (independently audited, Architect-
accepted, merged via PRs #185–#188) — the **code-in-main** state of PID §5's three-state Indicator
Definition of Done is satisfied. Production activation (deployment of this code, execution of the
governed H4 repair, execution of the bounded retention normalisation, and proof of fresh versioned
Redis payloads in production) has **not** occurred and is separately governed by
`ops/work_orders/WO-HELM-HERMES-INDICATOR-HISTORY-PRODUCTION-ACTIVATION-0001.md` (see §16) — the
**deployed-runner** and **fresh-versioned-Redis-payload** DoD states remain outstanding until that
separate WO is itself accepted and executed through the governed chain. Production indicator publication
(`hermes:indicators:{instrument}:{timeframe}:v1`) currently exposes **latest-only** values. HELIOS (and any
future consumer needing a historical governed indicator value for a specific closed bar) requires historical
governed indicator records, computed by the **same deterministic calculation already used for `latest`**
(`utils/indicators.py`, `utils/atr_calculator.py` — no second implementation), associated with the candle
history HERMES already retains (`utils/candle_history_v1.py`). The WO's proposed contract mirrors the
existing, proven candle-history pattern exactly (`hermes:indicators:{instrument}:{timeframe}:history:v1:
{open_epoch}` + a ZSET index, same `history` provenance block shape, same per-timeframe retention policy
already governing candle history) rather than inventing a new mechanism. No event ID or content hash is
required for this phase (Architect ruling) — natural identity is `(instrument, timeframe, open_epoch)`,
identical to the existing candle-history natural key.

## 12. Binding finding — H4 candle-history integrity gap (corrected, supersedes the prior version of this section within this same unmerged PR)

**Verified production facts** (HELM final read-only verification,
`helm:evidence:hermes:h4_history_final_verification:20261007T1640Z`, independently reproduced by this
project's own Auditor): the H4 history index for XAU_USD (`hermes:candles:XAU_USD:H4:history:v1:index`) has
250 members; **185 of the 250 referenced history objects exist; 65 do not**. All 65 missing objects fall
inside, and exactly span, `2026-09-05T14:00:00Z` through `2026-09-16T06:00:00Z` inclusive — zero missing
objects exist outside that interval. (Within just the newest 210 of the 250 members — the window the
indicator publisher actually reads — 145 exist and the same 65 are missing; this is consistent with, not a
contradiction of, the full-index 185/250 total, since the 65-wide gap falls entirely inside the newest-210
subset.) Of the 65 missing bars, 45 correspond to conventionally market-open periods and 20 correspond to
flat carry-forward weekend periods (see §13 — both classes are equally valid governed H4 market truth). All
65 have four complete H1 children available in durable SQL and are therefore deterministically
reconstructable using the existing governed H4 derivation (`H4_FROM_H1_NY1700_V1`, §3.3). This is the direct,
mechanical cause of `hermes:indicators:XAU_USD:H4:v1`'s `ema_200` field currently reading `null` in
production: the indicator publisher correctly refuses to fabricate `ema_200` when fewer than 200 of the
requested 210-bar deep window are actually present — this is not an indicator-code defect, it is a
candle-history data gap.

**Supported inference, not directly observed forensic fact** — root-cause wording corrected: the prior
statement in this section that "TTL expiry was ruled out" was **incorrect and is retracted**. Production
evidence strongly supports expiry under a historical shorter-TTL regime as the cause of the contiguous
missing H4 object window. All and only the relevant forward-written objects are absent, the loss window is
bounded by retention/deployment changes, and neighbouring survivors demonstrate distinct historical TTL
regimes. The exact original TTL of the already-expired objects cannot now be directly observed, so the
precise historical TTL value remains a supported historical inference rather than directly observed forensic
fact. No separate root-cause WO is required solely to prove the already-expired TTL.

**Architectural ruling — repair scope**: the governed repair must target **all 65** missing H4 history
objects (not a subset), reconstructed from durable authoritative `candles_H1` SQL children via the existing
governed `derive_h4`-equivalent canonical derivation, requiring exactly 4 complete H1 children per bar,
preserving `H4_FROM_H1_NY1700_V1` and the `22/02/06/10/14/18 UTC` anchors exactly. The repair must never
fabricate a missing H1 child, never overwrite an existing H4 survivor, must halt/fail safely on conflicting
existing data, and must record repair provenance. Reconstruction must use durable SQL H1 children, not
legacy/stale `candles_H4` SQL. No repair is authorised by this PID entry alone — implementation requires its
own governed WO acceptance and Architect authorisation (see `ops/work_orders/
WO-HERMES-INDICATOR-HISTORY-CONTRACT-0001.md` §6).

R2D2 reconciliation reference: `r2d2:audit:hermes:h4_history_defect_september_incident_reconciliation:20261007:v1`.

## 13. Binding architecture ruling — weekend flat carry-forward H4 bars are valid governed market truth

HERMES production deliberately constructs H4 bars from exactly four complete H1 children under
`H4_FROM_H1_NY1700_V1` (anchors `22/02/06/10/14/18 UTC`), including flat carry-forward H1 children during
weekend/non-trading periods. Those flat H1 children are complete, deterministic, 4/4, already accepted by
existing production H4 derivation, already represented in surviving H4 history, and already consumed by the
current governed indicator engine. **Valid 4/4 flat carry-forward H1-derived weekend H4 bars therefore remain
part of current governed HERMES H4 market truth.** The §12 repair must not exclude them, this PID does not
redefine H4 semantics, and EMA calculation must not be altered to exclude weekends. Any future decision to
change this market-data doctrine (e.g. to exclude weekend bars for strategy-research reasons) requires a
separate research/architecture/compatibility/strategy-retesting programme (APOLLO/ATHENA involved) — it is
explicitly not decided or opened by this PID.

## 14. Binding finding — H4 retention normalisation required alongside the §12 repair

HELM's final verification additionally found surviving forward-written H4 objects from `2026-09-02` and
`2026-09-03` with only approximately 0.1–0.9 days of TTL remaining (5 of 7 objects independently sampled at those two dates — the remaining 2 of 7 already carry ~99 days remaining, consistent with the current 120-day regime; the finding is not universally true of every object at those dates, only of the subset still on the older regime) — these objects appear to carry an older,
historical ~35-day TTL regime and are about to expire imminently. **The §12 repair must not restore 65
objects while knowingly allowing additional, currently-valid H4 history to disappear immediately afterward
under a stale TTL regime.** A bounded requirement is added: identify the affected surviving H4 objects,
preserve their payload bytes/semantic content exactly (no market-value rewrite), apply the existing current
canonical H4 retention policy (not a new policy) without extending any object beyond that governed policy,
and prove the required H4 warm-up depth will not immediately regress through legacy TTL expiry once §12's
repair completes. Full design: `ops/work_orders/WO-HERMES-INDICATOR-HISTORY-CONTRACT-0001.md` §6a. This is
retention normalisation to the already-governed policy, not invention of a new retention policy, and it is
not executed by this PID entry alone.

## 15. Binding finding — build/promotion classification governance gap (discovered preparing §11, unchanged)

Production's deployed image (`1c359067b0e00d3947fa314382bc125da398d662`, `prod-1c359067b0e0`) is labelled
`HERMES_BUILD_CLASSIFICATION=NON_PROMOTED_ENGINEERING_CANDIDATE`. This label is **not stale or incorrect** —
it is hardcoded, unconditionally, directly in the `Dockerfile`'s `ENV` instruction, with no alternate
`PROMOTED` value implemented anywhere in the codebase. `docs/design/container_mvp/
WP2_CANONICAL_BUILD_AND_EXTERNALISED_CONFIG.md` explicitly states "WP3 remains required before any
promotion," but no WP3 promotion mechanism exists anywhere in the repository. **Every image this build
system can currently produce is, by construction, a non-promoted engineering candidate — there is no path to
a genuinely promoted production build today.** This is real, separate HERMES delivery-governance debt,
independent of the indicator-history work in §11/§12. It is not remediated by, and must not be bundled into,
`WO-HERMES-INDICATOR-HISTORY-CONTRACT-0001`. Disposition (ownership of the fix) is returned to Central
Architecture — this PID records the finding, not a remediation plan.

## 16. Current status addendum (supplements §9, does not replace it)

| Item | State |
|---|---|
| Indicator history contract (§11, the EMA 200 lane realisation) | **CODE-IN-MAIN** — `WO-HERMES-INDICATOR-HISTORY-CONTRACT-0001` implemented, independently audited twice (PASS both times), merged to canonical main (PRs #185-#188). Deployed-runner and fresh-versioned-Redis-payload DoD states (PID §5) not yet met — see production-activation row below |
| H4 candle-history integrity gap (§12) | **REPAIR MECHANISM CODE-IN-MAIN, PRODUCTION REPAIR NOT YET EXECUTED** — the governed repair mechanism is implemented and fixture-proven; the actual 65-object production repair has not been authorised or performed; see production-activation row below |
| Weekend flat carry-forward H4 bars (§13) | **RULED** — valid governed market truth, preserved, not excluded from repair; no market-data doctrine change opened here |
| H4 retention normalisation (§14) | **MECHANISM CODE-IN-MAIN, PRODUCTION NORMALISATION NOT YET EXECUTED** — the bounded, `EXPIRE`-only mechanism is implemented and fixture-proven; the actual production normalisation has not been authorised or performed; see production-activation row below |
| Build/promotion classification gap (§15) | **OPEN GOVERNANCE GAP**, disposition pending Central Architecture, ownership not yet assigned |
| Indicator-history production activation | **PREPARATION** — `WO-HELM-HERMES-INDICATOR-HISTORY-PRODUCTION-ACTIVATION-0001` drafted, not yet Architect-accepted, no HELM dispatch, no production mutation |
| Same-bar indicator-history recomputation semantics (§17, B1 corrective) | **PREPARATION** — `WO-HERMES-INDICATOR-HISTORY-SAME-BAR-RECOMPUTATION-0001` drafted, not yet Architect-accepted, no FORGE dispatch. **Blocks production activation** until implemented, independently audited, merged, and R2D2 re-gates GREEN |

## 17. Binding ruling — same-bar indicator-history idempotent replay and deterministic-recomputation semantics (B1 corrective)

**Source finding**: R2D2's production-activation re-gate (`r2d2:audit:hermes:ih_production_activation_green_gate:
20261008:v1`) found the merged `_write_indicator_history` write path (§11,
`utils/hermes_runtime_publisher_steps_v1.py`) can reject legitimate recomputation of the same bar, blocking
production activation (**B1**). The conflict check (`_indicator_history_fingerprint`) already excludes
`generated_at_utc` and `history.published_at_utc`, but still treats `freshness_state` and every indicator
value as immutable conflict material — so a clock-derived `freshness_state` transition, or the exact proven
case (`ema_200` moving from `null` to a populated value once governed H4 history depth is restored), both
incorrectly raise `GOV-HERMES-IND-HIST-020`.

**Binding record identity (unchanged)**: `(instrument, timeframe, open_epoch)` remains the sole identity for
a historical indicator record, exactly as established in §11. This ruling does not change identity.

**Binding semantics (corrective)**:

1. **Idempotent replay.** Re-writing the same bar with governed-equivalent indicator truth is safe. Any
   publication-time metadata difference between two writes of the same bar must not, by itself, be treated
   as a conflict.
2. **Transient/clock-derived fields are never conflict material.** `generated_at_utc`, `history.
   published_at_utc` (already excluded), and `freshness_state` (newly confirmed) are excluded from the
   conflict comparison. This list is explicit and closed — it is not a general invitation to exclude
   arbitrary future fields; any further exclusion requires its own governance record.
3. **Legitimate deterministic recomputation is permitted, narrowly.** The existing architecture already
   guarantees every write that reaches this function came through the single governed call path
   (`indicator_step()` → `_compute_indicators()` → `_write_indicator_history()` — there is no other producer
   of this payload). Given that structural guarantee, a recomputed value for an indicator field is accepted,
   for the same bar, when the field's value transitions from **absent/`null` to a populated, schema-valid
   value** — i.e. the governed engine has legitimately become able to compute something it previously could
   not (the proven case: `ema_200: null → <value>` once H4 history depth is restored). This rule is defined
   once, generically, over every indicator field the contract carries — it is **not** EMA-200-specific
   special-casing.
4. **Genuine conflicts remain fail-loud.** This ruling does **not** convert the history store into
   unconditional last-write-wins. A value that was already populated changing to a *different* populated
   value, or regressing from populated back to `null`, for the same bar, remains a conflict and must still
   raise `GOV-HERMES-IND-HIST-020` exactly as today. Identity mismatch (different `open_epoch`) is already
   structurally impossible to collide, since it addresses a different key. Malformed/schema-invalid payloads
   are already rejected by the existing contract validation before reaching this comparison and are
   unaffected by this ruling.

**Explicitly not decided or reopened by this ruling**: candle-history semantics, H4 derivation, weekend
semantics (§13), retention policy (§14), repair logic (§12), Redis security, HELIOS, FALCON, or the
build-classification/promotion gap (§15). Governed by
`ops/work_orders/WO-HERMES-INDICATOR-HISTORY-SAME-BAR-RECOMPUTATION-0001.md`.
