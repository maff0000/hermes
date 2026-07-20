# FW-08 Prerequisite Verification

- **Base SHA:** `fe2037c5b4b4836d1038b4c0f734426ac9b3fa02` (worktree HEAD before this WO's commit).
- **Base == refs/remotes/origin/main:** yes (`git rev-parse refs/remotes/origin/main` == base).
- **PR#113 reuse present + unmodified:** `tools/hermes_clean_build_context_v1.py`,
  `tools/hermes_image_label_verify_v1.py`, `schemas/deployment_readiness/build_context_manifest.v1.schema.json`
  imported/composed, NOT edited (git status shows no modification to those files).
- **Regression baseline:** 51 FAILED + 4 ERROR nodes (env-dependent: Redis/DB/starlette/async — pre-existing),
  reproduced before changes (`/tmp/fw08_baseline_for_agent.txt`), empty diff after changes (see 15_node_diff.txt).
- **Live runtime unchanged (documented, not verified/mutated here):** deploy 71ea3bd, container d80018037b7f,
  image c5fc2a62f424, restarts 0, runners 9, consumer_live=false. This WO reads/mutates NO runtime.
- **No real docker / registry / SBOM / scan / deploy / Redis / SQL / migration / /etc write performed.**
- **No new third-party dependency** — stdlib only (proven by test_tools_and_models_stdlib_only).
