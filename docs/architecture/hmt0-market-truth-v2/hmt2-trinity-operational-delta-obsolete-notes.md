# HMT-2 Trinity operational-delta port -- retired artefacts, lesson-preservation only

This note documents what was retired on the Trinity host during the operational history this
port (see `market_truth/acquisition/orchestration/`, `deployment/hmt2/`) draws from, and why.
None of the logic described below was revived by the port -- this file exists purely so the
lessons are not lost, per the governing WO's "Explicit exclusions" (`obsolete/*` content is
preserved as documentation/lesson-preservation only, never revived logic).

## `hmt2_routine_recovery_driver_v2.py` -- THE anti-pattern (do not port, ever)

Invoked a **nested** `systemd-run`: the canonical worker identity (`hmt-compute`) tried to call
`systemd-run` a second time from inside its own already-scoped process. This failed with
`Interactive authentication required` (a polkit/dbus failure) -- and this is the CORRECT,
intended security boundary, not a bug to work around. Empirically-verified negative test:
`runuser -u hmt-compute -- systemd-run --scope --slice=hmt2.slice --uid=hmt-compute --gid=hmt --
/bin/true` fails exactly this way.

The corrected topology -- a single, root-owned orchestrator calling the canonical launcher
exactly once per session, never nested -- is what
`market_truth/acquisition/orchestration/canonical_root_orchestrator.py` implements. `hmt-compute`
(or whatever a given host names its canonical compute identity) must never be granted generic
`systemd-run` authority: no polkit rule, no sudo, no linger.

## `hmt2_routine_recovery_driver.py` (v1)

Predates v2 above; superseded by it, itself later superseded by the corrected orchestrator.
Preserved only as lineage context for the anti-pattern's history.

## `derive_next_chunk.py`

Standalone chunk-derivation script from earlier recovery work. Its CONCEPT (bounded,
ledger-truth-derived chunk allowlists, never a hardcoded ordinal position) is exactly what
`market_truth/acquisition/orchestration/chunk_allowlist.py` implements as a real, tested,
reusable module -- this script itself was not reused.

## `hmt2_single_session_psi_gated_run.py`

A one-off Batch-01 incident tool that added an extra 60-second pre-launch PSI-settle gate on top
of the routine guard. Standing authority ruled routine starts use the simpler existing gate, so
this literal script was never adopted for routine batches. The settle-gate CONCEPT remains
available as a documented option (an operator can always choose not to dispatch a session until
host PSI has genuinely settled) but is not implemented as a separate always-on mechanism in this
port, since the governing WO's scope is the four already-proven A-class mechanisms plus the
B-class deployment files and the one C-class test tool -- not every incident-specific tool ever
built on Trinity.

## `gc_2020_02_27_proof_run.py` / `gc_2020_02_27_proof_run2.py`

A pair of one-off exceptional-envelope proof scripts for a single pathological session
(GC-2020-02-27). The high-resolution (~1s cadence), started-before-launch telemetry-sampling
PATTERN they pioneered is exactly what
`market_truth/acquisition/orchestration/host_guards.CausalMemorySampler` implements as a
first-class, reusable, always-available mechanism -- see that class's docstring for the specific
post-kill-snapshot mistake (GC-2025-10-01) this design is built to make structurally impossible
to reintroduce. The literal one-off scripts themselves were not ported.

## `hmt2-run-run2-disposable.sh`

A one-off clone of `hmt2-run.sh` used only for a single determinism-reproduction proof. Not
ported; `deployment/hmt2/hmt2-run.sh` is the one, canonical launcher.

## `hmt2_acquire_credential_check.py`

A one-time, hardcoded-path credential-validation diagnostic. Its CONCEPT (a free, non-billable
credential-validation check, reusing the existing metadata-quote call as the auth proof) is
ported as a real, reusable, generalised tool at
`market_truth/acquisition/diagnostics/credential_check.py` (parametrised over session id and
ledger path, never hardcoding either). The original script itself was not ported.
