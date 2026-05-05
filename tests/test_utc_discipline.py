"""WO-HERMES-UTC-AUDIT-FIX-0001 — UTC discipline tests for HERMES.

Mirrors the platform tripwire — Python-level invariant that survives even
if the bash tripwire is bypassed. Asserts no `datetime.utcnow()` /
naked `datetime.now()` / `date.today()` in HERMES production code unless
annotated with `UTC_AUDIT_METADATA_OK: <reason>`.

Companion to tradingProteus/structure_engine/tests/unit/test_utc_discipline.py
in the platform-side WO.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


_BAD_PATTERNS = [
    re.compile(r'\bdatetime\.utcnow\(\)'),
    re.compile(r'\bdatetime\.now\(\)'),
    re.compile(r'\bdate\.today\(\)'),
]
_EXCLUDE_PARTS = {'tests', '__pycache__'}


def _repo_root() -> Path:
    # tests live at /srv-dev/tradingSignals/tests/test_utc_discipline.py
    # → repo root is parents[1]
    return Path(__file__).resolve().parents[1]


def test_static_scan_hermes_has_no_unannotated_unsafe_time_sources():
    """Mirror of HERMES bash tripwire — fails if any production .py file
    contains a banned time-source pattern without a UTC_AUDIT_METADATA_OK
    annotation on the same or preceding line."""
    repo_root = _repo_root()
    failures = []
    for py in repo_root.rglob('*.py'):
        if any(p in _EXCLUDE_PARTS for p in py.parts):
            continue
        try:
            lines = py.read_text(encoding='utf-8', errors='replace').splitlines()
        except Exception:
            continue
        for i, line in enumerate(lines):
            stripped = line.lstrip()
            if stripped.startswith('#'):
                continue
            for pat in _BAD_PATTERNS:
                if not pat.search(line):
                    continue
                if 'UTC_AUDIT_METADATA_OK:' in line:
                    m = re.search(r'UTC_AUDIT_METADATA_OK:\s*(\S.+)', line)
                    if m and m.group(1).strip():
                        continue
                if i > 0:
                    prev = lines[i - 1]
                    m = re.search(r'UTC_AUDIT_METADATA_OK:\s*(\S.+)', prev)
                    if m and m.group(1).strip():
                        continue
                rel = py.relative_to(repo_root)
                failures.append(f'{rel}:{i+1} {pat.pattern} {line.strip()[:120]}')
    assert not failures, (
        'HERMES production unsafe time-source usages without UTC_AUDIT_METADATA_OK '
        'annotation:\n' + '\n'.join(failures)
    )


def test_signaltick_received_at_uses_tz_aware_utc():
    """SignalTick.received_at fallback must source from datetime.now(timezone.utc),
    not datetime.utcnow() (timezone-naive)."""
    src_path = _repo_root() / 'models' / 'tick.py'
    if not src_path.exists():
        pytest.skip('models/tick.py not present')
    src = src_path.read_text()
    assert 'self.received_at = datetime.now(timezone.utc)' in src, (
        'SignalTick.received_at fallback must use datetime.now(timezone.utc).'
    )
    # No tz-naive utcnow() outside annotated comments.
    for lineno, line in enumerate(src.splitlines(), 1):
        if 'datetime.utcnow()' not in line:
            continue
        if line.lstrip().startswith('#'):
            continue
        if 'UTC_AUDIT_METADATA_OK:' in line:
            continue
        pytest.fail(f'models/tick.py:{lineno} still uses datetime.utcnow(): {line}')


def test_signal_builder_publish_uses_tz_aware_utc():
    """signal_builder.py redis-publish updated_at must source from
    datetime.now(timezone.utc)."""
    src_path = _repo_root() / 'signal_builder.py'
    if not src_path.exists():
        pytest.skip('signal_builder.py not present')
    src = src_path.read_text()
    assert "'updated_at': datetime.now(timezone.utc).isoformat()" in src, (
        'signal_builder.py redis-publish updated_at must use datetime.now(timezone.utc).'
    )


def test_oanda_adapter_source_timestamp_primary_path_uses_oanda_native():
    """OANDA adapter must prefer OANDA's native timestamp; wall-clock is
    a fallback for malformed input only."""
    src_path = _repo_root() / 'adapters' / 'oanda.py'
    if not src_path.exists():
        pytest.skip('adapters/oanda.py not present')
    src = src_path.read_text()
    # The primary path uses fromisoformat(time_str)
    assert 'datetime.fromisoformat(time_str' in src, (
        'OANDA adapter must parse OANDA native timestamp via fromisoformat.'
    )
    # Wall-clock is an exception fallback only
    assert 'except Exception' in src and 'source_timestamp = datetime.now(timezone.utc)' in src, (
        'OANDA adapter wall-clock fallback must be inside an exception handler.'
    )
