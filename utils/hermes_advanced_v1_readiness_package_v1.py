"""HERMES Advanced-v1 production-readiness package — governed manifest loaders + validators + the fail-closed
production CALENDAR gate. WO-HELM-HERMES-ADVANCED-V1-PRODUCTION-READINESS-0001.

Planning/validation only: this module reads governed JSON manifests (calendar provenance, dark-deployment controls,
health thresholds, activation sequence, rollback matrix) and enforces their structure + the production-eligibility
rules. It performs NO migration, NO deployment, NO publication, NO Redis/SQL/OANDA I/O. The calendar gate FAILS CLOSED:
a policy is production-eligible only when broker-confirmed, approved, and neither expired nor stale.
"""
from __future__ import annotations
import json
import pathlib
from datetime import datetime, timezone

UTC = timezone.utc
_ROOT = pathlib.Path(__file__).resolve().parents[1]
_CFG = _ROOT / "config" / "advanced_v1"

# Governed market-hours policy keys (must match the registry market_hours_policy domain / mhp resolver).
GOVERNED_POLICY_KEYS = ("fx_24x5", "metals", "index_cash", "energy")
# Calendar validation statuses; only the first two are production-eligible.
PRODUCTION_ELIGIBLE_STATUSES = ("BROKER_CONFIRMED", "BROKER_CONFIRMED_WITH_HOLIDAY_LIMITATION")
NON_PRODUCTION_STATUSES = ("ASSUMPTION_REQUIRES_PROVIDER", "NOT_SAFE_FOR_PRODUCTION")
ALL_STATUSES = PRODUCTION_ELIGIBLE_STATUSES + NON_PRODUCTION_STATUSES


class ReadinessPackageError(ValueError):
    """Fail-closed readiness-package fault (malformed manifest, missing field, ineligible/stale calendar policy)."""


def _load(name):
    p = _CFG / name
    if not p.exists():
        raise ReadinessPackageError(f"GOV-HERMES-RDY-001: governed manifest missing: {p}")
    try:
        return json.loads(p.read_text())
    except Exception as e:  # noqa: BLE001
        raise ReadinessPackageError(f"GOV-HERMES-RDY-002: manifest {name} not valid JSON: {e}")


def _parse_utc(s, field):
    if s is None:
        return None
    if not isinstance(s, str) or not s.endswith("Z"):
        raise ReadinessPackageError(f"GOV-HERMES-RDY-003: {field} must be a UTC ISO string ending 'Z' (got {s!r})")
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC)


# --------------------------------------------------------------------------- calendar provenance
def load_calendar_provenance():
    doc = _load("calendar_provenance_v1.json")
    policies = {p["policy_key"]: p for p in doc.get("policies", [])}
    missing = [k for k in GOVERNED_POLICY_KEYS if k not in policies]
    if missing:
        raise ReadinessPackageError(f"GOV-HERMES-RDY-010: calendar provenance missing policy keys {missing}")
    required = ("policy_key", "policy_version", "broker", "instrument_class", "timezone", "source",
                "weekly_schedule", "daily_breaks", "holiday_source", "validation_status", "production_approved")
    for k, p in policies.items():
        for f in required:
            if f not in p:
                raise ReadinessPackageError(f"GOV-HERMES-RDY-011: policy {k} missing provenance field {f!r}")
        if p["validation_status"] not in ALL_STATUSES:
            raise ReadinessPackageError(f"GOV-HERMES-RDY-012: policy {k} invalid validation_status {p['validation_status']!r}")
    return policies


def calendar_policy_status(policy_key):
    return load_calendar_provenance()[policy_key]["validation_status"]


def calendar_policy_production_ready(policy_key, *, now):
    """FAIL-CLOSED production calendar gate. Returns True only if the policy is broker-confirmed + production_approved +
    within its effective window + not stale (now < next_review_utc). Any ineligible/expired/stale/missing -> raise."""
    if policy_key not in GOVERNED_POLICY_KEYS:
        raise ReadinessPackageError(f"GOV-HERMES-RDY-020: unknown policy key {policy_key!r}")
    now = now.astimezone(UTC) if now.tzinfo else now.replace(tzinfo=UTC)
    p = load_calendar_provenance()[policy_key]
    status = p["validation_status"]
    if status not in PRODUCTION_ELIGIBLE_STATUSES:
        raise ReadinessPackageError(
            f"GOV-HERMES-RDY-021: policy {policy_key} not production-eligible (status={status}, "
            f"fault={p.get('fault_code')}); activation BLOCKED — {p.get('fail_safe_production_behaviour', 'fail closed')}")
    if not p.get("production_approved"):
        raise ReadinessPackageError(f"GOV-HERMES-RDY-022: policy {policy_key} not production_approved")
    eff_from = _parse_utc(p.get("effective_from_utc"), "effective_from_utc")
    eff_to = _parse_utc(p.get("effective_to_utc"), "effective_to_utc")
    review = _parse_utc(p.get("next_review_utc"), "next_review_utc")
    if eff_from is not None and now < eff_from:
        raise ReadinessPackageError(f"GOV-HERMES-RDY-023: policy {policy_key} not yet effective (from {eff_from})")
    if eff_to is not None and now >= eff_to:
        raise ReadinessPackageError(f"GOV-HERMES-RDY-024: policy {policy_key} expired (to {eff_to})")
    if review is None or now >= review:
        raise ReadinessPackageError(f"GOV-HERMES-RDY-025: policy {policy_key} calendar STALE (next_review {review})")
    return True


