"""market_truth.acquisition.orchestration -- HMT-2 governed canonical-dispatch orchestration.

Ports the Trinity-host operational delta (root-owned canonical orchestrator, host guards,
exceptional resource-envelope mechanism, ledger-truth-derived chunk allowlists, failed-
acquisition quarantine, and controlled retry) into portable, tested, externally-configured
HERMES repository code. See `docs/architecture/hmt0-market-truth-v2/hmt2-trinity-operational-delta-obsolete-notes.md`
for the anti-pattern this package deliberately does NOT reproduce (nested `systemd-run`).

Modules:
  config.py                     -- 3-tier configuration (Hmt2OpsConfig).
  host_guards.py                 -- host-wide MemAvailable/PSI reads, causal sampler, guard decisions.
  chunk_allowlist.py             -- ledger-truth-derived session allowlist derivation/validation.
  exceptional_envelope.py        -- governed apply/verify/restore resource-envelope exception.
  quarantine.py                  -- failed-acquisition quarantine (ledger untouched).
  retry.py                       -- controlled, explicitly-authorised ledger retry-repair.
  canonical_root_orchestrator.py -- the root-only, single-systemd-run-boundary dispatch loop.
"""
