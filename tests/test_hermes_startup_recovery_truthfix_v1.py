"""WO-HELM-HERMES-DEV-STARTUP-RECOVERY-RESILIENCE-AND-HEALTH-TRUTHFIX-0001 — recovery + truth-surface tests.

Covers the four truth/recovery defects proven by the 2026-08-20 Dell reboot:

  1. NEVER-CONNECTED RECOVERY — a failed initial OANDA connect must arm the SAME
     governed recovery loop as a dropped stream (task started in both branches,
     first-class StreamState.NEVER_CONNECTED, migration 026 admits it).
  2. HEALTH TRUTH — the compose container healthcheck must run the governed
     /health probe (utils/hermes_healthcheck_probe_v1), never a bare TCP connect.
  3. READINESS TRUTH — /readiness db/redis connectivity are live bounded probes
     (current truth), not a copy of the health colour.
  4. PUBLISHER HEARTBEAT TRUTH — top-level status derives from per-timeframe
     freshness; all-UNKNOWN can never publish OK.

NON-VACUITY: recovery-path tests drive the real failure paths (raising fakes, a
genuinely unconnected adapter); wiring pins parse the REAL main.py AST — they fail
if the create_task call moves back inside the success-only branch.
"""
from __future__ import annotations

import ast
import re
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
BASE_DIR = HERE.parent
sys.path = [p for p in sys.path if 'config' not in p or 'tradingSignals' in p]
sys.path.insert(0, str(BASE_DIR))

from utils.watchdog import HermesWatchdog, StreamState  # noqa: E402
from utils import hermes_readiness_observers_v1 as obs  # noqa: E402
from utils.hermes_runtime_publisher_steps_v1 import derive_publisher_status  # noqa: E402
from utils.hermes_control_plane_v1 import build_publisher_heartbeat, build_health_summary  # noqa: E402

MAIN_PY = BASE_DIR / "main.py"
COMPOSE = BASE_DIR / "docker-compose.yml"
MIGRATION_026 = BASE_DIR / "migrations" / "026_stream_state_never_connected.sql"
MIGRATION_026_ROLLBACK = BASE_DIR / "migrations" / "026_stream_state_never_connected_rollback.sql"
MIGRATION_013 = BASE_DIR / "migrations" / "013_stream_state_partial_flowing.sql"


# --------------------------------------------------------------------------- helpers
def _strip_sql_comments(sql_text):
    out = []
    for line in sql_text.splitlines():
        i = line.find('--')
        if i != -1:
            line = line[:i]
        out.append(line)
    return '\n'.join(out)


def _stream_state_enum_values(sql_text):
    code = _strip_sql_comments(sql_text)
    m = re.search(r"stream_state\s+ENUM\s*\((.*?)\)", code, re.IGNORECASE | re.DOTALL)
    assert m, "stream_state ENUM(...) not found in SQL"
    return re.findall(r"'([^']+)'", m.group(1))


def _make_watchdog():
    """HermesWatchdog with mocked collaborators (matches sibling test convention)."""
    w = HermesWatchdog(
        service_state=MagicMock(),
        persistence=MagicMock(),
        config={
            'per_instrument_sustained_red_threshold_sec': 300,
            'per_instrument_recovery_cooldown_sec': 600,
            'per_instrument_max_recovery_attempts_per_hour': 3,
        },
        market_hours_checker=MagicMock(return_value=True),
        logger=MagicMock(),
    )
    return w


