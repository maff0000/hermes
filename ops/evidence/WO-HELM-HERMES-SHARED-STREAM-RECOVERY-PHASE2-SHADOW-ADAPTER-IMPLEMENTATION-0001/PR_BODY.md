# WO-HELM-HERMES-SHARED-STREAM-RECOVERY-PHASE2-SHADOW-ADAPTER-IMPLEMENTATION-0001

Phase-2 read-only shadow-adapter capability — **PRODUCTION-OWNED, INERT, DISABLED-BY-DEFAULT, MECHANICALLY INCAPABLE OF RECONNECT**. Promotes the audited PR#110 design prototypes (`design/sss_phase2_*`) into production-owned `utils/hermes_sss_*_v1.py` modules that feed truthful LIVE evidence to the **unchanged** Phase-1 core (`utils/hermes_shared_stream_recovery_v1.py`) and record a comparable SHADOW decision.

## Boundaries (all satisfied)
- **Phase-2 implementation only** — code + tests + evidence.
- **Disabled-by-default** — `shadow_enabled` defaults false; no implicit/coerced/env enable; `shadow_consumer_live` const false (true → disabled).
- **Unimported by active runtime** — no `main.py` / `utils/watchdog.py` / `adapters/oanda.py` / `adapters/base.py` / compose / Dockerfile / cron / systemd references (grep empty; static-guard test enforces).
- **Unexecuted / no deployment / no runtime wiring / no image build.**
- **No reconnect capability** — no executor dependency; `RefusingShadowExecutor.refuse()` raises `ShadowExecutionForbidden`; no connect/disconnect/recovery-request handle; no DI path to substitute an executor; distinct `ShadowEvaluationResult` type.
- **No Redis / No SQL / no migration / no new third-party dependency** (stdlib + Phase-1 core only; `jsonschema` in tests only).
- **No config installation, no secrets, no /etc or host-global writes.**
- **Current authority unchanged** — sustained-red authority, OANDA adapter, Phase-1 core, limiter/cooldown, one-shot recovery request all untouched and never consumed. Evidence adapters read only injected immutable frozen VIEWS.
- **Exact base** `b50162a21640a67080d87e4638b879df8a0cb784`.

## Production modules (`utils/`)
`hermes_sss_evidence_snapshot_v1` · `hermes_sss_shadow_record_v1` · `hermes_sss_comparator_v1` · `hermes_sss_mapper_v1` · `hermes_sss_evidence_adapters_v1` · `hermes_sss_observer_v1` · `hermes_sss_config_v1` · `hermes_sss_redaction_v1` · `hermes_sss_jsonl_writer_v1` · `hermes_sss_shadow_adapter_v1`.

## Load-bearing honesty invariants (mechanically tested)
- **Double-count guard (§8):** heartbeat and shared-progress read the SAME `AdapterHealth.last_tick_at` surface → the mapper collapses shared-progress to the mirror form so the core can never count them as two independent transport confirmations.
- **Socket ambiguity (§9):** CONNECTED is never a health assertion; it leans on the heartbeat.
- **July-16:** strict observed-only replay → `EVIDENCE_INCOMPLETE` (indeterminate heartbeat, never observed-healthy); inference-permitted replay → heartbeat `JUSTIFIED_INFERENCE`, shadow `RECOVERY_PROPOSAL_ONLY`, comparison `SHADOW_DENIES_CURRENT_RECONNECT`; inferred can never be relabelled directly-observed.

## Test evidence
- Focused: `tests/test_hermes_sss_shadow_adapter_v1.py` (62) + `tests/test_sss_phase2_shadow_design_v1.py` + `tests/test_hermes_shared_stream_recovery_v1.py` → **155 passed**.
- Full suite: **51 failed, 1761 passed, 4 collection errors** (baseline 51F/1699P/4E; +62 new tests). Regression node diff **EMPTY**.
- Perf: shadow-eval **p99 ≈ 0.15 ms** (budget 25 ms; synthetic unit timing, NOT production validation).

## ⚠️ Reviewer note (R2D2 exact-head audit required)
One existing test was minimally extended: `tests/test_hermes_shared_stream_recovery_v1.py::test_static_guard_no_runtime_or_infra_imports_the_core` now also treats the **inert** `utils/hermes_sss_*_v1.py` siblings as allowed inert referrers of the Phase-1 core — exactly as the guard already treats `design/`. This is because the WO explicitly authorises `import utils.hermes_shared_stream_recovery_v1` from the Phase-2 modules and mandates their `utils/` location. The guard **retains all real teeth**: it still fails if any LIVE runtime/infra path (main/watchdog/adapters/compose/Dockerfile/cron/systemd) imports the core. The siblings are independently proven inert by `tests/test_hermes_sss_shadow_adapter_v1.py::test_static_guard_no_runtime_or_infra_imports_sss`. Flagged here for explicit R2D2 review.

## DO NOT MERGE
Required **R2D2 exact-head audit** before merge. No deploy / no enable / no execute-against-live-data.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
