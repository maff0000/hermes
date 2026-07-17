# Genuine-fault shadow scenarios (all inert, offline; tests in test_sss_phase2_shadow_design_v1.py)

| Scenario | Shadow action | Comparison |
|---|---|---|
| provider disconnect | RECONNECT_AUTHORISED (emergency) | AGREE_RECONNECT |
| socket disconnect | RECONNECT_AUTHORISED | AGREE_RECONNECT |
| auth failure | RECONNECT_AUTHORISED | AGREE_RECONNECT |
| heartbeat stale + ALL validated expected-flow stale | RECONNECT_AUTHORISED | AGREE_RECONNECT |
| shared-progress failure (silent stall) | RECONNECT_AUTHORISED | AGREE_RECONNECT |
| parser fatal | RECONNECT_AUTHORISED | AGREE_RECONNECT |
| limiter-exhausted + provider disconnect | RECONNECT_AUTHORISED (bypass) | AGREE_RECONNECT |
| genuine fault + current requested NO reconnect | RECONNECT_AUTHORISED | SHADOW_AUTHORIZES_CURRENT_NO_RECONNECT |
| adapter evidence incomplete | withheld | EVIDENCE_INCOMPLETE |
| conflicting evidence | fail closed | EVIDENCE_CONFLICT |

10 scenarios. Sample verified results: provider_disconnect -> AGREE_RECONNECT; heartbeat_stale_corroborated ->
AGREE_RECONNECT; genuine-fault-current-silent -> SHADOW_AUTHORIZES_CURRENT_NO_RECONNECT; incomplete ->
EVIDENCE_INCOMPLETE; conflict -> EVIDENCE_CONFLICT; adapter_error -> ADAPTER_ERROR. All 10 comparison classes
proven reachable (test_all_ten_comparison_classes_reachable).
