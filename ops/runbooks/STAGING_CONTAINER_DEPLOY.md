# HERMES — Staging Container Deployment & Cutover Runbook

**THREAD-ALPHA — Staged Container Isolation.**
Brings the PR#32 / PR#33 container footprints (`Dockerfile`, `docker-compose.yml`)
from *design-on-main* to *active configuration verification* on the staging/dev box,
and defines the socket-preserving cutover from the legacy bare-systemd unit to the
containerised stack.

---

> ## SAFETY BANNER — READ FIRST
> **This runbook is a FILES-ONLY DESIGN deliverable. NOTHING here has been executed.**
> - No systemd unit has been touched. No container has been started. No host has been SSH'd.
> - Every command block below is labelled **NOT EXECUTED — staging procedure for sign-off**.
> - **Production / live is untouched.** This procedure targets the staging/dev box ONLY.
> - The **canonical candle keyspace is NOT in scope here** — staging shadow-writes only;
>   canonical stays dark throughout the parallel window.
> - The legacy runtime (`signal-service-dev.service`, code pinned at `ed07173`) is a
>   **documentation target only** — this thread changes none of its code.

---

## 1. Purpose & Scope

| | |
|---|---|
| **Goal** | Stand up the container stack on staging, prove it healthy + capped + streaming, then cut over from the bare systemd unit **without dropping the inbound OANDA stream or in-flight HTTP/Redis/DB connections.** |
| **In scope** | Image build from on-main `Dockerfile`; `docker compose` validation; staging `.env`; parallel/shadow bring-up; health-gating; drained cutover; rollback; resource-cap verification. |
| **Out of scope** | Production deploy. Canonical candle keyspace activation. Any change to `signal-service-dev.service` code (`ed07173`). Any change to `proteus-mariadb-dev` / dev Redis schemas. |
| **Legacy target (do not modify)** | `signal-service-dev.service` on dell-debian: `ExecStart=/usr/bin/python3 main.py`, `WorkingDirectory=/srv-dev/tradingSignals`, `EnvironmentFile=/srv-dev/tradingSignals/.env`, `User=root`. Publishes FastAPI on host `:8210`; ingests live OANDA price stream; writes candles to `proteus-mariadb-dev` (host `:3307`) and shadow candles to dev Redis (`:6380`). |

---

## 2. Pre-flight checklist

> **NOT EXECUTED — staging procedure for sign-off.**

```bash
# --- 2.1 Toolchain present ---
docker --version                       # expect Docker Engine >= 24.x (cgroup v2)
docker compose version                 # expect Compose v2 plugin

# --- 2.2 Repo at the intended commit, staging .env in place ---
cd /srv-dev/tradingSignals             # staging checkout of the hermes repo
git rev-parse HEAD                     # confirm the container footprint commit
test -f .env && echo ".env present" || echo "MISSING .env — copy ops/staging/staging.env.example"
cp -n ops/staging/staging.env.example .env   # then fill placeholder secrets (NEVER commit .env)

# --- 2.3 Build the runtime image from the ON-MAIN two-stage Dockerfile ---
docker compose --env-file .env build hermes-signal     # target: runtime (no toolchain leak)

# --- 2.4 Validate the resolved compose (env interpolation, no surprises) ---
docker compose --env-file .env config | less
#   CONFIRM:
#     * hermes-mariadb has NO ports: block (internal bridge only)
#     * hermes-signal ports == "8211:8210"  (PARALLEL phase — see §3)
#     * cpus/mem_limit/mem_reservation/pids_limit resolve to 4 / 4g / 1g / 512
#     * logging.driver == gelf, gelf-address points at the staging Graylog
#     * security_opt no-new-privileges:true on BOTH services

# --- 2.5 Disk / inode headroom for named volumes (hermes_db_data, hermes_archive) ---
df -h  /var/lib/docker
df -i  /var/lib/docker                  # inodes matter for high-volume candle history

# --- 2.6 Host port 8210 ownership (still the systemd unit's during pre-flight) ---
ss -ltnp 'sport = :8210'                # EXPECT: owned by the python3 process of signal-service-dev
ss -ltnp 'sport = :8211'                # EXPECT: free (the parallel container will claim it)
systemctl is-active signal-service-dev.service   # EXPECT: active
```

**Gate:** do not proceed past pre-flight unless 8210 is the systemd unit's, 8211 is free,
`compose config` is clean, and the image built.

---

## 3. systemd → docker-compose handoff (CORE DELIVERABLE)

A precise, ordered, **socket-preserving** cutover. The container is first brought up on an
**alternate published host port (8211)** so it runs **in parallel** with the systemd unit
(which keeps host `:8210` and the live OANDA stream). Both can shadow-write harmlessly while
canonical stays dark. Only after the container is proven healthy + streaming do we drain and
disable the systemd unit, then re-point the container to canonical `:8210`.

