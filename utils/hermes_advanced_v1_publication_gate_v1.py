"""HERMES Advanced-v1 central PUBLICATION-ELIGIBILITY gate v1.
WO-HELM-HERMES-ADVANCED-V1-RUNTIME-MASTER-AND-SCOPE-GATE-0001.

ONE reusable runtime decision authority consumed by all five adopted publication families (tick, indicators, gaps,
backfill-status, feed-health). It distinguishes the already-authorised PILOT scope (governed data — currently XAU_USD)
from EXPANSION scope (every other instrument). The pilot may continue publishing under its EXISTING per-family
enable/authority controls WITHOUT the expansion master. Every expansion instrument is FAIL-CLOSED unless the runtime
expansion master, the publisher mode, the registry capability, the calendar-provenance gate and the readiness guard
are ALL satisfied. No ticker logic lives here or in the publisher modules — the pilot scope is external governed DATA.

Safe defaults (fail-closed): master=false, publisher_mode=DISABLED, unknown mode -> deny, malformed boolean -> deny,
missing pilot-scope authority -> deny, alias -> deny. This module performs NO Redis/SQL/OANDA I/O of its own except the
registry load it delegates to the (patchable) registry loader when an expansion ACTIVE decision needs capability/policy.
"""
from __future__ import annotations
import json
import pathlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

UTC = timezone.utc
_ROOT = pathlib.Path(__file__).resolve().parents[1]
_PILOT_CFG = _ROOT / "config" / "advanced_v1" / "pilot_scope_v1.json"

MASTER_ENV = "HERMES_ADVANCED_V1_MASTER_ENABLED"
MODE_ENV = "HERMES_ADVANCED_V1_PUBLISHER_MODE"

MODE_DISABLED = "DISABLED"
MODE_SHADOW = "SHADOW"
MODE_ACTIVE = "ACTIVE"
PUBLISHER_MODES = (MODE_DISABLED, MODE_SHADOW, MODE_ACTIVE)

SCOPE_PILOT = "PILOT"
SCOPE_EXPANSION = "EXPANSION"

_ALIAS_DENY = ("XAUUSD",)
# family -> registry capability the expansion path checks (backfill-status follows gap; feed-health follows tick).
FAMILY_CAPABILITY = {"tick": "tick", "indicator": "indicator", "gaps": "gap",
                     "backfill_status": "gap", "feed_health": "tick"}

# Deterministic fault/reason vocabulary (fail-closed). PERMIT reasons are also explicit + truthful.
PERMIT_PILOT = "PILOT_SCOPE_AUTHORISED"
PERMIT_EXPANSION_ACTIVE = "EXPANSION_ELIGIBLE_ACTIVE"
DENY_ALIAS = "ALIAS_REJECTED"
DENY_MASTER_DISABLED = "EXPANSION_MASTER_DISABLED"
DENY_PUBLISHER_DISABLED = "PUBLISHER_DISABLED"
DENY_UNKNOWN_MODE = "UNKNOWN_PUBLISHER_MODE"
DENY_SHADOW_UNAVAILABLE = "PUBLISHER_SHADOW_UNAVAILABLE"
DENY_MISSING_PILOT_AUTHORITY = "MISSING_PILOT_SCOPE_AUTHORITY"
DENY_MALFORMED_CONFIG = "MALFORMED_CONFIGURATION"
DENY_CAPABILITY_DISABLED = "REGISTRY_CAPABILITY_DISABLED"
DENY_MIGRATION_REQUIRED = "MIGRATION_REQUIRED"
DENY_CALENDAR_INELIGIBLE = "CALENDAR_INELIGIBLE"
DENY_UNKNOWN_FAMILY = "UNKNOWN_FAMILY"
DENY_READINESS_FAILED = "READINESS_FAILED"


class PublicationGateError(ValueError):
    """Fail-closed gate fault (malformed config / missing authority)."""


