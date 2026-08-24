"""WO-HELM-HERMES-DEV-REDIS-CONSUMER-INTEGRITY-READONLY-BOUNDARY-0001 — tests.

Writer-authority seam + wiring pins for the Redis consumer-integrity boundary:
consumers read `hermes:*` read-only; HERMES publishes through a dedicated ACL
writer authority; a misconfigured authority FAILS LOUDLY (no silent fallback
to default Redis authority — §9).
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE_DIR = HERE.parent
sys.path = [p for p in sys.path if 'config' not in p or 'tradingSignals' in p]
sys.path.insert(0, str(BASE_DIR))

from utils.hermes_redis_auth_v1 import RedisAuthConfigError, redis_auth_kwargs  # noqa: E402

WIRED_SITES = [
    "utils/redis_publisher.py",
    "utils/candle_d1_history_v1.py",
    "utils/hermes_gaps_v1.py",
    "utils/hermes_backfill_status_v1.py",
    "utils/candle_runtime_seam_v1.py",
    "utils/candle_history_forward_writer_v1.py",
    "utils/tick_live_emitter_v1.py",
    "utils/hermes_publisher_runtime_v1.py",
    "utils/tick_shadow_publisher_v1.py",
    "healthcheck/signal_health.py",
    "scripts/shadow_activate_publish.py",
]


def _env(**kv):
    return lambda k: kv.get(k)


class TestAuthKwargs(unittest.TestCase):
    def test_unset_username_is_legacy_open_model(self):
        self.assertEqual(redis_auth_kwargs(env_get=_env()), {})
        self.assertEqual(redis_auth_kwargs(env_get=_env(REDIS_USERNAME="")), {})
        self.assertEqual(redis_auth_kwargs(env_get=_env(REDIS_USERNAME="  ")), {})

    def test_configured_authority_returns_username_and_password(self):
        kw = redis_auth_kwargs(env_get=_env(REDIS_USERNAME="hermes-writer",
                                            REDIS_PASSWORD="sekret-fixture-8Zk2"))
        self.assertEqual(kw, {"username": "hermes-writer", "password": "sekret-fixture-8Zk2"})

    def test_missing_password_fails_loudly_never_falls_back(self):
        """§9: configured-but-incomplete authority must RAISE — never return {}
        (which would silently connect as the open default user)."""
        for pw in (None, "", "   "):
            with self.assertRaises(RedisAuthConfigError):
                redis_auth_kwargs(env_get=_env(REDIS_USERNAME="hermes-writer",
                                               REDIS_PASSWORD=pw))

    def test_error_message_names_no_secret(self):
        try:
            redis_auth_kwargs(env_get=_env(REDIS_USERNAME="hermes-writer", REDIS_PASSWORD=""))
        except RedisAuthConfigError as exc:
            self.assertNotIn("sekret", str(exc))
            self.assertIn("REDIS_USERNAME", str(exc))


class TestWiringPins(unittest.TestCase):
    def test_every_runtime_redis_constructor_uses_the_auth_seam(self):
        """Every registered runtime redis client constructor must splat
        redis_auth_kwargs() — one authority seam, no site left on implicit
        default authority once the boundary is active."""
        for rel in WIRED_SITES:
            src = (BASE_DIR / rel).read_text()
            self.assertIn("redis_auth_kwargs", src, f"{rel} not wired to the auth seam")

    def test_no_unwired_redis_constructor_exists(self):
        """Sweep: any module constructing a redis client outside tests/design
        must import the auth seam (prevents future drift)."""
        for path in BASE_DIR.rglob("*.py"):
            rel = str(path.relative_to(BASE_DIR))
            if rel.startswith(("tests/", "design/", ".git")) or "/design/" in rel:
                continue
            if rel == "utils/hermes_redis_auth_v1.py":
                continue
            src = path.read_text(errors="ignore")
            if re.search(r"redis\.Redis\(|redis\.from_url\(", src):
                self.assertIn("redis_auth_kwargs", src,
                              f"{rel} constructs a redis client without the auth seam")

    def test_redis_publisher_prefers_writer_authority_over_bare_password(self):
        src = (BASE_DIR / "utils/redis_publisher.py").read_text()
        self.assertIn('_auth.get("password", self.password)', src)
        self.assertIn('_auth.get("username")', src)

    def test_no_config_in_code(self):
        """No hardcoded redis endpoint/username/secret in the auth seam CODE
        (docstrings may reference example names; executable code may not)."""
        import ast
        src = (BASE_DIR / "utils/hermes_redis_auth_v1.py").read_text()
        tree = ast.parse(src)
        literals = [n.value for n in ast.walk(tree)
                    if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        # drop docstrings (module/class/function first-statement strings)
        docstrings = set()
        for node in [tree] + [n for n in ast.walk(tree)
                              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]:
            d = ast.get_docstring(node, clean=False)
            if d:
                docstrings.add(d)
        code_literals = " ".join(v for v in literals if v not in docstrings)
        for forbidden in ("hermes-writer", "hermes-consumer", "6379", "192.168.", "194.164."):
            self.assertNotIn(forbidden, code_literals,
                             f"auth seam must not hardcode {forbidden!r} (external config only)")


if __name__ == "__main__":
    unittest.main()
