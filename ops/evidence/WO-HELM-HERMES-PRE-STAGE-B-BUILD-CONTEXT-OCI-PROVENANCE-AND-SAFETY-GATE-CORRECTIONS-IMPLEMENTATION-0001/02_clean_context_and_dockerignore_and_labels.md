# Clean-context design, .dockerignore rationale, OCI-label contract, control mapping, prose

## §6 Clean exact-SHA build-context tool (`tools/hermes_clean_build_context_v1.py`)

- **Mechanism:** `git archive --format=tar <full-40-hex-sha>` (subprocess, explicit arg array, no
  `shell=True`, bounded timeout, captured stderr). Only the tracked content of the EXACT commit is
  exported; the working tree is never read for content, so a dirty/contaminated worktree cannot leak
  untracked files. Extraction is in-process from the archive stream with hard path-safety validation.
- **Exact-SHA enforcement:** `--source-sha` MUST be full 40-hex (`verify_source_sha`), MUST resolve to a
  `commit` object (`git cat-file -t`). Abbreviated / branch / tag / `latest` are rejected. Repo identity
  (`remote.origin.url`) MUST equal the expected repository.
- **Dirty-worktree isolation:** proven by `test_clean_context_dirty_worktree_cannot_contaminate` — a temp
  repo with an UNTRACKED secret file + a DIRTY modification exports only committed content
  (`keep.txt` == committed `v1`, never the working-tree `v2`; `UNTRACKED_SECRET.txt` absent).
- **Nested-repo / traversal / symlink-escape:** `_validate_member` rejects absolute paths, `..`
  traversal, symlink/hardlink targets escaping the dest, and any `.git/` path component (fail closed).
- **Fail-closed set:** invalid/abbreviated/non-commit SHA, wrong repo, escape, unexpected pre-existing
  output content (no sentinel), nested repo/prohibited path, unreadable checksum, manifest ≠ tracked git.
- **Bounded:** streaming chunked checksums, per-file content-scan cap (256 KiB), subprocess timeouts, no
  retry loop, no unbounded recursion. stdlib only, no network, no docker, no secrets.

## §12 Build-context security scan (folded into the tool)

Runs over the EFFECTIVE (post-`.dockerignore`) build context — the material `COPY . ${APP_HOME}` would
actually bring into the image. Reports scanned-path-count, prohibited-path findings (rule ids), secret
findings (**path + rule id ONLY — never the value**), result, UTC, source_sha, and the checksum manifest.
Rule ids cover private keys, AWS keys, bearer tokens, quoted api-key/secret/token/password literals,
OANDA account ids, Slack tokens, nested `.git`, `.claude`, worktrees, venvs, `*.jsonl`, `*.pem/.key/.crt`,
`*.sqlite/.db`, ssh material, shell history, `srv-dev` evidence mirrors, host `/etc` sensitive files.

## §13 Build-manifest contract (`schemas/.../build_context_manifest.v1.schema.json`)

Deterministic, tz-aware UTC, immutable-by-convention. `manifest_checksum = sha256(canonical_json(core))`.
No secrets, no raw file contents — only safe paths, per-file sha256, excluded-category summary, and
finding counts. Real run at base `a780a161`: `result=PASS`, `exported_file_count=1178`,
`scanned_path_count=268`, 0 prohibited, 0 secret findings (see `09_build_context_manifest_summary.json`).

## §7 `.dockerignore` hardening (see `03_dockerignore_diff.txt`)

Added (kept all existing): `.claude/` + `**/.claude/`; nested `.git` (`**/.git`, top `.git` kept);
`worktrees/`; venvs (`.venv/`, `venv/`, `virtualenvs/`, `**/…`); `*.jsonl` + `**/*.jsonl`;
`**/ops-evidence/`; `*.pid`, `*.sock`; private material `*.pem/*.key/*.crt/*.p12/*.pfx`; DB artefacts
`*.sqlite/*.sqlite3/*.db`; coverage (`.coverage*`, `htmlcov/`, `coverage.xml`); build output
(`build/`, `dist/`); container exports `*.tar`, `*.tar.gz`; editor (`*.swp/*.swo/.idea/.vscode/`);
OS metadata (`.DS_Store/Thumbs.db/._*`).