# --------------------------------------------------------------------------- dark-deployment controls
def load_dark_deployment():
    doc = _load("dark_deployment_v1.json")
    c = doc.get("controls", {})
    if not c.get("master_enable") or c["master_enable"].get("default") is not False:
        raise ReadinessPackageError("GOV-HERMES-RDY-030: master_enable must default false (fail-closed dark)")
    caps = c.get("per_capability_enable", {})
    for fam in ("tick", "indicators", "gaps", "backfill_status", "feed_health"):
        if fam not in caps or caps[fam].get("default") is not False:
            raise ReadinessPackageError(f"GOV-HERMES-RDY-031: capability {fam} must default false")
    if c.get("consumer_enabled", {}).get("value") is not False:
        raise ReadinessPackageError("GOV-HERMES-RDY-032: consumer_enabled must be false (consumer_live invariant)")
    if c.get("order_path", {}).get("present") is not False:
        raise ReadinessPackageError("GOV-HERMES-RDY-033: order_path must be absent")
    if c.get("oanda_streams", {}).get("max") != 1:
        raise ReadinessPackageError("GOV-HERMES-RDY-034: oanda_streams.max must be exactly 1 (one-stream invariant)")
    if not c.get("readiness_guard", {}).get("required"):
        raise ReadinessPackageError("GOV-HERMES-RDY-035: readiness_guard must be required")
    return doc


# --------------------------------------------------------------------------- health thresholds
_THRESHOLD_FIELDS = ("metric", "denominator", "window", "green", "amber", "red", "action")


def load_health_thresholds():
    doc = _load("health_thresholds_v1.json")
    fams = doc.get("families", {})
    for fam in ("tick", "indicators", "gaps", "backfill_status", "feed_health"):
        rows = fams.get(fam)
        if not rows:
            raise ReadinessPackageError(f"GOV-HERMES-RDY-040: health thresholds missing family {fam}")
        for r in rows:
            for f in _THRESHOLD_FIELDS:
                if f not in r:
                    raise ReadinessPackageError(f"GOV-HERMES-RDY-041: {fam} threshold {r.get('metric')} missing {f!r}")
    return doc


# --------------------------------------------------------------------------- activation sequence
def load_activation_sequence():
    doc = _load("activation_sequence_v1.json")
    gates = doc.get("gates", [])
    steps = [g["step"] for g in gates]
    if steps != list(range(1, len(gates) + 1)):
        raise ReadinessPackageError(f"GOV-HERMES-RDY-050: activation gates must be contiguously ordered from 1 (got {steps})")
    d = doc.get("doctrine", {})
    for k in ("source_merge_authorises_migration", "migration_authorises_deployment",
              "deployment_authorises_publication", "publication_authorises_consumers",
              "consumer_availability_authorises_orders"):
        if d.get(k) is not False:
            raise ReadinessPackageError(f"GOV-HERMES-RDY-051: promotion doctrine {k} must be false (no silent implication)")
    if d.get("consumer_live") is not False:
        raise ReadinessPackageError("GOV-HERMES-RDY-052: doctrine consumer_live must be false")
    return doc


# --------------------------------------------------------------------------- rollback matrix
_ROLLBACK_REQUIRED_TRIGGERS = (
    "migration_failure", "schema_mismatch", "calendar_validation_failure", "readiness_failure",
    "application_startup_failure", "unexpected_redis_writes", "duplicate_publication",
    "incorrect_instrument_identity", "stale_or_failed_health", "state_collision", "false_gap_classification",
    "one_stream_violation", "consumer_leakage", "order_path_evidence")


def load_rollback_matrix():
    doc = _load("rollback_matrix_v1.json")
    triggers = {t["trigger"]: t for t in doc.get("triggers", [])}
    missing = [t for t in _ROLLBACK_REQUIRED_TRIGGERS if t not in triggers]
    if missing:
        raise ReadinessPackageError(f"GOV-HERMES-RDY-060: rollback matrix missing triggers {missing}")
    for name, t in triggers.items():
        if "resume_authority" not in t or "incident_state" not in t:
            raise ReadinessPackageError(f"GOV-HERMES-RDY-061: rollback trigger {name} missing resume_authority/incident_state")
    return doc


def validate_all():
    """Load + fail-closed-validate every governed readiness manifest. Returns a small summary; raises on any fault."""
    cal = load_calendar_provenance()
    load_dark_deployment(); load_health_thresholds(); load_activation_sequence(); load_rollback_matrix()
    return {"calendar_policies": {k: v["validation_status"] for k, v in cal.items()},
            "manifests_valid": True}
