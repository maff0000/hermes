"""WO-HELM-HERMES-DEV-DEPLOYMENT-IDENTITY-AND-HOST-CONFIG-BINDING-0001 — tests.

Covers the R2D2-recorded deployment-identity defect (Redis contract publishing
environment=dev / run_env=STAGING / deployed_sha=unknown against a PROD runtime)
and the host/config binding that makes wrong-environment configuration
mechanically FAIL CLOSED.

NON-VACUITY: every negative case drives the REAL resolver with a genuinely
wrong/missing fact and asserts the specific IDENT-* reason code; wiring pins
parse the REAL main.py / publisher-steps source so a regression to defaulted
identity variables fails the suite.
"""
from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE_DIR = HERE.parent
sys.path = [p for p in sys.path if 'config' not in p or 'tradingSignals' in p]
sys.path.insert(0, str(BASE_DIR))

from utils import hermes_runtime_identity_v1 as ident  # noqa: E402

MAIN_PY = BASE_DIR / "main.py"
STEPS_PY = BASE_DIR / "utils" / "hermes_runtime_publisher_steps_v1.py"
COMPOSE = BASE_DIR / "docker-compose.yml"

GOOD_SHA = "37e587a2272ac312e643a57fcf5557109ba59361"


def _env(**overrides):
    base = {"ENVIRONMENT": "DEV", "RUN_ENV": "STAGING",
            "EXPECTED_HOSTNAME": "dell-debian", "HOST_HOSTNAME_PATH": "/x/host"}
    base.update(overrides)
    return lambda k: base.get(k)


def _build(valid=True, sha=GOOD_SHA):
    return lambda: {"source_sha": sha, "build_identity_valid": valid}


def _host(name="dell-debian"):
    def reader(path):
        if name is None:
            raise FileNotFoundError(path)
        return name + "\n"
    return reader


def _resolve(**kw):
    args = {"env_get": _env(), "build_identity_fn": _build(), "host_reader": _host()}
    args.update(kw)
    return ident.resolve_runtime_identity(**args)


class TestPositiveResolution(unittest.TestCase):
    def test_dev_config_on_dev_host_passes(self):
        r = _resolve()
        self.assertEqual((r.environment, r.run_env, r.source_sha, r.actual_hostname),
                         ("DEV", "STAGING", GOOD_SHA, "dell-debian"))

    def test_prod_config_on_prod_host_passes(self):
        r = _resolve(env_get=_env(ENVIRONMENT="PROD", RUN_ENV="PRODUCTION",
                                  EXPECTED_HOSTNAME="HERMES"),
                     host_reader=_host("HERMES"))
        self.assertEqual((r.environment, r.run_env), ("PROD", "PRODUCTION"))


class TestFailClosed(unittest.TestCase):
    def _expect(self, code, **kw):
        with self.assertRaises(ident.DeploymentIdentityError) as ctx:
            _resolve(**kw)
        self.assertEqual(ctx.exception.code, code)

    def test_dev_config_on_prod_host_fails_closed(self):
        # DEV env file (EXPECTED_HOSTNAME=dell-debian) accidentally on the PROD box.
        self._expect(ident.HOST_MISMATCH, host_reader=_host("HERMES"))

    def test_prod_config_on_dev_host_fails_closed(self):
        self._expect(ident.HOST_MISMATCH,
                     env_get=_env(ENVIRONMENT="PROD", RUN_ENV="PRODUCTION",
                                  EXPECTED_HOSTNAME="HERMES"),
                     host_reader=_host("dell-debian"))

    def test_wrong_expected_hostname_fails_closed(self):
        self._expect(ident.HOST_MISMATCH, env_get=_env(EXPECTED_HOSTNAME="not-this-box"))

    def test_missing_expected_hostname_fails_closed(self):
        self._expect(ident.EXPECTED_HOST_MISSING, env_get=_env(EXPECTED_HOSTNAME=None))
        self._expect(ident.EXPECTED_HOST_MISSING, env_get=_env(EXPECTED_HOSTNAME="  "))

    def test_missing_host_source_fails_closed(self):
        self._expect(ident.HOST_SOURCE_MISSING, host_reader=_host(None))

    def test_missing_environment_fails_closed(self):
        self._expect(ident.ENV_MISSING, env_get=_env(ENVIRONMENT=None))
        self._expect(ident.ENV_MISSING, env_get=_env(ENVIRONMENT=""))

    def test_invalid_environment_aliases_fail_closed(self):
        for bad in ("dev", "prod", "STAGING", "NON_PROD", "Dev"):
            self._expect(ident.ENV_INVALID, env_get=_env(ENVIRONMENT=bad))

    def test_missing_run_env_fails_closed(self):
        self._expect(ident.RUNENV_MISSING, env_get=_env(RUN_ENV=None))

    def test_run_env_pair_mismatch_fails_closed(self):
        # A PROD-flavoured RUN_ENV with a DEV environment (or vice versa) is a
        # wrong-environment config: one unambiguous identity, no contradictions.
        self._expect(ident.RUNENV_PAIR_MISMATCH, env_get=_env(RUN_ENV="PRODUCTION"))
        self._expect(ident.RUNENV_PAIR_MISMATCH,
                     env_get=_env(ENVIRONMENT="PROD", RUN_ENV="STAGING",
                                  EXPECTED_HOSTNAME="HERMES"),
                     host_reader=_host("HERMES"))

    def test_missing_or_invalid_build_identity_fails_per_contract(self):
        # 'unknown' is NOT a deployable identity — governed build contract.
        self._expect(ident.BUILD_SHA_INVALID, build_identity_fn=_build(sha="unknown"))
        self._expect(ident.BUILD_SHA_INVALID, build_identity_fn=_build(sha=""))
        self._expect(ident.BUILD_SHA_INVALID, build_identity_fn=_build(sha="UNKNOWN_SOURCE_SHA"))
        self._expect(ident.BUILD_SHA_INVALID, build_identity_fn=_build(valid=False))