def _lifespan_node(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "lifespan":
            return node
    raise AssertionError("lifespan() not found in main.py")


def _connect_if_node(lifespan):
    """The `if await state.oanda_adapter.connect():` node."""
    hits = []
    for node in ast.walk(lifespan):
        if isinstance(node, ast.If) and "oanda_adapter.connect" in ast.unparse(node.test):
            hits.append(node)
    assert len(hits) == 1, f"expected exactly one oanda-connect If in lifespan, got {len(hits)}"
    return hits[0]


def _contains_create_stream_task(node_or_list):
    nodes = node_or_list if isinstance(node_or_list, list) else [node_or_list]
    for n in nodes:
        for sub in ast.walk(n):
            if isinstance(sub, ast.Call):
                src = ast.unparse(sub)
                if "create_task" in src and "oanda_stream_task" in src:
                    return True
    return False


# =========================================================================== 1. never-connected recovery
class TestNeverConnectedState(unittest.TestCase):
    def test_stream_state_has_first_class_never_connected(self):
        self.assertEqual(StreamState.NEVER_CONNECTED, "NEVER_CONNECTED")

    def test_watchdog_accepts_and_reports_never_connected(self):
        w = _make_watchdog()
        w.set_stream_state(StreamState.NEVER_CONNECTED)
        self.assertEqual(w._current_stream_state, StreamState.NEVER_CONNECTED)

    def test_recovery_path_from_never_connected_reaches_flowing(self):
        """NEVER_CONNECTED -> RECOVERING -> (proof window) CONNECTED_UNPROVEN ->
        real tick -> FLOWING. The identical machinery a dropped stream uses —
        one recovery mechanism, not two."""
        w = _make_watchdog()
        w.set_stream_state(StreamState.NEVER_CONNECTED)
        w.set_stream_state(StreamState.RECOVERING)          # recovery loop attempt
        w.record_recovery_attempt()
        w.enter_proof_window()                               # connect() succeeded
        self.assertEqual(w._current_stream_state, StreamState.CONNECTED_UNPROVEN)
        w.record_tick(datetime.now(timezone.utc), instrument="XAU_USD")
        self.assertEqual(w._current_stream_state, StreamState.FLOWING)

    def test_unconnected_adapter_stream_raises_so_recovery_loop_engages(self):
        """The stream task's unified recovery depends on stream() failing loudly
        when no connection was ever established — prove that contract."""
        import asyncio
        from adapters.oanda import OANDAAdapter
        adapter = OANDAAdapter(api_key="k", account_id="a", instruments=["XAU_USD"],
                               environment="practice")

        async def consume():
            async for _ in adapter.stream():
                break

        with self.assertRaises(RuntimeError):
            asyncio.run(consume())

    def test_lifespan_starts_stream_task_in_both_connect_branches(self):
        """AST pin on the REAL main.py: create_task(oanda_stream_task()) must sit
        OUTSIDE the oanda-connect If (so a failed initial connect still starts
        the governed recovery loop), and the else-branch must set NEVER_CONNECTED.
        This is the exact wiring whose absence caused the 2026-08-20..24 outage."""
        tree = ast.parse(MAIN_PY.read_text())
        lifespan = _lifespan_node(tree)
        connect_if = _connect_if_node(lifespan)

        self.assertFalse(_contains_create_stream_task(connect_if.body),
                         "stream task creation must not live only in the connect-success branch")
        self.assertFalse(_contains_create_stream_task(connect_if.orelse),
                         "stream task creation must not be duplicated into the else branch")
        self.assertTrue(_contains_create_stream_task(lifespan),
                        "lifespan must start oanda_stream_task unconditionally")
        orelse_src = "\n".join(ast.unparse(n) for n in connect_if.orelse)
        self.assertIn("NEVER_CONNECTED", orelse_src,
                      "failed initial connect must set StreamState.NEVER_CONNECTED")

    def test_reconnect_backoff_compounds_across_failed_cycles(self):
        """AST pin: oanda_stream_task must NOT reset the governed backoff at loop
        top (the pre-existing unconditional reset ran after every FAILED
        reconnect too, so the delay never compounded — proven live 2026-08-24 as
        constant 5s reconnect hammering). The only reset sits on the PROVEN
        recovery path: the first real tick of a cycle."""
        tree = ast.parse(MAIN_PY.read_text())
        fn = None
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "oanda_stream_task":
                fn = node
                break
        self.assertIsNotNone(fn, "oanda_stream_task not found")
        outer_while = next(n for n in fn.body if isinstance(n, ast.While))
        try_node = next(n for n in outer_while.body if isinstance(n, ast.Try))

        def resets_delay(stmt):
            return (isinstance(stmt, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == "current_delay" for t in stmt.targets)
                    and isinstance(stmt.value, ast.Name) and stmt.value.id == "retry_initial")

        # No unconditional reset as a DIRECT statement of the try body (loop top).
        self.assertFalse(any(resets_delay(s) for s in try_node.body),
                         "backoff reset must not run unconditionally at loop top")
        # The guarded reset exists somewhere deeper (the tick path).
        guarded = [n for n in ast.walk(try_node) if isinstance(n, ast.If)
                   and any(resets_delay(s) for s in n.body)]
        self.assertTrue(guarded, "proven-recovery backoff reset (tick path) missing")

    def test_lifespan_gates_on_db_before_redis_publisher(self):
        """AST pin: wait_for_db_ready is awaited in lifespan BEFORE the
        RedisPublisher is constructed (i.e. before any DB-dependent init)."""
        src = MAIN_PY.read_text()
        tree = ast.parse(src)
        lifespan = _lifespan_node(tree)
        gate_line = redis_line = None
        for node in ast.walk(lifespan):
            if isinstance(node, ast.Call):
                call_src = ast.unparse(node)
                if "wait_for_db_ready" in call_src and gate_line is None:
                    gate_line = node.lineno
                if call_src.startswith("RedisPublisher(") and redis_line is None:
                    redis_line = node.lineno
        self.assertIsNotNone(gate_line, "lifespan must await wait_for_db_ready")
        self.assertIsNotNone(redis_line, "RedisPublisher construction not found")
        self.assertLess(gate_line, redis_line,
                        "DB gate must run before dependency-touching init")


class TestMigration026(unittest.TestCase):
    def setUp(self):
        self.assertTrue(MIGRATION_026.exists(), "migration 026 missing")
        self.enum026 = _stream_state_enum_values(MIGRATION_026.read_text())
        self.enum013 = _stream_state_enum_values(MIGRATION_013.read_text())

    def test_all_code_states_fit_enum_026(self):
        code_values = {v for k, v in vars(StreamState).items()
                       if not k.startswith('_') and isinstance(v, str)}
        missing = sorted(code_values - set(self.enum026))
        self.assertEqual(missing, [], f"enum 026 does not admit emitted states: {missing}")

    def test_append_only_ordinal_preservation(self):
        self.assertEqual(self.enum026[:len(self.enum013)], self.enum013,
                         "migration 026 must preserve 013 ordinals (append-only)")
        added = [v for v in self.enum026 if v not in self.enum013]
        self.assertEqual(added, ['NEVER_CONNECTED'])
        self.assertEqual(self.enum026[-1], 'NEVER_CONNECTED')

    def test_no_destructive_sql(self):
        code = _strip_sql_comments(MIGRATION_026.read_text()).upper()
        for forbidden in ("DROP TABLE", "TRUNCATE", "DELETE FROM"):
            self.assertNotIn(forbidden, code)

    def test_rollback_exists_and_narrows_back_to_013(self):
        self.assertTrue(MIGRATION_026_ROLLBACK.exists(), "rollback for 026 missing")
        rb = _stream_state_enum_values(MIGRATION_026_ROLLBACK.read_text())
        self.assertEqual(rb, self.enum013)


# =========================================================================== 2. container health truth
class TestComposeHealthcheckTruth(unittest.TestCase):
    def test_compose_healthcheck_uses_governed_probe(self):
        """A listening HTTP port is not health. The compose healthcheck must run
        the governed application probe — the same truth the Dockerfile uses."""
        text = COMPOSE.read_text()
        m = re.search(r"healthcheck:.*?start_period", text, re.DOTALL)
        # Find the hermes-signal healthcheck (the python one, not the DB's).
        blocks = re.findall(r"healthcheck:\s*\n\s*test:.*?(?=\n\s*\w+:|\Z)", text, re.DOTALL)
        signal_blocks = [b for b in blocks if "python" in b]
        self.assertTrue(signal_blocks, "hermes-signal healthcheck not found in compose")
        for b in signal_blocks:
            self.assertIn("hermes_healthcheck_probe_v1", b,
                          "compose healthcheck must call the governed /health probe")
            self.assertNotIn("connect_ex", b,
                             "bare TCP-connect healthcheck is forbidden (false-green class)")
        self.assertIsNotNone(m)

    def test_probe_semantics_listening_but_dead_is_unhealthy(self):
        """A 200 with no health contract, or a RED 503, must be UNHEALTHY; only
        GREEN/AMBER are healthy (AMBER tolerated to avoid recovery flap)."""
        from utils.hermes_healthcheck_probe_v1 import healthcheck_decode
        self.assertEqual(healthcheck_decode(200, "GREEN"), 0)
        self.assertEqual(healthcheck_decode(200, "AMBER"), 0)
        self.assertEqual(healthcheck_decode(503, "RED"), 1)
        self.assertEqual(healthcheck_decode(200, None), 1)   # listening-but-dead
        self.assertEqual(healthcheck_decode(None, None), 1)  # refused


# =========================================================================== 3. readiness truth
class TestReadinessConnectivityTruth(unittest.TestCase):
    DB = {"host": "h", "port": 3306, "user": "u", "password": "p", "database": "d"}

    def test_db_probe_true_only_on_live_roundtrip(self):
        conn = MagicMock()
        cur = MagicMock()
        conn.cursor.return_value = cur
        self.assertTrue(obs.probe_db_connectivity(self.DB, connect_fn=MagicMock(return_value=conn)))
        cur.execute.assert_called_once_with("SELECT 1")
        conn.close.assert_called_once()

    def test_db_probe_fail_closed_on_connect_error(self):
        def refuse(**kw):
            raise OSError("connection refused")
        self.assertFalse(obs.probe_db_connectivity(self.DB, connect_fn=refuse))

    def test_db_probe_fail_closed_on_query_error(self):
        conn = MagicMock()
        conn.cursor.side_effect = RuntimeError("server gone away")
        self.assertFalse(obs.probe_db_connectivity(self.DB, connect_fn=MagicMock(return_value=conn)))
        conn.close.assert_called_once()             # still closed, never leaked

    def test_db_probe_recovers_to_true_after_earlier_failure(self):
        """Current-truth semantics: a past failure must not poison a recovered
        runtime — the SAME call pattern returns True once the DB answers."""
        calls = {"n": 0}

        def flaky(**kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("down")
            conn = MagicMock()
            conn.cursor.return_value = MagicMock()
            return conn
        self.assertFalse(obs.probe_db_connectivity(self.DB, connect_fn=flaky))
        self.assertTrue(obs.probe_db_connectivity(self.DB, connect_fn=flaky))

    def test_redis_probe_semantics(self):
        ok = MagicMock()
        ok.ping.return_value = True
        self.assertTrue(obs.probe_redis_connectivity(ok))
        broken = MagicMock()
        broken.ping.side_effect = ConnectionError("refused")
        self.assertFalse(obs.probe_redis_connectivity(broken))
        self.assertFalse(obs.probe_redis_connectivity(None))

    def test_core_snapshot_wired_to_live_probes_not_health_colour(self):
        """AST pin on main.py: core_snapshot must call BOTH live probes and must
        not return the health-colour boolean twice (the 2026-08-24 false
        'database_connectivity: FAILED' defect)."""
        tree = ast.parse(MAIN_PY.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "core_snapshot":
                src = ast.unparse(node)
                self.assertIn("probe_db_connectivity", src)
                self.assertIn("probe_redis_connectivity", src)
                self.assertNotIn("return core, ok, ok", src)
                return
        self.fail("core_snapshot not found in main.py")

    def test_never_connected_maps_to_determinate_down_for_readiness(self):
        proj = obs.project_watchdog_stream({"stream_state": "NEVER_CONNECTED", "last_tick_utc": None})
        self.assertIsNotNone(proj)
        self.assertEqual(proj.health.state, "disconnected")
        proj_failed = obs.project_watchdog_stream({"stream_state": "FAILED", "last_tick_utc": None})
        self.assertIsNotNone(proj_failed)
        self.assertEqual(proj_failed.health.state, "disconnected")


# =========================================================================== 4. publisher heartbeat truth
class TestPublisherHeartbeatTruth(unittest.TestCase):
    def test_all_fresh_is_ok(self):
        self.assertEqual(derive_publisher_status(
            {"M1": "FRESH", "M5": "FRESH", "M15": "FRESH", "H1": "FRESH", "H4": "FRESH"}), "OK")

    def test_all_unknown_cannot_report_ok(self):
        """The exact 2026-08-20..24 lie: every timeframe UNKNOWN, status OK."""
        self.assertEqual(derive_publisher_status(
            {"M1": "UNKNOWN", "M5": "UNKNOWN", "M15": "UNKNOWN", "H1": "UNKNOWN", "H4": "UNKNOWN"}), "FAIL")

    def test_mixed_freshness_degrades(self):
        self.assertEqual(derive_publisher_status(
            {"M1": "FRESH", "M5": "UNKNOWN", "M15": "FRESH", "H1": "STALE", "H4": "FRESH"}), "WARN")

    def test_empty_map_fails_closed(self):
        self.assertEqual(derive_publisher_status({}), "FAIL")
        self.assertEqual(derive_publisher_status(None), "FAIL")

    def test_stale_status_propagates(self):
        self.assertEqual(derive_publisher_status({"M1": "STALE", "M5": "STALE"}), "FAIL")

    def test_recovered_state_returns_to_ok(self):
        halted = {"M1": "UNKNOWN", "M5": "UNKNOWN"}
        recovered = {"M1": "FRESH", "M5": "FRESH"}
        self.assertEqual(derive_publisher_status(halted), "FAIL")
        self.assertEqual(derive_publisher_status(recovered), "OK")

    def test_builders_accept_degraded_statuses(self):
        """The governed OK/WARN/FAIL vocabulary flows through both builders."""
        now = datetime.now(timezone.utc)
        for status in ("OK", "WARN", "FAIL"):
            hb = build_publisher_heartbeat(updated_at_utc=now, status=status,
                                           latest_key_freshness={"M1": "UNKNOWN"})
            self.assertEqual(hb["status"], status)
            hs = build_health_summary(generated_at_utc=now, overall_status=status,
                                      control_plane_active=True)
            self.assertEqual(hs["overall_status"], status)

    def test_control_plane_step_derives_status_not_hardcoded(self):
        """Source pin: the runtime step must pass the derived status into BOTH the
        heartbeat and the health summary — never a literal OK."""
        src = (BASE_DIR / "utils" / "hermes_runtime_publisher_steps_v1.py").read_text()
        step = src[src.index("def control_plane_step"):src.index("# =========================================================================== indicators")]
        self.assertIn("derive_publisher_status(lp_fresh)", step)
        self.assertIn("status=pub_status", step)
        self.assertIn("overall_status=pub_status", step)
        self.assertNotIn('status="OK"', step)


if __name__ == "__main__":
    unittest.main()
