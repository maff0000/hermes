# HERMES market-hours schedule — `config_version` 2 retirement & compatibility ruling (v1)

WO: `WO-HELM-HERMES-CONFIG-V2-RETIREMENT-AND-COMPATIBILITY-DESIGN-0001`
Authority: HELM (HERMES market-data lane). Base: canonical `main` @ `71ea3bd`. All times UTC.
Status: DESIGN + minimal inert code, UNMERGED pending R2D2 audit. Does NOT touch the running deployment.

---

## 1. Question

`utils/hermes_market_hours_health_v1.py` gates accepted schedule configs with:

```python
SUPPORTED_CONFIG_VERSIONS = frozenset({"2", "3"})
```

The deployed `config/market_hours_schedule.v1.json` is `config_version "3"`. Version 2 was the earlier
deployment-readiness iteration whose real-inventory assumptions were superseded by the WTICO/ICO correction
(PR102). An informational R2D2 finding flagged that keeping both `{2,3}` supported is a mild ambiguity because v2
carries superseded (phantom-inventory) assumptions. This document determines whether v2 support is needed, proves
the safety envelope, and issues a ruling.

---

## 2. Evidence gathered (read-only)

### 2.1 Repo / fixture / artefact usage of v2

| Check | Command | Result |
|-------|---------|--------|
| Every `config_version` occurrence in the tree | `grep -rn config_version` | Only value present is `"3"` (the one deployed config). No v2 config file exists in the tree. |
| Schedule config file history | `git log -p -- config/market_hours_schedule.v1.json` | Values evolved `"1" -> "2" -> "3"`. v2 existed transiently during the PR101/PR102 sequence and was **superseded to v3 before merge**. The committed/current file is v3. |
| `SUPPORTED_CONFIG_VERSIONS` history | `git log -p -- utils/hermes_market_hours_health_v1.py` | Introduced **once**, already as `frozenset({"2", "3"})`. There was never a `{"2"}`-only supported state; v2 support was born vestigial, in the same WTICO-correction WO that had already superseded the v2 config with v3. |
| Test fixtures | `grep -rn config_version tests/` | Fixtures load the real v3 config. The only version-gate tests use `"3"` (valid) and `"999"` (unsupported). **No test asserts v2 is supported or structurally safe.** |
| Other `config_version` in repo | `grep -rn config_version` | `utils/candle_features.py` (`"v1"`) and `main.py` log the loaded version — unrelated to the schedule-version gate. |

### 2.2 Prior / rollback image `c79100851c05` (dell `192.168.11.10`, read-only)

Image created `2026-07-15T20:56:21+01:00` (`sha256:c79100851c05...`). Inspected via a throwaway
`docker create` + `docker cp` (no live container run/recreate):

| Artefact in prior image | Result |
|-------------------------|--------|
| `/app/config/market_hours_schedule.v1.json` | **FILE ABSENT** (no `/app/config` directory at all). |
| `/app/utils/hermes_market_hours_health_v1.py` | **MODULE ABSENT**. |

The prior image predates the market-hours feature entirely. This is the **Mode-C first deployment** of the
module + schedule. The prior/rollback image shipped neither the loader nor any schedule config, so at runtime it
uses the legacy fixed-UTC checker (`main.py` falls back on load failure).

### 2.3 Loader path (read `utils/hermes_market_hours_health_v1.py`)

`load_schedule()` uses `config_version` **only** as an accept/reject gate:

```python
if str(cfg.get("config_version")) not in SUPPORTED_CONFIG_VERSIONS:
    return None                # unsupported -> fail loud (behave as open, normal detection)
```

After the gate, **resolution is identical for every accepted version**: `fail_closed_unvalidated` -> `instrument_map`
+ `named_schedules` -> v1 back-compat `instruments` (explicit only). There is **no version-specific branch** and
**no `_default_fx` path anywhere** in the module (removed in the PR101 hardening; asserted by
`test_truly_unknown_instrument_resolves_none_no_default`). `DstAwareMarketHours` also routes through the same
`load_schedule`.

---

## 3. Findings

1. **No repo fixture, deployment artefact, or environment uses v2.** The only committed schedule config is v3; no
   v2 config file exists in the tree or is referenced by any test.
