# WO-HELM-HERMES-PH2-BACKFILL-STATUS-PUBLISH-WIRING-0001 — HELM PR verdict

## GREEN_PH2_BACKFILL_STATUS_PUBLISH_WIRING_PR_READY
Tracking: HELM_HERMES_PH2_BACKFILL_STATUS_PUBLISH_WIRING_PR::2026-07-13T12:40Z::GREEN_PH2_BACKFILL_STATUS_PUBLISH_WIRING_PR_READY

Mode: CODE BUILD PR ONLY. No runtime mutation/deploy/activation/publication. Branch off main e4face3.
Authority: R2D2_HERMES_PH2_BACKFILL_STATUS_SURFACE_PR91_DEPLOY_DARK_AUDIT::2026-07-13T12:03:13Z::GREEN_..._APPROVED

## Blocker addressed
PR#91 shipped the status module deployed-dark AND inert (no publish path, no runner). This PR adds both, DARK by default,
mirroring the PR#90 gaps pattern, so a future activation WO can flip the two gates and publish.

## Changed files (additive; exact.diff)
- utils/hermes_backfill_status_v1.py — add BackfillStatusPublisher.publish() (single SET of BACKFILL_STATUS_KEY, re-validates
  first); add env-only gate backfill_status_publish_enabled(); refresh docstrings. Pure builder/validation/reader unchanged.
- utils/hermes_runtime_publisher_steps_v1.py — import bfs; add backfill_status_step(client): gate-first no-op; enabled-without-
  authorised -> SystemExit(101); enabled+authorised -> pub.publish(now=_now()).
- utils/hermes_publisher_runtime_v1.py — append ("backfill_status", steps.backfill_status_step, DEFAULT_INTERVAL) AFTER gaps,
  ONLY when bfs.backfill_status_publish_enabled() (env-only, no client). Dark by default -> runner set unchanged.
- tests/test_hermes_backfill_status_publish_wiring_v1.py (NEW) — 11 wiring tests.
- tests/test_hermes_backfill_status_v1.py — updated ONE PR#91 assertion (no .set) to the new invariant (exactly one SET of
  BACKFILL_STATUS_KEY, no other write/delete).

## Gate names (external config, not code constants; via env_config.get_env_bool)
HERMES_BACKFILL_STATUS_PUBLISH_ENABLED / HERMES_BACKFILL_STATUS_PUBLISH_AUTHORISED (as the WO specified).

## Publication + wiring summary
publish(): analyze (GET gaps key only) -> validate_backfill_status_contract (rejects OK / XAUUSD / bad invariants) ->
redis.set(BACKFILL_STATUS_KEY, json) — ONE key, no TTL (parity with gaps). backfill_status_step gate-first via
build_backfill_status_publisher_from_env(redis_client=client). Runner appended only when gate true (env-only check).

## Findings (all PROVEN by tests)
- disabled no-op: step returns {published:0}, zero writes/deletes/zadds
- enabled-without-authorised fail-closed: SystemExit(101) at build/gate/step/runner-assembly
- enabled+authorised single-key publish: r.sets == [(BACKFILL_STATUS_KEY, None)] (test-only; no live publication)
- never writes gaps key / candle-history / any other key ; no delete ; no zadd
- no SQL / vendor / market_map / Falcon / D1-seed-backfill invocation / subprocess / consumer_live=True in publish+step code
- publish() performs exactly ONE .set( ; step delegates writing to publish() (no direct .set)
- status invariants preserved through publish: consumer_live/execution_enabled/backfill_executed/repair_executed=False;
  active_job/completed_pct=null ; missing gaps->GAPS_SURFACE_MISSING ; GAPS_FOUND->READY_FOR_BACKFILL_DESIGN ; OK rejected ;
  XAUUSD denied/can't leak
- runner set unchanged when disabled ; PH1 D1 + gaps surfaces regression-free

## Tests
new wiring 11/11 ; backfill-status suite (updated) ; gaps + gaps-wiring 30/30 ; curated regression all pass (see test_output.txt)

## Next gate
R2D2 cold audit of this PR.
