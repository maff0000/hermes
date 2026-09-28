"""
Pytest fixtures for tradingSignals tests
EPIC-D002 / STORY-D002-05
GOV-ENV-001: Environment-aware configuration
"""
import os
import sys
import pytest
from datetime import datetime
from pathlib import Path

# Add parent path for imports (relative to this file)
# CRITICAL: Insert at position 0 to override any /srv-dev/config shadows
TESTS_DIR = Path(__file__).parent.absolute()
BASE_DIR = TESTS_DIR.parent

# Remove any paths that might shadow our imports
sys.path = [p for p in sys.path if 'config' not in p or 'tradingSignals' in p]
sys.path.insert(0, str(BASE_DIR))

# Load test environment
from dotenv import load_dotenv
load_dotenv(BASE_DIR / '.env')


@pytest.fixture
def sample_tick_data():
    """Sample tick data for testing"""
    return {
        "instrument": "XAU_USD",
        "bid": 2650.50,
        "ask": 2651.00,
        "timestamp": datetime.utcnow().isoformat(),
        "source": "mock"
    }


@pytest.fixture
def sample_candle_data():
    """Sample candle data for testing"""
    return {
        "instrument": "XAU_USD",
        "timeframe": "M5",
        "timestamp": datetime.utcnow().isoformat(),
        "open": 2650.00,
        "high": 2655.00,
        "low": 2649.00,
        "close": 2652.00,
        "volume": 150,
        "complete": True
    }


@pytest.fixture
def redis_config():
    """Redis configuration from environment"""
    return {
        "host": os.getenv("REDIS_HOST", "127.0.0.1"),
        "port": int(os.getenv("REDIS_PORT", "6379")),
        "password": os.getenv("REDIS_PASSWORD", ""),
        "db": int(os.getenv("REDIS_DB", "1"))
    }


@pytest.fixture
def db_config():
    """Database configuration from environment"""
    return {
        "host": os.getenv("DB_HOST", "127.0.0.1"),
        "port": int(os.getenv("DB_PORT", "3306")),
        "user": os.getenv("DB_USER", "root"),
        "password": os.getenv("DB_PASSWORD", ""),
        "database": os.getenv("DB_NAME", "tradingSignals")
    }


# ---------------------------------------------------------------------------
# WO-HELM-HERMES-DEV-DEPLOYMENT-IDENTITY-AND-HOST-CONFIG-BINDING-0001:
# publishers now derive Redis identity metadata from the SINGLE canonical
# runtime identity (resolved fail-closed at startup). Tests exercising the
# publisher steps run with a deterministic seeded identity instead of the
# host-bound resolution (which would correctly fail closed in a test
# environment). Autouse + reset: no test can leak identity into another,
# and identity-resolution tests construct their own resolver inputs.
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _seed_runtime_identity_for_tests():
    from utils import hermes_runtime_identity_v1 as _ident
    _ident._CACHED = _ident.RuntimeIdentity(
        environment="DEV", run_env="STAGING",
        source_sha="0" * 40,
        expected_hostname="test-host", actual_hostname="test-host",
    )
    yield
    _ident._reset_cached_identity_for_tests()


# ---------------------------------------------------------------------------
# WO-HELM-HERMES-DEV-REDIS-CAPACITY-RETENTION-AND-PROD-INCIDENT-RECOVERY-
# DESIGN-0001: history retention is now REQUIRED external config (no hidden
# default in application logic — see candle_history_v1.history_retention_days).
# Autouse so every test gets a deterministic value without needing a local
# .env (which is gitignored and not present in CI); tests that specifically
# exercise the missing/invalid-config error paths delete/override it themselves.
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _seed_history_retention_days_for_tests(monkeypatch):
    monkeypatch.setenv("HERMES_REDIS_HISTORY_RETENTION_DAYS", "14")


# ---------------------------------------------------------------------------
# hmt-2/emergency-destructive-test-fix (2026-09-25) -- TEST-EXECUTION DOCTRINE GUARD.
#
# Incident: a merged test resolved the REAL default HMT-2 canonical-store root (no
# --canonical-research-root override, no HMT2_CANONICAL_RESEARCH_ROOT env var) and
# unconditionally `shutil.rmtree()`'d it in a `finally` block. Run against the persistent
# authoritative worktree (which had 150 real sessions' canonical data), this destroyed all of
# it. See tests/hmt2/test_hmt2i_snapshot_output_path.py and tests/support/
# destructive_cleanup_guard.py for the fix to that one test and the new reusable
# cleanup-ownership invariant every recursive-delete-performing test must now use.
#
# This is the second, independent layer: a session-start sanity check that refuses to run ANY
# part of this suite at all if the REAL, default-resolved HMT-2 canonical-store root or
# research-source root is found non-empty. A disposable/fresh checkout or worktree never has
# either directory populated -- both are gitignored, never committed -- so this can only ever
# trip when pytest's cwd/worktree already holds real retained/canonicalised HMT-2 data, which is
# exactly the situation that must never be exercised by this suite again.
#
# Doctrine, made explicit: run this test suite ONLY from a disposable/fresh checkout, or a
# worktree you know has no real HMT-2 data in it -- never from the persistent authoritative
# HMT-2 worktree. Conscious, explicit override (e.g. a reviewer who has independently verified
# it is safe): HMT2_ALLOW_TESTS_AGAINST_POPULATED_DEFAULT_ROOT=1.
# ---------------------------------------------------------------------------
def pytest_sessionstart(session):  # noqa: ARG001 - pytest hook signature
    if os.environ.get("HMT2_ALLOW_TESTS_AGAINST_POPULATED_DEFAULT_ROOT"):
        return
    try:
        from market_truth.acquisition import canonical_worker as _canonical_worker
    except ImportError:
        return  # this checkout has no HMT-2 module at all -- nothing to guard

    candidates = []
    try:
        candidates.append(("HMT-2 default canonical-store root", Path(_canonical_worker.corpus_canonical_store_root(None))))
    except Exception:
        pass
    try:
        repo_root = Path(_canonical_worker.__file__).resolve().parents[2]
        candidates.append(("HMT-2 default research-source root", repo_root / "research-source"))
    except Exception:
        pass

    populated = [
        f"{label} ({path}) already exists and is non-empty"
        for label, path in candidates
        if path.exists() and any(path.rglob("*"))
    ]
    if populated:
        raise pytest.UsageError(
            "REFUSING TO RUN THE TEST SUITE: " + "; ".join(populated) + ". This worktree/cwd "
            "resolves the REAL default HMT-2 canonical-store/research-source root to a location "
            "that already contains real data -- running the suite here risks repeating the "
            "hmt-2/emergency-destructive-test-fix incident (a destructive test wiped 150 real "
            "sessions' canonical data by resolving to exactly this default). Run the suite from "
            "a disposable/fresh checkout instead, or set "
            "HMT2_ALLOW_TESTS_AGAINST_POPULATED_DEFAULT_ROOT=1 if you have independently "
            "verified this run is safe."
        )
