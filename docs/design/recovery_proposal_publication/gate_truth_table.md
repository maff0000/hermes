# Publication Gate Truth Table (§17)

Publication gates are **separate** from planner gates. Planner enablement is **never** publication authority: a planner may be
enabled (producing a holder) while publication remains dark. No gate is installed by this WO.

## Gates (env-name only; values via approved external mechanism, `configuration_doctrine.md`)
- `HERMES_RECOVERY_PUBLISHER_ENABLED` — publisher feature on/off.
- `HERMES_RECOVERY_PUBLISHER_AUTHORISED` — authority to write the external contract.
- (contract-version authorisation comes from governed config, not an env gate.)

## Truth table
| ENABLED | AUTHORISED | Result | Behaviour |
|---|---|---|---|
| absent | absent | `PUB_DISABLED` | zero-read, no key; dark (default) |
| absent | true | `PUB_DISABLED` | disabled wins; zero-read |
| true | absent | `PUB_GATE_MISMATCH` (fail-closed) | **component-level fault**, no publish, no key; **no `SystemExit`**, HERMES keeps running |
| true | true | `PUB_PLAN_PUBLISH_ENABLED` | proceed to full eligibility predicate (still may refuse) |
| malformed either | — | `PUB_GATE_MISMATCH` (fail-closed) | treat unparseable as disabled/mismatch; component fault only |

## Principles (mirrors the audited planner gate doctrine)
- **Missing gate ⇒ disabled.** Partial/malformed ⇒ **fail closed**.
- Enabled-without-authorised is the *publication* analogue of the planner's `105`: a **typed component-level fault**, counted
  and logged, that **never** raises `SystemExit` and never terminates HERMES. Sibling runners are unaffected.
- Append-on-ENABLED (non-raising) keeps the mismatch fault component-local (surfaced in the publisher step, not at supervisor
  assembly) — same pattern proven for the planner.
- Gate truth is read fresh each cycle; a mid-run gate change is honoured on the next cycle (and revalidation, §13).
- `true/true` grants only the *right to evaluate* eligibility; it is **not** itself sufficient to publish (the matrix is).
