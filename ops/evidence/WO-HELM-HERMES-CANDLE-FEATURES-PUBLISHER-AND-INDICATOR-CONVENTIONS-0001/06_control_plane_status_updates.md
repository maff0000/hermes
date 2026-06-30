# Control-Plane Status Updates (Part E, code-only)
build_health_summary gains candle_features_built / candle_features_active:
- default -> candle_features NOT_IMPLEMENTED (in missing_but_expected)
- candle_features_built=True -> BUILT_NOT_ACTIVE (still listed as expected; never falsely ACTIVE)
- candle_features_active=True -> ACTIVE + dropped from missing_but_expected
D1 candle_features remain gated until D1 latest GREEN (publisher D1 gating). Indicator method metadata is carried
in the indicator payloads (discoverable by consumers). No live publish; the next deploy/activate WO applies the flags.
