# WO-HELM-HERMES-PH2-GAPS-SURFACE-PUBLISH-WIRING-0001 — HELM PR verdict

## GREEN_PH2_GAPS_PUBLISH_WIRING_PR_READY
Tracking: HELM_HERMES_PH2_GAPS_PUBLISH_WIRING_PR::2026-07-13T00:20Z::GREEN_PH2_GAPS_PUBLISH_WIRING_PR_READY

Mode: CODE BUILD PR ONLY. No runtime mutation, no deploy, no activation, no publication. Branch off main f9e77b9.

## Blocker addressed
HELM activation AMBER (HELM_HERMES_PH2_GAPS_SURFACE_ACTIVATION::2026-07-13T00:00Z::AMBER_...REQUIRES_CODE_WO):
PR#89 shipped the gaps module dark AND inert — GapsPublisher had no SET path and no runtime call site. This PR adds both,
still dark by default, so a future activation WO can flip the two existing gates and publish.

## Changed files (additive; diff = exact.diff)
- utils/hermes_gaps_v1.py — add GAPS_KEY; add GapsPublisher.publish() (single SET of GAPS_KEY, re-validates first); add
  env-only gate gaps_publish_enabled(); refresh docstrings. GapsPublisher.__init__/analyze unchanged; pure builders unchanged.
- utils/hermes_runtime_publisher_steps_v1.py — import gaps; add gaps_step(client): gate-first no-op; enabled-without-
  authorised -> SystemExit(101); enabled+authorised -> pub.publish(now=_now(), forward gates reported from D1 env).
- utils/hermes_publisher_runtime_v1.py — append ("gaps", steps.gaps_step, DEFAULT_INTERVAL) ONLY when
  gaps.gaps_publish_enabled() (env-only, no client). Dark by default -> 4 default runners unchanged.
- tests/test_hermes_gaps_publish_wiring_v1.py (NEW) — 12 wiring tests.
- tests/test_hermes_gaps_v1.py — updated ONE PR#89 assertion whose premise this WO supersedes (publisher had no .set)
  to the new invariant: exactly one SET of GAPS_KEY, no other write/delete. (Not a schema/classification test.)

## Publication method summary
GapsPublisher.publish(now, forward_enabled, forward_authorised): analyze_gaps() -> validate_gaps_contract() ->
redis.set(GAPS_KEY, json) — ONE key, persistent (no TTL; parity with control-plane manifest/catalog). Returns
{published:1, key, overall_gap_state, consumer_live:False, repair_executed:False, backfill_executed:False}.

## Runtime wiring summary
gaps_step gate-first via build_gaps_publisher_from_env(redis_client=client); runner appended only when both gates true.
No Redis client constructed during runner assembly (env-only gate). Existing PH1/D1 surfaces unaffected when disabled.

## Gate behaviour
HERMES_GAPS_PUBLISH_ENABLED / HERMES_GAPS_PUBLISH_AUTHORISED (external config via env_config.get_env_bool; NOT code
constants). Disabled -> no-op (no write). Enabled-without-authorised -> SystemExit(101). Enabled+authorised -> single-key
publish. No hidden default; nothing enables publication in this WO.

## Findings
- disabled no-op: gaps_step returns {published:0}, zero writes/deletes/zadds — PROVEN (test_disabled_publisher_and_step_are_noop)
- enabled-without-authorised fail-closed: SystemExit(101) at build/gate/step and runner-assembly — PROVEN
- enabled+authorised single-key publish: r.sets == [(GAPS_KEY, None)] — PROVEN (test-only; no live publication)
- no runtime publication in this WO: no deploy/activation; gates remain unset; PR code-only
- no Redis delete / no candle-history write / no zadd: PROVEN (test_publish_writes_only_gaps_key_no_candle_no_delete)
- no SQL / market_map / Falcon / consumer_live=True in new code: PROVEN (test_new_code_has_no_forbidden_deps_or_semantics)
- invariants consumer_live/repair_executed/backfill_executed hard false in published payload — PROVEN
- XAUUSD alias never leaks through publish — PROVEN ; weekend closed slots never GAPS_FOUND through publish — PROVEN
- PH1 D1 regression: NONE — full D1/catalog/control-plane suites green (160 curated tests pass)

## Next gate
R2D2 cold audit of this PR. Then merge -> deploy-dark -> re-run activation WO.