**Required-inclusion proof** (`test_dockerignore_keeps_required_production_files`): the effective build
manifest still INCLUDES Phase-1 core (`utils/hermes_shared_stream_recovery_v1.py`), all ten Phase-2
`utils/hermes_sss_*_v1.py` modules, `main.py`, `config.py`/`env_config.py`, `utils/hermes_market_hours_health_v1.py`,
`adapters/`, and `config/market_hours_schedule.v1.json`; `.claude/`, `*.jsonl`, `tests/`, `ops/evidence/`
are EXCLUDED. `.env.example` remains shipped; `.env`/`.env.*` excluded. `.dockerignore` is an INDEPENDENT
DEFENCE, never a substitute for the exact-SHA export.

## §8 OCI provenance labels (F-DR-02; see `05_dockerfile_diff.txt`, `04b_label_verify_cli_output.txt`)

Dockerfile runtime stage adds ONLY `ARG SOURCE_SHA`, `ARG BUILD_UTC`, and
`LABEL org.opencontainers.image.revision="${SOURCE_SHA}"` + `org.opencontainers.image.created="${BUILD_UTC}"`.
Entrypoint / CMD / FROM / base-image / packages / RUN / USER / HEALTHCHECK / EXPOSE / env-defaults are
byte-identical otherwise (see `test_dockerfile_runtime_only_adds_arg_and_label` + the diff).

`tools/hermes_image_label_verify_v1.py`: both labels required; revision MUST be full-40-hex commit
(abbreviated/branch/`latest` rejected); created MUST be tz-aware UTC (naive/non-UTC rejected);
missing/empty/malformed fail closed; **candidate-readiness FAILS without labels**; revision MUST match
the clean build-context source SHA; no env fallback invents a SHA; a `docker inspect`-based comparison is
provided but NO real build is run (fixtures only). **Legacy image `c5fc2a62f424` is NOT retroactively
invalid** — scoped to future candidate images (`is_candidate` flag).

## §9 Canonical control-identifier mapping (`models/control_identifier_mapping.v1.json`)

| Control | Acceptance tests | Lifecycle gate | Blocking effect |
|---------|------------------|----------------|-----------------|
| **F-DR-01** | T-DOCKERIGNORE, T-CLEAN-CONTEXT, T-BUILD-MANIFEST, T-SECRET-SCAN, T-PROHIBITED-PATH | Stage A/B | Stage B FAILS if clean context not reproducible / manifest diverges / secret/prohibited finding |
| **F-DR-02** | T-SBOM-SCAN (label check), T-OCI-LABELS, FW-08, FW-19 | Stage B | Stage B FAILS if either label absent/incorrect or revision ≠ clean-context SHA |

No contradictory duplicate identifiers. `T-SOCKET-AMBIGUITY` and `T-HISTORICAL-INFERENCE` are the two
dedicated safety gates (blocking, implemented here).

## Corrected-prose inventory (architecture_v1.md)

- §7 requirements: clean exact-SHA context + labels made MANDATORY; F-DR mapping table added.
- §7 adequacy ruling: "only recommended change is labels" → labels + clean context MANDATORY, Stage B
  FAILS if absent/incorrect.
- §7A NEW: dedicated pre-Stage-B safety gates (socket-ambiguity + historical-inference).
- §14 Stage B summary: clean context + labels flagged MANDATORY/blocking.
- §19 (~line 334): "PR111 wants a source-SHA label … not blocking" → scoped to the LEGACY image's current
  diagnostic limitation only; SUBSTANTIVELY BLOCKING for future candidate images; legacy does not set the
  contract for new candidates.
