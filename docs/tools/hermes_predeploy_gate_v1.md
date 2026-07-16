# HERMES pre-deployment market-hours gate — `tools/hermes_predeploy_gate_v1.py`

**WO:** WO-HELM-HERMES-MARKET-HOURS-PREDEPLOY-GATE-HARNESS-0001 · **Owner:** HERMES (Helm) · **Status:** INERT (not runtime-wired)

## What it is

A project-local, **INERT** pre-deployment CLI validation gate. It is **not** imported by `main.py`, and it is **not**
part of `docker-compose.yml`, the Dockerfile, systemd units, or any startup path. An operator or CI runs it **before a
deploy** to prove that the configured instrument inventory and the governed market-hours config
(`config/market_hours_schedule.v1.json`) are mutually complete and free of the historical **`WTICO_USD` -> `ICO_USD`
phantom-substitution** defect (a `[A-Z]{3}_[A-Z]{3}` regex once misread `ICO_USD` out of `WTICO_USD`).

It reuses the already-governed **pure** validator
`utils.hermes_market_hours_health_v1.validate_config_completeness()` and layers pre-deploy-only guards on top.

## Purity / safety

Reads files only (the config, optional inventory files) and reads the worktree git HEAD via `git rev-parse` (read-only
subprocess). **No Redis, no SQL, no network, no env defaults, no datastore writes.** Prints machine-readable JSON to
stdout, returns a deterministic exit code. UTC only. Project-relative paths.

## Anti-substring doctrine (the whole point)

- Inventory tokens are split on **comma/newline only**, stripped of surrounding whitespace, then treated **atomically**.
- The tool **never regex-extracts** an instrument-looking substring out of a token: `WTICO_USD` is never silently turned
  into `ICO_USD`.
- A token that (after surrounding-whitespace strip) still contains internal whitespace, is not fully UPPER-CASE, or does
  not fully match `^[A-Z0-9]+_[A-Z0-9]+$` is recorded as **INVALID** and fails the gate — never silently normalised.
- Blank lines and `#`-prefixed comment lines in inventory files are ignored.

## Usage

```bash
# From the repo root. Validate the configured inventory against the governed config.
python3 -m tools.hermes_predeploy_gate_v1 \
    --inventory-file tools/hermes_predeploy_gate.inventory.example

# With drift/phantom detection against an expected inventory:
python3 -m tools.hermes_predeploy_gate_v1 \
    --inventory-file tools/hermes_predeploy_gate.inventory.example \
    --expected-inventory-file tools/hermes_predeploy_gate.inventory.example

# Inline inventory string (comma-separated):
python3 -m tools.hermes_predeploy_gate_v1 \
    --inventory "XAU_USD,XAG_USD,XPT_USD,XCU_USD,GBP_USD,EUR_USD,USD_JPY,AUD_USD,NZD_USD,USD_CAD,USD_CHF,EUR_GBP,WTICO_USD,SPX500_USD"
```

### Arguments

| Arg | Meaning |
|-----|---------|
| `--inventory` | configured inventory as a comma/newline string (mutually exclusive with `--inventory-file`) |
| `--inventory-file` | path to a file with the configured inventory (one per line or comma-separated) |
| `--config` | governed market-hours config (default: `config/market_hours_schedule.v1.json`) |
| `--expected-inventory` | optional expected inventory string (drift detection) |
| `--expected-inventory-file` | optional expected inventory file (drift detection) |

## Documented CI command

```bash
# INERT pre-deploy CI check (NOT wired into startup). Exit 0 = clean pass.
ops/ci/hermes_predeploy_gate_check.sh tools/hermes_predeploy_gate.inventory.example \
                                      tools/hermes_predeploy_gate.inventory.example
```

## Report fields

Stable JSON (sorted keys). Key fields: `verdict`, `exit_code`, `scheduled[]`, `intentionally_fail_closed[]`,
`unmapped[]`, `invalid[]`, `duplicates[]`, `inventory_drift{missing,unexpected,phantom_substitutions}`, `failures[]`,
`inventory_digest` (sha256 of the sorted exact tokens), `config_sha256`, `config_version`, `validator_version`,
`tool_version`, `source_sha` (git HEAD), `utc_execution_timestamp`.

## Exit-code matrix

| Code | Meaning |
|------|---------|
| 0  | clean pass |
| 10 | invalid/contaminated inventory token (whitespace / lowercase / malformed) |
| 11 | inventory drift vs expected |
| 12 | duplicate instrument / alias |
| 13 | unmapped instrument (ungoverned, not declared fail-closed) |
| 14 | unsupported config version |
| 15 | invalid market timezone |
| 16 | mapping to a missing schedule / malformed schedule |
| 17 | fail-closed entry that would resolve to a real (suppressive) schedule |
| 18 | phantom-instrument substitution (e.g. `ICO_USD` where `WTICO_USD` expected) |
| 19 | other invalid config (e.g. reopening_grace out of bounds) |
| 64 | usage error (missing/both inventory sources) |
| 66 | config file missing / not JSON |

When several failure classes co-occur, the reported `exit_code` is chosen by a fixed priority so output is
deterministic. All detected failures are always listed in `failures[]`.
```
