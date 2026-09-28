"""HMT-2 (real-money checkpoint) — static AST proof that the two new real-acquisition methods
on `DatabentoHistoricalProvider` are genuinely hardcoded to their one authorised request shape
each, not merely named as if they were.

This complements (does not replace) `tests/hmt2/test_no_network_guard.py`'s own extended
`test_no_bulk_download_or_live_streaming_call_sites` guard (which proves WHERE `.get_range` may
be called at all) with a stricter, call-site-level check: WHAT is actually passed to it. Every
assertion here parses the real module source with `ast` and inspects the literal AST node at
each `.get_range(...)` call's keyword arguments — never trusts a docstring, a comment, or a
runtime side-effect. A caller cannot override `schema`/`symbols`/`stype_in` on either method
(neither method even accepts those as parameters — see the behavioural tests in
`test_databento_historical_provider.py` for the runtime half of this proof), and this file
proves the hardcoding is real at the source level, not just an accident of the current test
inputs.
"""
from __future__ import annotations

import ast
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent.parent.parent / "market_truth" / "acquisition"
ADAPTER_PATH = PACKAGE_DIR / "providers" / "databento_historical.py"

# HMT-2 real-money checkpoint, MBP-1 pilot: "mbp-1" is REMOVED from the forbidden set — it is
# now exactly what `acquire_mbp1_pilot_session_data` (and ONLY that function) is authorised to
# request. TBBO/trades/MBO remain forbidden everywhere, no exceptions.
_FORBIDDEN_BULK_SCHEMAS = frozenset({"tbbo", "trades", "mbo"})


def _parse_adapter() -> ast.Module:
    return ast.parse(ADAPTER_PATH.read_text(encoding="utf-8"), filename=str(ADAPTER_PATH))


def _find_function(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"function {name!r} not found in {ADAPTER_PATH}")


def _find_get_range_calls(function_node: ast.FunctionDef) -> list[ast.Call]:
    calls = []
    for node in ast.walk(function_node):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get_range"
        ):
            calls.append(node)
    return calls


def _kwarg_value_node(call: ast.Call, name: str) -> ast.AST:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    raise AssertionError(f"call {ast.dump(call)} has no keyword argument {name!r}")


def _assert_literal_string(node: ast.AST, expected: str, *, context: str) -> None:
    assert isinstance(node, ast.Constant) and isinstance(node.value, str), (
        f"{context}: expected a literal string constant, got {ast.dump(node)} — a variable or "
        f"any non-literal expression here would mean this parameter could be overridden by a "
        f"caller, which is never permitted for this hardcoded field"
    )
    assert node.value == expected, f"{context}: expected literal {expected!r}, got {node.value!r}"


def _assert_literal_single_element_list(node: ast.AST, expected: str, *, context: str) -> None:
    assert isinstance(node, ast.List), f"{context}: expected a literal list, got {ast.dump(node)}"
    assert len(node.elts) == 1, f"{context}: expected exactly one element, got {len(node.elts)}"
    _assert_literal_string(node.elts[0], expected, context=context)


# ------------------------------------------------------------------------------------------
# (a) acquire_reference_series_ohlcv1h — hardcodes schema="ohlcv-1h", symbols=["GC.v.0"],
#     stype_in="continuous", dataset="GLBX.MDP3" — all as literal AST constants, not variables.
# ------------------------------------------------------------------------------------------

def test_acquire_reference_series_ohlcv1h_call_site_is_literally_hardcoded():
    tree = _parse_adapter()
    function_node = _find_function(tree, "acquire_reference_series_ohlcv1h")
    calls = _find_get_range_calls(function_node)
    assert len(calls) == 1, (
        f"acquire_reference_series_ohlcv1h must contain exactly one .get_range(...) call site, "
        f"found {len(calls)}"
    )
    call = calls[0]
    _assert_literal_string(
        _kwarg_value_node(call, "dataset"), "GLBX.MDP3",
        context="acquire_reference_series_ohlcv1h .get_range dataset=",
    )
    _assert_literal_string(
        _kwarg_value_node(call, "schema"), "ohlcv-1h",
        context="acquire_reference_series_ohlcv1h .get_range schema=",
    )
    _assert_literal_string(
        _kwarg_value_node(call, "stype_in"), "continuous",
        context="acquire_reference_series_ohlcv1h .get_range stype_in=",
    )
    _assert_literal_single_element_list(
        _kwarg_value_node(call, "symbols"), "GC.v.0",
        context="acquire_reference_series_ohlcv1h .get_range symbols=",
    )


