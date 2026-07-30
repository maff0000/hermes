# HERMES Container MVP — WP2: Canonical Build & Externalised Configuration

**WO:** WO-HELM-HERMES-CONTAINER-MVP-WP2-CANONICAL-BUILD-AND-EXTERNALISED-CONFIGURATION-0001
**Status:** NON-PROMOTED ENGINEERING CANDIDATE — design + source only. All UTC.

> **HARD STATEMENT — read first.** The image described here is a **NON-PROMOTED engineering candidate**.
> It has **NOT** been deployed. It has **NOT** contacted OANDA. It has **NOT** written SQL or Redis.
> It has **NOT** been shadow-tested. **WP3 remains required** before any promotion. Nothing in this WP
> starts, launches, or promotes anything.

---

## 1. Purpose

Make the HERMES signal-service container image **externally configurable**, **externally identifiable**,
and **observably correct**, with BOUNDED, backward-compatible changes:

- deterministic dependency resolution (pinned `constraints.txt`);
- externally-visible, secret-free **build identity** (`/status` + `/buildinfo`);
- generic secret **`_FILE`** references (fail-closed);
- HERMES-owned Discord (the ARES cross-app path is **retired/forbidden**);
- an **application-level** container healthcheck that is **flap-safe** during transient OANDA recovery;
- a `/status` correction so it no longer reports `disconnected` when the stream is `FLOWING`.

No SQL migration, no Redis contract/key/TTL change, no entrypoint/user change, no cross-application change.

---

## 2. Candidate build command

```bash
docker build \
  --build-arg SOURCE_SHA=<full-40-hex-commit-sha> \
  --build-arg BUILD_UTC=<tz-aware UTC ISO-8601, e.g. 2026-07-29T00:00:00+00:00> \
  --build-arg HERMES_IMAGE_REF=hermes-signal:candidate-<sha12> \
  -t hermes-signal:candidate-<sha12> \
  .
```

- `SOURCE_SHA` **MUST** be the full 40-hex commit SHA. If absent/`latest`/non-hex, the running app sets
  `build_identity_valid: false` (diagnostic; it does not crash).
- **Candidate tag convention:** `hermes-signal:candidate-<sha12>` — **never** `latest`, `stable`, or
  `prod`. The first 12 hex chars of `SOURCE_SHA` are the `<sha12>`.

## 3. Deterministic dependency mechanism (`constraints.txt`)

- `requirements.txt` keeps the **declared ranges** (`>=`) — the human-authored intent.
- `constraints.txt` **pins** every resolved version (46 packages) to the currently-deployed runtime image,
  so the candidate builds **byte-for-byte the same dependency set** (max compatibility, no upgrades).
- The `Dockerfile` **consumes** it in BOTH stages:
  - builder: `pip wheel ... -r requirements.txt -c constraints.txt`
  - runtime: `pip install --no-index --find-links=/wheels -r requirements.txt -c constraints.txt`
- `constraints.txt` is **not regenerated** by this WP; it is authoritative input.

## 4. External configuration + secret `_FILE` references

`env_config.get_secret(key, *, default, required)` resolves a secret with `_FILE` support:

| Order | Source | Notes |
|-------|--------|-------|
| 1 | `{ENV}_{key}_FILE` then `{key}_FILE` | read the referenced file (source = `file:<path>`) |
| 2 | `{ENV}_{key}` then `{key}` (via `get_env`) | direct value (source = `env`) |

Fail-closed rules (the secret **value is never logged** — only the key name + source kind):

- **Both** a `_FILE` ref AND a direct value set → `ValueError('SECRET-SOURCE-CONFLICT')` (names the two
  SOURCES, never the values). *One explicit documented rule: file+direct fails closed.*
- Missing/unreadable file → `SECRET-FILE-UNREADABLE`.
- Empty file (after stripping a single trailing newline + trailing whitespace) → `SECRET-FILE-EMPTY`.
- `required=True` and neither present → `SECRET-REQUIRED-MISSING`.
- File mode `& 0o077` (group/world accessible) → **WARNING** only (policy = warn, does not fail).

`_FILE` refs are **preferred** and MUST be HERMES-owned / container-mounted (e.g. a Docker/Compose secret),
**never** an ARES path such as `/srv-dev/tradingProteus/ares/.env`.