@dataclass(frozen=True)
class PublicationDecision:
    instrument: str
    family: str
    scope: str
    master_enabled: bool
    publisher_mode: str
    permitted: bool
    reason: str
    registry_capability: Optional[bool] = None
    family_enabled: Optional[bool] = None
    family_authorised: Optional[bool] = None
    readiness_ok: Optional[bool] = None
    calendar_status: Optional[str] = None
    evaluated_at_utc: str = ""
    policy_version: str = "v1"

    def as_dict(self):
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


# --------------------------------------------------------------------------- external config (fail-closed)
def _env_bool(name, default=False):
    from env_config import get_env
    raw = get_env(name, default=None)
    if raw is None:
        return default
    s = str(raw).strip().lower()
    if s in ("true", "1", "yes", "on"):
        return True
    if s in ("false", "0", "no", "off", ""):
        return False
    raise PublicationGateError(f"GOV-HERMES-PGATE-001: malformed boolean for {name}={raw!r} (fail-closed)")


def load_master_enabled():
    return _env_bool(MASTER_ENV, default=False)


def load_publisher_mode():
    from env_config import get_env
    raw = get_env(MODE_ENV, default=None)
    if raw is None or not str(raw).strip():
        return MODE_DISABLED                                   # safe default
    mode = str(raw).strip().upper()
    if mode not in PUBLISHER_MODES:
        return "__UNKNOWN__"                                   # sentinel -> fail closed at decision time
    return mode


def load_pilot_scope():
    if not _PILOT_CFG.exists():
        raise PublicationGateError("GOV-HERMES-PGATE-002: pilot-scope authority config missing (fail-closed)")
    try:
        doc = json.loads(_PILOT_CFG.read_text())
        scope = doc["pilot_scope"]
    except Exception as e:  # noqa: BLE001
        raise PublicationGateError(f"GOV-HERMES-PGATE-003: pilot-scope authority malformed: {e}")
    if not isinstance(scope, list) or not scope or any((not isinstance(s, str) or not s) for s in scope):
        raise PublicationGateError("GOV-HERMES-PGATE-004: pilot-scope must be a non-empty list of symbols")
    if len(set(scope)) != len(scope):
        raise PublicationGateError("GOV-HERMES-PGATE-005: pilot-scope contains duplicate symbols")
    return frozenset(scope)


