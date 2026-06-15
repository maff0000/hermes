"""Tests for the HERMES structure-ingest boundary (DEP-2/DEP-3 severance).
WO-HELM-HERMES-STRUCTURE-ENGINE-SEVERANCE-0001. Pure-logic + static source scan; no DB/Redis.
"""
import dataclasses
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
import utils.structure_ingest_boundary as sib  # noqa: E402

MAIN = os.path.join(ROOT, "main.py")


# ---------- boundary behaviour ----------
def test_disabled_returns_noop_and_publish_ok():
    pub = sib.build_publisher(enabled=False)
    assert isinstance(pub, sib.NoopStructureIngestPublisher)
    res = pub.publish({"instrument": "XAU_USD"})
    assert res.ok is True and res.error_tag is None and res.error_detail is None


def test_enabled_fails_loud_severed():
    try:
        sib.build_publisher(enabled=True)
        assert False, "expected GOV-SE-SEVERED-001"
    except RuntimeError as e:
        assert "GOV-SE-SEVERED-001" in str(e)
        assert "tick contract" in str(e)  # directs to the contract, not a tradingProteus import


def test_publish_result_contract_shape():
    fields = {f.name for f in dataclasses.fields(sib.PublishResult)}
    assert fields == {"ok", "error_tag", "error_detail"}  # no strategy/ownership fields introduced


def _import_lines(src):
    return [ln.strip() for ln in src.splitlines() if ln.strip().startswith(("import ", "from "))]


def test_boundary_module_no_proteus_or_structure_engine_in_code():
    src = open(sib.__file__, encoding="utf-8").read()
    # import statements never reference the severed package (docstring prose may explain it)
    for ln in _import_lines(src):
        assert "tradingProteus" not in ln and "structure_engine" not in ln
    # no executable sys.path insertion (a docstring mention is prose, not code)
    for ln in src.splitlines():
        if "sys.path.insert" in ln and not ln.lstrip().startswith(("#", "the ", "`", '"', "'")):
            assert "tradingProteus" not in ln


# ---------- static scan: main.py is severed ----------
def test_main_py_no_tradingproteus_path_or_structure_engine_import():
    src = open(MAIN, encoding="utf-8").read()
    assert "/srv-dev/tradingProteus" not in src and "/srv/tradingProteus" not in src
    # no import statement references the severed package
    for ln in _import_lines(src):
        assert "structure_engine" not in ln, f"severed import survives: {ln}"
        assert "tradingProteus" not in ln
    # no sys.path insert pointing at the legacy path
    for ln in src.splitlines():
        if "sys.path.insert" in ln:
            assert "Proteus" not in ln


def test_main_py_uses_hermes_owned_boundary():
    src = open(MAIN, encoding="utf-8").read()
    assert "from utils.structure_ingest_boundary import build_publisher as _se_build_publisher" in src


def test_main_py_call_sites_preserved():
    src = open(MAIN, encoding="utf-8").read()
    # the publish hook + init contract are unchanged (no behaviour rewrite, just the boundary source)
    assert "state.structure_engine_publisher.publish(tick)" in src
    assert "state.structure_engine_publisher = _se_build_publisher()" in src


# ---------- standalone import proof ----------
def test_boundary_imports_without_tradingproteus_on_path():
    # ensure no tradingProteus path is present and the module still imports + builds (disabled)
    assert not any("tradingProteus" in p for p in sys.path)
    pub = sib.build_publisher(enabled=False)
    assert pub.publish(None).ok is True


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    p = 0
    for fn in fns:
        try:
            fn(); p += 1; print("PASS", fn.__name__)
        except Exception:
            print("FAIL", fn.__name__); traceback.print_exc()
    print(f"{p}/{len(fns)} passed"); raise SystemExit(0 if p == len(fns) else 1)