`.env.example` (HERMES-owned, no real values) documents: `SOURCE_SHA`, `BUILD_UTC`, `HERMES_IMAGE_REF`,
`OANDA_API_KEY` / `OANDA_API_KEY_FILE`, `DB_PASSWORD` / `DB_PASSWORD_FILE`,
`DISCORD_WEBHOOK_DEV` / `_FILE`, `DISCORD_WEBHOOK_PROD` / `_FILE`, `CONSUMER_LIVE`, `RUN_ENV`, `SIGNAL_PORT`.

## 5. HERMES-owned Discord (ARES path RETIRED)

`utils/discord_alerts.py` sources webhooks **only** via `get_secret("DISCORD_WEBHOOK_DEV" | "..._PROD")`,
so a `_FILE` reference works. An explicit guard **REFUSES** any resolved path/value that points at
`/srv-dev/tradingProteus` or an `ares/.env` path → logs `DISCORD-CROSS-APP-REFERENCE-REJECTED`
(no secret) and returns unconfigured (no cross-project fallback). New helpers:

- `discord_configured() -> bool`
- `discord_destination_metadata() -> {"configured": bool, "source": "env"|"file"|"none", "environment": ENV}`
  — **never** contains the URL.

> The ARES deploy-time value `MONITOR_DISCORD_WEBHOOK_SOURCE_FILE=/srv-dev/tradingProteus/ares/.env`
> is **RETIRED/forbidden**. It is not in canonical source, not read by HERMES code, and is rejected at
> runtime. `docker-compose.yml` carries a prominent comment to the same effect.

## 6. Externally-visible build identity

`utils/hermes_build_identity_v1.build_identity()` returns a **secret-free** dict read from Dockerfile ENV,
fail-soft to explicit sentinels (**never** `latest`):

| Field | Source | Sentinel |
|-------|--------|----------|
| `application` | constant | `HERMES` |
| `source_sha` | `SOURCE_SHA` | `UNKNOWN_SOURCE_SHA` |
| `build_utc` | `BUILD_UTC` | `UNKNOWN_BUILD_UTC` |
| `image_ref` | `HERMES_IMAGE_REF` | `UNKNOWN_IMAGE` |
| `config_version` | `HERMES_CONFIG_VERSION` → live market-hours config_version → | `UNKNOWN` |
| `build_classification` | `HERMES_BUILD_CLASSIFICATION` | `UNVERIFIED` |
| `repository` | `HERMES_REPO` | `hermes` |
| `build_identity_valid` / `build_identity_reasons` | computed in CANDIDATE mode | — |

`validate_build_identity(d) -> list[str]` (pure) returns reason codes:
`BI-SHA-MISSING`, `BI-SHA-NOT-40-HEX`, `BI-SHA-IS-LATEST`, `BI-BUILD-UTC-MISSING`.
In CANDIDATE mode (`build_classification == NON_PROMOTED_ENGINEERING_CANDIDATE`) an invalid `source_sha`
sets `build_identity_valid: false` — **the app does not crash** (runtime diagnostic).

Exposed via:
- `GET /buildinfo` → `build_identity()` + `{consumer_live, runtime_mode, authoritative_stream_state}`.
- `GET /status` → top-level `build_identity` block + `config_version`.

Dockerfile ENV/LABEL additions: `HERMES_APP=HERMES`,
`HERMES_BUILD_CLASSIFICATION=NON_PROMOTED_ENGINEERING_CANDIDATE`, `HERMES_CONFIG_VERSION=3`,
`HERMES_REPO=hermes`, `HERMES_IMAGE_REF`, plus OCI labels
`org.opencontainers.image.title="HERMES signal-service"`, `org.opencontainers.image.source`,
`com.hermes.build.classification`, `com.hermes.config.version="3"`.

## 7. Authoritative health / ready / status meanings

| Endpoint | Truth | Notes |
|----------|-------|-------|
| `/health` | **AUTHORITATIVE** | `watchdog.get_health_snapshot()` — `health_state` GREEN/AMBER/RED, `stream_state` (e.g. `FLOWING`), per-instrument freshness. HTTP 503 iff RED. **UNCHANGED by WP2.** |
| `/ready` | tick-freshness | 503 if no/stale ticks. **UNCHANGED.** |
| `/metrics` | Prometheus text | **UNCHANGED.** |
| `/status` | admin detail — **now authoritative-consistent** | see §8. |
| `/buildinfo` | build identity (new) | secret-free. |

