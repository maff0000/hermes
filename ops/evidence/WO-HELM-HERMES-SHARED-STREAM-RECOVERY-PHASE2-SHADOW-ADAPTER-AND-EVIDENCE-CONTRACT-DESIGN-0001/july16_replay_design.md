# July-16 shadow replay design (deterministic, offline)

Fixture: `tests/fixtures/sss_phase2/july16_snapshot.json`. Harness: `design/sss_phase2_replay_harness_v1.py`.
Evaluation instant: 21:09:00Z (current-authority reconnect #1, triggering_instrument=SPX500_USD).

## Inputs (as recorded)
- current authority: evaluated=True, triggering=SPX500_USD, recovery_requested=True, reconnect_executed=True
  (3 reconnects total at 21:09/21:19/21:29Z).
- snapshot: socket connected; heartbeat/shared-progress healthy (INFERRED); XAU+metals governed_closed; FX
  progressing (validated, not stale); SPX500_USD + WTICO_USD stale + validated=False; limiter 0/3.

## Result
- shadow decision: RECOVERY_PROPOSAL_ONLY, reconnect_authorised=False, transport_state=TRANSPORT_HEALTHY,
  reason UNVALIDATED_INSTRUMENT_STALE_NO_TRANSPORT_AUTHORITY.
- comparison: SHADOW_DENIES_CURRENT_RECONNECT (granular: SHADOW_PROPOSAL_CURRENT_RECONNECT).
- NO shadow execution. Deterministic: same snapshot -> identical record_id.

## Honesty-caveat handling (the load-bearing distinction)
DIRECTLY_OBSERVED (logged): socket CONNECTED; conn=CONNECTED/fault=NONE throughout; XAU/metals prices stopped
20:59->22:04; SPX500/WTICO stale+RED; FX progressing; 3 SPX-driven reconnects.
JUSTIFIED_INFERENCE (NOT a logged measurement): heartbeat healthy / heartbeat_age_s / shared_progress — inferred
from (a) adapter bumps last_tick_at on HEARTBEAT ~5 s, (b) socket CONNECTED + fault NONE, (c) FX progressing. The
fixture's field_provenance marks these JUSTIFIED_INFERENCE so the replay NEVER presents inferred heartbeat as a
logged fact. provider_maintenance_indication = NOT_CURRENTLY_AVAILABLE (never inferred from the break).
Test asserts this distinction: test_july16_honesty_heartbeat_is_inferred_not_measured.