2. **v2 support is NOT needed for rollback.** The rollback image `c79100851c05` contains neither the module nor any
   schedule config; it cannot load a v2 config because it loads no schedule at all. Rollback runs its own bytes and
   is unaffected by the accepted-version set in the new code. **Rollback dependency on v2: NO.**
3. **v2 was never a deployed runtime state.** v2 config was superseded to v3 before this first (Mode-C) deploy, and
   the pre-deploy image had the feature FILE/MODULE ABSENT.
4. **Supporting v2 creates a mild operational ambiguity.** A hand-authored or copy-pasted v2 config (which encodes
   the superseded phantom-inventory assumption) would be **silently accepted** by the gate, whereas the current
   truth is that only v3 is governed. This is exactly the R2D2-flagged ambiguity.
5. **v2 support provides zero safety value.** Because the gate selects no code path, v2 and v3 resolve through the
   identical hardened resolver. Retiring v2 removes an accept-branch; it cannot make resolution *less* safe.
6. **No existing test depends on v2 being supported** (contrary to the WO's precautionary hypothesis). Verified by
   grep: the version tests are `"3"` and `"999"` only. Retiring to `{"3"}` therefore breaks no existing test.

---

## 4. Safety proof — v2 cannot restore `_default_fx` or phantom-inventory behaviour

This proof holds regardless of which option is chosen (it is why Option A would be *safe*, and why Option B is
*clean*):

- **No `_default_fx` exists in the module.** `load_schedule` never consults a `_default_fx`; unknown/unmapped
  instruments return `None` (fail loud). The version gate does not, and cannot, re-enable a `_default_fx` path
  because that path was deleted from the code. Asserted by
  `test_truly_unknown_instrument_resolves_none_no_default` and
  `test_unknown_instrument_never_suppresses_weekend_staleness`.
- **Phantom inventory (`ICO_USD`) is a property of the config file's contents, not of the version string.** The
  version number selects no resolution behaviour; only the `instrument_map`/`fail_closed_unvalidated` keys of the
  actual JSON decide what resolves. Accepting `config_version "2"` on the *current v3 JSON body* yields identical
  resolution to v3. The only way phantom `ICO_USD` could return is by shipping a config whose body contains it —
  which is a config-content defect the completeness validator already catches (`ICO_USD` -> `unmapped` ->
  `ok=False`), independent of the version gate.
- **Fail-closed floor is unconditional.** Any version the gate rejects -> `None` -> caller behaves as OPEN (normal
  detection/recovery). Retiring v2 can only *tighten* (move a v2 config from "accepted" to "fail loud"); it can
  never loosen suppression.

Conclusion: the version gate is a pure allow-list with no behavioural side effect. v2 support is inert value-wise
and, if retired, its removal is provably non-loosening.

---

## 5. RULING — Option B: Retire v2 (inert change, UNMERGED pending audit)

**Chosen: Option B.** One line — *v2 is vestigial: unused by any fixture/artefact/environment, not required for
rollback (the rollback image has the feature FILE/MODULE ABSENT), and retiring it removes the R2D2-flagged ambiguity
with zero test breakage and a provably non-loosening failure semantic.*

Option A (retain) was rejected: it would preserve an accept-branch that has no consumer, no rollback role, and a
known ambiguity, purely to avoid a change that is already safe and test-clean. Retention has cost (ambiguity) and no
benefit here.

### 5.1 Supported-version change

```python
SUPPORTED_CONFIG_VERSIONS = frozenset({"3"})   # was frozenset({"2", "3"})
```

Implemented in this branch as an inert change (see §6). Comment records the WO, rationale, and pointer to this doc.

### 5.2 Failure semantics (v2 config after retirement)

| Path | Behaviour |
|------|-----------|
| `load_schedule(cfg_v2, inst)` | returns `None` -> caller treats instrument as **OPEN** (fail loud, normal stale detection stays active). Never suppresses. |
| `validate_config_completeness(cfg_v2, ...)` | appends `unsupported config_version '2' (supported: ['3'])` to `report["errors"]`, `ok=False`. Startup/readiness surfaces a **loud validator error**. |
| `DstAwareMarketHours.is_market_open` | primary instrument schedule is `None` -> returns `(True, MARKET_HOURS_UNKNOWN_FAILCLOSED)` -> watchdog runs normal detection. No hidden state. |

Net: a v2 config is rejected loudly and conservatively — the correct outcome for a stale/superseded config.

### 5.3 Tests

- Added `test_config_v2_retired_fails_loud` (in `tests/test_hermes_market_hours_readiness_v1.py`): asserts
  `SUPPORTED_CONFIG_VERSIONS == {"3"}`, a v2 config -> `load_schedule` `None`, and validator error.
- Retained `test_unsupported_config_version_fails_closed` (`"999"`) unchanged.
- No existing test asserted v2 was supported, so **no test required reconciliation/weakening**. Full run:
  `tests/test_hermes_market_hours_health_v1.py` + `tests/test_hermes_market_hours_readiness_v1.py` = **46 passed**.

### 5.4 Rollback compatibility

- Rolling back to `c79100851c05`: unaffected — that image has no module/config and uses the legacy checker.
- Rolling back to the **current Mode-C v3 image** (the one in force before this WO merges): unaffected — it accepts
  v3, which is exactly the deployed config. Retiring v2 changes nothing for a v3 config on any image.
- There is **no image anywhere that both (a) loads the schedule and (b) depends on a v2 config**. The v2 accept-
  branch has never had, and cannot acquire, a rollback consumer.

### 5.5 Deployment sequencing (non-negotiable order)

1. R2D2 cold-audit of this branch (build GREEN, read-only, non-activated, doctrine-compliant).
2. Merge WO branch -> `main` (regular merge). **Change is still inert**: the running container runs the deployed
   image bytes, not `main`.
3. Retirement becomes live only on a **separate** HERMES image rebuild + deploy. Because the running config is v3
   (still accepted), that deploy is a **no-op for runtime behaviour** — no config migration, no observer change.
4. Sunset gate confirmation at deploy time: assert the live `/app/config/market_hours_schedule.v1.json` is
   `config_version "3"` (readiness validator `ok=True`) before/after — already covered by
   `test_config_completeness_all_configured_resolve_or_failclosed` and the file-shape test.

### 5.6 Evidence requirements for closure

- This ruling doc + grep results + prior-image FILE/MODULE-ABSENT capture + `SHA256SUMS` (in
  `ops/evidence/WO-HELM-HERMES-CONFIG-V2-RETIREMENT-AND-COMPATIBILITY-DESIGN-0001/`).
- Green run of the two market-hours test files (46 passed) recorded in the evidence bundle.
- PR opened against `main`; NOT merged, NOT deployed, running config untouched (v3).

### 5.7 Version-deprecation messaging

- Loader comment records: v2 RETIRED, why (pre-WTICO-correction, phantom-inventory, never deployed), and the fail-
  loud semantic.
- Validator error string already names the supported set dynamically (`sorted(SUPPORTED_CONFIG_VERSIONS)` -> `['3']`),
  so any stray v2 config self-documents: `unsupported config_version '2' (supported: ['3'])`.
- Config provenance in `market_hours_schedule.v1.json` already states "Supersedes config_version 2" — consistent
  with this retirement.

---

## 6. Code delta in this branch (inert, reversible)

| File | Change |
|------|--------|
| `utils/hermes_market_hours_health_v1.py` | `SUPPORTED_CONFIG_VERSIONS` `{"2","3"}` -> `{"3"}` + WO comment/rationale. Reversible by restoring the frozenset literal. |
| `tests/test_hermes_market_hours_readiness_v1.py` | `+test_config_v2_retired_fails_loud`. |
| `docs/design/market_hours_health/config_v2_retirement_compatibility_ruling_v1.md` | this document. |

No change to `config/market_hours_schedule.v1.json`, `main.py`, the observer, or any deployment artefact. The change
is inert until a future audited image rebuild+deploy, and is a no-op for the running v3 config.

---

## 7. Verdict

`GREEN_HERMES_MARKET_HOURS_CONFIG_V2_COMPATIBILITY_RULING_COMPLETE` — Option B (retire v2). v2 is unused, not a
rollback dependency, never a deployed runtime state; retirement removes the R2D2 ambiguity, is provably non-
loosening, and breaks no tests. Held UNMERGED pending R2D2 audit; running deployment untouched.