### 8. `/status` correction + backward-compat

The legacy `/status` read `oanda_adapter.health.to_dict()` and could report `state: "disconnected"` even
while the watchdog stream was `FLOWING`. WP2 makes `/status` derive the **current** state from the
authoritative watchdog snapshot, while **keeping every legacy field present** (relabelled historical):

`adapters["oanda"]`:
- `state` → authoritative current (`FLOWING` → `connected`); **never** `disconnected` when FLOWING.
- `current_stream_state` → authoritative `stream_state`; `is_flowing` → boolean.
- `last_tick_at` → kept.
- `legacy_adapter_state`, `historical_cumulative_error_count`, `historical_last_error` +
  `historical_last_error_is_current: false` → the demoted legacy values.

Backward-compatible top-level keys retained: `service`, `version`, `started_at`, `active_source`,
`instruments`, `tick_count`, `adapters`. Added: `build_identity`, `config_version`,
`authoritative_health`, `authoritative_stream_state`, `consumer_live`, `runtime_mode`, `runner_count`,
`freshness_summary`, `sql_state`, `redis_state`, `last_recovery_utc`. **No secrets**; any OANDA account id
is masked to its first 6 chars via `mask_account`. `sql_state`/`redis_state` are best-effort
`configured`/`unknown` from ENV presence only — **no new connections opened**.

The builder is factored into `utils/hermes_status_payload_v1.build_status_payload(state)` (pure, testable
without a running server).

## 9. Application-level, flap-safe healthcheck

The Dockerfile `HEALTHCHECK` now GETs `http://127.0.0.1:${SIGNAL_PORT}/health` (stdlib urllib, bounded 3s)
via `utils/hermes_healthcheck_probe_v1.run_probe()`. Decision (`healthcheck_decode(status, health_state)`):

| Response | Exit | Rationale |
|----------|------|-----------|
| 200 + GREEN | 0 | healthy |
| 200 + AMBER | 0 | **transient-recovery tolerant — does NOT flap** during a short OANDA recovery |
| 503 + RED | 1 | unhealthy |
| connection-refused (no response) | 1 | process not listening |
| 200 + no `health_state` body | 1 | **listening-but-dead** rejected (distinct from connection-refused) |

Cadence unchanged: `--interval=30s --timeout=5s --start-period=40s --retries=3`. Non-root `hermes`,
entrypoint (`/app/docker/entrypoint.sh` → `python main.py`), WORKDIR `/app`, EXPOSE are **UNCHANGED**.

## 10. Candidate image inspection steps

```bash
# Identity labels
docker inspect --format '{{json .Config.Labels}}' hermes-signal:candidate-<sha12>
# Build-identity ENV baked in
docker inspect --format '{{json .Config.Env}}' hermes-signal:candidate-<sha12>
# Runtime identity (in a throwaway run — WP3 gated; NOT part of WP2)
#   curl -s localhost:${SIGNAL_PORT}/buildinfo
#   curl -s localhost:${SIGNAL_PORT}/status | jq '.build_identity, .authoritative_stream_state'
```

## 11. WP3 prerequisites & rollback

- **WP3 is required** before promotion: shadow test the candidate, prove it reaches OANDA/SQL/Redis
  correctly under governance, verify `/buildinfo` + `/status` on a live run, and confirm the app-level
  healthcheck behaves under real transient recovery.
- **Rollback image preservation:** keep the previously-deployed image tag; promote by re-tagging a
  validated `candidate-<sha12>`, never by mutating `latest`. The prior candidate/tag is retained so a
  rollback is a re-point, not a rebuild.

---

### Explicit non-actions in WP2
No image built, no service launched, no OANDA contact, no SQL/Redis write, no migration, no Redis
key/TTL change, no entrypoint/user change, no cross-application change, no real secret read.

---

## Secret-file path boundary (C-WP2-SECRET-FILE-UNBOUNDED-PATH correction)

WO-HELM-HERMES-CONTAINER-MVP-WP2-PR125-SECRET-FILE-PATH-BOUNDARY-CORRECTION-0001.

