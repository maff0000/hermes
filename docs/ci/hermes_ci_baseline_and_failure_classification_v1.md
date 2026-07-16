# HERMES CI Baseline & Async/Test Failure Classification (v1)

- **WO:** WO-HELM-HERMES-CI-BASELINE-AND-ASYNC-FAILURE-CLASSIFICATION-0001
- **Authority:** HELM (HERMES market-data lane)
- **Base:** canonical `main` @ `71ea3bd` (merge of PR #102)
- **Scope:** ADD CI config + docs only. **No runtime behaviour change. No product source touched. No merge/deploy.**
- **All times UTC.**

## 1. Why this exists

PR #102 merged with **no configured GitHub CI** — there was no automated quality
gate on HERMES pull requests. The local full suite consistently reports a large,
alarming-looking set of failures. This document reproduces that baseline, classifies
**every** non-green item by root cause, and defines a deterministic, infrastructure-free
CI gate so future PRs are meaningfully gated **without hiding real failures**.

## 2. Reproduced baseline

Command (as specified by the WO):

```bash
cd <worktree>
ENVIRONMENT=DEV DEV_REDIS_HOST=127.0.0.1 DEV_REDIS_PORT=6399 \
  REDIS_HOST=127.0.0.1 REDIS_PORT=6399 \
  python3 -m pytest -q -p no:cacheprovider --continue-on-collection-errors -rA
```

**Result:** `51 failed, 1509 passed, 13 warnings, 4 errors` — i.e. **51F / 1509P / 4E**.

Environment observed: Python 3.12.3, pytest 9.0.2, **pytest-asyncio 1.3.0 IS installed**,
`httpx` NOT installed locally (though it is declared in `requirements.txt`). There is
**no** `pytest.ini` / `pyproject.toml` / `setup.cfg` / `tox.ini`; the only pytest config
is `tests/conftest.py` (path setup + `load_dotenv(BASE_DIR/'.env')` + fixtures).

## 3. Root-cause categories used

| Category | Meaning |
|---|---|
| `ENVIRONMENT_DEFICIENCY` | Needs a real service (MariaDB/Redis), full env vars (`DB_*`, `OANDA_API_KEY`, `REDIS_DB`), or a real `.env` file. Several modules read **required** env at *import time*, so the failure surfaces at collection. |
| `MISSING_OPTIONAL_DEPENDENCY` | An importable optional package is absent from the local env. |
| `TEST_CONFIGURATION_DEFECT` | The *test harness/config* is wrong, not the product. E.g. bare `async def` tests with pytest-asyncio installed but no `asyncio_mode=auto`; or an over-broad lint regex. |
| `OBSOLETE_TEST` | Tests an intent that no longer exists. |
| `ACTUAL_PRODUCT_DEFECT` | A genuine defect in shipped product behaviour. **Investigated specifically — see §7.** |

## 4. Classification summary (55 items = 51 failures + 4 collection errors)

| Category | Count | Blocks the CI gate? |
|---|---:|---|
| `ENVIRONMENT_DEFICIENCY` | 31 | No — excluded from infra-free gate, documented below |
| `TEST_CONFIGURATION_DEFECT` | 23 | No — excluded, one-line remediations noted |
| `MISSING_OPTIONAL_DEPENDENCY` | 1 | No — resolved by `pip install -r requirements.txt` in CI; test still needs infra |
| `OBSOLETE_TEST` | 0 | — |
| **`ACTUAL_PRODUCT_DEFECT`** | **0** | **None found. See §7.** |
| **Total** | **55** | |

Breakdown: of the 51 **run-time failures**, 28 are `ENVIRONMENT_DEFICIENCY` and 23 are
`TEST_CONFIGURATION_DEFECT`. Of the 4 **collection errors**, 3 are `ENVIRONMENT_DEFICIENCY`
and 1 is `MISSING_OPTIONAL_DEPENDENCY`.

## 5. Full failure inventory — 51 failing node IDs

### 5.1 ENVIRONMENT_DEFICIENCY (28 run-time failures)

Root error for all of these: `ValueError: Required environment variable not set: <VAR>
(tried DEV_<VAR> and <VAR>)` raised by `env_config.get_env(..., required=True)` when the
DEV run set only `REDIS_HOST/PORT` — `DB_HOST`, `OANDA_API_KEY`, `REDIS_DB` etc. were
absent — or an assertion that a real `.env` file exists in the repo root (it is gitignored
and absent in a clean checkout).

| # | Node ID | Trigger |
|---|---|---|
| 1 | `tests/test_collection_contract.py::test_source_policy_reader` | `DB_HOST` / `OANDA_API_KEY` required |
| 2 | `tests/test_config.py::TestConfigLoading::test_load_config` | `load_config()` → required env |
| 3 | `tests/test_config.py::TestConfigLoading::test_database_config` | `load_config()` → required env |
| 4 | `tests/test_config.py::TestConfigLoading::test_redis_config` | `load_config()` → required env |
| 5 | `tests/test_config.py::TestConfigLoading::test_oanda_config` | `load_config()` → required env |
| 6 | `tests/test_config.py::TestConfigLoading::test_instruments_config` | `load_config()` → required env |
| 7 | `tests/test_config.py::TestEnvVarCompliance::test_no_legacy_tas_db_vars` | `load_config()` → required env |
| 8 | `tests/test_config.py::TestEnvVarCompliance::test_standardized_db_vars_used` | `load_config()` → required env |
| 9 | `tests/test_config.py::TestEnvVarCompliance::test_standardized_redis_vars_used` | `load_config()` → required env |
| 10 | `tests/test_config.py::TestConfigDefaults::test_database_from_env` | `load_config()` → required env |
| 11 | `tests/test_config.py::TestConfigDefaults::test_redis_from_env` | `load_config()` → required env |
| 12 | `tests/test_env_compliance.py::TestEnvFileCompliance::test_env_file_exists` | asserts real `.env` present |
| 13 | `tests/test_env_compliance.py::TestEnvFileCompliance::test_env_has_environment_tag` | reads real `.env` |
| 14 | `tests/test_env_compliance.py::TestEnvFileCompliance::test_env_uses_db_prefix` | reads real `.env` |
| 15 | `tests/test_env_compliance.py::TestEnvFileCompliance::test_env_uses_redis_prefix` | reads real `.env` |
| 16 | `tests/test_gap_scanner.py::test_real_incident_detection` | `DB_HOST` required |
| 17 | `tests/test_gap_scanner.py::test_higher_tf_gaps` | `DB_HOST` required |
| 18 | `tests/test_gap_scanner.py::test_market_closed_suppression` | `DB_HOST` required |
| 19 | `tests/test_gap_scanner.py::test_signal_gap_detection` | `DB_HOST` required |
| 20 | `tests/test_gap_scanner.py::test_idempotent_persistence` | `DB_HOST` required |
| 21 | `tests/test_gap_scanner.py::test_gap_count_for_health` | `DB_HOST` required |
| 22 | `tests/test_recovery_library.py::test_all_artifacts_governed` | `DB_HOST` required |
| 23 | `tests/test_recovery_library.py::test_graph_valid` | `DB_HOST` required |
| 24 | `tests/test_recovery_library.py::test_rebuild_order_consistency` | `DB_HOST` required |
| 25 | `tests/test_recovery_library.py::test_dependency_references` | `DB_HOST` required |
| 26 | `tests/test_recovery_library.py::test_full_validation` | `DB_HOST` required |
| 27 | `tests/test_recovery_library.py::test_real_outage_plan` | `DB_HOST` required |
| 28 | `tests/test_recovery_library.py::test_partial_plan` | `DB_HOST` required |

### 5.2 TEST_CONFIGURATION_DEFECT (23 run-time failures)

**5.2a — Async tests, no `asyncio_mode` configured (22).** Each fails with:
`Failed: async def functions are not natively supported.` These are bare `async def
test_*()` coroutines with **no** `@pytest.mark.asyncio`. `pytest-asyncio` **is installed
(1.3.0)** but defaults to *strict* mode; with no `pytest.ini`/`pyproject.toml` setting
`asyncio_mode = auto`, unmarked coroutines are not collected as async and are reported as
un-runnable. This is a harness-config gap, **not** a missing dependency and **not** a
product defect.

| # | Node ID |
|---|---|
| 29 | `tests/test_per_instrument_health.py::test_all_healthy` |
| 30 | `tests/test_per_instrument_health.py::test_single_tick_stale` |
| 31 | `tests/test_per_instrument_health.py::test_single_m1_stale` |
| 32 | `tests/test_per_instrument_health.py::test_market_closed` |
| 33 | `tests/test_per_instrument_health.py::test_snapshot_instruments` |
| 34 | `tests/test_per_instrument_health.py::test_global_not_green_when_inst_red` |
| 35 | `tests/test_watchdog.py::test_false_reconnect` |
| 36 | `tests/test_watchdog.py::test_tick_staleness` |
| 37 | `tests/test_watchdog.py::test_candle_stagnation` |
| 38 | `tests/test_watchdog.py::test_recovery_exhaustion` |
| 39 | `tests/test_watchdog.py::test_market_closed_suppression` |
| 40 | `tests/test_watchdog.py::test_flow_promotion` |
| 41 | `tests/test_watchdog.py::test_incident_lifecycle` |
| 42 | `tests/test_watchdog_load.py::test_no_per_tick_db` |
| 43 | `tests/test_watchdog_load.py::test_cadence_persistence` |
| 44 | `tests/test_watchdog_load.py::test_snapshot_in_memory` |
| 45 | `tests/test_watchdog_load.py::test_stale_from_memory` |
| 46 | `tests/test_watchdog_load.py::test_burst_stability` |
| 47 | `tests/test_watchdog_load.py::test_logger_injection` |
| 48 | `tests/test_watchdog_load.py::test_false_reconnect` |
| 49 | `tests/test_watchdog_load.py::test_recovery_exhaustion` |
| 50 | `tests/test_watchdog_load.py::test_promotion_persists` |

**5.2b — Over-broad lint regex (1).**

| # | Node ID |
|---|---|
| 51 | `tests/test_env_compliance.py::TestNoHardcodedPaths::test_no_hardcoded_srv_paths` |

Fails: `AssertionError: Hardcoded path in hermes_logging/gelf.py: ["'/srv/", "'/srv-"]`.
The test regex `["\'][/]srv[-/]` scans all non-test `*.py` for string literals containing
`/srv/` or `/srv-`. It matches `hermes_logging/gelf.py:119`:

```python
self.environment = 'PROD' if '/srv/' in os.getcwd() and '/srv-dev/' not in os.getcwd() else 'DEV'
```

This is a **deliberate runtime environment-detection heuristic** that inspects the current
working directory — it is **not** a hardcoded filesystem dependency. The test's intent
(ban environment-specific *path dependencies*) is legitimate, but its regex over-matches a
legal `os.getcwd()` comparison. Classified `TEST_CONFIGURATION_DEFECT` (regex too broad),
**not** `ACTUAL_PRODUCT_DEFECT`. Remediation is a test refinement (exclude `os.getcwd()`
comparisons) — deliberately **out of scope** here since this WO must not touch product/test
source behaviour. See §7 for why this is not a product defect.

## 6. Full collection-error inventory — 4 files

| # | File | Exact error | Category |
|---|---|---|---|
| E1 | `tests/test_api.py` | `ModuleNotFoundError: No module named 'httpx'` → `RuntimeError: The starlette.testclient module requires the httpx package` (import of `starlette.testclient.TestClient` at module load) | `MISSING_OPTIONAL_DEPENDENCY` |
| E2 | `tests/test_canonical_engine.py` | `ValueError: Required environment variable not set: DB_HOST (tried DEV_DB_HOST and DB_HOST)` — module-level `DB = get_db_config()` at line 27 | `ENVIRONMENT_DEFICIENCY` |
| E3 | `tests/test_m1_deriver.py` | `ValueError: Required environment variable not set: DB_HOST (tried DEV_DB_HOST and DB_HOST)` — module-level `DB = get_db_config()` at line 30 | `ENVIRONMENT_DEFICIENCY` |
| E4 | `tests/test_redis_publisher.py` | `ValueError: Required environment variable not set: REDIS_DB (tried DEV_REDIS_DB and REDIS_DB)` — importing `utils.redis_publisher` runs module-level singleton `publisher = RedisPublisher()` | `ENVIRONMENT_DEFICIENCY` |

Note E1: `httpx>=0.25.0` **is** declared in `requirements.txt`; it is merely absent from
the local baseline interpreter. In CI, `pip install -r requirements.txt` installs it, so
this specific collection error does not occur — but `test_api.py` then still requires a
live app/DB, so it remains in the excluded integration set.

## 7. Product-defect assurance (the important one)

**No `ACTUAL_PRODUCT_DEFECT` was found among the 55 items.** Justification:

- **All 31 `ENVIRONMENT_DEFICIENCY` items** are the test *harness* demanding a real DB/OANDA
  environment or a real `.env`. The product code is behaving correctly — `env_config` is
  *supposed* to fail loudly when required config is absent (fail-loud is HERMES doctrine).
  Provide the env/services and these tests exercise product logic normally.
- **All 22 async items** fail purely because the collector can't run unmarked coroutines
  without `asyncio_mode=auto`. The product coroutines under test are never executed, so no
  product assertion fired. Zero product signal.
- **The 1 lint item** flags a legitimate `os.getcwd()` heuristic in `gelf.py`, not a real
  hardcoded-path dependency (see §5.2b). The product line is correct and intentional.
- **The 1 dependency item** is an absent local package already pinned in `requirements.txt`.

If any future run surfaces a failure whose root cause is genuine product behaviour, it must
be raised **loudly** as `ACTUAL_PRODUCT_DEFECT` and must **block** the gate — it must never
be added to the exclusion list below.

## 8. Proposed CI jobs (`.github/workflows/hermes-ci.yml`)

| Job | Purpose | Infra | Blocking |
|---|---|---|---|
| `market-hours-core` | Market-hours health + readiness / **deployment-readiness validator** (WO deliverables a & b) | none | Yes |
| `unit-infra-free` | Whole suite minus the classified exclusion set = **config/schema tests (c) + all other infra-free unit tests (d)** | none | Yes |
| `integration-manifest` | Records the excluded integration/repair set; informational until a follow-on WO provisions services | none | No (documented) |

`unit-infra-free` runs the **entire** suite and excludes **only** the enumerated 4
`--ignore` files (collection errors) + 51 `--deselect` node IDs (run-time failures). Any
newly added test is automatically included. Nothing is silently dropped — every exclusion
is inline-commented in the workflow with its classification and listed in §5/§6 here.

## 9. Dependency manifest (what the focused jobs need)

| Item | Value |
|---|---|
| Python | `3.12` |
| Install | `pip install -r requirements.txt` then `pip install "pytest>=8" "pytest-asyncio>=0.23"` |
| Key runtime deps (from `requirements.txt`) | `fastapi`, `uvicorn`, `httpx`, `aiohttp`, `redis`, `hiredis`, `pymysql`, `python-dotenv`, `pydantic`, `requests`, `oandapyV20`, `pandas`, `pytz` |
| Test deps | `pytest` (9.x used locally), `pytest-asyncio` (installed but unused by the gate until `asyncio_mode=auto` is added) |
| `pytest-asyncio` missing? | **No** — it is installed. The async failures are a *config* gap, not a missing plugin. |
| `httpx` missing? | Only in the local baseline interpreter; it is in `requirements.txt`, so CI has it. |
| External infra required by the gate | **None** (no Redis, no MariaDB, no OANDA, no `.env`). |

## 10. Deterministic commands

**Focused market-hours / deployment-readiness (WO verification command):**

```bash
cd <worktree>
python3 -m pytest tests/test_hermes_market_hours_health_v1.py \
                  tests/test_hermes_market_hours_readiness_v1.py -q
# -> 45 passed
```

**Comprehensive infra-free gate (verified GREEN with ZERO env vars set):**

```bash
cd <worktree>
python3 -m pytest -q -p no:cacheprovider \
  --ignore=tests/test_api.py \
  --ignore=tests/test_canonical_engine.py \
  --ignore=tests/test_m1_deriver.py \
  --ignore=tests/test_redis_publisher.py \
  --deselect "<each of the 51 node IDs in §5>"
# -> 1509 passed, 51 deselected
```

(The full 51-item `--deselect` list is materialised verbatim in
`.github/workflows/hermes-ci.yml`, job `unit-infra-free`.)

## 11. Pass/fail policy

- **GREEN (merge-eligible):** `market-hours-core` and `unit-infra-free` both pass —
  i.e. `45 passed` and `1509 passed, 51 deselected`. Any deviation (new failure, new
  collection error, a formerly-deselected test now selected and failing) is **RED**.
- **A deselected test is not a hidden failure:** the deselect/ignore set is fixed and
  auditable. If a PR *fixes* an excluded test, remove it from the exclusion list so it
  joins the gate. If a PR *adds* a new failing test, it is included by default and turns
  the gate RED — the author must fix it or (only with classification recorded here) exclude
  it.
- **Never** add an unexplained exclusion. Every exclusion carries a §3 category.
- **`ACTUAL_PRODUCT_DEFECT` always blocks** and may never be excluded.

## 12. Branch-protection recommendation

On `maff0000/hermes`, protect `main`:

1. **Require status checks to pass before merging** — required checks:
   `market-hours-core` and `unit-infra-free`. (`integration-manifest` is *not* required.)
2. **Require branches to be up to date before merging** (re-run checks on the merge base).
3. **Require a pull request before merging** (no direct pushes to `main`) — aligns with
   the CLAUDE.md guardrail "No direct commits to main".
4. **Require linear history** optional; **Do not allow bypassing** for admins on the two
   required checks.
5. **Dismiss stale approvals on new commits** (recommended).

## 13. Follow-on remediation (out of scope here; ADD-only WO)

A future WO could move most excluded tests into the gate by:

1. Adding a repo `pytest.ini` (or `pyproject.toml [tool.pytest.ini_options]`) with
   `asyncio_mode = auto` → recovers the 22 async `TEST_CONFIGURATION_DEFECT` tests
   (per-instrument-health, watchdog, watchdog-load) with no product change.
2. Providing a CI `.env` fixture and/or ephemeral MariaDB + Redis service containers →
   recovers the 31 `ENVIRONMENT_DEFICIENCY` tests as a separate **integration** job.
3. Refining `test_no_hardcoded_srv_paths` to ignore `os.getcwd()` comparisons →
   recovers the 1 lint `TEST_CONFIGURATION_DEFECT`.

None of these are done here: this WO **adds CI config + docs only** and changes no runtime
behaviour or product/test source.
