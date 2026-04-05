"""
HERMES Recovery Planner — DB-Driven Rebuild Planning
WO-HERMES-RECOVERY-LIBRARY-0003

Reads hermes_recovery_library and hermes_recovery_dependencies to produce
deterministic, ordered rebuild plans. Does NOT execute recovery — that is WO-4.

The planner is the bridge between gap truth (WO-2) and deterministic repair (WO-4).
"""
import sys
import os
import json
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field

import pymysql
import pymysql.cursors

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================
# Data structures
# ============================================================

@dataclass
class ArtifactDef:
    """A recoverable artifact definition from the registry."""
    artifact_code: str
    artifact_type: str
    producer_name: str
    instrument_scope: str
    timeframe: str
    target_table: str
    rebuild_strategy: str
    required_lookback_bars: int
    validation_strategy: str
    is_enabled: bool
    rebuild_order: int
    version: str


@dataclass
class RebuildStep:
    """A single step in a rebuild plan."""
    order: int
    artifact_code: str
    artifact_type: str
    instrument: str
    timeframe: str
    target_table: str
    rebuild_strategy: str
    window_start_utc: datetime
    window_end_utc: datetime
    lookback_start_utc: Optional[datetime]
    required_lookback_bars: int
    validation_strategy: str
    depends_on: List[str]

    def to_dict(self) -> dict:
        return {
            'order': self.order,
            'artifact_code': self.artifact_code,
            'artifact_type': self.artifact_type,
            'instrument': self.instrument,
            'timeframe': self.timeframe,
            'target_table': self.target_table,
            'rebuild_strategy': self.rebuild_strategy,
            'window_start_utc': self.window_start_utc.isoformat(),
            'window_end_utc': self.window_end_utc.isoformat(),
            'lookback_start_utc': self.lookback_start_utc.isoformat() if self.lookback_start_utc else None,
            'required_lookback_bars': self.required_lookback_bars,
            'validation_strategy': self.validation_strategy,
            'depends_on': self.depends_on,
        }


@dataclass
class RebuildPlan:
    """A complete rebuild plan for a given instrument/window."""
    instrument: str
    window_start_utc: datetime
    window_end_utc: datetime
    steps: List[RebuildStep]
    artifact_codes: List[str]

    def to_dict(self) -> dict:
        return {
            'instrument': self.instrument,
            'window_start_utc': self.window_start_utc.isoformat(),
            'window_end_utc': self.window_end_utc.isoformat(),
            'total_steps': len(self.steps),
            'artifact_codes': self.artifact_codes,
            'steps': [s.to_dict() for s in self.steps],
        }


# ============================================================
# Timeframe helpers
# ============================================================

TIMEFRAME_SECONDS = {
    'M1': 60, 'M5': 300, 'M15': 900, 'H1': 3600, 'D1': 86400,
}


# ============================================================
# Recovery Library Reader
# ============================================================

