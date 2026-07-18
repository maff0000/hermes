# Prerequisite verification

- **WO:** WO-HELM-HERMES-PRE-STAGE-B-BUILD-CONTEXT-OCI-PROVENANCE-AND-SAFETY-GATE-CORRECTIONS-IMPLEMENTATION-0001
- **Authority:** HELM (HERMES market-data lane)
- **Created (UTC):** 2026-07-18
- **Branch:** `wo/WO-HELM-HERMES-PRE-STAGE-B-BUILD-CONTEXT-OCI-PROVENANCE-AND-SAFETY-GATE-CORRECTIONS-IMPLEMENTATION-0001`
- **Base SHA:** `a780a16182c3035e1161ac396092e203aa0a5eac` (merge of PR#112; carries undeployed PR#103-#112)
- **Remote:** `git@github.com:maff0000/hermes.git`

## Scope assertion (bounded pre-Stage-B corrections)

Code + tooling + tests + design-prose correction ONLY. This WO:

- builds NO image (no `docker build`; fixtures/dry-runs/manifests only);
- replaces NO container, restarts nothing, deploys nothing, deploy-dark nothing;
- wires NO runtime, imports NO Phase-2 into an active runtime path;
- installs NO config, creates NO evidence mount, enables/executes NO shadow;
- implements NO N-1/N-2, adds NO Redis/SQL/migration;
- mutates NO `helm:state:hermes:current` (documented as a dependency on a separate HELM
  state-truth correction WO — FW-16 — but the record is NOT touched here);
- does NOT repair the PR#108 fabric anomaly (separate CA-authorised FW-17);
- changes NO reconnect authority / limiter / cooldown, consumes NO recovery request.

## Live-runtime non-mutation (verified context, unchanged)

- deploy `71ea3bd`; container `d80018037b7f`; image `c5fc2a62f424`; restarts 0; runners 9;
  `consumer_live=false`. This WO reads only; it makes ZERO runtime changes.

## R2D2 ruling honoured

OCI provenance labels are SUBSTANTIVELY BLOCKING for future candidate images. The PR#112 architecture
line (~§19) that called the source-SHA label "not blocking" is corrected to scope that observation to
the currently-running LEGACY image only. Models T-DOCKERIGNORE / T-SBOM-SCAN / FW-08 / FW-19 already
treated labels as Stage-B blocking; the prose is now consistent with them.
