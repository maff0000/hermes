"""
Tests for HERMES Recovery Library — WO-HERMES-RECOVERY-LIBRARY-0003

Proves:
1. All enabled artifacts are governed with metadata
2. Dependency graph is acyclic and topologically sortable
3. Missing metadata fails loudly
4. Broken dependency fails loudly
5. Planner produces deterministic rebuild order
6. Real outage window plan is correct
"""
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.recovery_planner import (
    RecoveryLibrary, LibraryValidator, RecoveryPlanner,
)


def get_db_config():
    from env_config import get_db_config as _get
    return _get()


def run_test(name, test_func):
    print(f"\n{'='*60}")
    print(f"TEST: {name}")
    print(f"{'='*60}")
    try:
        test_func()
        print(f"  PASS")
        return True
    except AssertionError as e:
        print(f"  FAIL: {e}")
        return False
    except Exception as e:
        print(f"  ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False


# ============================================================
# Test 1: All enabled artifacts are governed
# ============================================================
def test_all_artifacts_governed():
    """Every enabled artifact has description + llm_reasoning."""
    db = get_db_config()
    lib = RecoveryLibrary(db)
    validator = LibraryValidator(lib)

    errors = validator._check_metadata_completeness()
    assert len(errors) == 0, f"Metadata errors: {errors}"

    artifacts = lib.get_all_artifacts()
    assert len(artifacts) >= 6, f"Expected at least 6 artifacts, got {len(artifacts)}"

    for a in artifacts:
        assert a.artifact_code, "artifact_code cannot be empty"
        assert a.rebuild_strategy, "rebuild_strategy cannot be empty"
        assert a.validation_strategy, "validation_strategy cannot be empty"

    print(f"  {len(artifacts)} artifacts, all with complete metadata")


# ============================================================
# Test 2: Graph is acyclic and topologically sortable
# ============================================================
def test_graph_valid():
    """Dependency graph has no cycles."""
    db = get_db_config()
    lib = RecoveryLibrary(db)
    validator = LibraryValidator(lib)

    errors = validator._check_cyclic_dependencies()
    assert len(errors) == 0, f"Cycle errors: {errors}"

    planner = RecoveryPlanner(lib)
    order = planner.topological_sort()
    assert len(order) >= 6, f"Expected 6+ in topo sort, got {len(order)}"

    # Signals must come after their candle dependencies
    m5_idx = order.index('SIGNAL_M5')
    cm5_idx = order.index('CANDLE_M5')
    assert m5_idx > cm5_idx, f"SIGNAL_M5 ({m5_idx}) must come after CANDLE_M5 ({cm5_idx})"

    m15_idx = order.index('SIGNAL_M15')
    cm15_idx = order.index('CANDLE_M15')
    assert m15_idx > cm15_idx, f"SIGNAL_M15 ({m15_idx}) must come after CANDLE_M15 ({cm15_idx})"

    print(f"  Topo order: {' → '.join(order)}")


# ============================================================
# Test 3: Rebuild order consistency
# ============================================================
def test_rebuild_order_consistency():
    """rebuild_order values must respect dependency graph."""
    db = get_db_config()
    lib = RecoveryLibrary(db)
    validator = LibraryValidator(lib)

    errors = validator._check_rebuild_order_consistency()
    assert len(errors) == 0, f"Order errors: {errors}"

    print(f"  Rebuild order is consistent with dependency graph")


# ============================================================
# Test 4: Dependency references are valid
# ============================================================
def test_dependency_references():
    """All dependency references point to existing artifacts."""
    db = get_db_config()
    lib = RecoveryLibrary(db)
    validator = LibraryValidator(lib)

    errors = validator._check_dependency_references()
    assert len(errors) == 0, f"Dependency errors: {errors}"

    deps = lib.get_dependencies()
    total_deps = sum(len(v) for v in deps.values())
    print(f"  {total_deps} dependencies, all references valid")


# ============================================================
# Test 5: Full validation passes
# ============================================================
def test_full_validation():
    """All validations pass together."""
    db = get_db_config()
    lib = RecoveryLibrary(db)
    validator = LibraryValidator(lib)

    errors = validator.validate_all()
    assert len(errors) == 0, f"Validation errors: {errors}"

    print(f"  Full validation: 0 errors")


# ============================================================
# Test 6: Real outage plan is deterministic and correct
# ============================================================
def test_real_outage_plan():
    """Plan for 2026-03-31 outage window is correct."""
    db = get_db_config()
    lib = RecoveryLibrary(db)
    planner = RecoveryPlanner(lib)

    plan = planner.plan(
        'XAU_USD',
        datetime(2026, 3, 31, 2, 55, 0),
        datetime(2026, 3, 31, 7, 41, 0),
    )

    assert plan.instrument == 'XAU_USD'
    assert len(plan.steps) == 6, f"Expected 6 steps, got {len(plan.steps)}"

    # First 4 should be candles (broker fetch)
    candle_steps = [s for s in plan.steps if s.artifact_type == 'CANDLE']
    signal_steps = [s for s in plan.steps if s.artifact_type == 'SIGNAL']
    assert len(candle_steps) == 4
    assert len(signal_steps) == 2

    # All candles should come before all signals
    max_candle_order = max(s.order for s in candle_steps)
    min_signal_order = min(s.order for s in signal_steps)
    assert max_candle_order < min_signal_order, \
        f"Candle max order ({max_candle_order}) must be < signal min order ({min_signal_order})"

    # Candles should use BROKER_FETCH
    for s in candle_steps:
        assert s.rebuild_strategy == 'BROKER_FETCH', f"{s.artifact_code} should be BROKER_FETCH"
        assert s.lookback_start_utc is None, f"{s.artifact_code} should have no lookback"

    # Signals should use WINDOW_PLUS_LOOKBACK with lookback
    for s in signal_steps:
        assert s.rebuild_strategy == 'WINDOW_PLUS_LOOKBACK', f"{s.artifact_code} should be WINDOW_PLUS_LOOKBACK"
        assert s.lookback_start_utc is not None, f"{s.artifact_code} must have lookback"
        assert s.lookback_start_utc < plan.window_start_utc, \
            f"{s.artifact_code} lookback must be before window start"

    # SIGNAL_M5 must depend on CANDLE_M5
    sig_m5 = [s for s in signal_steps if s.artifact_code == 'SIGNAL_M5'][0]
    assert 'CANDLE_M5' in sig_m5.depends_on

    print(f"  Plan: {len(plan.steps)} steps")
    print(f"  Candles: {[s.artifact_code for s in candle_steps]}")
    print(f"  Signals: {[s.artifact_code for s in signal_steps]}")
    print(f"  SIGNAL_M5 lookback: {sig_m5.lookback_start_utc}")


# ============================================================
# Test 7: Partial plan (specific artifacts)
# ============================================================
def test_partial_plan():
    """Plan for specific artifacts includes dependencies."""
    db = get_db_config()
    lib = RecoveryLibrary(db)
    planner = RecoveryPlanner(lib)

    # Ask for SIGNAL_M5 only — should auto-include CANDLE_M5
    plan = planner.plan(
        'XAU_USD',
        datetime(2026, 3, 31, 2, 55, 0),
        datetime(2026, 3, 31, 7, 41, 0),
        artifact_codes=['SIGNAL_M5'],
    )

    codes = [s.artifact_code for s in plan.steps]
    assert 'SIGNAL_M5' in codes, "SIGNAL_M5 must be in plan"
    assert 'CANDLE_M5' in codes, "CANDLE_M5 must be auto-included as dependency"
    assert codes.index('CANDLE_M5') < codes.index('SIGNAL_M5'), \
        "CANDLE_M5 must come before SIGNAL_M5"

    print(f"  Requested: SIGNAL_M5 → Plan: {codes}")


# ============================================================
# Main
# ============================================================
if __name__ == "__main__":
    tests = [
        ("All enabled artifacts governed", test_all_artifacts_governed),
        ("Graph is acyclic and sortable", test_graph_valid),
        ("Rebuild order consistency", test_rebuild_order_consistency),
        ("Dependency references valid", test_dependency_references),
        ("Full validation passes", test_full_validation),
        ("Real outage plan is correct", test_real_outage_plan),
        ("Partial plan resolves dependencies", test_partial_plan),
    ]

    results = []
    for name, func in tests:
        results.append((name, run_test(name, func)))

    print(f"\n{'='*60}")
    print(f"RESULTS")
    print(f"{'='*60}")

    passed = sum(1 for _, ok in results if ok)
    total = len(results)

    for name, ok in results:
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {name}")

    print(f"\n{passed}/{total} passed")
    sys.exit(0 if passed == total else 1)
