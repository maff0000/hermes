# Evidence — WO-HELM-HERMES-MARKET-HOURS-PREDEPLOY-GATE-HARNESS-0001

**Verdict:** GREEN_HERMES_MARKET_HOURS_PREDEPLOY_GATE_HARNESS_PR_OPEN · **UTC:** 2026-07-16 · Repo maff0000/hermes
**INERT pre-deployment CLI. Not wired into startup. No deploy, no container touch, no Redis/SQL/network.**

## Base
Canonical main `71ea3bd4598d8af5628f78de10f8022088ad3f05`. Branch
`wo/WO-HELM-HERMES-MARKET-HOURS-PREDEPLOY-GATE-HARNESS-0001` in worktree
`/srv/trading/hermes-worktrees/wp-b-predeploy-gate`. The running container `hermes-signal-dev`, the live deployment and
the dell observer were NOT touched. `config/market_hours_schedule.v1.json` was NOT modified.

## What was added
- `tools/hermes_predeploy_gate_v1.py` — INERT pre-deploy validation gate. Reuses the governed **pure**
  `utils.hermes_market_hours_health_v1.validate_config_completeness()` and adds pre-deploy-only guards:
  exact/atomic tokenisation (no substring extraction), contamination rejection (internal whitespace / lowercase /
  malformed), inventory-drift + phantom-substitution detection vs an optional expected inventory, and a fail-closed
  suppressive-resolution guard. Emits stable sorted-key JSON with `inventory_digest` (sha256 of sorted exact tokens),
  `config_sha256`, `validator_version`, `source_sha` (git HEAD), `utc_execution_timestamp`. Distinct non-zero exit code
  per failure class.
- `tools/hermes_predeploy_gate.inventory.example` — non-secret real 14-instrument inventory.
- `tests/test_predeploy_gate_v1.py` — 21 assertion-rich tests.
- `docs/tools/hermes_predeploy_gate_v1.md` — usage, exit-code matrix, documented CI command.
- `ops/ci/hermes_predeploy_gate_check.sh` — the documented INERT CI command (NOT a startup hook).

## Purity
Reads files + `git rev-parse HEAD` (read-only subprocess) only. No Redis, no SQL, no network, no env defaults, no
datastore writes. UTC only. Project-relative paths.

## Tests
`python3 -m pytest tests/test_predeploy_gate_v1.py -q` -> **21 passed** (see `pytest_output.txt`). The unrelated
repo-wide async failures / collection errors are pre-existing and out of scope.

## Artifacts in this bundle
| File | What |
|------|------|
| `verdict.txt` | verdict + exit-code matrix summary |
| `help.txt` | captured `--help` |
| `sample_run_pass.json` | clean PASS run (real 14 inventory), exit 0 |
| `sample_run_phantom_fail.json` | WTICO_USD->ICO_USD phantom substitution, exit 18 |
| `pytest_output.txt` | pytest result |
| `SHA256SUMS` | sha256 of the deliverable files + this bundle's artifacts |
