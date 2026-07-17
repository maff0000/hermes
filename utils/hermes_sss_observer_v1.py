"""HERMES shared-stream recovery Phase-2 — PASSIVE CURRENT-AUTHORITY OBSERVER (PRODUCTION-OWNED, INERT).

WO-HELM-HERMES-SHARED-STREAM-RECOVERY-PHASE2-SHADOW-ADAPTER-IMPLEMENTATION-0001.
Authority: HELM (HERMES market-data lane). Created (UTC): 2026-07-17. Owner: HERMES (Helm).
Contract: docs/design/shared_stream_recovery/architecture_phase2_v1.md §10 (binding). Contract version "1".

STATUS: INERT / NOT WIRED. Imported by NO live runtime path; only tests + sibling utils/hermes_sss_* modules.

Builds a PASSIVE, read-only CurrentAuthorityObservation from an immutable WatchdogStateView (a frozen COPY of the
existing sustained-red authority's already-produced state/events). It reads existing state ONLY. It MUST NOT — and,
because the view is an immutable frozen dataclass, MECHANICALLY CANNOT — call, wrap, gate, re-order, delay, mutate
the limiter/cooldown, invoke a reconnect, or consume/clear the one-shot recovery request. Where a safe observation
is impossible without mutation or consumption, the field is left UNAVAILABLE (evaluated=False) rather than weakening
the boundary. Standard-library only.
"""
from __future__ import annotations

from typing import Optional

from utils.hermes_sss_evidence_adapters_v1 import WatchdogStateView
from utils.hermes_sss_shadow_record_v1 import CurrentAuthorityObservation


def _parse_triggering_instrument(reason: Optional[str]) -> Optional[str]:
    """Read-only parse of `instrument=<SYM>` from an already-produced recovery-request reason string. Never mutates
    the reason; returns None when absent."""
    if not reason:
        return None
    for token in reason.split():
        if token.startswith("instrument="):
            value = token.split("=", 1)[1].strip()
            return value or None
    return None


def observe_current_authority(view: WatchdogStateView, *, now_utc: str) -> CurrentAuthorityObservation:
    """Return an immutable, PASSIVE observation of what the existing sustained-red authority did. PURE, read-only.

    `evaluated` is True when the watchdog produced a recovery-evaluation signal for this cycle (a pending request or
    a recorded last-request instant) — i.e. there is something to compare. When neither is present the authority did
    not run a recovery evaluation and the observation is `evaluated=False` -> CURRENT_AUTHORITY_NOT_EVALUATED.

    IMPORTANT: this function does NOT consume `recovery_request_pending`; it merely READS it. The one-shot request is
    left intact for the existing authority to consume in its own path, exactly as before the shadow existed."""
    evaluated = bool(view.recovery_request_pending or view.last_recovery_request_at_utc is not None)
    requested = bool(view.recovery_request_pending)
    trigger = _parse_triggering_instrument(view.recovery_request_reason)
    return CurrentAuthorityObservation(
        evaluated=evaluated,
        triggering_instrument=trigger,
        recovery_requested=requested,
        # The shadow observes only what the watchdog surfaced. Whether the request was actually executed vs
        # suppressed by the limiter is derived from the passive limiter view, never by driving the authority.
        reconnect_executed=False,
        reconnect_suppressed=bool(requested and not view.limiter.available),
        reason=view.recovery_request_reason,
        observed_at_utc=now_utc,
    )