> **Why the overlap window is safe:** shadow writes are **idempotent** (same candle key →
> same value), the **canonical keyspace is untouched** throughout, and the **DB upsert is
> idempotent** (insert-or-update on the candle primary key). Two writers briefly producing the
> same shadow candle converge to one value — no divergence, no data loss. The OANDA inbound
> socket is a per-process long-lived connection; running a second consumer does not disturb
> the first.

> **NOT EXECUTED — staging procedure for sign-off.**

### (a) Parallel bring-up on the ALTERNATE port (8211)

```bash
cd /srv-dev/tradingSignals
# staging.env sets SIGNAL_BIND_PORT=8211, SIGNAL_PORT=8210 -> publishes 8211:8210
docker compose --env-file .env up -d            # brings up hermes-mariadb + hermes-signal
docker compose ps                               # both Up; hermes-mariadb (healthy)
```
The systemd unit continues to own `:8210` and the live stream — untouched.

### (b) Health-gate the container

```bash
# Container healthcheck must be green (FastAPI accepting on its in-container :8210).
docker inspect --format '{{.State.Health.Status}}' hermes-signal   # EXPECT: healthy

# Confirm it is actually STREAMING + writing SHADOW keys (not just listening):
curl -fsS http://127.0.0.1:8211/health           # service-level health on the parallel port
docker logs --since 2m hermes-signal | grep -Ei 'oanda|stream|connected'   # stream established
docker logs --since 2m hermes-signal | grep -Ei 'shadow'                   # shadow writes flowing
# Cap binding proof (see §4): run the harness INSIDE the container.
docker exec -it hermes-signal python ops/staging/resource_cap_verify.py
```
**Gate:** healthcheck `healthy` AND stream connected AND shadow keys advancing AND
`resource_cap_verify.py` => `RESULT: PASS`. Do not drain until all four hold.

### (c) Drain the systemd unit

```bash
# Stop NEW traffic to the systemd unit; let in-flight HTTP requests complete.
# (Front-door dependent: stop routing to :8210, or pause upstream callers.) Then observe quiesce:
ss -tnp 'dport = :8210 or sport = :8210'         # watch ESTABLISHED count fall to ~0
journalctl -u signal-service-dev.service --since '1 min ago' -f   # confirm requests draining
```
Allow in-flight requests to finish naturally. Do **not** kill mid-request.

### (d) Disable the legacy systemd unit

```bash
sudo systemctl disable --now signal-service-dev.service
systemctl is-active signal-service-dev.service    # EXPECT: inactive
systemctl is-enabled signal-service-dev.service   # EXPECT: disabled
ss -ltnp 'sport = :8210'                          # EXPECT: now FREE
```
Host `:8210` is released and the unit will not return on reboot.

### (e) Re-point the container to canonical port 8210

```bash
# Flip the published host port back to canonical 8210.
# Option A (preferred): edit .env -> SIGNAL_BIND_PORT=8210, then:
docker compose --env-file .env up -d              # recreates hermes-signal on 8210:8210
# Option B: a compose override file pinning ports: ["8210:8210"], then up -d.
docker compose ps                                 # hermes-signal published on 8210
```

### (f) Verify the cutover

```bash
docker inspect --format '{{.State.Health.Status}}' hermes-signal   # healthy
curl -fsS http://127.0.0.1:8210/health                              # canonical port answers
docker logs --since 2m hermes-signal | grep -Ei 'oanda|stream'      # stream re-established on container
ss -ltnp 'sport = :8210'                                            # owned by docker-proxy / container
docker exec -it hermes-signal python ops/staging/resource_cap_verify.py   # caps still PASS
```

### (g) Rollback (if any gate fails)

```bash
# 1. Re-enable the legacy unit (reclaims :8210 + the live stream):
sudo systemctl enable --now signal-service-dev.service
systemctl is-active signal-service-dev.service     # active
# 2. Tear the container stack down (DB volume preserved unless -v is added):
docker compose --env-file .env down                # NEVER pass -v here (keep hermes_db_data)
```
Rollback returns to the known-good systemd runtime with no data loss (shadow writes were
idempotent; canonical was never touched).

---

## 4. Resource-cap verification

The compose stack pins `cpus=4`, `mem_limit=4g`, `mem_reservation=1g`, `pids_limit=512` to
emulate a 4 vCore / 4 GB IONOS M+ VPS. **`ops/staging/resource_cap_verify.py`** proves those
pins actually bound as effective cgroup v2 limits.