# ------------------------------------------------------------------------------------------
# (b) acquire_gc_definitions — hardcodes schema="definition", symbols=["GC.FUT"],
#     stype_in="parent", dataset="GLBX.MDP3" — all as literal AST constants, not variables.
# ------------------------------------------------------------------------------------------

def test_acquire_gc_definitions_call_site_is_literally_hardcoded():
    tree = _parse_adapter()
    function_node = _find_function(tree, "acquire_gc_definitions")
    calls = _find_get_range_calls(function_node)
    assert len(calls) == 1, (
        f"acquire_gc_definitions must contain exactly one .get_range(...) call site, "
        f"found {len(calls)}"
    )
    call = calls[0]
    _assert_literal_string(
        _kwarg_value_node(call, "dataset"), "GLBX.MDP3",
        context="acquire_gc_definitions .get_range dataset=",
    )
    _assert_literal_string(
        _kwarg_value_node(call, "schema"), "definition",
        context="acquire_gc_definitions .get_range schema=",
    )
    _assert_literal_string(
        _kwarg_value_node(call, "stype_in"), "parent",
        context="acquire_gc_definitions .get_range stype_in=",
    )
    _assert_literal_single_element_list(
        _kwarg_value_node(call, "symbols"), "GC.FUT",
        context="acquire_gc_definitions .get_range symbols=",
    )


# ------------------------------------------------------------------------------------------
# (c) No function anywhere in the package calls .get_range with an MBP-1/TBBO/trades/MBO-
#     shaped schema. Structurally redundant with test_no_network_guard's location guard (only
#     these two functions may call .get_range at all, and (a)/(b) above pin their schemas), but
#     checked directly and independently here as defence-in-depth, across the WHOLE package.
# ------------------------------------------------------------------------------------------

def test_no_get_range_call_anywhere_in_the_package_uses_a_forbidden_bulk_schema():
    for path in sorted(PACKAGE_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get_range"
            ):
                for kw in node.keywords:
                    if kw.arg == "schema" and isinstance(kw.value, ast.Constant):
                        assert kw.value.value not in _FORBIDDEN_BULK_SCHEMAS, (
                            f"{path}:{node.lineno}: forbidden MBP-1/TBBO/trades/MBO-shaped "
                            f"schema {kw.value.value!r} at a .get_range(...) call site"
                        )


def test_exactly_three_get_range_call_sites_exist_in_the_whole_package():
    """Pinning the total count is itself a guard: if a fourth call site is ever added anywhere,
    this test fails immediately even before inspecting what it hardcodes. Bumped from 2 to 3 by
    the HMT-2 MBP-1 pilot's `acquire_mbp1_pilot_session_data` — see that test below for its own
    call-site-level hardcoding proof."""
    total = 0
    for path in sorted(PACKAGE_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get_range"
            ):
                total += 1
    assert total == 3, f"expected exactly 3 .get_range(...) call sites in the whole package, found {total}"


# ------------------------------------------------------------------------------------------
# (e) acquire_mbp1_pilot_session_data — hardcodes dataset="GLBX.MDP3", schema="mbp-1",
#     stype_in="raw_symbol" as literal AST constants, exactly like (a)/(b) above. Unlike those
#     two, `symbols`/`start`/`end`/`path` are genuinely data-driven parameters here (the
#     pilot's contracts/sessions are not a single fixed request) — this test proves they are
#     passed through as plain `Name` references to the function's own parameters, never
#     re-literalised, and never silently replaced by some other hardcoded value either.
# ------------------------------------------------------------------------------------------

