# Gate Failure Domain (DESIGN-ONLY) — DECISION

Gates: HERMES_RECOVERY_PLANNER_ENABLED, HERMES_RECOVERY_PLANNER_AUTHORISED (already implemented; unset in runtime).
Truth table: F/F->DISABLED ; F/T->DISABLED ; T/F->SystemExit(105) ; T/T->plan-only.

## Where 105 could occur
(a) container boot ; (b) planner-runner construction ; (c) caller invocation ; (d) dedicated validation phase.

## DECISION: component-level fail-closed (NOT whole-container)
Evaluate the gate at (b)/(d) INSIDE the planner runner. Enabled-without-authorised becomes a LOUD, BINDING planner-component
fault: fault code GATE_FAILCLOSED_105, planner runner NOT started, health surface RED, log ERROR — while the supervisor and ALL
critical runners (tick, candle, quote, feed-health, gaps, backfill_status) keep running.

## Risk analysis (why NOT whole-container exit)
Crashing the HERMES market-data spine because an ADVISORY, non-critical planning feature is misconfigured is disproportionate
and unsafe: it would take tick/candle/quote/feed-health offline for a feature that publishes nothing. The 105 code remains
visible + binding at component level (health RED + log + no runner). This is a DELIBERATE divergence from the gaps/backfill
surfaces, whose *_publish_enabled() raise SystemExit(105) during default_runner_specs() (governed DATA surfaces where whole-
container fail-closed was accepted). The planner is advisory, so component-level is correct.

Whole-container exit(105) would be selected ONLY if governance explicitly mandates it — this design does NOT recommend it, and
requires an explicit risk sign-off if ever chosen. No gate change is made in this WO. (ADR-0005.)
