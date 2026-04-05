"""
Environment variable compliance tests
EPIC-D002 / STORY-D002-05
GOV-ENV-001: Environment-aware configuration

Tests:
- Verify no legacy TAS_* variables in codebase
- Verify standardized DB_*, REDIS_* vars used
- Verify .env file compliance
- Verify no hardcoded paths (GOV-CAB-CODE-001)
"""
import pytest
import os
import re
from pathlib import Path

# Base directory (relative to this test file)
TESTS_DIR = Path(__file__).parent.absolute()
BASE_DIR = TESTS_DIR.parent


class TestEnvFileCompliance:
    """Test .env file uses correct variable names"""

    def test_env_file_exists(self):
        """Test .env file exists"""
        env_path = BASE_DIR / ".env"
        assert env_path.exists(), ".env file not found"

    def test_env_has_environment_tag(self):
        """Test .env has ENVIRONMENT tag (GOV-ENV-001)"""
        env_path = BASE_DIR / ".env"
        content = env_path.read_text()
        assert "ENVIRONMENT=" in content, "Missing ENVIRONMENT tag"

    def test_env_uses_db_prefix(self):
        """Test .env uses DB_* not TAS_DB_*"""
        env_path = BASE_DIR / ".env"
        content = env_path.read_text()

        # Should have DB_HOST (with or without DEV_/PROD_ prefix)
        assert "DB_HOST=" in content or "DEV_DB_HOST=" in content, "Missing DB_HOST"
        assert "TAS_DB_HOST=" not in content, "Legacy TAS_DB_HOST found"

    def test_env_uses_redis_prefix(self):
        """Test .env uses REDIS_* not TAS_REDIS_*"""
        env_path = BASE_DIR / ".env"
        content = env_path.read_text()

        assert "REDIS_HOST=" in content or "DEV_REDIS_HOST=" in content, "Missing REDIS_HOST"
        assert "TAS_REDIS_HOST=" not in content, "Legacy TAS_REDIS_HOST found"


class TestCodebaseCompliance:
    """Test codebase uses correct variable names"""

    PYTHON_FILES = [
        BASE_DIR / "config.py",
        BASE_DIR / "utils" / "db_writer.py",
        BASE_DIR / "utils" / "redis_publisher.py",
        BASE_DIR / "scripts" / "backfill_oanda.py",
    ]

    def test_no_legacy_tas_db_vars(self):
        """Test no TAS_DB_* variables in Python files"""
        legacy_pattern = re.compile(r'TAS_DB_\w+')

        for filepath in self.PYTHON_FILES:
            if filepath.exists():
                content = filepath.read_text()
                matches = legacy_pattern.findall(content)
                assert len(matches) == 0, f"Legacy TAS_DB_* found in {filepath}: {matches}"

    def test_no_legacy_tas_redis_vars(self):
        """Test no TAS_REDIS_* variables in Python files"""
        legacy_pattern = re.compile(r'TAS_REDIS_\w+')

        for filepath in self.PYTHON_FILES:
            if filepath.exists():
                content = filepath.read_text()
                matches = legacy_pattern.findall(content)
                assert len(matches) == 0, f"Legacy TAS_REDIS_* found in {filepath}: {matches}"

    def test_uses_env_config(self):
        """Test config.py uses env_config module"""
        config_path = BASE_DIR / "config.py"
        content = config_path.read_text()

        assert 'env_config' in content, "config.py should use env_config module"


class TestNoHardcodedPaths:
    """Test no hardcoded environment-specific paths (GOV-CAB-CODE-001)"""

    def test_no_hardcoded_srv_paths(self):
        """Test no hardcoded /srv-dev/ or /srv/ paths in main code"""
        hardcoded_pattern = re.compile(r'["\'][/]srv[-/]')

        # Check all Python files except tests
        for py_file in BASE_DIR.rglob("*.py"):
            if "tests" in str(py_file) or "__pycache__" in str(py_file):
                continue

            content = py_file.read_text()
            matches = hardcoded_pattern.findall(content)
            assert len(matches) == 0, f"Hardcoded path in {py_file.relative_to(BASE_DIR)}: {matches}"

    def test_env_config_uses_relative_paths(self):
        """Test env_config.py uses Path(__file__) for relative paths"""
        env_config_path = BASE_DIR / "env_config.py"
        if env_config_path.exists():
            content = env_config_path.read_text()
            assert "__file__" in content, "env_config.py should use __file__ for paths"


class TestNoHardcodedCredentials:
    """Test no hardcoded credentials in code"""

    PYTHON_FILES = [
        BASE_DIR / "main.py",
        BASE_DIR / "config.py",
        BASE_DIR / "utils" / "db_writer.py",
        BASE_DIR / "utils" / "redis_publisher.py",
    ]

    def test_no_hardcoded_passwords(self):
        """Test no hardcoded passwords in code"""
        password_patterns = [
            re.compile(r'password\s*=\s*["\'][^"\']+["\']', re.IGNORECASE),
        ]

        for filepath in self.PYTHON_FILES:
            if filepath.exists():
                content = filepath.read_text()
                for pattern in password_patterns:
                    matches = pattern.findall(content)
                    # Filter out os.getenv patterns and empty defaults
                    real_matches = [m for m in matches
                                    if 'getenv' not in m.lower()
                                    and 'get_env' not in m.lower()
                                    and '""' not in m
                                    and "''" not in m]
                    assert len(real_matches) == 0, \
                        f"Possible hardcoded password in {filepath}: {real_matches}"
