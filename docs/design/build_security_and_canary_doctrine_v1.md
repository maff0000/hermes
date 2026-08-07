# HERMES build-security & canary doctrine (v1)

**WO-HELM-HERMES-BUILD-CONTEXT-SECRET-LEAK-CONTAINMENT-AND-CLEAN-SOURCE-BUILD-HARDENING-0001.**

## Incident (why this exists)
A real, **shared** read-only DB credential (`trinity@'%'` — `SELECT` on `tradingRisk`/`tradingSignals`/`tradingProteus`)
was baked into HERMES production images through an **untracked** `healthcheck/canary/.env`. The tracked `.dockerignore`
excluded only the repo-root `.env`, not nested `**/.env`, and the governed build wrapper built the **mutable checkout**
via `docker compose build`, so an untracked host file entered the image. The identical file is byte-identical in the
running production image and historical images.

## Build-security doctrine (permanent)
> **`.dockerignore` is defence-in-depth. The governed HERMES production build boundary is an exact Git SHA exported
> into a clean temporary build context. Mutable source checkouts are never production build contexts.**

Mandatory promotion gates — a production image is **not** promotable merely because SOURCE_SHA, OCI revision and tests
are correct. It must also prove:
1. **Exact-SHA clean context** — `tools/hermes_clean_build_context_v1.py` exports only tracked files at the exact
   commit via `git archive`; untracked / ignored / dirty-worktree files are structurally excluded.
2. **Clean-context manifest** — deterministic, secret-safe (source SHA, file count, context/Dockerfile/lock digests,
   prohibited-path findings). Prohibited-path findings fail closed.
3. **Image secret-artefact gate** — `ops/build/hermes_image_secret_scan_v1.py` scans the built image filesystem (no
   execution) for nested `.env`, key material, `.ssh`, `.netrc`, and the known `healthcheck/canary/.env` sentinel. Any
   hard-reject artefact ⇒ `REJECTED_SECRET_CONTAMINATED` (never promotable).

The governed wrapper `ops/build/build_production_candidate.sh` now: clean tracked tree → export clean exact-SHA context
→ validate manifest → `docker build` **from the context** (never the checkout) → verify OCI revision → image secret
gate → SHA→digest + context-manifest evidence → cleanup. A `docker compose build` on the mutable checkout is **not** a
governed production build.

## Canary doctrine (permanent)
> **Health/canary utilities must use external, application-specific credentials. They must not rely on plaintext
> credential files under application source/build directories and must not reuse estate-wide shared credentials.**

`healthcheck/canary/hermes_signal_truth_canary.py` now loads config **strictly from the process environment**
(`HERMES_CANARY_*` preferred, legacy `CANARY_*` accepted) — it no longer reads a `.env` beside the script and never
falls back to a shared account or embedded default; any missing key ⇒ deterministic RED, exit 1. A safe placeholder
template lives at `healthcheck/canary/canary.env.template`; the real runtime file is external and Git/build-context
excluded.

## Dedicated least-privilege canary account (design; provisioned by a SEPARATE governed WO)
- **Consumer:** HERMES canary only.
- **Database:** `tradingSignals` only.
- **Operations:** the minimum read-only `SELECT` the canary actually issues.
- **Explicitly NOT:** `tradingRisk`, `tradingProteus`, any write, any DDL, any admin.
- **Host origin:** restricted where operationally possible (not `'%'`).
- **Delivery:** credential supplied externally via the governed secret mechanism; documented rotation/revocation.
- Example label only (source must not depend on it): `hermes_canary`.

## Shared-credential migration (HELD — outside this WO's authority)
`trinity@'%'` is a **shared estate credential** reused beyond HERMES (evidence: `tradingProteus/helios/canary`
templates across multiple worktrees; Trinity read tooling). It **cannot** be rotated/locked/dropped until a separately
governed credential-migration WO performs an **authoritative live-consumer inventory** and migrates every legitimate
consumer to dedicated least-privilege identities. `.env.example` placeholders are **not** proof of active use.

## Current security status (must be stated honestly)
- **Build vulnerability:** CLOSED in source (this WO) — future clean candidates cannot carry the file.
- **Shared credential:** `SHARED_EXPOSED_CREDENTIAL_REMAINS_VALID_PENDING_CROSS_CONSUMER_MIGRATION` — still valid;
  historical/current images that embed it are `SECURITY_DEGRADED_SHARED_CREDENTIAL_EMBEDDED` and must not be treated as
  clean production candidates.
- **Production runtime:** continues on the current image pending a separately authorised clean rebuild + cutover.
