"""One-shot / periodic maintenance: prune stale members from HERMES candle-history ZSET indexes.

WO-HELM-HERMES-DEV-REDIS-CAPACITY-RETENTION-AND-PROD-INCIDENT-RECOVERY-DESIGN-0001.

Context: `candle_history_forward_writer_v1.CandleHistoryForwardWriter` now prunes each index
incrementally on every write, so going forward the index and the live (non-expired) key set stay
bounded together. This tool exists to clear the BACKLOG that accumulated before that fix existed
(entries whose underlying candle key has already expired, but which were never removed from the
index), and as a safety-net that can be re-run at any time — it is idempotent and touches nothing
else.

Scope, by construction (never broadened):
  * only keys matching `hermes:candles:*:history:v1:index` are ever touched (SCAN MATCH, never KEYS);
  * the only write operation used is ZREMRANGEBYSCORE on those keys — no DEL, no other pattern;
  * the cutoff is the same `candle_history_v1.history_retention_cutoff_epoch()` the live write path
    uses, so a manual run can never diverge from the governed retention window;
  * dry-run by default; `--apply` is required to actually prune.

Usage:
    python3 tools/hermes_history_index_prune_v1.py            # report only, no writes
    python3 tools/hermes_history_index_prune_v1.py --apply    # prune for real
"""
from __future__ import annotations
import argparse
import sys
from datetime import datetime, timezone

from utils import candle_history_v1 as chv
from utils.hermes_redis_auth_v1 import redis_auth_kwargs

INDEX_PATTERN = "hermes:candles:*:history:v1:index"


def _redis_client():
    import os
    import redis
    from env_config import get_env, get_env_int
    return redis.Redis(
        host=get_env("HERMES_CANDLE_CANONICAL_REDIS_HOST", required=True),
        port=get_env_int("HERMES_CANDLE_CANONICAL_REDIS_PORT", required=True),
        db=get_env_int("HERMES_CANDLE_CANONICAL_REDIS_DB", required=True),
        decode_responses=True,
        **redis_auth_kwargs(),
    )


def prune_all_indexes(client, *, apply: bool, now_utc=None):
    """Scan every governed history index and report/apply pruning to the shared retention cutoff.
    Returns a list of per-index result dicts. Never touches any key outside INDEX_PATTERN."""
    cutoff = chv.history_retention_cutoff_epoch(now_utc or datetime.now(timezone.utc))
    results = []
    for key in client.scan_iter(match=INDEX_PATTERN, count=1000):
        # belt-and-braces: refuse anything that isn't a governed history index, even though the SCAN
        # pattern already restricts this — never trust a pattern match alone for a write operation.
        chv.assert_history_target(key)
        if not key.endswith(":index"):
            continue
        before = client.zcard(key)
        stale = client.zcount(key, "-inf", cutoff)
        removed = 0
        if apply and stale:
            removed = client.zremrangebyscore(key, "-inf", cutoff)
        results.append({
            "index_key": key, "cutoff_epoch": cutoff, "cardinality_before": before,
            "stale_members": stale, "removed": removed, "applied": apply,
        })
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="actually prune (default: dry-run report only)")
    args = parser.parse_args(argv)

    client = _redis_client()
    results = prune_all_indexes(client, apply=args.apply)

    total_before = sum(r["cardinality_before"] for r in results)
    total_stale = sum(r["stale_members"] for r in results)
    total_removed = sum(r["removed"] for r in results)

    for r in sorted(results, key=lambda r: -r["stale_members"]):
        if r["stale_members"]:
            print(f"{r['index_key']}: cardinality={r['cardinality_before']} "
                  f"stale={r['stale_members']} removed={r['removed']}")

    mode = "APPLIED" if args.apply else "DRY-RUN"
    print(f"\n[{mode}] indexes_scanned={len(results)} total_cardinality={total_before} "
          f"total_stale={total_stale} total_removed={total_removed}")
    if not args.apply and total_stale:
        print("Re-run with --apply to prune the reported stale members.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
