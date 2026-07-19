# FW-08 PR#114 exact-audit corrections — evidence bundle

WO-HELM-HERMES-PR114-FW08-EXACT-AUDIT-CORRECTIONS-AND-CANONICAL-PREFLIGHT-CLOSURE-0001
Authority: HELM. Continues OPEN PR #114 (branch wo/WO-HELM-HERMES-FW08-GOVERNED-STAGE-B-IMAGE-BUILD-ENFORCEMENT-IMPLEMENTATION-0001).
Prior audited head: 066ff0aa. Canonical base: fe2037c5.

All 8 R2D2 exact-head findings closed on the open PR (no merge, no rebase, no force-push, no real build):
- §6  secret-scanner false-positive -> typed, versioned, fingerprint-bound disposition (12 adversarial tests all pass; value never stored)
- §7  real canonical preflight reaches USABLE against exact fe2037c5 (no docker/image/tag/SBOM/vuln/publish/deploy)
- §8  trusted-ref freshness (authenticated-fetch OR governed immutable binding; 8+ reject cases, fixture repos)
- §9  final TOCTOU boundary (replace/symlink-swap/added/removed/mode-change all reject before runner)
- §10 docker-pattern parity (comment/escape/inverted-class/malformed-fail-closed/literal-bracket/parent+child/repeated**/trailing/rooted)
- §11 vuln allow-list governance (exact typed unexpired disposition binds every field; no blanket exception)
- §12 readiness evidence binding (no fabricatable bare booleans; fabricated/mismatched/stale/contradictory/missing reject)
- §13 active Phase-2 import evidence (direct/transitive/dynamic/runner/plugin/env-default/test-only reject; inert accept)

Files:
- 20_original_selfmatch_reproduction.md  — rule SEC-PRIVATE-KEY-PPK, path tools/hermes_clean_build_context_v1.py, value NOT disclosed
- 21_scanner_disposition_contract.json   — governed fingerprint-bound disposition(s)
- 22_scanner_12_adversarial_tests.txt    — the 12 §6 adversarial cases (+ integration) PASS
- 23_canonical_preflight_usable.json      — preflight report: terminal_state USABLE, no-docker proof, 0 docker subprocess calls
- 24_*_tests.txt                          — §8/§9/§10/§11/§12/§13 tests PASS
- 25_no_docker_proof.md                   — docker images before/after (18/18), no hermes-fw08 image, runner refuses
- 26_focused_test_output.txt              — governed_build + corrections suites PASS (incl. prior 74)
- 27_full_suite_totals.txt                — full suite totals
- 28_node_diff.txt                        — baseline vs head node diff = EMPTY (51F+4E preserved)
- 29_pr113_regression.txt                 — PR#113 scanner-tool contract tests PASS (no regression)

NEVER contains a matched secret-like value.
