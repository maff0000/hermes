# HMT-2 Exceptional Envelope — Deferred Cross-Process Serialization Item

**Status:** `DEFERRED — MUST BE RESOLVED BEFORE EXCEPTIONAL ENVELOPE IS INTEGRATED INTO CONCURRENT/AUTOMATED DISPATCH`
**Recorded:** 2026-09-27
**Origin:** PR #181 (`hmt-2/slice-idle-guard-fix`, candidate SHA `35cf0b0f30232e21a98a81ab192ff4baa83d8924`, merge SHA
`f4a5b8af070488321720c815b48646dfc1bd2b4e`) — the `slice_is_idle()` exact-zero `memory.current` defect correction.

## The gap

`market_truth/acquisition/orchestration/exceptional_envelope.py`'s `exceptional_envelope()` context manager
performs its idle-check-then-apply sequence as a single synchronous call stack with no yield point — this
was independently verified (by both the implementing engineer and a fresh-context adversarial audit) to make
the sequence race-proof **within one call**, in one process.

**It does not close a cross-process race.** Nothing in this repository today provides mutual exclusion
between two independently launched worker-launch paths:

- `market_truth/acquisition/orchestration/canonical_root_orchestrator.py` does not currently call
  `exceptional_envelope()` at all — it is a standalone, operator-driven procedure, not part of the
  automated dispatch loop.
- `deployment/hmt2/hmt2-run.sh` places every session in an **unnamed** transient `systemd-run --scope`,
  with no `--unit=`, no lock file, no PID file, and no polkit/systemd single-instance constraint anywhere
  in the repository (confirmed via a repo-wide grep for `flock`/`fcntl`/`lockfile`/`PIDFile` — zero hits
  outside this documentation).

The conceptually dangerous sequence this gap describes is:

```text
check idle
        |
        v
new worker starts   (nothing currently prevents this)
        |
        v
change resource envelope
```

## Why this is currently safe to leave deferred

`exceptional_envelope()` is not reachable from the automated dispatch loop today. Its only real invocations
to date have been operator-driven, one-off, single-session procedures (see
`docs/architecture/hmt0-market-truth-v2/hmt2-trinity-operational-delta-obsolete-notes.md` and the
`GC-2025-12-29` controlled retry) — a human explicitly confirms the slice is quiescent immediately before
invoking it, and no second HMT-2 process is launched concurrently by convention. The gap is real but
currently inert, not actively exploitable via any code path that runs today.

## What must happen before this gap can be closed by relying on it going away

**Before `exceptional_envelope()` (or any exceptional-resource-envelope mechanism) is wired into
concurrent or fully automated dispatch — i.e. before any code path can launch two HMT-2 workers, or an
envelope mutation and a worker launch, without a human confirming quiescence in between — this
cross-process serialization gap must be closed.** Closing it correctly will require synchronization
spanning `exceptional_envelope.py` **and** every worker-launch entry point, including the launcher shell
scripts (`deployment/hmt2/hmt2-run.sh`, `deployment/hmt2/hmt2-acquire-run.sh`) — this is a real
synchronization/locking architecture decision, not a narrow guard fix, and must be scoped and authorised
as its own WO rather than folded into a future unrelated change.

This record exists so that fact does not silently disappear from the engineering record.
