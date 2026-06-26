# No-Activation / No-Cross-Lane Proof

- **Code-only.** No deploy, no restart, no env change on any host, no Redis writes (tests use in-memory
  fakes), no SQL, no backfill, no shadow/canonical activation, no consumer cutover.
- **Lane:** changes confined to `utils/candle_contract_v1.py`, `utils/candle_runtime_seam_v1.py` + candle
  tests. No `main.py`/`signal_builder.py` change. No ARES/HELIOS/FALCON/SOLO/NEO/Proteus/structure_engine.
- **No H4/D1 publication, no regime, no unversioned keys** — all still enforced (writer guards + grid).
- **No secrets** in code/tests/evidence/fabric.
- The live `hermes-signal-dev` remains as left by the prior WO: merged image, candle-forward DISABLED.
  Re-activation is a separate step after this fix merges.
