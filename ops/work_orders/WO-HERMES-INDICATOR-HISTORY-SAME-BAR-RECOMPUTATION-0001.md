# WO-HERMES-INDICATOR-HISTORY-SAME-BAR-RECOMPUTATION-0001 — B1 corrective: idempotent replay & deterministic recomputation

**Persona:** Rogue (Delivery Controller), governance preparation, per direct Central Architecture dispatch.
**Status:** ACCEPTED — Central Architecture has reviewed and accepted this Work Order as durable project
authority and has explicitly authorised dispatch of a bounded Implementer mandate against it (see Acceptance
record below). This supersedes the original "PREPARATION ONLY — not yet accepted. No FORGE dispatch has
occurred." line this document carried at merge time (PR #190) — that line was accurate when written and is
retained in this file's Git history, not rewritten, per this project's own discipline against silently
erasing prior state. No HERMES application code, Redis, or SQL has been modified by this WO itself;
implementation is Git-tracked separately per the governance chain (§1).

## Acceptance record

- **Accepted by:** Central Architecture, via explicit instruction to the Delivery Controller (Rogue):
  "ROGUE — DELIVERY CONTROLLER / ARCHITECT DECISION — R2D2 B1 CORRECTIVE WORK" — "Once the corrective WO is
  properly authorised through the existing governance process, FORGE may make the smallest implementation
  necessary in the indicator-history write path" (§5 of that mandate), following the Architect's explicit
  acceptance of this WO's governance package for merge (PR #190, merge SHA
  `5a3a780f1f0b4018d8c5db890491a35cf05ba3bb`).
- **Dispatch authorised:** the same mandate's §5–§8 constitute the bounded implementation scope, exclusions,
  required test matrix, mandatory end-to-end R2D2 regression sequence, and dual audit/R2D2-regate
  requirement a dispatched Implementer (FORGE) must work within — reproduced in full in §4–§8 of this
  document.