**How to run** (inside the running container):
```bash
docker exec -it hermes-signal python ops/staging/resource_cap_verify.py
# optional bounded mock telemetry load while watching the caps hold (still safe/bounded):
docker exec -it hermes-signal python ops/staging/resource_cap_verify.py --load --duration 10
```

**What it asserts:** the EFFECTIVE cgroup v2 caps (`/sys/fs/cgroup/memory.max`,
`/sys/fs/cgroup/cpu.max`, `/sys/fs/cgroup/pids.max`) **equal** the CONFIGURED compose pins
(parsed from `docker-compose.yml`, or supplied via `--cpus/--mem/--pids` / `HERMES_*` env).

**Pass / fail criteria:**

| Outcome | Meaning |
|---------|---------|
| `RESULT: PASS` | every effective cap matches its configured pin — sign-off evidence |
| `GOV-STAGE-CAP-001` | effective cap drifts from configured (or is `max`/unlimited — pin did not bind) → **FAIL** |
| `GOV-STAGE-CAP-002` | cannot read a cgroup v2 limit (not in-container / cgroup v1 host) → **FAIL** |
| `GOV-STAGE-CAP-003` | a configured cap could not be resolved from compose and none supplied → **FAIL** |

The harness is **standalone, default `--dry-run`, pure stdlib, no network, no deletes**, and
is **never imported by `main.py`**.

---

## 5. Environment configuration

The staging container profile is **`ops/staging/staging.env.example`**. Copy it to `.env` on
the staging box and fill the placeholder secrets at deploy time (never commit a populated
`.env`). Key points:

- **`RUN_ENV=STAGING`** — explicit, non-production. Gates the out-of-band purge engine
  (`ops/purge.py`): only `RUN_ENV=PRODUCTION` can delete, so STAGING **bypasses** (ledger
  preserved). `RUN_ENV` is deliberately separate from `ENVIRONMENT` (which drives
  `{ENVIRONMENT}_DB_HOST` resolution in `env_config`).
- **`SIGNAL_BIND_PORT`** — `8211` during the parallel phase (§3a), flipped to `8210` at
  cutover (§3e). `SIGNAL_PORT=8210` stays constant (in-container bind + healthcheck probe).
- **Resource pins** — `HERMES_CPUS/MEM_LIMIT/MEM_RESERVATION/PIDS_LIMIT` must equal the caps
  verified in §4.
- **Bundled DB** — `DB_NAME/DB_PASSWORD/DB_ROOT_PASSWORD` for the internal `hermes-mariadb`
  (internal bridge only, no host port). Secrets are placeholders.
- **GELF/SIEM** — `SIEM_GRAYLOG_IP` (canonical), `GRAYLOG_HOST` (legacy fallback),
  `SIEM_GRAYLOG_PORT=12201`.

---

## 6. Reason-code / rollback reference

| Code / signal | Where | Meaning | Action |
|---------------|-------|---------|--------|
| `GOV-STAGE-CAP-001` | §4 harness | effective cgroup cap ≠ configured pin (or uncapped) | FAIL — do not cut over; re-check compose `cpus/mem_limit/pids_limit` |
| `GOV-STAGE-CAP-002` | §4 harness | cannot read cgroup v2 limit | run INSIDE container on a cgroup-v2 host |
| `GOV-STAGE-CAP-003` | §4 harness | configured cap unresolved | pass `--cpus/--mem/--pids` or set `HERMES_*` |
| healthcheck `unhealthy` | §3b | FastAPI not accepting on in-container `:8210` | hold at parallel phase; inspect `docker logs` |
| stream not connected | §3b | OANDA inbound socket not established | hold; do NOT drain the systemd unit |
| in-flight not drained | §3c | ESTABLISHED on `:8210` not at ~0 | wait; never kill mid-request |
| any cutover gate fails | §3 | container not proven good | **§3g rollback** — re-enable systemd unit, `compose down` (no `-v`) |

---

## 7. Definition of Done

**Done = container live AND reboot-safe AND systemd unit cleanly disabled.**

- [ ] `hermes-signal` running, healthcheck `healthy`, OANDA stream connected, shadow writes flowing.
- [ ] `ops/staging/resource_cap_verify.py` => `RESULT: PASS` (caps bound; no `GOV-STAGE-CAP-*`).
- [ ] Container published on canonical host `:8210`; `curl :8210/health` answers.
- [ ] **Reboot-safe:** compose `restart: unless-stopped` set on both services (in `docker-compose.yml`), so the stack returns after a host reboot.
- [ ] **systemd unit cleanly disabled:** `signal-service-dev.service` is `inactive` AND `disabled` (will not return on reboot); host `:8210` owned by the container.
- [ ] Production / live untouched; canonical candle keyspace untouched.
