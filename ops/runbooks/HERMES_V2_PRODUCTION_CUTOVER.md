# HERMES v2 Production Cutover — Playbook (DRAFT / NOT EXECUTED)

> ## ⛔ AUTHORIZATION BANNER — READ FIRST
>
> **This document authorises NOTHING. It is a files-only blueprint.** No command below has been run.
> Execution is **BLOCKED** behind the gates in §7. In particular:
>
> ### `PRODUCTION KEYSPACE WRITE AUTHORIZATION: NOT GIVEN`
>
> The HERMES candle pipeline must **not** be activated against any production keyspace
> (`hermes:candles:*` canonical, or the `hermes:shadow:prod:candles:*` shadow prefix) as part of this
> cutover. Per `WO-HERMES-PROD-SHADOW-BURN-001`, prod-keyspace writes + execution are recorded
> **NOT GIVEN**. The container deploys with the candle-forward seam in its current **gated/disabled**
> state and the backfill recovery engine **inert** (no architect signature). The live runtime remains
> **frozen at `ed07173`** until a separate, explicitly-authorised cutover WO.

| Field | Value |
|-------|-------|
| Status | **DRAFT — NOT EXECUTED** |
| Lane | Helm / HERMES |
| Target master HEAD | `1b47a97` (`origin/main`, audited-green) |
| Current runtime | bare systemd `signal-service-dev.service` @ `ed07173` |
| Container stack | dedicated `hermes` compose project (NOT yet deployed) |

---

## 1. Ground truth of the target host (verified read-only, 2026-06-23)

The original cutover draft referenced objects that **do not exist** on `dell-debian`. Corrected here:

| Original (WRONG) | Reality |
|------------------|---------|
| `hermes-signal-service.service` | **No such unit.** Real unit: **`signal-service-dev.service`** (`ExecStart=/usr/bin/python3 main.py`, `WorkingDirectory=/srv-dev/tradingSignals`, `EnvironmentFile=/srv-dev/tradingSignals/.env`). |
| `/app/hermes-runtime` | **Does not exist.** Runtime dir: **`/srv-dev/tradingSignals`** (a **dev** path — a true prod path must be confirmed before any prod cutover). |
| "down the old HERMES compose stack" | **There is no HERMES compose stack.** The runtime is **bare systemd**; the container image has never been deployed. |
| `docker compose down --volumes` | **FORBIDDEN.** The host runs **shared** compose projects — `proteus-infra` (incl. `proteus-redis` :6379, `proteus-redis-dev` :6380, `proteus-mariadb-dev` :3307), `ares`, `graylog`, `mt5_plutus`, `web-console`. A broad `down --volumes` would **delete shared platform data volumes** (ARES/NEO/Solo/etc.) — catastrophic, irreversible. |

## 2. Non-negotiable safety rules for this cutover

1. **NEVER `docker compose down --volumes`.** HERMES owns named volumes `hermes_archive` + `hermes_db_data`; deleting them is data loss, and a mis-scoped `down` endangers shared infra.
2. **ALWAYS scope compose to the dedicated project + file:** `docker compose -p hermes -f docker-compose.yml …`. Never a bare `docker compose` from an ambiguous directory.
3. **NEVER `git reset --hard`.** Advance the tree with a clean-tree `git fetch` + fast-forward/checkout only; abort if the working tree is dirty.
4. **Canonical `hermes:candles:*` stays DARK.** No prod-keyspace activation (see banner).
5. Every command block below is marked **NOT EXECUTED** — run only under the §7 authorization.

---

## 3. Phase 1 — Legacy quiesce (prevent dual-writer collision)

```bash
# NOT EXECUTED — requires §7 authorization.
# 1. Stop the REAL bare-systemd unit (not the non-existent hermes-signal-service.service).
sudo systemctl stop signal-service-dev.service
systemctl is-active signal-service-dev.service   # expect: inactive

# 2. There is NO HERMES compose stack to tear down. DO NOT run `docker compose down --volumes`.
#    (If a prior hermes project somehow exists, scope it explicitly and WITHOUT --volumes:)
#    docker compose -p hermes -f docker-compose.yml down            # NO --volumes, NO --remove-orphans on shared infra

# 3. Confirm the canonical keyspace is idle/dark (READ-ONLY — never flushes):
docker exec proteus-redis redis-cli -p 6379 --scan --pattern 'hermes:candles:*' | head   # expect: empty (DARK)
docker exec proteus-redis redis-cli -p 6379 CLIENT LIST | grep -c hermes                  # expect: 0 hermes writers
```

