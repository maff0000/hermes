# Control-Plane Health Self-Listing Fix (R2D2 finding)
BEFORE: build_health_summary always listed contract_manifest/publisher_heartbeat/candle_catalog/health_summary
under missing_but_expected_families and control_plane_health=PENDING — even though the control-plane keys are live.
AFTER: build_health_summary(control_plane_active=True) reports those four families + control_plane_health = ACTIVE
and removes them from missing_but_expected_families. Genuinely-missing surfaces (indicators, candle_features,
feed_health, instrument_catalog, sessions, candle_history_d1, ticks) remain listed. Default (control_plane_active=
False) preserves the prior behaviour. indicators_built=True -> BUILT_NOT_ACTIVE (never falsely ACTIVE pre-activation).
Code-only: the live correction lands when the control-plane publisher (future deploy) passes control_plane_active=True.
