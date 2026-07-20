# FW-08 Design Summary (evidence pointer)

Full contract: `docs/design/deployment_readiness/fw08_governed_stage_b_build_v1.md`
and `docs/design/deployment_readiness/architecture_v1.md` §7B.

- §6 wrapper `tools/hermes_stage_b_build_v1.py` — sole governed Stage-B entrypoint.
- §7 trusted provenance (F-113-01): reachable-from refs/remotes/origin/main OR authorised PR head + audit
  binding; --no-replace-objects; rejects orphan/foreign/spoofed/replaced/abbrev/branch/HEAD.
- §8/§9 clean-context mandatory + quarantine (F-113-06): pre-materialisation prohibited rejection,
  UNUSABLE marker, destroyed-on-failure, no partial/success-on-failure.
- §10 docker-faithful matcher (F-113-03): design/hermes_fw08_dockerignore_matcher_v1.py + parity fixtures.
- §11/§12 mandatory inputs + explicit command array + injectable runners (fake/gated-real).
- §13 OCI post-build (F-113-02): expected_source_sha never optional.
- §14 image content, §15 SBOM (mandatory), §16 vuln-scan (mandatory).
- §17 secret scan = defence-in-depth (F-113-04, documented limits).
- §18 state machine (no PUBLISHED/DEPLOYED state) + §19 pure evaluator (all-green + safety flags clear).
- §20 no-publish/no-deploy static+behavioural guards; §21 config/Phase-2 safety.
