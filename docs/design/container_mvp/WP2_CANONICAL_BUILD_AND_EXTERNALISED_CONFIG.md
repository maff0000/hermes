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