R2D2's WP2 exact-head audit (AMBER, `C-WP2-SECRET-FILE-UNBOUNDED-PATH`) proved the first-cut loader was an
arbitrary-file read primitive (it read `/etc/passwd` and `/proc/self/environ`). The corrected loader enforces
a HERMES-owned path policy:

- **Allowed root** — every `*_FILE` secret reference must resolve BENEATH an explicit, externally
  configurable, **non-secret** root: `HERMES_SECRET_ROOT` (default `/run/secrets`, the conventional
  read-only Docker-secret mount). The root must be an absolute, existing **directory** or the loader fails
  closed (`SECRET-ROOT-RELATIVE` / `SECRET-ROOT-MISSING` / `SECRET-ROOT-NOT-DIRECTORY`). The root is never
  a hidden host default — it is overridable and defaults only to the conventional secret mount.
- **Containment** — the requested path (absolute, or relative-to-root) is resolved with `realpath` (which
  collapses `..` and symlinks) and must remain beneath the canonical root, else `SECRET-PATH-OUTSIDE-ROOT`.
  Traversal and symlink escape therefore fail. A final-component symlink is additionally refused at open
  time via `O_NOFOLLOW` (`SECRET-PATH-SYMLINK`).
- **Pseudo-filesystems / cross-app** — a resolved path under `/proc`, `/sys`, `/dev`, `/srv-dev` →
  `SECRET-SPECIAL-FS`; a path referencing another application's tree (`tradingproteus`, `ares/.env`) →
  `SECRET-CROSS-APP-PATH`.
- **File validation on the opened descriptor** (TOCTOU-hardened — we validate the fd we actually opened,
  not the path): must be a **regular file** (`SECRET-FILE-NOT-REGULAR` for dir/FIFO/socket/device/pseudo-
  file), within a bounded **8 KiB** (`MAX_SECRET_FILE_BYTES`, `SECRET-FILE-TOO-LARGE`), non-empty after a
  single trailing-newline + whitespace strip (`SECRET-FILE-EMPTY`), readable (`SECRET-FILE-UNREADABLE`).
- **Source conflict** — a `_FILE` reference AND a direct value both set → `SECRET-SOURCE-CONFLICT` (the two
  values are never compared and never appear in the message; only the SOURCE names). Neither present +
  required → `SECRET-REQUIRED-MISSING`; neither present + optional → the caller's explicit default.
- **No leakage** — the secret value never appears in logs (only the key name + source kind), in exception
  messages, or on any endpoint. Docker-secret and read-only bind-mounted secret directories remain
  supported; no secret is ever copied into the project tree to satisfy the policy.

Deploy pattern: mount HERMES-owned secrets read-only under the root, e.g.
`--mount type=bind,ro,src=/host/hermes-secrets,dst=/run/secrets` and set `OANDA_API_KEY_FILE=/run/secrets/oanda_api_key`.

---

## Positive secret-root policy (C-WP2-SECRET-ROOT-BROAD-ROOT-ACCEPTED correction)

WO-HELM-HERMES-CONTAINER-MVP-WP2-PR125-POSITIVE-SECRET-ROOT-POLICY-CORRECTION-0001.

R2D2's re-audit closed the path-containment finding but flagged a residual: the root check accepted **any**
absolute existing directory, so `HERMES_SECRET_ROOT=/etc` (with `_FILE=/etc/passwd`) would read a regular
file beneath a broad root. The root is now a **positive allow-list**, not a deny-list.

- **Authorised roots** — `_AUTHORISED_SECRET_ROOTS = ("/run/secrets", "/run/hermes/secrets",
  "/var/run/hermes/secrets")` is a **module constant** (an env/operator cannot broaden it). The configured
  `HERMES_SECRET_ROOT` must equal, or be a directory beneath, one of these governed HERMES secret parents.
  Default is `/run/secrets`. A governed alternative is declared by extending the constant in the config
  contract, never via an environment allow-list variable.
