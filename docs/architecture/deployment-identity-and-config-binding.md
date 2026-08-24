# HERMES deployment identity + host/config binding — PERMANENT DEPLOYMENT RULE

**WO:** `WO-HELM-HERMES-DEV-DEPLOYMENT-IDENTITY-AND-HOST-CONFIG-BINDING-0001`
**Change:** `CHG-HERMES-2026-08-24-DEPLOYMENT-IDENTITY-AND-CONFIG-BINDING`
**Context:** hardening from the closed startup/recovery incident
(INC-HERMES-2026-08-20-DEV-SILENT-STREAM-HALT — referenced, not reopened):
the PROD Redis contract published `environment=dev / run_env=STAGING /
deployed_sha=unknown` while `/buildinfo` and `/readiness` were correct.

## Deployment identity doctrine

- **One canonical runtime environment identity**: `ENVIRONMENT` ∈ {`DEV`,`PROD`}
  paired with its governed `RUN_ENV` (`DEV→STAGING`, `PROD→PRODUCTION`, the
  purge-gate identity). No aliases (`dev`, `NON_PROD`, bare `STAGING`); a pair
  contradiction is a wrong-environment config and fails closed.
- **One canonical build identity**: the baked build identity
  (`SOURCE_SHA` == OCI revision == `/buildinfo`). `deployed_sha` in any public
  surface is populated mechanically from it. There is no independently
  maintained deployed-SHA variable; "unknown" is not a deployable identity —
  an invalid build identity fails closed per the governed build contract.
- **Redis/public metadata derives from those same sources**: the manifest,
  publisher heartbeat, `/buildinfo`, `/health` (`deployment_identity` block)
  and `/readiness` all read the single validated `RuntimeIdentity` resolved at
  startup (`utils/hermes_runtime_identity_v1`). The retired variables
  `HERMES_ENVIRONMENT` / `HERMES_RUN_ENV` / `HERMES_DEPLOYED_SHA` must never
  return.
- **Identity-consistency invariant (§9)**: each control-plane publish cycle
  compares the identity a consumer could currently read against the canonical
  runtime identity; a material mismatch (stale keys from an older/foreign
  writer) is surfaced loudly (`[DEPLOYMENT_IDENTITY_MISMATCH]` +
  `fault_counters_summary.deployment_identity_mismatch` on the heartbeat) and
  corrected by that same truthful publish.

## Host binding

- Each environment owns an expected host identity: `EXPECTED_HOSTNAME` in the
  environment-owned runtime env file (DEV: the dell host; PROD: the IONOS
  host — values live in config, never in code).
- The deployment mounts the **host machine's** `/etc/hostname` read-only at
  `HOST_HOSTNAME_PATH` (default `/etc/host-hostname`). Container hostnames are
  not host truth.
- At startup, before any external connection:
  `actual host == EXPECTED_HOSTNAME` or **FAIL CLOSED** (`IDENT-HOST-MISMATCH`).
  Missing expected hostname, unreadable host source, missing/aliased
  environment, and invalid build identity likewise fail closed with stable
  `IDENT-*` codes.
- Behaviour matrix: DEV config on DEV host → PASS · PROD config on PROD host →
  PASS · DEV config on PROD host → FAIL CLOSED · PROD config on DEV host →
  FAIL CLOSED.

## Config-preservation doctrine

- Promotion moves **assured code/image**, never a source environment's
  effective configuration or secrets.
- One shared config schema; DEV and PROD keep separate runtime values/secrets.
- New schema keys are added deliberately to each environment's config with
  environment-appropriate values — never by copying the other environment's
  env file.
- Every deploy captures the effective config before and diffs it after,
  classified UNCHANGED / EXPECTED_NEW_SCHEMA_VALUE / INTENTIONAL_CHANGE /
  UNEXPECTED_DRIFT, requiring `UNEXPECTED_DRIFT = 0`.

## Schema keys

| Key | Meaning |
|-----|---------|
| `EXPECTED_HOSTNAME` | environment-owned declared host (required, no default) |
| `HOST_HOSTNAME_PATH` | where the deployment mounts host `/etc/hostname` (default `/etc/host-hostname`) |

Existing canonical keys reused: `ENVIRONMENT`, `RUN_ENV`, `SOURCE_SHA` (baked).
