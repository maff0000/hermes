#!/usr/bin/env python3
"""Bounded operator-run HERMES SHADOW activation harness.
WO-HELM-HERMES-REDIS-TICK-PUBLISHER-SHADOW-ACTIVATE-0001.

Not a background loop, not a recurring job, not a service unit. A single bounded run that:
  1. builds a synthetic SHADOW-activation tick (or aggregate),
  2. JSON-serialises the envelope (real client never receives a dict),
  3. SET EX 10 to an authorised hermes:shadow:* key on an explicitly-configured Redis target,
  4. reads the key back, deserialises, validates against tick_contract_v1,
  5. checks TTL is present and within range,
  6. asserts no canonical hermes:ticks:* key and no falcon/structure key was created,
  7. cleans up the shadow keys it wrote,
  8. prints a JSON evidence object (no secrets).

Redis target is supplied ONLY via explicit CLI flags — there is NO default host/port/db and NO
hidden env fallback. localhost/loopback is rejected as a production target.

Usage:
  python3 scripts/shadow_activate_publish.py --host <h> --port <p> --db <n> \
      --shadow-authorised --instrument XAU_USD [--treat-as-production] [--test-only]
"""
from __future__ import annotations
import argparse
import json
import sys
from datetime import datetime, timezone

sys.path.insert(0, ".")
from utils import tick_contract_v1 as tc                    # noqa: E402
from utils import tick_shadow_publisher_v1 as sh            # noqa: E402
from utils import tick_shadow_activation_v1 as act          # noqa: E402

SHADOW_ACTIVATION_SOURCE = "SHADOW_ACTIVATION_SYNTHETIC"     # marks input as test/shadow-activation


def _synthetic_tick(instrument):
    now = datetime.now(timezone.utc)
    return {"instrument": instrument, "bid": 4207.015, "ask": 4207.265,
            "source": SHADOW_ACTIVATION_SOURCE, "source_received_at_utc": now}, now


def run(args, redis_client):
    cfg = sh.ShadowPublisherConfig(
        publisher_enabled=True, write_mode=sh.WRITE_MODE_SHADOW, namespace="hermes",
        shadow_prefix="hermes:shadow:", contract_version="v1", redis_ex_seconds=10,
        payload_ttl_seconds=5, redis_host=args.host, redis_port=args.port, redis_db=args.db,
        shadow_authorised=args.shadow_authorised, treat_as_production=args.treat_as_production,
        test_only=args.test_only)
    writer = act.SerializingShadowWriter(config=cfg, redis_client=redis_client)

    # snapshot canonical/forbidden namespaces BEFORE (must stay empty of new keys)
    live_before = sorted(redis_client.scan_iter("hermes:ticks:*"))
    foreign_before = sorted(redis_client.scan_iter("falcon:*")) + sorted(redis_client.scan_iter("helios:*"))

    keys_written, results = [], []
    tick, gen = _synthetic_tick(args.instrument)
    res = writer.publish_tick(tick, generated_at_utc=gen, instrument_registry={args.instrument})
    keys_written.append(res["key"]); results.append(res)
    agg = writer.publish_aggregate([args.instrument], generated_at_utc=gen)
    keys_written.append(agg["key"]); results.append(agg)

    # readback + deserialise + validate + TTL
    readbacks = []
    for k in keys_written:
        raw = redis_client.get(k)
        value_type = type(raw).__name__
        env = act.deserialize_envelope(raw)                 # parses + validates (fail-loud)
        ttl = redis_client.ttl(k)
        readbacks.append({"key": k, "redis_value_type": value_type, "value_is_dict": isinstance(raw, dict),
                          "deserialised_validates": True, "ttl_seconds": ttl,
                          "ttl_in_range": isinstance(ttl, int) and 0 < ttl <= 10,
                          "freshness_state": env["freshness_state"]})

    # forbidden-namespace assertions
    live_after = sorted(redis_client.scan_iter("hermes:ticks:*"))
    foreign_after = sorted(redis_client.scan_iter("falcon:*")) + sorted(redis_client.scan_iter("helios:*"))
    new_live = [k.decode() if isinstance(k, bytes) else k for k in live_after if k not in live_before]
    new_foreign = [k.decode() if isinstance(k, bytes) else k for k in foreign_after if k not in foreign_before]
    if new_live:
        raise SystemExit(f"GOV-ACT-FAIL: canonical live key(s) created: {new_live}")
    if new_foreign:
        raise SystemExit(f"GOV-ACT-FAIL: falcon/structure key(s) created: {new_foreign}")

    # cleanup the shadow keys we wrote
    deleted = redis_client.delete(*keys_written)

    return {"wo": "WO-HELM-HERMES-REDIS-TICK-PUBLISHER-SHADOW-ACTIVATE-0001",
            "target": {"host": args.host, "port": args.port, "db": args.db,
                       "shadow_only": True, "production": bool(args.treat_as_production)},
            "keys_written": keys_written, "ex_seconds": 10, "payload_ttl_seconds": 5,
            "results": results, "readbacks": readbacks,
            "new_canonical_live_keys": new_live, "new_falcon_or_structure_keys": new_foreign,
            "cleanup_deleted": deleted, "all_values_json_str": all(r["value_type"] in ("str", "bytes") for r in results)}


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--host", required=True)
    p.add_argument("--port", required=True, type=int)
    p.add_argument("--db", required=True, type=int)
    p.add_argument("--instrument", default="XAU_USD")
    p.add_argument("--shadow-authorised", dest="shadow_authorised", action="store_true")
    p.add_argument("--treat-as-production", dest="treat_as_production", action="store_true")
    p.add_argument("--test-only", dest="test_only", action="store_true")
    args = p.parse_args(argv)
    import redis  # real client, lazy
    client = redis.Redis(host=args.host, port=args.port, db=args.db, socket_timeout=5)
    out = run(args, client)
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