class RecoveryLibrary:
    """Reads and validates the recovery artifact registry."""

    def __init__(self, db_config: dict):
        self._db_config = db_config

    def _get_conn(self):
        return pymysql.connect(
            host=self._db_config['host'],
            port=self._db_config['port'],
            user=self._db_config['user'],
            password=self._db_config['password'],
            database=self._db_config['database'],
            autocommit=True,
            connect_timeout=5,
        )

    def get_all_artifacts(self, enabled_only: bool = True) -> List[ArtifactDef]:
        """Load all artifact definitions."""
        conn = self._get_conn()
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            sql = "SELECT * FROM hermes_recovery_library"
            if enabled_only:
                sql += " WHERE is_enabled = 1"
            sql += " ORDER BY rebuild_order"
            cur.execute(sql)
            rows = cur.fetchall()
        conn.close()

        return [ArtifactDef(
            artifact_code=r['artifact_code'],
            artifact_type=r['artifact_type'],
            producer_name=r['producer_name'],
            instrument_scope=r['instrument_scope'],
            timeframe=r['timeframe'],
            target_table=r['target_table'],
            rebuild_strategy=r['rebuild_strategy'],
            required_lookback_bars=r['required_lookback_bars'],
            validation_strategy=r['validation_strategy'],
            is_enabled=bool(r['is_enabled']),
            rebuild_order=r['rebuild_order'],
            version=r['version'],
        ) for r in rows]

    def get_dependencies(self) -> Dict[str, List[str]]:
        """Load dependency graph as {artifact: [depends_on, ...]}."""
        conn = self._get_conn()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT artifact_code, depends_on_artifact_code FROM hermes_recovery_dependencies"
            )
            rows = cur.fetchall()
        conn.close()

        deps = {}
        for art, dep in rows:
            deps.setdefault(art, []).append(dep)
        return deps

    def get_artifact(self, artifact_code: str) -> Optional[ArtifactDef]:
        """Get a single artifact definition."""
        conn = self._get_conn()
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute("SELECT * FROM hermes_recovery_library WHERE artifact_code = %s", (artifact_code,))
            r = cur.fetchone()
        conn.close()

        if not r:
            return None

        return ArtifactDef(
            artifact_code=r['artifact_code'],
            artifact_type=r['artifact_type'],
            producer_name=r['producer_name'],
            instrument_scope=r['instrument_scope'],
            timeframe=r['timeframe'],
            target_table=r['target_table'],
            rebuild_strategy=r['rebuild_strategy'],
            required_lookback_bars=r['required_lookback_bars'],
            validation_strategy=r['validation_strategy'],
            is_enabled=bool(r['is_enabled']),
            rebuild_order=r['rebuild_order'],
            version=r['version'],
        )


# ============================================================
# Validation
# ============================================================

class LibraryValidator:
    """Validates recovery library integrity."""

    def __init__(self, library: RecoveryLibrary):
        self._lib = library

    def validate_all(self) -> List[str]:
        """Run all validations. Returns list of errors (empty = valid)."""
        errors = []
        errors.extend(self._check_metadata_completeness())
        errors.extend(self._check_dependency_references())
        errors.extend(self._check_cyclic_dependencies())
        errors.extend(self._check_rebuild_order_consistency())
        return errors

    def _check_metadata_completeness(self) -> List[str]:
        """Verify all enabled artifacts have required metadata."""
        errors = []
        conn = self._lib._get_conn()
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(
                "SELECT artifact_code, description, llm_reasoning FROM hermes_recovery_library WHERE is_enabled = 1"
            )
            for row in cur.fetchall():
                code = row['artifact_code']
                if not row['description'] or row['description'].strip() == '':
                    errors.append(f"MISSING_DESCRIPTION: {code}")
                if not row['llm_reasoning'] or row['llm_reasoning'].strip() == '':
                    errors.append(f"MISSING_LLM_REASONING: {code}")
        conn.close()
        return errors

    def _check_dependency_references(self) -> List[str]:
        """Verify all dependency references point to existing artifacts."""
        errors = []
        artifacts = {a.artifact_code for a in self._lib.get_all_artifacts(enabled_only=False)}
        deps = self._lib.get_dependencies()

        for art, dep_list in deps.items():
            if art not in artifacts:
                errors.append(f"ORPHAN_DEPENDENCY: {art} is not in registry")
            for dep in dep_list:
                if dep not in artifacts:
                    errors.append(f"BROKEN_DEPENDENCY: {art} depends on {dep} which does not exist")
        return errors

    def _check_cyclic_dependencies(self) -> List[str]:
        """Detect cycles in dependency graph via topological sort."""
        errors = []
        deps = self._lib.get_dependencies()
        artifacts = {a.artifact_code for a in self._lib.get_all_artifacts(enabled_only=False)}

        # Build adjacency for all artifacts (including those with no deps)
        in_degree = {a: 0 for a in artifacts}
        adj = {a: [] for a in artifacts}

        for art, dep_list in deps.items():
            for dep in dep_list:
                if dep in adj:
                    adj[dep].append(art)
                    in_degree[art] = in_degree.get(art, 0) + 1

        # Kahn's algorithm
        queue = [a for a in artifacts if in_degree.get(a, 0) == 0]
        sorted_count = 0

        while queue:
            node = queue.pop(0)
            sorted_count += 1
            for neighbor in adj.get(node, []):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if sorted_count != len(artifacts):
            errors.append(f"CYCLIC_DEPENDENCY: Graph has cycle ({sorted_count} sorted of {len(artifacts)} artifacts)")

        return errors

    def _check_rebuild_order_consistency(self) -> List[str]:
        """Verify rebuild_order respects dependency graph."""
        errors = []
        artifacts = {a.artifact_code: a for a in self._lib.get_all_artifacts(enabled_only=True)}
        deps = self._lib.get_dependencies()

        for art, dep_list in deps.items():
            if art not in artifacts:
                continue
            for dep in dep_list:
                if dep not in artifacts:
                    continue
                if artifacts[art].rebuild_order <= artifacts[dep].rebuild_order:
                    errors.append(
                        f"ORDER_VIOLATION: {art} (order {artifacts[art].rebuild_order}) "
                        f"must come after {dep} (order {artifacts[dep].rebuild_order})"
                    )
        return errors


