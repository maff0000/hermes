"""Advanced-v1 production-readiness package — governed manifest + gate validation. Pure, no I/O (reads committed
governed JSON/SQL/evidence under the repo). WO-HELM-HERMES-ADVANCED-V1-PRODUCTION-READINESS-0001.
"""
import json
import pathlib
from datetime import datetime, timezone

import pytest

import utils.hermes_advanced_v1_readiness_package_v1 as rp
import utils.hermes_market_hours_policy_v1 as mhp

UTC = timezone.utc
ROOT = pathlib.Path(__file__).resolve().parents[1]
NOW = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)          # within the fx/metals review window


# --------------------------------------------------------------------------- calendar provenance
def test_calendar_provenance_structure_all_four_policies():
    pol = rp.load_calendar_provenance()
    assert set(pol) == set(rp.GOVERNED_POLICY_KEYS) == set(mhp.GOVERNED_POLICY_KEYS)   # policy/registry key parity


def test_fx_and_metals_production_ready():
    for key in ("fx_24x5", "metals"):
        assert rp.calendar_policy_production_ready(key, now=NOW) is True
        assert rp.calendar_policy_status(key) == "BROKER_CONFIRMED_WITH_HOLIDAY_LIMITATION"


@pytest.mark.parametrize("key", ["index_cash", "energy"])
def test_index_and_energy_fail_closed_assumption_requires_provider(key):
    assert rp.calendar_policy_status(key) == "ASSUMPTION_REQUIRES_PROVIDER"
    with pytest.raises(rp.ReadinessPackageError, match="RDY-021"):
        rp.calendar_policy_production_ready(key, now=NOW)          # BLOCKED until OANDA session evidence


def test_stale_calendar_rejected():
    # a validated policy past its next_review_utc is STALE -> fail closed
    future = datetime(2027, 6, 1, tzinfo=UTC)                      # beyond fx/metals next_review 2026-11-05
    with pytest.raises(rp.ReadinessPackageError, match="RDY-025"):
        rp.calendar_policy_production_ready("fx_24x5", now=future)


def test_unknown_policy_rejected():
    with pytest.raises(rp.ReadinessPackageError, match="RDY-020"):
        rp.calendar_policy_production_ready("SPX500_USD", now=NOW)  # ticker is not a policy key


def test_index_energy_have_documented_fail_safe():
    pol = rp.load_calendar_provenance()
    for key in ("index_cash", "energy"):
        assert pol[key]["fault_code"] == "SCHEDULE_AMBIGUOUS_FAIL_CLOSED"
        assert "fail_safe_production_behaviour" in pol[key] and pol[key]["production_approved"] is False


# --------------------------------------------------------------------------- dark deployment
def test_dark_deployment_fail_closed_defaults():
    doc = rp.load_dark_deployment()
    c = doc["controls"]
    assert c["master_enable"]["default"] is False
    for fam in ("tick", "indicators", "gaps", "backfill_status", "feed_health"):
        assert c["per_capability_enable"][fam]["default"] is False
    assert c["consumer_enabled"]["value"] is False and c["order_path"]["present"] is False
    assert c["oanda_streams"]["max"] == 1


def test_dark_deployment_controls_are_external_not_code():
    doc = rp.load_dark_deployment()
    caps = doc["controls"]["per_capability_enable"]
    for fam, spec in caps.items():
        assert spec["env"].startswith("HERMES_") and spec["authorised_env"].startswith("HERMES_")


# --------------------------------------------------------------------------- one-stream proof
def test_one_stream_invariant_in_config_and_code():
    assert rp.load_dark_deployment()["controls"]["oanda_streams"]["max"] == 1
    # the adopted downstream families construct NO OANDA/pricing stream and no per-instrument worker/thread
    for mod in ("utils/tick_live_emitter_v1.py", "utils/hermes_indicators_v1.py", "utils/hermes_gaps_v1.py",
                "utils/hermes_backfill_status_v1.py", "utils/hermes_feed_health_v1.py"):
        src = (ROOT / mod).read_text()
        for tok in ("PricingStream", "pricing.PricingStream", "oandapyV20", "stream(", "Thread(", "multiprocessing"):
            assert tok not in src, f"{mod} must not create a stream/worker ({tok})"


