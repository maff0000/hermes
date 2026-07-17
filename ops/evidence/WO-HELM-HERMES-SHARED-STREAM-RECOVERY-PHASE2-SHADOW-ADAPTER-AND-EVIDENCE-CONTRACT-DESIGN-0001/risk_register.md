# Risk register (top 4)

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| R1 | Heartbeat horizons PROVISIONAL (July-16 age never logged) -> mis-tuned band | Med | flagged provisional; validated-corroboration required; Phase-2 soak calibrates on real data |
| R2 | Inferred July-16 heartbeat presented as fact -> false confidence | High | provenance marks JUSTIFIED_INFERENCE; test asserts the distinction; directly-observed facts kept separate |
| R3 | Deploying the correction ships PR103-107 unreviewed-for-deploy | High | §29 per-PR deploy sign-off / governed lineage; no unsafe cherry-pick |
| R4 | Future shadow adapter blocks market-data / recovery path | High | off hot path; single-flight; no shared lock; skip-on-overrun; failure isolation (§11/§19/§21) |
