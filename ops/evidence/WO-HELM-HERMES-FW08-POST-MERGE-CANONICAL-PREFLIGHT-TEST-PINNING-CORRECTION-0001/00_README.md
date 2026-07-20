# WO-HELM-HERMES-FW08-POST-MERGE-CANONICAL-PREFLIGHT-TEST-PINNING-CORRECTION-0001

TEST-ONLY correction. NO production code changed. NO freshness weakening. NO merge/revert. NO image built.
Base SHA: 11a43f6bf1a07642626f1157688d84ce604a35e4 (merge of PR#114).

## Defect (root cause)
Three FW-08 canonical-preflight tests hardcoded CANONICAL_BASE=fe2037c5... and ran run_canonical_preflight
against the LIVE repo (repo_dir=/srv/trading/hermes) in fresh-current source-trust mode. PR#114 advanced
origin/main to 11a43f6b, so fe2037c5 became a historical ANCESTOR. The FW-08 freshness control CORRECTLY
rejected it: reasons ('FRESHNESS-NOT-FRESH','LOCAL-REF-NOT-AUTHORISED-SHA'). The PRODUCTION CONTROL IS
CORRECT; the TEST DESIGN was wrong (depended on mutable live-repo state + a stale pinned SHA).
See 01_original_failing_nodes_repro.txt.

Failing nodes:
  tests/test_fw08_corrections_v1.py::test_s7_canonical_preflight_usable_against_exact_fe2037c5
  tests/test_fw08_corrections_v1.py::test_s7_preflight_invokes_no_real_docker
  tests/test_fw08_final_trust_v1.py::test_s17_canonical_preflight_usable_and_no_docker

## Fix (isolated deterministic git topology)
The three tests now bind to an ISOLATED fixture whose refs/remotes/origin/main == the EXACT requested SHA,
never the live host repo. Helpers added to tests/test_fw08_corrections_v1.py (test-namespace only):
  _make_canonical_fixture(root, selfmatch=True)  -> governed source A; origin/main == HEAD == returned SHA.
      Optionally commits a rule-definition self-match literal so the governed disposition mechanism is
      exercised end-to-end (dispositioned == 1). Reuses the existing _make_repo/_git isolated patterns.
  _canonical_advance(root)  -> commits source B and advances origin/main to it (A retained as ancestor);
      simulates a FUTURE canonical merge advancing origin/main.
  _selfmatch_disposition(sha, tmp_path)  -> fingerprint-bound governed disposition at the fixture's SHA.
  _preflight(...)  -> runs the NON-BUILDING preflight against the isolated fixture.

## Proofs
A. CONTROLLED CANONICAL-CURRENT SUCCESS: fixture origin/main == requested SHA -> USABLE via
   FRESH_AUTHENTICATED_FETCH; governed self-match disposition cleared (dispositioned==1); no docker.
   (test_s7_canonical_preflight_usable_against_exact_fe2037c5, test_s17..., test_s7_canonical_current_clean_fresh_usable)
   NON-TAUTOLOGY: the same fixture WITHOUT the disposition is REJECTED (test_s7_preflight_without_dispositions_rejects).
B. HISTORICAL-SHA REJECTION: advance origin/main to B, request historical A ->
   fresh mode: FRESHNESS-NOT-FRESH + AUTHORISED-SHA-MISMATCH (test_s7_historical_ancestor_rejected_fresh_mode)
   immutable-binding mode (exact original defect class): FRESHNESS-NOT-FRESH + LOCAL-REF-NOT-AUTHORISED-SHA
   (test_s7_historical_ancestor_rejected_immutable_binding).
C. FUTURE-MERGE DETERMINISM: one fixture, A@mainA -> USABLE; advance to B; A -> REJECTED; B -> USABLE
   (test_s7_origin_main_advance_determinism). Proves independence from whichever SHA is live.

## Freshness matrix (all fixture-based, all assert docker_invoked=False & runner_constructed=False)
current-fresh -> USABLE; historical ancestor -> REJECTED; descendant-not-on-main -> REJECTED (provenance);
stale local origin/main -> REJECTED; fetch-failure(no binding) -> REJECTED; remote-mismatch -> REJECTED;
abbreviated SHA -> REJECTED; foreign remote -> REJECTED. See 03_freshness_matrix.txt.
(§8 verify_trusted_ref_freshness-level matrix — stale/tamper/authorised-mismatch/canonical-state/foreign/
offline/fetch-fail — is retained unchanged in tests/test_fw08_corrections_v1.py test_s8_*.)

## No real build (§12)
DEFAULT RefusingDockerRunner; the preflight constructs NO DockerRunner. Every preflight case in this bundle
asserts runner_constructed=False and docker_invoked=False. No docker subprocess, image, tag, SBOM, vuln,
publish or deploy. trinty host has no docker; tests run in-process. See 10_no_docker_proof.txt.

## Node-set restoration
Full suite returns to EXACTLY 51F/4E (the pre-PR114 baseline); the 3 canonical-preflight failures are gone.
07_node_diff_vs_51F_baseline.txt is EMPTY. No other node changed.

## Production non-mutation
git diff --name-only 11a43f6b HEAD shows ONLY test files. See 08_production_non_mutation.txt,
09_changed_file_inventory.txt.