def test_no_consumer_or_order_path_in_adopted_modules():
    for mod in ("utils/hermes_gaps_v1.py", "utils/hermes_backfill_status_v1.py", "utils/hermes_feed_health_v1.py"):
        src = (ROOT / mod).read_text().lower()
        for tok in ("place_order", "submit_order", "order_path", "consumer_live=true", "execute_trade"):
            assert tok not in src, f"{mod} must not introduce consumer/order path ({tok})"


# --------------------------------------------------------------------------- health thresholds
def test_health_thresholds_complete_every_family():
    doc = rp.load_health_thresholds()
    for fam in ("tick", "indicators", "gaps", "backfill_status", "feed_health"):
        rows = doc["families"][fam]
        assert rows
        for r in rows:
            for f in ("metric", "denominator", "window", "green", "amber", "red", "action"):
                assert f in r and r[f] not in (None, "")


# --------------------------------------------------------------------------- activation sequence + doctrine
def test_activation_sequence_ordered_and_doctrine_fail_closed():
    doc = rp.load_activation_sequence()
    steps = [g["step"] for g in doc["gates"]]
    assert steps == list(range(1, len(steps) + 1)) and len(steps) >= 16
    d = doc["doctrine"]
    assert d["consumer_live"] is False and d["order_path"] == "absent"
    assert d["migration_authorises_deployment"] is False and d["deployment_authorises_publication"] is False


# --------------------------------------------------------------------------- rollback matrix
def test_rollback_matrix_covers_all_triggers():
    doc = rp.load_rollback_matrix()
    names = {t["trigger"] for t in doc["triggers"]}
    for req in ("migration_failure", "one_stream_violation", "consumer_leakage", "order_path_evidence",
                "duplicate_publication", "state_collision", "false_gap_classification"):
        assert req in names
    for t in doc["triggers"]:
        assert "resume_authority" in t and "incident_state" in t


# --------------------------------------------------------------------------- migration 025 SQL structure
def test_migration_025_idempotent_and_additive():
    sql = (ROOT / "migrations/025_advanced_v1_registry_metadata.sql").read_text()
    assert "ADD COLUMN IF NOT EXISTS" in sql and "'energy'" in sql
    assert "DELETE FROM instruments" not in sql and "DROP TABLE" not in sql   # additive; no row/table loss
    assert sql.count("ADD COLUMN IF NOT EXISTS") >= 12


def test_migration_025_rollback_reverts():
    rb = (ROOT / "migrations/025_advanced_v1_registry_metadata_rollback.sql").read_text()
    assert "base_metals" in rb and "DROP COLUMN IF EXISTS" in rb
    assert "WTICO_USD" in rb


# --------------------------------------------------------------------------- production non-mutation evidence
def test_production_preflight_evidence_shows_migration_absent():
    ev = ROOT / "ops/evidence/WO-HELM-HERMES-ADVANCED-V1-PRODUCTION-READINESS-0001/migration_025_production_preflight.json"
    d = json.loads(ev.read_text())
    assert d["rowcount"] == 14 and d["wtico_category"] == "base_metals"
    assert d["category_enum_has_energy"] is False and d["adv_v1_cols_present"] == 0    # migration 025 NOT applied in prod


# --------------------------------------------------------------------------- whole package
def test_validate_all_green():
    s = rp.validate_all()
    assert s["manifests_valid"] is True
    assert s["calendar_policies"]["fx_24x5"] == "BROKER_CONFIRMED_WITH_HOLIDAY_LIMITATION"
    assert s["calendar_policies"]["index_cash"] == "ASSUMPTION_REQUIRES_PROVIDER"
