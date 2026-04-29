# WO-HERMES-LOCAL-MAIN-RECONCILIATION-0001 — ratification + resolution

**Persona:** Helm
**Date:** 2026-04-29
**Branch:** `wo/WO-HERMES-LOCAL-MAIN-RECONCILIATION-0001`
**Repo:** `tradingSignals` (`maff0000/hermes`)
**Baseline:** `origin/main = 9e5f58fb63d2cd87c5fe9d413da1aa69022ab0a7`
**Pre-WO local main:** `dabff828` (14 commits behind)

## Motivation

`/srv-dev/tradingSignals/` on dell-debian carried a pre-existing
operator-state condition: local main was 14 commits behind origin/main with
two genuine working-tree edits and two append-only log files. The two
recently-shipped UTC WOs (PR #5/#6/#7) verified per-file SHA against canonical
main, but governance bar requires `local == origin/main` + clean working tree
(or explicitly approved overlay).

This WO closes the splinter.

## Discovery

### HEADs (pre-WO)

```
local main:  dabff8287c46c4bee06d5c74b77699b1a5e17b19
origin/main: 9e5f58fb63d2cd87c5fe9d413da1aa69022ab0a7
behind by:   14 commits (FF available)
```

### Working-tree status (pre-WO)

23 staged "modified/new" entries (Class A — match origin/main; product of
PR #5/#6/#7 targeted-checkout deploys against the ancient local HEAD).

4 unstaged "modified" entries (Class B + C — genuine local divergences).

### FF range overlap with operator-state files

```
$ git diff --name-only main..origin/main | grep -E 'utils/healthcheck|utils/watchdog'
(empty)

→ utils/healthcheck.py and utils/watchdog.py are isolated from the FF range.
   The FF can land cleanly without touching the operator changes.
```

## Ratification table

| File / path | Class | Current state | Operational purpose | Risk | Recommendation | Required action | Owner / status |
|---|---|---|---|---|---|---|---|
| `adapters/base.py` `adapters/oanda.py` `healthcheck/signal_health.py` `main.py` `ops/canonical_m1_parallel_writer.sh` `ops/equivalence_check.sh` `scripts/archive_old_data.py` `scripts/backfill_oanda.py` `scripts/backfill_signals.py` `scripts/backfill_signals_m15.py` `services/market-map/market_map.py` `tests/test_utc_compliance.py` `tests/test_utc_db_boundary.py` `utils/atr_calculator.py` `utils/break_detector.py` `utils/compression_detector.py` `utils/db_writer.py` `utils/discord_alerts.py` `utils/gap_scanner.py` `utils/level_engine.py` `utils/redis_publisher.py` `utils/trading_hours.py` + 5 new evidence/work-order/verify files | **A** — canonical origin/main delta already deployed | Match origin/main byte-for-byte (per-file sha256 verified during PR #5/#6/#7) | Tracked sources for tradingSignals UTC cleanup + verify harness | None | **Auto-resolve on FF** | `git pull --ff-only origin main` after this WO merges | Helm — auto |
| `ops/evidence/WO-HERMES-PROOF-H/equivalence_log.jsonl` | **B** — runtime/operator data | Tracked, +571 lines of append-only proof-log | Operator audit log for WO-HERMES-PROOF-H | Low (data, not code) | **Untrack + .gitignore** so future log accretion doesn't pollute git status | `git rm --cached` + add to `.gitignore` (in this PR) | Helm — done in PR |
| `ops/evidence/WO-HERMES-PROOF-OF-WORKING-0006/soak/soak_log.jsonl` | **B** — runtime/operator data | Tracked, +6,841 lines | Operator soak-test log | Low | **Untrack + .gitignore** | same | Helm — done in PR |
| `utils/healthcheck.py` | **C** — local operator code change | +11/-8 lines: stubs IRIS healthcheck dispatch with `success = True  # Stub — IRIS disabled`; comments out the failed-publish warning | IRIS service was retired; without this stub, healthcheck reporter logs `Failed to send healthcheck to IRIS` every cycle. This is currently **deployed and running** (signal-service-dev imports `utils/healthcheck.py` directly). Reverting reintroduces continuous log spam. | Low to medium. Not config-in-code (uses literal `True` only because IRIS doesn't exist). No defaults/fallbacks. No new behaviour beyond a stub. Test impact: none — IRIS path isn't covered. | **Promote to canonical** with explicit reason in commit. The stub IS the right operational state for the current IRIS-retired environment. | Promote in this PR (commit 1) | Helm — done |
| `utils/watchdog.py` | **C** — local operator code change | +13/0 lines: HERMES-RESILIENCE-001 auto-recovery — when `tick_age > 3600s` (weekend-gap signal), forces `STALE → RECOVERING` so the reconnect loop kicks in | Currently **deployed and running**. Reverting re-introduces the stuck-STALE bug after weekend-to-weekday transitions (42-hour-old last tick stays STALE forever, requiring manual intervention). | Low. No config-in-code. No defaults. No new strategy logic. Pure resilience improvement. Has been running cleanly for 5+ weeks. | **Promote to canonical** with reference to the HERMES-RESILIENCE-001 design rationale. | Promote in this PR (commit 2) | Helm — done |

**No Class D items** (nothing to discard as stale/unsafe).

## Decision per Class C item

- **`utils/healthcheck.py`**: PROMOTE. Architect rule "no defaults/fallbacks hiding bad data" is satisfied — the stub does NOT silently fall back to "GREEN"; it short-circuits the dispatch entirely with explicit `# IRIS disabled` comment + TODO. The stub status is honest about the disabled state.
- **`utils/watchdog.py`**: PROMOTE. Architect rule "preserve service safety while reconciling" is satisfied — the auto-recovery code IS the service-safety improvement; reverting would degrade safety.

## Class B handling

Both `.jsonl` files are append-only operational logs that were over-eagerly committed to git in earlier work. They grow indefinitely; tracking them as source produces noise. Untrack + gitignore is the standard pattern. The historical snapshot remains in git history.

## Class A handling

All 23 Class A entries auto-resolve when local main fast-forwards to
origin/main. No code change needed.

## Automated verification

New target: `make -f ops/verify/Makefile verify-local-main-reconciliation`

`ops/verify/check_local_main.sh` asserts:

1. `local main HEAD == origin/main HEAD` (clean canonical) OR
   local is strict ancestor of origin AND only approved overlay items differ
2. `git status --porcelain` empty modulo approved overlay
3. **Approved overlay set after this WO closes: empty** (no permanent local-only changes)
4. Catches future divergence-introduction loud (FAIL on unexpected modified files)

This target is added to `verify-all` so the full hermes harness includes it.

## Acceptance criteria

- AC-1: Class C operator code promoted to canonical.
- AC-2: Class B logs untracked + gitignored.
- AC-3: Class A auto-resolves on FF pull.
- AC-4: `make verify-local-main-reconciliation` PASS post-FF.
- AC-5: `make verify-all` PASS post-FF.
- AC-6: `signal-service-dev` and `hermes-canary` healthy post-deploy (no service restart needed — deployed code on disk is unchanged; the canonical main now formally contains what was already running).
- AC-7: Final declaration: **CLEAN CANONICAL**.

## Final declaration

After this PR merges + dell-debian FF pulls, the state will be **CLEAN CANONICAL**: zero approved overlay, zero unapproved divergence.

## Evidence

See `ops/evidence/WO-HERMES-LOCAL-MAIN-RECONCILIATION-0001/SUMMARY.md`.
