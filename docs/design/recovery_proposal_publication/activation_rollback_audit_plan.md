# Activation, Rollback & Audit Plan (§26.19)

Mirrors the proven planner governed cycle. Nothing here is executed by this design WO.

- **Activation (future, isolated first):** install publication config (mounted read-only) + set publication gates on an
  ISOLATED test Redis namespace only; recreate only hermes-signal; verify one eligible publish + refusal paths; bounded window.
- **Rollback (mandatory default):** unset publication gates (absent, not "false"); remove config; recreate; verify current
  pointer expired/removed; no K1/K2 in the live namespace; runners return to prior count; consumer sees ABSENT; consumer_live
  false; PH1/PH2/feed healthy.
- **Audit:** each stage gets an independent R2D2 cold/deploy-dark/controlled audit at the exact immutable head. Production
  activation requires a separately-authorised consumer + the SQL audit sink live.

Every stage: evidence dir + SHA256SUMS + HELM fabric records; runtime non-mutation proofs; cross-application non-mutation.