class TestConsistencyInvariant(unittest.TestCase):
    IDENT = ident.RuntimeIdentity(environment="DEV", run_env="STAGING", source_sha=GOOD_SHA,
                                  expected_hostname="dell-debian", actual_hostname="dell-debian")

    def test_consistent_surfaces_return_no_faults(self):
        manifest = {"environment": "DEV", "run_env": "STAGING", "deployed_sha": GOOD_SHA}
        heartbeat = {"run_env": "STAGING", "deployed_sha": GOOD_SHA}
        self.assertEqual(ident.check_identity_consistency(self.IDENT, manifest), [])
        self.assertEqual(ident.check_identity_consistency(self.IDENT, heartbeat), [])

    def test_environment_mismatch_detected(self):
        faults = ident.check_identity_consistency(
            self.IDENT, {"environment": "dev", "run_env": "STAGING", "deployed_sha": GOOD_SHA})
        self.assertIn("IDENT-PUBLISHED-ENV-MISMATCH", faults)

    def test_sha_mismatch_detected(self):
        faults = ident.check_identity_consistency(
            self.IDENT, {"environment": "DEV", "run_env": "STAGING", "deployed_sha": "unknown"})
        self.assertIn("IDENT-PUBLISHED-SHA-MISMATCH", faults)

    def test_runenv_mismatch_detected(self):
        faults = ident.check_identity_consistency(
            self.IDENT, {"environment": "DEV", "run_env": "PRODUCTION", "deployed_sha": GOOD_SHA})
        self.assertIn("IDENT-PUBLISHED-RUNENV-MISMATCH", faults)


class TestWiringPins(unittest.TestCase):
    """The mechanism that produced false identity must be structurally gone."""

    def test_steps_no_longer_read_defaulted_identity_variables(self):
        src = STEPS_PY.read_text()
        for forbidden in ("HERMES_DEPLOYED_SHA", "HERMES_RUN_ENV", "HERMES_ENVIRONMENT"):
            self.assertNotIn(forbidden, src,
                             f"{forbidden} must not exist — identity derives from the canonical source")
        deployed_sha_fn = src[src.index("def _deployed_sha"):src.index("def _run_env")]
        self.assertNotIn('"unknown"', deployed_sha_fn,
                         "deployed_sha must never default to 'unknown'")
        self.assertIn("cached_runtime_identity", src)

    def test_manifest_and_heartbeat_environment_cannot_mismatch_runtime(self):
        """Both Redis identity surfaces are built from the SAME _run_env()/_deployed_sha()
        values inside one control_plane_step call, which now read the single cached
        runtime identity — mismatch is impossible by construction. Pin the wiring."""
        src = STEPS_PY.read_text()
        step = src[src.index("def control_plane_step"):]
        self.assertIn("run_env, environment = _run_env()", step)
        self.assertIn("_deployed_sha()", step)
        self.assertIn("check_identity_consistency", step)   # §9 invariant present

    def test_lifespan_validates_identity_before_any_external_init(self):
        tree = ast.parse(MAIN_PY.read_text())
        lifespan = None
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "lifespan":
                lifespan = node
                break
        self.assertIsNotNone(lifespan)
        src = ast.unparse(lifespan)
        ident_pos = src.index("cached_runtime_identity()")
        # Match the CALLS, not the imports at the top of lifespan.
        for later in ("RedisPublisher(", "await wait_for_db_ready(", "state.config = load_config()"):
            self.assertLess(ident_pos, src.index(later),
                            f"identity validation must precede {later}")

    def test_compose_mounts_host_hostname_readonly(self):
        text = COMPOSE.read_text()
        self.assertIn("/etc/hostname:/etc/host-hostname:ro", text,
                      "deployment must mount the HOST /etc/hostname read-only for host truth")


if __name__ == "__main__":
    unittest.main()