## 4. Phase 2 — Code alignment (NO `git reset --hard`)

```bash
# NOT EXECUTED — requires §7 authorization.
cd /srv-dev/tradingSignals        # NOTE: dev path — confirm the true PROD path before a real prod cutover.

# Pre-flight: the tree MUST be clean (only the known pre-existing untracked *.pre_*_backup_* allowed).
git status --porcelain=v1 | grep -vE '\.pre_.*_backup_|/ops/evidence/' && echo "DIRTY -> STOP" || echo "clean"

# Advance to the audited-green HEAD via fast-forward / checkout — NEVER reset --hard.
git fetch origin
git checkout 1b47a97b165156a3f39d3a53d7c79ff88916291b    # detached checkout of the verified SHA
grep -c 'def normalise_utc' utils/candle_contract_v1.py  # sanity: fix present

# Enforce the immutable boot-gate permission bit.
chmod 0755 docker/entrypoint.sh
```

## 5. Phase 3 — Build & launch (dedicated project, candle pipeline GATED)

```bash
# NOT EXECUTED — requires §7 authorization.
# .env MUST NOT activate prod-keyspace writes: leave HERMES_CANDLE_FORWARD_* in its gated/disabled state,
# do NOT set HERMES_CANDLE_FORWARD_SHADOW_KEY_PREFIX=hermes:shadow:prod:candles, and do NOT set
# HERMES_BACKFILL_RECOVERY_ENABLED/HERMES_ARCHITECT_RECOVERY_SIGNATURE (recovery stays INERT).
# .env MUST surface the cap pins so the boot gate can assert: HERMES_CPUS/MEM_LIMIT/PIDS_LIMIT.

docker compose -p hermes -f docker-compose.yml build --no-cache    # cache-bust the entrypoint hook
docker compose -p hermes -f docker-compose.yml up -d               # scoped to the `hermes` project ONLY
```

## 6. Boot-gate verification (accurate expected output)

```bash
docker compose -p hermes -f docker-compose.yml logs hermes-signal | tail -40
```

**GREEN (boots):**
```text
[BOOT-GATE] Initializing infrastructure alignment check...
[BOOT-GATE] Verification passed cleanly.
[RECOVERY] inert — HERMES_BACKFILL_RECOVERY_ENABLED != 'TRUE'; recovery is dead-code (no-op).
[BOOT-GATE] Handoff to live real-time runtime...
... (signal service starts; candle-forward seam DISABLED; canonical DARK) ...
```

**HALT — cgroup cap mismatch (fail-closed, RC=101):**
```text
[BOOT-GATE] Initializing infrastructure alignment check...
=================================================================
[CRITICAL FAULT] CONTAINER INFRASTRUCTURE BREAKOUT OR MISMATCH
Execution aborted by Guard Gate. Diagnostic Signature Below:
Diagnostic: GOV-STAGE-CAP-001 (Memory Ceiling Breach / CPU Allocation Mismatch)
=================================================================
```

> Note: the recovery engine is **inert** in this deployment, so the `RC=102` terminal-halt path is not
> reachable here (it only fires when the engine is fully armed under a separate signed WO). Macro/funding
> indicator log lines from the original draft are **out of scope** — HERMES is a raw OANDA candle/tick
> service and emits no such signals.

## 7. Authorization gates (ALL required before any execution)

| # | Gate | State |
|---|------|-------|
| 1 | **Production keyspace write authorization** | **NOT GIVEN** |
| 2 | Explicit cutover/execution authorization (unfreeze `ed07173`) | **NOT GIVEN** |
| 3 | Confirmed **true production** host + path (this playbook targets the dev box) | **OPEN** |
| 4 | `.env` reviewed to keep candle-forward gated + recovery inert | **OPEN** |
| 5 | Rollback rehearsed (below) | **OPEN** |

## 8. Rollback

```bash
# NOT EXECUTED. Scoped to the hermes project; NEVER --volumes.
docker compose -p hermes -f docker-compose.yml down            # stop new containers (data volumes retained)
git -C /srv-dev/tradingSignals checkout ed07173               # restore prior code pointer
sudo systemctl start signal-service-dev.service               # restore the legacy bare-systemd runtime
```

---

*Provenance:* host reality verified read-only on dell-debian 2026-06-23; master HEAD `1b47a97`; runtime
frozen at `ed07173`. Authorization gates per `WO-HERMES-PROD-SHADOW-BURN-001`. **DRAFT — NOT EXECUTED.**
