# WO-HERMES-RECOVERY-RUNBOOK-0005 — Evidence Summary

## Date: 2026-03-31
## Author: Helm (Claude Code)

## What was delivered

1. **ops/runbooks/hermes-recovery.md** — operator-grade recovery runbook:
   - Health check procedure
   - Stale stream incident response (decision tree)
   - Gap scan commands and interpretation
   - Recovery commands (window, gap-id, verify)
   - Fault code reference table
   - Truth semantics (GREEN/AMBER/RED, governance labels)
   - March 31 incident full reference with M5 count clarification
   - System architecture reference
   - Config and table reference

## Runbook design principles
- Procedural, not prose
- Command-first (copy-paste ready)
- Fault-code aware
- Decision trees, not paragraphs
- Usable at 3am by someone who did not build it

## M5 candle count clarification (Matt's review flag)
Explicitly documented in runbook section 7:
- WO-2 found zero M5 gaps because startup backfill at 08:41 already recovered them
- WO-4 Job #3 shows 59 M5 rows because planner auto-included CANDLE_M5 as SIGNAL_M5 dependency
- These were idempotent overwrites, not new recovery
- Net new M5 recovery was done by startup backfill, not WO-4

## Program completion summary

| WO | Deliverable | Tests | Status |
|----|-------------|-------|--------|
| WO-0000 | Service architecture recon | N/A | COMPLETE |
| WO-0001 | Fail-loud watchdog | 7/7 | LOCAL MAIN COMPLETE |
| WO-0001A | Watchdog remediation | 9/9 | LOCAL MAIN COMPLETE, DEPLOYED, VERIFIED |
| WO-0002 | Gap truth + scanner | 8/8 | LOCAL MAIN COMPLETE |
| WO-0003 | Recovery library + planner | 7/7 | LOCAL MAIN COMPLETE |
| WO-0004 | Backfill engine + executor | Real incident | LOCAL MAIN COMPLETE |
| WO-0005 | Operator runbook | N/A | LOCAL MAIN COMPLETE |

**All WOs: REMOTE/PR CLOSURE PENDING DUE TO GH OUTAGE**
