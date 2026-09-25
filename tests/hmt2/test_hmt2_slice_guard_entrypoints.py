"""HMT-2 slice-containment guard — wiring tests for the real, real-data HMT-2 entry points.

Confirms `market_truth.acquisition.hmt2_slice_guard.require_hmt2_slice()` is genuinely wired
into each real-data entry point's `main()`, as the very first thing that happens, before any
file I/O against retained corpus data:

  - `research/hmt2/hmt2i_gc_corpus_canonicalise.py`  (canonicalisation driver)
  - `research/hmt2/hmt2h_gc_corpus_acquire.py`        (bulk acquisition driver)
  - `research/hmt2/hmt2e_mbp1_pilot_acquire.py`        (pilot acquisition -- same real-data
                                                          profile: real vendor bytes into the
                                                          durable `research-source/` store)
  - `research/hmt2/hmt2f_mbp1_pilot_replay.py`         (pilot replay -- runs the real
                                                          canonicalisation pipeline over real,
                                                          retained pilot session bytes)

Two layers, deliberately:

  1. STATIC (AST) -- `main()`'s first statement in every one of these four files really is a
     call to `require_hmt2_slice(...)`, mirroring this repo's own established static-guard
     pattern (`tests/hmt2/test_hmt2c_mbp1_quote_script_guard.py`).
  2. DYNAMIC -- each script is loaded as a real module (same `importlib.util.spec_from_file_
     location` pattern `tests/hmt2/test_hmt2h_gc_corpus_acquire.py` already uses) and its real
     `main()` is actually called, with the guard's OWN cgroup-read simulated via the injectable
     seam (`market_truth.acquisition.hmt2_slice_guard.read_own_cgroup_text`) -- never the real
     filesystem. Every script's own downstream real-work entry point (`run_batch`, `run`,
     `load_databento_api_key`) is monkeypatched to a sentinel stub REGARDLESS of the simulated
     cgroup state, so this test can never trigger real network access, real spend, or a real
     write into `research-source/`/`research-canonical-store/`, no matter what the guard
     decides.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from market_truth.acquisition import hmt2_slice_guard as guard_mod  # noqa: E402

INSIDE_SLICE_CGROUP_TEXT = "0::/hmt2.slice/hmt2-20260924T194554Z-1996826.scope\n"
OUTSIDE_SLICE_CGROUP_TEXT = "0::/user.slice/user-0.slice/session-102753.scope\n"


class _DownstreamReached(Exception):
    """Raised by every stub below -- proves execution reached the real business logic, never
    that it actually ran (nothing past the stub ever executes)."""


def _load_module(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GUARDED_ENTRYPOINTS = [
    "research/hmt2/hmt2i_gc_corpus_canonicalise.py",
    "research/hmt2/hmt2h_gc_corpus_acquire.py",
    "research/hmt2/hmt2e_mbp1_pilot_acquire.py",
    "research/hmt2/hmt2f_mbp1_pilot_replay.py",
]


# ---------------------------------------------------------------------------------------------
# STATIC: require_hmt2_slice(...) is main()'s first statement, in every guarded entry point.
# ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("relative_path", GUARDED_ENTRYPOINTS)
def test_entrypoint_exists(relative_path):
    assert (REPO_ROOT / relative_path).exists()


@pytest.mark.parametrize("relative_path", GUARDED_ENTRYPOINTS)
def test_entrypoint_imports_require_hmt2_slice(relative_path):
    tree = ast.parse((REPO_ROOT / relative_path).read_text(encoding="utf-8"), filename=relative_path)
    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "market_truth.acquisition.hmt2_slice_guard":
            imported_names.update(alias.name for alias in node.names)
    assert "require_hmt2_slice" in imported_names, (
        f"{relative_path}: must import require_hmt2_slice from market_truth.acquisition.hmt2_slice_guard"
    )


@pytest.mark.parametrize("relative_path", GUARDED_ENTRYPOINTS)
def test_entrypoint_main_calls_require_hmt2_slice_as_its_first_statement(relative_path):
    tree = ast.parse((REPO_ROOT / relative_path).read_text(encoding="utf-8"), filename=relative_path)
    main_funcs = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    ]
    assert len(main_funcs) == 1, f"{relative_path}: expected exactly one top-level main()"
    main_body = main_funcs[0].body
    # Skip a leading docstring/comment-only Expr(Constant) if present -- none of these scripts
    # have one on main(), but this keeps the check honest rather than positional-fragile.
    first_real_stmt = main_body[0]
    if (
        isinstance(first_real_stmt, ast.Expr)
        and isinstance(getattr(first_real_stmt, "value", None), ast.Constant)
    ):
        first_real_stmt = main_body[1]
    assert isinstance(first_real_stmt, ast.Expr), f"{relative_path}: main()'s first statement is not a call"
    call = first_real_stmt.value
    assert isinstance(call, ast.Call), f"{relative_path}: main()'s first statement is not a call"
    assert isinstance(call.func, ast.Name) and call.func.id == "require_hmt2_slice", (
        f"{relative_path}: main()'s first statement must be require_hmt2_slice(...), found "
        f"{ast.dump(call.func)}"
    )


# ---------------------------------------------------------------------------------------------
# DYNAMIC: the real main() of each script, with the guard's cgroup-read simulated and every
# downstream real-work call stubbed to a sentinel exception (so nothing real ever executes).
# ---------------------------------------------------------------------------------------------

def test_hmt2i_canonicalise_refuses_outside_slice(monkeypatch):
    module = _load_module("hmt2i_gc_corpus_canonicalise_dyntest", GUARDED_ENTRYPOINTS[0])
    monkeypatch.setattr(guard_mod, "read_own_cgroup_text", lambda: OUTSIDE_SLICE_CGROUP_TEXT)
    monkeypatch.delenv(guard_mod.ALLOW_OUTSIDE_SLICE_ENV_VAR, raising=False)
    monkeypatch.setattr(module, "run_batch", lambda **kw: (_ for _ in ()).throw(_DownstreamReached()))
    monkeypatch.setattr(sys, "argv", ["hmt2i_gc_corpus_canonicalise.py", "--session-ids", "FAKE-SESSION"])

    with pytest.raises(RuntimeError) as excinfo:
        module.main()
    assert "hmt2.slice" in str(excinfo.value)


def test_hmt2i_canonicalise_proceeds_inside_slice(monkeypatch):
    module = _load_module("hmt2i_gc_corpus_canonicalise_dyntest2", GUARDED_ENTRYPOINTS[0])
    monkeypatch.setattr(guard_mod, "read_own_cgroup_text", lambda: INSIDE_SLICE_CGROUP_TEXT)
    monkeypatch.delenv(guard_mod.ALLOW_OUTSIDE_SLICE_ENV_VAR, raising=False)
    monkeypatch.setattr(module, "run_batch", lambda **kw: (_ for _ in ()).throw(_DownstreamReached()))
    monkeypatch.setattr(sys, "argv", ["hmt2i_gc_corpus_canonicalise.py", "--session-ids", "FAKE-SESSION"])

    with pytest.raises(_DownstreamReached):
        module.main()


def test_hmt2i_canonicalise_proceeds_via_explicit_test_bypass(monkeypatch, capsys):
    module = _load_module("hmt2i_gc_corpus_canonicalise_dyntest3", GUARDED_ENTRYPOINTS[0])
    monkeypatch.setattr(guard_mod, "read_own_cgroup_text", lambda: OUTSIDE_SLICE_CGROUP_TEXT)
    monkeypatch.setenv(guard_mod.ALLOW_OUTSIDE_SLICE_ENV_VAR, "1")
    monkeypatch.setattr(module, "run_batch", lambda **kw: (_ for _ in ()).throw(_DownstreamReached()))
    monkeypatch.setattr(sys, "argv", ["hmt2i_gc_corpus_canonicalise.py", "--session-ids", "FAKE-SESSION"])

    with pytest.raises(_DownstreamReached):
        module.main()
    assert "WARNING" in capsys.readouterr().err


def test_hmt2h_acquire_refuses_outside_slice(monkeypatch):
    module = _load_module("hmt2h_gc_corpus_acquire_dyntest", GUARDED_ENTRYPOINTS[1])
    monkeypatch.setattr(guard_mod, "read_own_cgroup_text", lambda: OUTSIDE_SLICE_CGROUP_TEXT)
    monkeypatch.delenv(guard_mod.ALLOW_OUTSIDE_SLICE_ENV_VAR, raising=False)
    monkeypatch.setattr(module, "run_batch", lambda **kw: (_ for _ in ()).throw(_DownstreamReached()))
    monkeypatch.setattr(sys, "argv", ["hmt2h_gc_corpus_acquire.py"])

    with pytest.raises(RuntimeError) as excinfo:
        module.main()
    assert "hmt2.slice" in str(excinfo.value)


def test_hmt2h_acquire_proceeds_inside_slice(monkeypatch):
    module = _load_module("hmt2h_gc_corpus_acquire_dyntest2", GUARDED_ENTRYPOINTS[1])
    monkeypatch.setattr(guard_mod, "read_own_cgroup_text", lambda: INSIDE_SLICE_CGROUP_TEXT)
    monkeypatch.delenv(guard_mod.ALLOW_OUTSIDE_SLICE_ENV_VAR, raising=False)
    monkeypatch.setattr(module, "run_batch", lambda **kw: (_ for _ in ()).throw(_DownstreamReached()))
    monkeypatch.setattr(sys, "argv", ["hmt2h_gc_corpus_acquire.py"])

    with pytest.raises(_DownstreamReached):
        module.main()


def test_hmt2e_pilot_acquire_refuses_outside_slice(monkeypatch):
    module = _load_module("hmt2e_mbp1_pilot_acquire_dyntest", GUARDED_ENTRYPOINTS[2])
    monkeypatch.setattr(guard_mod, "read_own_cgroup_text", lambda: OUTSIDE_SLICE_CGROUP_TEXT)
    monkeypatch.delenv(guard_mod.ALLOW_OUTSIDE_SLICE_ENV_VAR, raising=False)
    # Belt-and-suspenders: even if the guard were somehow bypassed, this stub would stop
    # execution before any credential read or network call.
    monkeypatch.setattr(module, "load_databento_api_key", lambda: (_ for _ in ()).throw(_DownstreamReached()))

    with pytest.raises(RuntimeError) as excinfo:
        module.main()
    assert "hmt2.slice" in str(excinfo.value)


def test_hmt2e_pilot_acquire_proceeds_inside_slice(monkeypatch):
    module = _load_module("hmt2e_mbp1_pilot_acquire_dyntest2", GUARDED_ENTRYPOINTS[2])
    monkeypatch.setattr(guard_mod, "read_own_cgroup_text", lambda: INSIDE_SLICE_CGROUP_TEXT)
    monkeypatch.delenv(guard_mod.ALLOW_OUTSIDE_SLICE_ENV_VAR, raising=False)
    monkeypatch.setattr(module, "load_databento_api_key", lambda: (_ for _ in ()).throw(_DownstreamReached()))

    # Reaches the real (but harmless, local-file-only) manifest/activity reads, then hits the
    # stubbed credential loader -- never a real network call either way.
    with pytest.raises(_DownstreamReached):
        module.main()


def test_hmt2f_pilot_replay_refuses_outside_slice(monkeypatch, tmp_path):
    module = _load_module("hmt2f_mbp1_pilot_replay_dyntest", GUARDED_ENTRYPOINTS[3])
    monkeypatch.setattr(guard_mod, "read_own_cgroup_text", lambda: OUTSIDE_SLICE_CGROUP_TEXT)
    monkeypatch.delenv(guard_mod.ALLOW_OUTSIDE_SLICE_ENV_VAR, raising=False)
    monkeypatch.setattr(module, "run", lambda *a, **kw: (_ for _ in ()).throw(_DownstreamReached()))
    monkeypatch.setattr(
        sys, "argv",
        [
            "hmt2f_mbp1_pilot_replay.py",
            "--output-root", str(tmp_path / "output"),
            "--summary-path", str(tmp_path / "summary.json"),
        ],
    )

    with pytest.raises(RuntimeError) as excinfo:
        module.main()
    assert "hmt2.slice" in str(excinfo.value)


def test_hmt2f_pilot_replay_proceeds_inside_slice(monkeypatch, tmp_path):
    module = _load_module("hmt2f_mbp1_pilot_replay_dyntest2", GUARDED_ENTRYPOINTS[3])
    monkeypatch.setattr(guard_mod, "read_own_cgroup_text", lambda: INSIDE_SLICE_CGROUP_TEXT)
    monkeypatch.delenv(guard_mod.ALLOW_OUTSIDE_SLICE_ENV_VAR, raising=False)
    monkeypatch.setattr(module, "run", lambda *a, **kw: (_ for _ in ()).throw(_DownstreamReached()))
    monkeypatch.setattr(
        sys, "argv",
        [
            "hmt2f_mbp1_pilot_replay.py",
            "--output-root", str(tmp_path / "output"),
            "--summary-path", str(tmp_path / "summary.json"),
        ],
    )

    with pytest.raises(_DownstreamReached):
        module.main()
