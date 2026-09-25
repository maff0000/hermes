"""HMT-2 slice-containment guard — unit tests for
`market_truth.acquisition.hmt2_slice_guard`.

Covers the guard's own logic in isolation (cgroup-text parsing, fail-closed default, the
explicit test-only bypass and its loud warning) using the module's injectable seams
(`read_cgroup`, `env`) — never the real filesystem or real environment. See
`tests/hmt2/test_hmt2_slice_guard_entrypoints.py` for the dynamic/static tests wiring this guard
into the real HMT-2 entry points.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from market_truth.acquisition import hmt2_slice_guard as guard  # noqa: E402

INSIDE_SLICE_DIRECT = "0::/hmt2.slice\n"
INSIDE_SLICE_NESTED_SCOPE = "0::/hmt2.slice/hmt2-20260924T194554Z-1996826.scope\n"
OUTSIDE_SLICE_USER_SESSION = "0::/user.slice/user-0.slice/session-102753.scope\n"
OUTSIDE_SLICE_SIMILAR_NAME = "0::/not-hmt2.slice-foo/some.scope\n"  # must NOT false-positive
CGROUP_V1_STYLE_INSIDE = (
    "12:pids:/hmt2.slice/hmt2-20260924T194554Z-1996826.scope\n"
    "1:name=systemd:/hmt2.slice/hmt2-20260924T194554Z-1996826.scope\n"
)
CGROUP_V1_STYLE_OUTSIDE = (
    "12:pids:/user.slice/user-0.slice/session-102753.scope\n"
    "1:name=systemd:/user.slice/user-0.slice/session-102753.scope\n"
)


# ---------------------------------------------------------------------------------------------
# is_inside_hmt2_slice() -- pure cgroup-text parsing
# ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "cgroup_text",
    [INSIDE_SLICE_DIRECT, INSIDE_SLICE_NESTED_SCOPE, CGROUP_V1_STYLE_INSIDE],
)
def test_is_inside_hmt2_slice_true_cases(cgroup_text):
    assert guard.is_inside_hmt2_slice(cgroup_text) is True


@pytest.mark.parametrize(
    "cgroup_text",
    [
        OUTSIDE_SLICE_USER_SESSION,
        OUTSIDE_SLICE_SIMILAR_NAME,
        CGROUP_V1_STYLE_OUTSIDE,
        "",
        "\n\n",
    ],
)
def test_is_inside_hmt2_slice_false_cases(cgroup_text):
    assert guard.is_inside_hmt2_slice(cgroup_text) is False


def test_is_inside_hmt2_slice_never_substring_matches_a_similarly_named_slice():
    """A slice whose name merely CONTAINS the string "hmt2.slice" (e.g. a hypothetical
    "not-hmt2.slice-foo") must never be treated as hmt2.slice itself -- matching is by exact
    path SEGMENT, not substring."""
    assert guard.is_inside_hmt2_slice("0::/not-hmt2.slice-foo.scope\n") is False
    assert guard.is_inside_hmt2_slice("0::/prefix-hmt2.slice/hmt2-x.scope\n") is False


# ---------------------------------------------------------------------------------------------
# require_hmt2_slice() -- fail-closed default, via the injectable `read_cgroup`/`env` seams
# ---------------------------------------------------------------------------------------------

def test_require_hmt2_slice_raises_when_outside_slice_and_no_bypass():
    with pytest.raises(RuntimeError) as excinfo:
        guard.require_hmt2_slice(
            script_name="hmt2i_gc_corpus_canonicalise.py",
            read_cgroup=lambda: OUTSIDE_SLICE_USER_SESSION,
            env={},
        )
    message = str(excinfo.value)
    assert "hmt2i_gc_corpus_canonicalise.py" in message
    assert "REFUSING TO PROCEED" in message
    assert "hmt2.slice" in message
    # actionable: names the exact remedy
    assert guard.HMT2_RUN_WRAPPER_PATH in message
    assert "HMT2_ALLOW_OUTSIDE_SLICE_FOR_TESTS" in message


def test_require_hmt2_slice_returns_silently_when_inside_slice():
    # Must not raise, and must not print anything (no warning noise for the normal, correct
    # path).
    guard.require_hmt2_slice(
        script_name="hmt2i_gc_corpus_canonicalise.py",
        read_cgroup=lambda: INSIDE_SLICE_NESTED_SCOPE,
        env={},
    )


def test_require_hmt2_slice_returns_silently_when_inside_slice_directly_on_the_slice():
    guard.require_hmt2_slice(
        script_name="hmt2h_gc_corpus_acquire.py",
        read_cgroup=lambda: INSIDE_SLICE_DIRECT,
        env={},
    )


# ---------------------------------------------------------------------------------------------
# The explicit, narrow test-only bypass
# ---------------------------------------------------------------------------------------------

def test_bypass_env_var_allows_proceeding_outside_slice():
    called = {"read_cgroup_invoked": False}

    def _read_cgroup():
        called["read_cgroup_invoked"] = True
        return OUTSIDE_SLICE_USER_SESSION

    # Must not raise.
    guard.require_hmt2_slice(
        script_name="hmt2i_gc_corpus_canonicalise.py",
        read_cgroup=_read_cgroup,
        env={guard.ALLOW_OUTSIDE_SLICE_ENV_VAR: "1"},
    )
    # The bypass short-circuits before even reading the cgroup -- it never needs to know the
    # real membership once the explicit override is present.
    assert called["read_cgroup_invoked"] is False


def test_bypass_env_var_logs_a_loud_warning_when_used(capsys):
    guard.require_hmt2_slice(
        script_name="hmt2i_gc_corpus_canonicalise.py",
        read_cgroup=lambda: OUTSIDE_SLICE_USER_SESSION,
        env={guard.ALLOW_OUTSIDE_SLICE_ENV_VAR: "1"},
    )
    captured = capsys.readouterr()
    assert captured.out == ""  # the warning goes to stderr, never silently swallowed, never stdout
    assert "WARNING" in captured.err
    assert "hmt2i_gc_corpus_canonicalise.py" in captured.err
    assert guard.ALLOW_OUTSIDE_SLICE_ENV_VAR in captured.err
    assert "must NEVER be set for a real retained-corpus session run" in captured.err


def test_bypass_env_var_is_never_silent_when_it_is_not_even_needed():
    """Even when the process genuinely IS inside the slice, an accidentally-left-set bypass
    variable still logs its loud warning -- "never silently invisible" holds unconditionally,
    not just for the case where it actually changed the outcome."""
    import io
    import contextlib

    stderr_buffer = io.StringIO()
    with contextlib.redirect_stderr(stderr_buffer):
        guard.require_hmt2_slice(
            script_name="hmt2h_gc_corpus_acquire.py",
            read_cgroup=lambda: INSIDE_SLICE_NESTED_SCOPE,
            env={guard.ALLOW_OUTSIDE_SLICE_ENV_VAR: "1"},
        )
    assert "WARNING" in stderr_buffer.getvalue()


@pytest.mark.parametrize("near_miss_value", ["true", "True", "TRUE", "yes", "0", "01", " 1", "1 ", ""])
def test_bypass_only_activates_on_exact_string_one_never_on_a_near_miss(near_miss_value):
    """Guards against accidental activation via a truthy-looking-but-wrong value -- the bypass
    is deliberately strict (exact string "1" only) so it is never easy to set by accident."""
    with pytest.raises(RuntimeError):
        guard.require_hmt2_slice(
            script_name="hmt2i_gc_corpus_canonicalise.py",
            read_cgroup=lambda: OUTSIDE_SLICE_USER_SESSION,
            env={guard.ALLOW_OUTSIDE_SLICE_ENV_VAR: near_miss_value},
        )


def test_bypass_env_var_name_is_long_and_unambiguous():
    """Requirement: never a short/ambiguous flag name that could be set by accident."""
    assert guard.ALLOW_OUTSIDE_SLICE_ENV_VAR == "HMT2_ALLOW_OUTSIDE_SLICE_FOR_TESTS"
    assert len(guard.ALLOW_OUTSIDE_SLICE_ENV_VAR) >= 30


def test_require_hmt2_slice_defaults_to_real_os_environ_and_real_cgroup_read_when_uninjected():
    """Production callers pass neither `env` nor `read_cgroup` -- confirm the defaults really
    are `os.environ` and the real `/proc/self/cgroup` reader (exercised end-to-end against
    THIS test process's own, real, ambient cgroup membership, which is never hmt2.slice in a
    plain pytest run) -- i.e. the fail-closed default truly is live, not just in the injectable
    path."""
    import os

    assert guard.ALLOW_OUTSIDE_SLICE_ENV_VAR not in os.environ, (
        "test invalidated: the real environment already has the bypass set"
    )
    with pytest.raises(RuntimeError):
        guard.require_hmt2_slice(script_name="hmt2i_gc_corpus_canonicalise.py")