# ============================================================
# Planner
# ============================================================

class RecoveryPlanner:
    """Produces deterministic rebuild plans from library metadata."""

    def __init__(self, library: RecoveryLibrary):
        self._lib = library

    def plan(
        self,
        instrument: str,
        window_start_utc: datetime,
        window_end_utc: datetime,
        artifact_codes: List[str] = None,
    ) -> RebuildPlan:
        """
        Build a rebuild plan for the given instrument and window.

        Args:
            instrument: Instrument code
            window_start_utc: Start of gap window
            window_end_utc: End of gap window
            artifact_codes: Specific artifacts to rebuild, or None for ALL_ENABLED

        Returns:
            RebuildPlan with ordered steps
        """
        all_artifacts = {a.artifact_code: a for a in self._lib.get_all_artifacts(enabled_only=True)}
        deps = self._lib.get_dependencies()

        # Determine target artifacts
        if artifact_codes:
            targets = set(artifact_codes)
        else:
            targets = set(all_artifacts.keys())

        # Resolve dependencies — add upstream artifacts
        resolved = set()
        to_resolve = list(targets)
        while to_resolve:
            code = to_resolve.pop(0)
            if code in resolved:
                continue
            resolved.add(code)
            for dep in deps.get(code, []):
                if dep not in resolved:
                    to_resolve.append(dep)

        # Build steps in rebuild_order
        steps = []
        for code in sorted(resolved, key=lambda c: all_artifacts[c].rebuild_order if c in all_artifacts else 999):
            if code not in all_artifacts:
                raise ValueError(f"Artifact {code} is required but not in registry or disabled")

            art = all_artifacts[code]
            tf_seconds = TIMEFRAME_SECONDS.get(art.timeframe, 300)

            # Compute lookback window
            lookback_start = None
            if art.required_lookback_bars > 0:
                lookback_delta = timedelta(seconds=tf_seconds * art.required_lookback_bars)
                lookback_start = window_start_utc - lookback_delta

            step = RebuildStep(
                order=art.rebuild_order,
                artifact_code=code,
                artifact_type=art.artifact_type,
                instrument=instrument,
                timeframe=art.timeframe,
                target_table=art.target_table,
                rebuild_strategy=art.rebuild_strategy,
                window_start_utc=window_start_utc,
                window_end_utc=window_end_utc,
                lookback_start_utc=lookback_start,
                required_lookback_bars=art.required_lookback_bars,
                validation_strategy=art.validation_strategy,
                depends_on=deps.get(code, []),
            )
            steps.append(step)

        return RebuildPlan(
            instrument=instrument,
            window_start_utc=window_start_utc,
            window_end_utc=window_end_utc,
            steps=steps,
            artifact_codes=[s.artifact_code for s in steps],
        )

    def topological_sort(self) -> List[str]:
        """Return artifact codes in valid dependency order."""
        artifacts = {a.artifact_code for a in self._lib.get_all_artifacts(enabled_only=True)}
        deps = self._lib.get_dependencies()

        in_degree = {a: 0 for a in artifacts}
        adj = {a: [] for a in artifacts}

        for art, dep_list in deps.items():
            if art not in artifacts:
                continue
            for dep in dep_list:
                if dep in adj:
                    adj[dep].append(art)
                    in_degree[art] = in_degree.get(art, 0) + 1

        queue = sorted([a for a in artifacts if in_degree[a] == 0])
        result = []

        while queue:
            node = queue.pop(0)
            result.append(node)
            for neighbor in sorted(adj.get(node, [])):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        return result