- **This correction's own authority:** this status update is itself a Delivery-Controller-level
  durable-record correction (not new architecture, not implementation against this WO's technical scope) —
  making the already-given, already-acted-upon Architect acceptance visible in Git, per this project's own
  "no Git record → not durable project authority" rule. It was prompted by a FORGE Implementer correctly
  refusing to proceed against the stale "PREPARATION ONLY / not yet accepted" line (and a stale Base SHA)
  still present in Git at dispatch time — exactly the STOP-on-ambiguity discipline this WO requires, working
  as intended.
**Date:** 2026-10-08
**Repo:** `maff0000/hermes` (canonical checkout `/srv/rogue-hermes/canonical` on dell-debian)
**Parent governance:** `docs/governance/PID-HERMES-MVP-001.md` §17 (the binding ruling this WO implements),
§11 (the original indicator-history decision this corrects), §8 (delivery discipline — binding on this WO).
**Base SHA (authored against):** `origin/main = b9eb84177687e405306bdbd7e3440f4cd631eab7` (canonical
main after `WO-HELM-HERMES-INDICATOR-HISTORY-PRODUCTION-ACTIVATION-0001`'s governance merge, PR #189).
**Implementation base (Architect-authorised, post-governance-merge)**: `5a3a780f1f0b4018d8c5db890491a35cf05ba3bb`
(canonical `main` after this WO's own governance package, PR #190, merged). The only commit between the
original authored base and this implementation base is this WO's own merge (`775a8c7`, merge `5a3a780f`),
touching only this file and `docs/governance/PID-HERMES-MVP-001.md` — a benign, documentation-only,
self-referential advance that changes nothing this WO's technical scope depends on. FORGE must implement
against `5a3a780f1f0b4018d8c5db890491a35cf05ba3bb`, not the original authored base.
**Source finding:** R2D2 production-activation re-gate, `r2d2:audit:hermes:ih_production_activation_
green_gate:20261008:v1` — **B1**, blocking.

## 1. Governance chain (binding, copied forward)

```
Architecture decision → PID/Amendment → Git-tracked Work Order → Delivery Controller → Implementer → Independent Audit/R2D2 → PR → Architect Acceptance → Merge → Production Re-Gate
```

**NO PID → NO WORK ORDER.** **NO WORK ORDER → NO IMPLEMENTATION.** **NO INDEPENDENT AUDIT + ARCHITECT
ACCEPTANCE → NO MERGE.** **NO R2D2 GREEN → NO PRODUCTION ACTIVATION.** **NO GIT RECORD → NOT DURABLE PROJECT
AUTHORITY.** Plus PID §8 (binding for every HERMES WO): one WO/branch/worktree/PR; R2D2 GREEN before
merge/deploy/activation; external configuration only; no duplicate utilities; canonical/deployed/operational
states recorded separately.

## 2. The defect (B1)

`utils/hermes_runtime_publisher_steps_v1.py::_write_indicator_history()`'s conflict check
(`_indicator_history_fingerprint()`) excludes only `generated_at_utc` and `history.published_at_utc` from
the same-bar comparison. R2D2 reproduced, against the real function:

- Initial write of a bar → OK.
- Identical same-bar replay shortly later → OK (already-excluded fields match).
- Same bar where `freshness_state` changes (clock-derived) → **conflict** (incorrect).
- Same bar where `ema_200` moves from `null` to a populated value after governed H4 history depth is
  restored → **conflict** (incorrect — this is the exact scenario that blocks production activation after
  the §12 H4 repair).

## 3. Objective

Implement PID §17's binding semantics so that: (a) `freshness_state` is excluded from the conflict
comparison, same as the already-excluded timestamp fields; (b) a field transitioning from `null`/absent to a
populated, schema-valid value for the same bar is accepted as legitimate deterministic recomputation, not a
conflict; (c) every other kind of value difference (populated→different-populated, populated→null
regression) continues to raise `GOV-HERMES-IND-HIST-020` exactly as today; (d) this rule applies generically
across every indicator field the contract carries — no `ema_200`-specific special-casing.

## 4. Exact scope (for a future FORGE dispatch, not executed by this document)

1. Modify the conflict-detection logic at the correct abstraction boundary — most likely by changing how
   `_indicator_history_fingerprint()` (or a new, narrowly-scoped comparison function alongside it) treats
   `freshness_state` and per-field null-to-populated transitions, rather than altering
   `_write_indicator_history()`'s overall control flow, `build_history_envelope()`, or
   `build_history_write_plan()` beyond what's strictly necessary to support the new comparison.
2. `freshness_state` joins `generated_at_utc`/`history.published_at_utc` as excluded transient/clock-derived
   comparison material — this list is explicit and closed (PID §17 item 2); do not add further exclusions
   without their own governance record.
3. For every remaining (non-transient) field in the compared payload: if the existing stored value is
   `null`/absent and the new value is a populated, schema-valid value, that specific field's difference is
   not conflict material. If the existing stored value is already populated and the new value differs
   (whether to a different populated value or back to `null`), that remains `GOV-HERMES-IND-HIST-020`
   exactly as today.
4. This comparison is **symmetric across every field** in the indicator-history payload (not a field
   allow-list naming `ema_200` specifically) — implement it generically against the payload's actual field
   set.
5. No change to `_compute_indicators()`, ATR/EMA/RSI/Bollinger/ADX mathematics, candle-history semantics, H4
   derivation, weekend semantics, retention policy, or the repair mechanism.

## 5. Exclusions (binding on any future FORGE dispatch)

Do not: reimplement ATR or EMA; alter `_compute_indicators()` mathematics; change candle-history semantics;
change H4 derivation or anchor policy; change weekend semantics (PID §13); change retention policy (PID
§14) or the retention-normalisation mechanism; change the H4 repair/backfill mechanism (PID §12); touch
Redis security/ACL; touch HELIOS; touch FALCON; address the build-classification/promotion debt (PID §15);
widen this correction to any instrument/timeframe handling beyond the indicator-history write path's
conflict semantics.

## 6. Required test matrix (for a future implementation stage — at minimum, all 13 items, deterministic,
no real Redis/SQL I/O, no sleep-heavy wall-clock tests)

1. First write of a bar succeeds.
2. Byte/semantic-equivalent same-bar replay succeeds (regression — must still hold).
3. `generated_at_utc` difference does not conflict (regression — must still hold).
4. `published_at_utc` difference does not conflict (regression — must still hold).
5. Clock-derived `freshness_state` transition does not conflict (new).
6. Legitimate `ema_200: null → populated` recomputation succeeds and the stored historical record contains
   the populated governed value (new — the proven blocking case).
7. Equivalent legitimate recomputation remains idempotent afterward (replaying the now-populated value again
   does not conflict).
8. The same null→populated acceptance rule is proven generically against at least one *other* indicator
   field (not `ema_200`), demonstrating the rule is not EMA-200-specific hardcoding.
9. Identity mismatch cannot overwrite another bar (a write to a different `open_epoch` never touches another
   bar's key — regression).
10. Malformed/invalid payload remains rejected (regression — existing contract validation unaffected).
11. A genuinely prohibited rewrite — a populated value changing to a *different* populated value for the
    same bar — still fails loudly with `GOV-HERMES-IND-HIST-020` (must NOT be weakened to last-write-wins).
12. A populated value regressing to `null` for the same bar still fails loudly (must NOT be weakened).
13. Normal forward writes (the existing, already-tested happy path) remain unaffected; the full existing
    `WO-HERMES-INDICATOR-HISTORY-CONTRACT-0001` test suite (41 tests) remains green, unmodified in substance.

## 7. R2D2 regression case (mandatory — proves the actual blocking defect is closed, not a helper in isolation)

The exact production-activation sequence that blocked activation must become a regression test, exercising
the real `_write_indicator_history()` function end-to-end (not a unit test of an isolated comparison helper):

1. H4 history is insufficient for `ema_200` (fixture: fewer than 200 usable closes in the deep window).
2. The same H4 bar is evaluated and its historical indicator snapshot is written with `ema_200=null`.
3. Governed missing H4 history is restored (fixture: the deep window now has ≥200 usable closes).
4. The same H4 bar is recomputed by the real governed indicator implementation (`_compute_indicators()`,
   unmodified).
5. `ema_200` becomes populated in the freshly computed result.
6. The same historical key is written again with this new result — the write **succeeds**.
7. No `GOV-HERMES-IND-HIST-020` is raised.
8. A subsequent equivalent replay (writing the same now-populated result again) remains idempotent (no
   conflict, no error).

## 8. Independent audit + R2D2 re-gate (both required, neither substitutes for the other)

After implementation: run the targeted new tests, the full existing HERMES `unit-infra-free` gate, CI, and
gitleaks. Commission a fresh independent audit of the exact candidate SHA (verifying scope, exclusions,
test coverage, no scope creep, Git hygiene — the standard audit this project has used throughout). **Then,
separately, the exact same candidate SHA must go back through R2D2 specifically for the activation-blocker
re-gate** — R2D2 must explicitly verify B1 is closed, reproducing §7's regression sequence itself. A generic
independent-agent PASS does not substitute for the explicit R2D2 GREEN PID §8 requires before any production
activation may resume.

## 9. Disposition

**ACCEPTED — implementation dispatch authorised.** The original "Preparation only... Returned to Central
Architecture for review and acceptance before any implementation dispatch" line above was accurate when
written (PR #190) and is preserved in this file's Git history, not rewritten. Central Architecture has since
reviewed and accepted this governed package (see Acceptance record above) and explicitly authorised dispatch
of a bounded FORGE Implementer mandate against the implementation base recorded above
(`5a3a780f1f0b4018d8c5db890491a35cf05ba3bb`). Implementation must stay strictly within §4–§8 of this
document; any architectural ambiguity encountered during implementation must STOP and return to Central
Architecture, per §1.
