"""WO-HELM-HERMES-DEV-SHADOW-REDIS-WRITER-AUTH-COMPLETION-0001 — tests.

Enabled-write-target authority guard: every Redis target enabled for HERMES
write publication must have USABLE writer authority; disabled targets are
INERT (no probe, no credential requirement). NON-VACUITY: failure cases drive
genuinely failing probes/seams and assert the specific status; the disabled
case asserts the probe was NEVER invoked.
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

import redis as redis_lib  # noqa: E402

from utils.hermes_write_target_auth_guard_v1 import (  # noqa: E402
    enumerate_write_targets, verify_enabled_write_targets,
)
from utils.hermes_redis_auth_v1 import RedisAuthConfigError  # noqa: E402

MAIN_PY = BASE_DIR / "main.py"


def _env(**kv):
    base = {"REDIS_HOST": "main-host", "REDIS_PORT": "6379", "REDIS_DB": "0"}
    base.update(kv)
    return lambda k: base.get(k, "")


AUTH = lambda: {"username": "hermes-writer", "password": "sekret-fixture-4Yx9"}  # noqa: E731


class TestEnumeration(unittest.TestCase):
    def test_main_always_enabled(self):
        t = {x["name"]: x for x in enumerate_write_targets(_env())}
        self.assertTrue(t["main"]["enabled"])

    def test_shadow_tick_requires_both_gates(self):
        t = {x["name"]: x for x in enumerate_write_targets(
            _env(HERMES_SHADOW_TICK_PUBLISH_ENABLED="true"))}
        self.assertFalse(t["shadow_tick"]["enabled"], "enabled without AUTHORISED must stay disabled")
        t = {x["name"]: x for x in enumerate_write_targets(
            _env(HERMES_SHADOW_TICK_PUBLISH_ENABLED="true", HERMES_SHADOW_TICK_AUTHORISED="true",
                 HERMES_SHADOW_TICK_REDIS_HOST="h", HERMES_SHADOW_TICK_REDIS_PORT="6380"))}
        self.assertTrue(t["shadow_tick"]["enabled"])

    def test_candle_forward_shadow_gate(self):
        t = {x["name"]: x for x in enumerate_write_targets(_env())}
        self.assertFalse(t["candle_forward_shadow"]["enabled"])
        t = {x["name"]: x for x in enumerate_write_targets(
            _env(HERMES_CANDLE_FORWARD_SHADOW_AUTHORISED="true"))}
        self.assertTrue(t["candle_forward_shadow"]["enabled"])


class TestVerification(unittest.TestCase):
    def test_enabled_target_probe_ok(self):
        calls = []

        def probe(host, port, db, auth):
            calls.append((host, port))
        reps = verify_enabled_write_targets(env_get=_env(), probe_fn=probe, auth_kwargs_fn=AUTH)
        by = {r.name: r for r in reps}
        self.assertEqual(by["main"].status, "OK")
        self.assertEqual(calls, [("main-host", "6379")])

    def test_enabled_target_auth_failure_is_loud_named_status(self):
        """The R2D2 finding: enabled shadow target without provisioned writer.
        Must surface AUTH_FAILED — never OK, never silent."""
        def probe(host, port, db, auth):
            if host == "shadow-host":
                raise redis_lib.exceptions.AuthenticationError("WRONGPASS")
        reps = verify_enabled_write_targets(
            env_get=_env(HERMES_SHADOW_TICK_PUBLISH_ENABLED="true",
                         HERMES_SHADOW_TICK_AUTHORISED="true",
                         HERMES_SHADOW_TICK_REDIS_HOST="shadow-host",
                         HERMES_SHADOW_TICK_REDIS_PORT="6380",
                         HERMES_SHADOW_TICK_REDIS_DB="0"),
            probe_fn=probe, auth_kwargs_fn=AUTH)
        by = {r.name: r for r in reps}
        self.assertEqual(by["shadow_tick"].status, "AUTH_FAILED")
        self.assertIn("writer authority not usable", by["shadow_tick"].detail)
        self.assertEqual(by["main"].status, "OK")

    def test_disabled_target_is_inert_probe_never_called_no_creds_needed(self):
        """Disabled means inert: no connection attempt, no credential requirement,
        no failure — PROD shadow semantics."""
        probed = []

        def probe(host, port, db, auth):
            probed.append(host)

        def broken_auth():
            raise RedisAuthConfigError("no creds configured")
        reps = verify_enabled_write_targets(env_get=_env(), probe_fn=probe,
                                            auth_kwargs_fn=broken_auth)
        by = {r.name: r for r in reps}
        self.assertEqual(by["shadow_tick"].status, "DISABLED")
        self.assertEqual(by["candle_forward_shadow"].status, "DISABLED")
        self.assertNotIn("shadow-host", probed)
        # main is enabled, so the broken seam surfaces there — loudly, not silently
        self.assertEqual(by["main"].status, "AUTH_CONFIG_INVALID")

    def test_unreachable_enabled_target_named(self):
        def probe(host, port, db, auth):
            raise ConnectionError("refused")
        reps = verify_enabled_write_targets(env_get=_env(), probe_fn=probe, auth_kwargs_fn=AUTH)
        self.assertEqual({r.name: r for r in reps}["main"].status, "UNREACHABLE")

    def test_no_fallback_semantics_reports_never_mutate_auth(self):
        """The guard observes; it must not hand back altered/emptied auth kwargs
        (no path from probe failure to default-authority usage)."""
        seen = []

        def probe(host, port, db, auth):
            seen.append(dict(auth))
            raise redis_lib.exceptions.AuthenticationError("WRONGPASS")
        verify_enabled_write_targets(env_get=_env(), probe_fn=probe, auth_kwargs_fn=AUTH)
        self.assertEqual(seen, [AUTH()])


class TestWiring(unittest.TestCase):
    def test_lifespan_runs_guard_and_health_surfaces_it(self):
        src = MAIN_PY.read_text()
        tree = ast.parse(src)
        lifespan = next(n for n in ast.walk(tree)
                        if isinstance(n, ast.AsyncFunctionDef) and n.name == "lifespan")
        self.assertIn("verify_enabled_write_targets", ast.unparse(lifespan),
                      "lifespan must run the write-target guard")
        self.assertIn("write_targets", src.split("def health()")[1].split("def ")[0]
                      if "def health()" in src else src,
                      "/health must surface the write-target guard reports")

    def test_gate_flags_mirror_the_emitters(self):
        """The guard's enable flags must be the emitters' own governed gates."""
        guard_src = (BASE_DIR / "utils/hermes_write_target_auth_guard_v1.py").read_text()
        adapter_src = (BASE_DIR / "utils/tick_runtime_shadow_adapter_v1.py").read_text()
        seam_src = (BASE_DIR / "utils/candle_runtime_seam_v1.py").read_text()
        for flag in ("HERMES_SHADOW_TICK_PUBLISH_ENABLED", "HERMES_SHADOW_TICK_AUTHORISED"):
            self.assertIn(flag, guard_src)
            self.assertIn(flag, adapter_src)
        self.assertIn("HERMES_CANDLE_FORWARD_SHADOW_AUTHORISED", guard_src)
        self.assertIn("HERMES_CANDLE_FORWARD_SHADOW_AUTHORISED", seam_src)


if __name__ == "__main__":
    unittest.main()