# --------------------------------------------------------------------------- central decision
def decide(instrument, family, *, now=None, records=None, master=None, publisher_mode=None, pilot_scope=None,
           family_enabled=None, family_authorised=None):
    """The single publication-eligibility authority. Returns a PublicationDecision. Test-injectable: master/
    publisher_mode/pilot_scope/records/family_* may be supplied; otherwise resolved fail-closed from governed config."""
    now = (now or datetime.now(UTC)) if False else now         # never call now() implicitly (resume-safety); require caller
    ts = (now.astimezone(UTC).isoformat() if now is not None else "")
    if family not in FAMILY_CAPABILITY:
        return PublicationDecision(instrument, family, "?", False, "?", False, DENY_UNKNOWN_FAMILY, evaluated_at_utc=ts)
    try:
        pilot = pilot_scope if pilot_scope is not None else load_pilot_scope()
    except PublicationGateError:
        return PublicationDecision(instrument, family, "?", False, "?", False, DENY_MISSING_PILOT_AUTHORITY, evaluated_at_utc=ts)
    try:
        m = load_master_enabled() if master is None else bool(master)
        pm = load_publisher_mode() if publisher_mode is None else str(publisher_mode).strip().upper()
    except PublicationGateError:
        return PublicationDecision(instrument, family, "?", False, "?", False, DENY_MALFORMED_CONFIG, evaluated_at_utc=ts)

    if not instrument or instrument in _ALIAS_DENY:
        return PublicationDecision(instrument, family, "?", m, pm, False, DENY_ALIAS, evaluated_at_utc=ts)

    scope = SCOPE_PILOT if instrument in pilot else SCOPE_EXPANSION

    # ---- PILOT: preserved under existing family controls; does NOT require the expansion master ----
    if scope == SCOPE_PILOT:
        return PublicationDecision(instrument, family, SCOPE_PILOT, m, pm, True, PERMIT_PILOT,
                                   family_enabled=family_enabled, family_authorised=family_authorised, evaluated_at_utc=ts)

    # ---- EXPANSION: fail-closed unless every gate is satisfied ----
    if not m:
        return PublicationDecision(instrument, family, SCOPE_EXPANSION, m, pm, False, DENY_MASTER_DISABLED, evaluated_at_utc=ts)
    if pm == MODE_DISABLED:
        return PublicationDecision(instrument, family, SCOPE_EXPANSION, m, pm, False, DENY_PUBLISHER_DISABLED, evaluated_at_utc=ts)
    if pm == MODE_SHADOW:
        return PublicationDecision(instrument, family, SCOPE_EXPANSION, m, pm, False, DENY_SHADOW_UNAVAILABLE, evaluated_at_utc=ts)
    if pm != MODE_ACTIVE:                                       # env sentinel or any injected non-governed value
        return PublicationDecision(instrument, family, SCOPE_EXPANSION, m, pm, False, DENY_UNKNOWN_MODE, evaluated_at_utc=ts)
    # pm == ACTIVE
    if family_enabled is False or family_authorised is False:
        return PublicationDecision(instrument, family, SCOPE_EXPANSION, m, pm, False, DENY_PUBLISHER_DISABLED,
                                   family_enabled=family_enabled, family_authorised=family_authorised, evaluated_at_utc=ts)
    # registry capability + calendar + readiness (load records lazily via patchable loader)
    from utils import hermes_advanced_v1_selection_v1 as sel
    if records is None:
        from utils import hermes_instrument_registry_v1 as reg
        try:
            records = reg.load_from_db()
        except Exception:  # noqa: BLE001
            return PublicationDecision(instrument, family, SCOPE_EXPANSION, m, pm, False, DENY_MIGRATION_REQUIRED, evaluated_at_utc=ts)
    cap = FAMILY_CAPABILITY[family]
    selected = set(sel.backfill_status_instruments(records)) if family == "backfill_status" else set(sel.selection_for(cap, records))
    if instrument not in selected:
        return PublicationDecision(instrument, family, SCOPE_EXPANSION, m, pm, False, DENY_CAPABILITY_DISABLED,
                                   registry_capability=False, evaluated_at_utc=ts)
    # readiness guard (migration-025 metadata)
    from utils import hermes_advanced_v1_readiness_v1 as readiness
    try:
        readiness.assert_ready(records)
    except Exception:  # noqa: BLE001
        return PublicationDecision(instrument, family, SCOPE_EXPANSION, m, pm, False, DENY_READINESS_FAILED,
                                   registry_capability=True, readiness_ok=False, evaluated_at_utc=ts)
    # calendar provenance gate for the instrument's market-hours policy
    policy_key = next((r.market_hours_policy for r in records if r.symbol == instrument), None)
    from utils import hermes_advanced_v1_readiness_package_v1 as rp
    cal_status = None
    try:
        cal_status = rp.calendar_policy_status(policy_key)
        rp.calendar_policy_production_ready(policy_key, now=now)
    except Exception:  # noqa: BLE001
        return PublicationDecision(instrument, family, SCOPE_EXPANSION, m, pm, False, DENY_CALENDAR_INELIGIBLE,
                                   registry_capability=True, readiness_ok=True, calendar_status=cal_status, evaluated_at_utc=ts)
    return PublicationDecision(instrument, family, SCOPE_EXPANSION, m, pm, True, PERMIT_EXPANSION_ACTIVE,
                               registry_capability=True, family_enabled=family_enabled, family_authorised=family_authorised,
                               readiness_ok=True, calendar_status=cal_status, evaluated_at_utc=ts)


def permitted(instrument, family, **kw) -> bool:
    """Convenience boolean for publisher call-sites."""
    return decide(instrument, family, **kw).permitted
