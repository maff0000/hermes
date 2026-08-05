# HERMES CI runner environment contract
WO-HELM-HERMES-CI-RUNNER-ENVIRONMENT-CONTRACT-REPAIR-0001

Governed HERMES tests assume a repository-faithful environment. GitHub Actions must satisfy:
1. FULL GIT HISTORY — FW08/pre-stage-b governed-build tests inspect canonical trees/blobs by SHA
   (git cat-file / ls-tree). `actions/checkout` MUST use `fetch-depth: 0` (all checkout steps do).
2. REPOSITORY IDENTITY, NOT TRANSPORT — the provenance check compares CANONICAL identity
   (host/owner/repo via tools.hermes_clean_build_context_v1.canonical_repo_identity), so an HTTPS
   Actions checkout of the SAME repo is accepted while foreign repos are rejected.
3. NON-ROOT-SAFE SNAPSHOT TAMPER TESTS — the runner user is non-root; the post-finalise mutation test
   re-enables directory writes before its deliberate tamper (root's perm-bypass previously masked this).
   The immutability DETECTION assertion is unchanged.
No test is skipped, deselected, xfailed or weakened. No privileged/sudo/chmod-777. Cost: fetch-depth:0
fetches full history (small repo) — acceptable.