- **Rejections (fail-closed):** a symlinked root → `SECRET-ROOT-SYMLINK`; a broad system/user root
  (`/`, `/etc`, `/root`, `/home`, `/usr`, `/var`, `/bin`, `/boot`, `/lib`, `/opt`, `/mnt`, `/media`, `/tmp`,
  `/var/tmp`, `/run`, `/var/run`, `/proc`, `/sys`, `/dev`) → `SECRET-ROOT-SYSTEM-PATH`; a cross-application
  tree (`/srv`, `/srv-dev`, `tradingproteus`, `ares`) → `SECRET-ROOT-CROSS-APPLICATION`; any other
  unauthorised directory → `SECRET-ROOT-NOT-AUTHORISED`; relative → `SECRET-ROOT-RELATIVE`; missing →
  `SECRET-ROOT-MISSING`; not-a-directory → `SECRET-ROOT-NOT-DIRECTORY`; group/world-writable →
  `SECRET-ROOT-UNSAFE-PERMISSIONS`.
- Proven: `HERMES_SECRET_ROOT=/etc` cannot read `/etc/passwd`; `=/root` cannot read `/root/.bashrc`; `=/`
  cannot read arbitrary files (all rejected at root resolution, zero content leak). The default
  `/run/secrets` remains authorised. All prior path-containment controls are preserved unchanged.

### Deployment ownership contract (§7)

- Mount the authorised secret root **read-only**, owned by `root` (or a governed deploy identity), **not
  writable by user `hermes`** (uid 10001), e.g.
  `--mount type=bind,ro,src=/host/hermes-secrets,dst=/run/secrets`.
- Mount only the individual secrets HERMES requires; the root is HERMES-owned; **no ARES/other-application
  secret directory is mounted**; secret files are regular and ≤ 8 KiB; secrets are supplied at **runtime,
  never at image build time**.
- The loader enforces the portable part of this (positive allow-list, no group/world-writable root, regular
  bounded files). Container-level ownership (root-owned, read-only to `hermes`) is a **deployment gate** —
  the loader fails closed on clearly unsafe modes; the mount ownership is verified at deploy time.

---

## Runtime-writable secret-root rejection (C-WP2-SECRET-ROOT-RUNTIME-WRITABLE-ACCEPTED correction)

WO-HELM-HERMES-CONTAINER-MVP-WP2-PR125-RUNTIME-WRITABLE-SECRET-ROOT-CORRECTION-0001.

R2D2's static re-audit closed both prior findings but flagged a third residual: the root permission check
rejected only group/world-writable bits, so a root **owned by the effective runtime identity** and
owner-writable (e.g. `hermes`-owned `0700`) was accepted — the secret-consuming process could plant, replace,
rename or delete its own secrets.

The resolver now rejects a root that is **writable by the effective HERMES runtime identity**, via
deterministic effective-identity + mode analysis (`_root_writable_by_runtime`), independent of the
developer/CI UID:

- **world-writable** → reject;
- **group-writable** and the runtime is in the owning group (effective GID or a supplementary group) → reject;
- **owner-writable** and the root is owned by the effective runtime UID → reject;
- **effective runtime UID 0** (root can mutate anything) → reject;
- `os.access(root, W_OK, effective_ids=True)` True — applied only when the module's effective-UID notion
  equals the real process euid (so it is not misled by root-run tests).

Fault: `SECRET-ROOT-RUNTIME-WRITABLE`. The pure group/world mode-bit check keeps its own
`SECRET-ROOT-UNSAFE-PERMISSIONS` fault. Proven: a runtime-owned `0700` root beneath an authorised parent is
rejected; a root owned by a distinct deploy identity at `0755` (not writable by the runtime) is accepted.

`_effective_runtime_uid()` / `_effective_runtime_gids()` are small module functions (default to the real
`os.geteuid()`/`os.getegid()`+`os.getgroups()`) — tests monkeypatch them to simulate the non-root `hermes`
identity so the policy is deterministic regardless of who runs the suite.

### Code-enforced vs deployment-gated

- **Code-enforced:** positive allow-list, no group/world-writable root, no runtime-identity-writable root,
  regular bounded files under the root, all path-containment controls.
- **Deployment requirement:** the root is a **read-only** mount, owned by root or a governed deploy identity,
  not writable by `hermes` (uid 10001); only the required individual secrets are mounted; no ARES/other-app
  secrets; secrets supplied at runtime, never at image build time.
- **Portability / limitations:** POSIX mode bits + ownership are what the loader can assert portably. ACLs,
  overlay/tmpfs mount semantics, and Kubernetes `readOnly`/`defaultMode` are enforced at the mount layer and
  verified at deploy time — the loader fails closed on the mode/ownership signals it can see.