# ============================================================
# CLI
# ============================================================

def main():
    import argparse

    parser = argparse.ArgumentParser(description="HERMES Recovery Planner — WO-HERMES-RECOVERY-LIBRARY-0003")
    subparsers = parser.add_subparsers(dest='command', required=True)

    # validate
    val_p = subparsers.add_parser('validate', help='Validate library integrity')

    # plan
    plan_p = subparsers.add_parser('plan', help='Generate rebuild plan')
    plan_p.add_argument('--instrument', '-i', required=True)
    plan_p.add_argument('--start', '-s', required=True, help='Window start UTC')
    plan_p.add_argument('--end', '-e', required=True, help='Window end UTC')
    plan_p.add_argument('--artifacts', '-a', default=None, help='Comma-separated artifact codes, or ALL')

    # list
    list_p = subparsers.add_parser('list', help='List registered artifacts')

    # topo-sort
    topo_p = subparsers.add_parser('topo-sort', help='Show topological dependency order')

    args = parser.parse_args()

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/..')
    from env_config import get_db_config
    db_config = get_db_config()

    lib = RecoveryLibrary(db_config)

    if args.command == 'validate':
        validator = LibraryValidator(lib)
        errors = validator.validate_all()
        if errors:
            print("VALIDATION FAILED:")
            for err in errors:
                print(f"  {err}")
            sys.exit(1)
        else:
            artifacts = lib.get_all_artifacts()
            print(f"VALIDATION PASSED: {len(artifacts)} enabled artifacts, graph is valid.")
            sys.exit(0)

    elif args.command == 'list':
        artifacts = lib.get_all_artifacts(enabled_only=False)
        deps = lib.get_dependencies()
        print(f"{'Code':<16} {'Type':<8} {'TF':<4} {'Strategy':<28} {'Order':>5} {'Lookback':>8} {'Enabled':<7} {'Deps'}")
        print("-" * 110)
        for a in artifacts:
            dep_str = ", ".join(deps.get(a.artifact_code, [])) or "(none)"
            print(f"{a.artifact_code:<16} {a.artifact_type:<8} {a.timeframe:<4} {a.rebuild_strategy:<28} {a.rebuild_order:>5} {a.required_lookback_bars:>8} {'YES' if a.is_enabled else 'NO':<7} {dep_str}")

    elif args.command == 'topo-sort':
        planner = RecoveryPlanner(lib)
        order = planner.topological_sort()
        print("Topological rebuild order:")
        for i, code in enumerate(order, 1):
            print(f"  {i}. {code}")

    elif args.command == 'plan':
        planner = RecoveryPlanner(lib)
        start = datetime.fromisoformat(args.start)
        end = datetime.fromisoformat(args.end)
        codes = args.artifacts.split(',') if args.artifacts and args.artifacts != 'ALL' else None

        plan = planner.plan(args.instrument, start, end, codes)
        print(f"REBUILD PLAN: {plan.instrument} [{start} → {end}]")
        print(f"Steps: {len(plan.steps)}")
        print()
        for step in plan.steps:
            lb = f" (lookback from {step.lookback_start_utc})" if step.lookback_start_utc else ""
            deps = f" [depends: {', '.join(step.depends_on)}]" if step.depends_on else ""
            print(f"  {step.order:>3}. [{step.artifact_type}] {step.artifact_code} → {step.target_table}")
            print(f"       Strategy: {step.rebuild_strategy}{lb}")
            print(f"       Validate: {step.validation_strategy}{deps}")
            print()


if __name__ == '__main__':
    main()