def test_acquire_mbp1_pilot_session_data_call_site_hardcodes_dataset_schema_stype_in_only():
    tree = _parse_adapter()
    function_node = _find_function(tree, "acquire_mbp1_pilot_session_data")
    calls = _find_get_range_calls(function_node)
    assert len(calls) == 1, (
        f"acquire_mbp1_pilot_session_data must contain exactly one .get_range(...) call site, "
        f"found {len(calls)}"
    )
    call = calls[0]
    _assert_literal_string(
        _kwarg_value_node(call, "dataset"), "GLBX.MDP3",
        context="acquire_mbp1_pilot_session_data .get_range dataset=",
    )
    _assert_literal_string(
        _kwarg_value_node(call, "schema"), "mbp-1",
        context="acquire_mbp1_pilot_session_data .get_range schema=",
    )
    _assert_literal_string(
        _kwarg_value_node(call, "stype_in"), "raw_symbol",
        context="acquire_mbp1_pilot_session_data .get_range stype_in=",
    )
    # symbols/start/end/path are genuine parameters — must be plain Name nodes (a reference to
    # this function's own parameter, or a locally-derived variable holding it unmodified —
    # `symbols` is passed through `symbol_list = list(symbols)` purely to defend against a
    # one-shot iterator being exhausted by validation before the real call; `start`/`end`/`path`
    # are passed through completely unchanged as the function's own parameters), never a
    # literal constant (which would falsely claim a single fixed request shape) and never
    # anything more exotic (which could hide a caller-uncontrolled substitution).
    expected_name_by_param = {
        "symbols": "symbol_list",
        "start": "start",
        "end": "end",
        "path": "path",
    }
    for param_name, expected_var_name in expected_name_by_param.items():
        node = _kwarg_value_node(call, param_name)
        assert isinstance(node, ast.Name), (
            f"acquire_mbp1_pilot_session_data .get_range {param_name}=: expected a plain Name "
            f"reference, got {ast.dump(node)}"
        )
        assert node.id == expected_var_name, (
            f"acquire_mbp1_pilot_session_data .get_range {param_name}=: expected the Name to be "
            f"{expected_var_name!r}, got {node.id!r}"
        )


def test_no_get_range_call_anywhere_in_the_package_uses_a_forbidden_bulk_schema_including_mbp1_call_site():
    """Defence-in-depth, mirroring test_no_get_range_call_anywhere_in_the_package_uses_a_forbidden_bulk_schema
    below but confirming explicitly that adding the MBP-1 call site did not also smuggle in a
    literal `tbbo`/`trades`/`mbo` schema anywhere (schema="mbp-1" itself is, correctly, NOT in
    `_FORBIDDEN_BULK_SCHEMAS` — MBP-1 is exactly what this checkpoint's pilot is authorised to
    acquire; TBBO/trades/MBO remain forbidden everywhere, including at this new call site)."""
    tree = _parse_adapter()
    function_node = _find_function(tree, "acquire_mbp1_pilot_session_data")
    for call in _find_get_range_calls(function_node):
        for kw in call.keywords:
            if kw.arg == "schema" and isinstance(kw.value, ast.Constant):
                assert kw.value.value == "mbp-1"
                assert kw.value.value not in _FORBIDDEN_BULK_SCHEMAS


# ------------------------------------------------------------------------------------------
# (d) .Live never appears anywhere in the package (source-level, not just at .get_range sites).
# ------------------------------------------------------------------------------------------

def test_dot_live_never_appears_anywhere_in_the_package():
    for path in sorted(PACKAGE_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                assert node.attr != "Live", f"{path}:{node.lineno}: forbidden `.Live` attribute access"
            if isinstance(node, ast.Name):
                assert node.id != "Live", f"{path}:{node.lineno}: forbidden bare `Live` name reference"


# ------------------------------------------------------------------------------------------
# download_historical_range() remains untouched: still exactly one statement, an unconditional
# raise, with zero .get_range call sites of its own (already covered by the count==2 test
# above, since neither of the 2 found sites can be inside it, but pinned explicitly too).
# ------------------------------------------------------------------------------------------

def test_download_historical_range_still_only_ever_raises():
    tree = _parse_adapter()
    function_node = _find_function(tree, "download_historical_range")
    assert _find_get_range_calls(function_node) == []
    non_docstring_statements = [
        s for s in function_node.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))
    ]
    assert len(non_docstring_statements) == 1
    assert isinstance(non_docstring_statements[0], ast.Raise)
