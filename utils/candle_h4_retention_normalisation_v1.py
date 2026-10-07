"""HERMES governed H4 candle-history RETENTION NORMALISATION v1 — dark/dry-run by default, NO live write here.
WO-HERMES-INDICATOR-HISTORY-CONTRACT-0001 §6a / PID-HERMES-MVP-001 §14.

Bounded, narrowly-scoped companion to the §6/§12 H4 repair (`candle_h4_history_seed_backfill_v1.py`): HELM's
final verification found a MAJORITY (5 of 7 independently sampled) surviving forward-written H4
candle-history objects from 2026-09-02/2026-09-03 carrying only ~0.1-0.9 days of TTL remaining under an
apparent, now-superseded, ~35-day TTL regime, about to expire imminently — while the OTHER 2 of 7 already
carry ~99 days remaining, consistent with the CURRENT canonical H4 retention policy
(`candle_history_v1.H4_HISTORY_TTL_SECONDS` = 120 days). Repairing the 65 missing §6/§12 objects while
allowing this additional, currently-valid H4 history to disappear immediately afterward under a stale TTL
regime would be self-defeating for the `ema_200` warm-up depth the indicator-history contract (§5) requires.

This module does EXACTLY ONE THING: identify H4 candle-history objects whose *implied original TTL* (derived
from their OWN governed `history.backfill_inserted_at_utc` provenance field + their CURRENT remaining Redis
TTL) is inconsistent with the current canonical H4 retention policy, and — on an explicitly authorised LIVE
run only — re-arm ONLY their expiry via `EXPIRE` (never `SET`), so the stored payload bytes are left
BYTE-IDENTICAL. It is NOT a general-purpose TTL-rewriting utility: it never touches an object whose implied
original TTL is already consistent with the current policy, it never extends any object beyond what the
current policy would already allow it (measured from that object's own recorded insertion time, not from
"now"), and it operates ONLY over the already-governed, already-bounded H4 history index (at most
`H4_HISTORY_RETAIN_COUNT` = 250 members) — never an unbounded scan of the keyspace.

Identification formula (pure, deterministic, fixture-provable):
    elapsed_seconds               = now - history.backfill_inserted_at_utc
    expected_ttl_under_current_policy = H4_HISTORY_TTL_SECONDS - elapsed_seconds   (floored at 0)
    needs_normalisation            = expected_ttl_under_current_policy > 0
                                      AND current_actual_ttl < expected_ttl_under_current_policy - tolerance

An object whose age already exceeds the current 120-day cap (expected_ttl_under_current_policy <= 0) is left
alone: letting it expire under the current policy is correct behaviour, not a defect this module corrects
(the per-object TTL cap is a generous safety net around the real, count-based H4 retention bound; see
`candle_history_v1` module docstring).

Guarantees: DISABLED + DRY-RUN by default (mirrors `candle_h4_history_seed_backfill_v1`'s gate shape exactly).
NO Redis writes in dry-run. Live apply requires ALL THREE of enabled+authorised+dry_run=False AND is NOT
executed by this WO. Never touches payload bytes (only `EXPIRE`, never `SET`/`DEL`). Never extends an object
beyond the current governed cap. Bounded to the existing H4 history index. UTC only. No regime/risk/
strategy/signal/trade semantics. Reuses the EXACT existing H4 retention constants
(`candle_history_v1.H4_HISTORY_TTL_SECONDS`, `candle_history_v1.history_index_key`) — no new retention
policy, no new config surface.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone

from utils import candle_contract_v1 as cc
from utils import candle_history_v1 as chv

CANONICAL_INSTRUMENT = "XAU_USD"
TIMEFRAME = "H4"
HALT_CODE = 101

# The current canonical H4 per-object TTL cap, reused directly — NEVER a second retention constant.
CURRENT_H4_TTL_SECONDS = chv.H4_HISTORY_TTL_SECONDS   # 120 days

# A drift-tolerance margin (processing/clock-skew only) below which a small TTL discrepancy is NOT flagged —
# keeps this a narrow legacy-regime detector, not a hair-trigger on ordinary scheduling jitter.
DEFAULT_TOLERANCE_SECONDS = 3600   # 1 hour

# Provenance marker for this exact bounded operation — recorded in the RETURNED report only. Never written
# into the candle's own payload (that would mutate payload bytes, which this module must never do).
NORMALISATION_RUN_MARKER = "H4_RETENTION_NORMALISATION_V1:WO-HERMES-INDICATOR-HISTORY-CONTRACT-0001"

# Gates (all DARK by default), mirroring candle_h4_history_seed_backfill_v1's exact shape.
NORMALISATION_ENABLED_ENV = "HERMES_H4_RETENTION_NORMALISATION_ENABLED"
NORMALISATION_AUTHORISED_ENV = "HERMES_H4_RETENTION_NORMALISATION_AUTHORISED"
NORMALISATION_DRY_RUN_ENV = "HERMES_H4_RETENTION_NORMALISATION_DRY_RUN"   # default true (safe)

REASON_ALREADY_COMPLIANT = "ALREADY_COMPLIANT_WITH_CURRENT_POLICY"
REASON_BEYOND_CAP = "AGE_ALREADY_BEYOND_CURRENT_CAP_LEFT_ALONE"
REASON_UNREADABLE = "OBJECT_UNREADABLE_OR_MISSING_PROVENANCE"
REASON_FLAGGED = "LEGACY_TTL_REGIME_FLAGGED"


# --------------------------------------------------------------------------- config (dark by default)
class NormalisationConfig:
    def __init__(self, *, enabled, authorised, dry_run, tolerance_seconds=DEFAULT_TOLERANCE_SECONDS):
        self.enabled = bool(enabled)
        self.authorised = bool(authorised)
        self.dry_run = bool(dry_run)
        self.tolerance_seconds = int(tolerance_seconds)

    @property
    def live_apply_allowed(self):
        """Live EXPIRE-only apply is allowed ONLY with enabled AND authorised AND dry_run explicitly false."""
        return self.enabled and self.authorised and not self.dry_run

    def as_dict(self):
        return {"enabled": self.enabled, "authorised": self.authorised, "dry_run": self.dry_run,
                "tolerance_seconds": self.tolerance_seconds, "live_apply_allowed": self.live_apply_allowed}


def parse_normalisation_config_from_env():
    """DISABLED + DRY-RUN by default. Enabled-without-authorised -> SystemExit(101)."""
    from env_config import get_env_bool, get_env_int
    enabled = get_env_bool(NORMALISATION_ENABLED_ENV, False)
    if not enabled:
        return NormalisationConfig(enabled=False, authorised=False, dry_run=True)
    if not get_env_bool(NORMALISATION_AUTHORISED_ENV, False):
        raise SystemExit(HALT_CODE)
    dry_run = get_env_bool(NORMALISATION_DRY_RUN_ENV, True)     # default SAFE (dry-run) even when enabled+authorised
    tolerance = get_env_int("HERMES_H4_RETENTION_NORMALISATION_TOLERANCE_SECONDS", DEFAULT_TOLERANCE_SECONDS) \
        or DEFAULT_TOLERANCE_SECONDS
    return NormalisationConfig(enabled=True, authorised=True, dry_run=dry_run, tolerance_seconds=tolerance)


# --------------------------------------------------------------------------- identification (pure, bounded)
def _parse_utc(ts):
    return datetime.strptime(ts[:-1], cc._UTC_MS).replace(tzinfo=timezone.utc)


def _decode(raw):
    return raw.decode() if isinstance(raw, (bytes, bytearray)) else raw


def classify_h4_history_object(*, inserted_at_utc, current_actual_ttl_seconds, now,
                               tolerance_seconds=DEFAULT_TOLERANCE_SECONDS):
    """Pure classification (no I/O): given a single H4 history object's own recorded insertion time and its
    CURRENT actual remaining Redis TTL, decide whether it carries a legacy/shorter TTL regime inconsistent
    with the current canonical policy. Returns a dict with the full working (never a bare boolean) so the
    decision is auditable."""
    elapsed_seconds = (cc.normalise_utc(now) - cc.normalise_utc(inserted_at_utc)).total_seconds()
    expected_ttl = CURRENT_H4_TTL_SECONDS - elapsed_seconds
    out = {
        "elapsed_seconds": elapsed_seconds,
        "expected_ttl_under_current_policy_seconds": max(0.0, expected_ttl),
        "current_actual_ttl_seconds": current_actual_ttl_seconds,
    }
    if expected_ttl <= 0:
        out["needs_normalisation"] = False
        out["reason"] = REASON_BEYOND_CAP
        return out
    if current_actual_ttl_seconds is None or current_actual_ttl_seconds < 0:
        out["needs_normalisation"] = False
        out["reason"] = REASON_UNREADABLE
        return out
    if current_actual_ttl_seconds < (expected_ttl - tolerance_seconds):
        out["needs_normalisation"] = True
        out["reason"] = REASON_FLAGGED
        out["new_ttl_seconds"] = int(round(expected_ttl))   # NEVER beyond the current governed cap (§6a step 3)
        return out
    out["needs_normalisation"] = False
    out["reason"] = REASON_ALREADY_COMPLIANT
    return out


# --------------------------------------------------------------------------- bounded scan (read-only)
def identify_stale_h4_history_objects(redis_client, *, now=None, tolerance_seconds=DEFAULT_TOLERANCE_SECONDS):
    """Bounded, READ-ONLY identification pass over the existing, already-governed H4 history index (at most
    `H4_HISTORY_RETAIN_COUNT` members — never an unbounded keyspace scan). For every surviving member, reads
    its OWN `history.backfill_inserted_at_utc` provenance + its CURRENT actual Redis TTL and classifies it
    via `classify_h4_history_object`. Never writes, never deletes. A member whose object is missing (the
    §6/§12 defect) or whose TTL cannot be read is reported as UNREADABLE, never raised (this pass must
    survive exactly the gap this module's sibling WO-§6 mechanism repairs)."""
    now = now or datetime.now(timezone.utc)
    idx = chv.history_index_key(CANONICAL_INSTRUMENT, TIMEFRAME)
    members = [int(m) for m in redis_client.zrange(idx, 0, -1)]
    results = []
    for open_epoch in members:
        key = chv.history_key(CANONICAL_INSTRUMENT, TIMEFRAME, open_epoch)
        raw = redis_client.get(key)
        ttl = redis_client.ttl(key)
        if not raw:
            results.append({"key": key, "open_epoch": open_epoch, "needs_normalisation": False,
                            "reason": REASON_UNREADABLE})
            continue
        try:
            env = json.loads(_decode(raw))
            inserted_at = env["history"]["backfill_inserted_at_utc"]
        except (ValueError, KeyError, TypeError):
            results.append({"key": key, "open_epoch": open_epoch, "needs_normalisation": False,
                            "reason": REASON_UNREADABLE})
            continue
        cls = classify_h4_history_object(inserted_at_utc=_parse_utc(inserted_at),
                                         current_actual_ttl_seconds=ttl, now=now,
                                         tolerance_seconds=tolerance_seconds)
        cls["key"] = key
        cls["open_epoch"] = open_epoch
        results.append(cls)
    return results


# --------------------------------------------------------------------------- live apply (GATED; NOT run in this WO)
def apply_retention_normalisation(redis_client, *, config=None, now=None):
    """LIVE apply — REFUSES unless enabled+authorised+dry_run=false (fail-loud), exactly mirroring the §6
    seed/backfill gate shape. For every object flagged `needs_normalisation`, calls ONLY `EXPIRE(key,
    new_ttl_seconds)` — never `SET`, never `DEL` — so payload bytes are provably preserved. `new_ttl_seconds`
    is derived from that object's OWN recorded age and can never exceed what the current governed policy
    already allows it (never an extension beyond the cap). Bounded to the existing H4 history index. Returns
    a report; records this operation's own provenance marker in the REPORT only (never inside the candle's
    payload, which this module must never mutate)."""
    now = now or datetime.now(timezone.utc)
    config = config or parse_normalisation_config_from_env()
    if not config.live_apply_allowed:
        raise ValueError("GOV-CANDLE-H4-RET-NORM-001: live retention normalisation refused — requires "
                         f"{NORMALISATION_ENABLED_ENV}=true AND {NORMALISATION_AUTHORISED_ENV}=true AND "
                         f"{NORMALISATION_DRY_RUN_ENV}=false (dark/dry-run by default)")
    findings = identify_stale_h4_history_objects(redis_client, now=now, tolerance_seconds=config.tolerance_seconds)
    normalised, skipped = [], {REASON_ALREADY_COMPLIANT: 0, REASON_BEYOND_CAP: 0, REASON_UNREADABLE: 0}
    for f in findings:
        if not f["needs_normalisation"]:
            skipped[f["reason"]] = skipped.get(f["reason"], 0) + 1
            continue
        redis_client.expire(f["key"], f["new_ttl_seconds"])       # TTL ONLY — payload bytes untouched
        normalised.append({"key": f["key"], "open_epoch": f["open_epoch"],
                           "new_ttl_seconds": f["new_ttl_seconds"]})
    return {
        "mode": "LIVE_APPLY", "run_id": NORMALISATION_RUN_MARKER, "scanned": len(findings),
        "normalised": normalised, "normalised_count": len(normalised), "skipped": skipped,
        "payload_mutation": "NONE_TTL_ONLY",
    }


def dry_run_report(redis_client, *, config=None, now=None):
    """Read-only report (NO writes at all, regardless of config) — the same identification pass as
    `apply_retention_normalisation` but never calls EXPIRE. Mirrors `candle_h4_history_seed_backfill_v1.
    dry_run_plan`'s no-write-proof discipline."""
    now = now or datetime.now(timezone.utc)
    config = config or parse_normalisation_config_from_env()
    findings = identify_stale_h4_history_objects(redis_client, now=now, tolerance_seconds=config.tolerance_seconds)
    flagged = [f for f in findings if f["needs_normalisation"]]
    return {
        "mode": "DRY_RUN", "no_write_proof": True, "config": config.as_dict(),
        "scanned": len(findings), "flagged_count": len(flagged), "flagged": flagged,
    }
