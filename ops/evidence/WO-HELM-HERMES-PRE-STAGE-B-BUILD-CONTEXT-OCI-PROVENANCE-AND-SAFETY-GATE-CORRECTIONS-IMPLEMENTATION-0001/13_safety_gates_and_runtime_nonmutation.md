# Safety gates + runtime non-mutation

## §10 Socket-ambiguity gate (`T-SOCKET-AMBIGUITY`) — behavioural, on merged inert modules

Exercised against `utils/hermes_sss_*` + `utils/hermes_shared_stream_recovery_v1`:

| Case | Result | Assertion |
|------|--------|-----------|
| connected + heartbeat fresh + FX flowing | `TRANSPORT_HEALTHY`, `RECOVERY_PROPOSAL_ONLY`, reconnect **not** authorised | socket CONNECTED ≠ authorised — connectivity alone did not force reconnect |
| connected + heartbeat unavailable + INCOMPLETE | `EVIDENCE_INCOMPLETE`, shadow reconnect False | connected + unavailable-heartbeat = incomplete (fail-closed) |
| connected + heartbeat hard-stale | `SILENT_UNCONFIRMED`, reconnect not authorised | stale ≠ healthy |
| connected + auth-failure | `AUTH_FAILED` | authority from the fault, not from connectivity (fault-governed) |
| connected + contradictory evidence | `EVIDENCE_CONFLICT`, reconnect False | fail-closed |
| executor stand-in | `RefusingShadowExecutor.refuse` raises `ShadowExecutionForbidden` | socket alone can neither authorise nor deny as-if-proven |

## §11 Historical-inference gate (`T-HISTORICAL-INFERENCE`) — July-16 fixture

Fixture: `tests/fixtures/sss_phase2/july16_snapshot.json`.

- **STRICT:** heartbeat unavailable → `EVIDENCE_INCOMPLETE` → reconnect **unauthorised**; no silent
  inferred heartbeat substituted (provenance marked `UNAVAILABLE`).
- **INFERENCE-permitted:** heartbeat carries `JUSTIFIED_INFERENCE` preserved through
  snapshot→mapping→decision→comparison→record; shadow action `RECOVERY_PROPOSAL_ONLY`; comparison
  `SHADOW_DENIES_CURRENT_RECONNECT`.
- **Never relabelled:** inferred heartbeat fields never serialise as `DIRECTLY_OBSERVED`.
- **Relabel-reject:** the gate helper detects a dishonest promotion of an inferred heartbeat field to
  `DIRECTLY_OBSERVED` (returns True → gate FAILS).

## §20 HELM state dependency (NOT mutated here)

`helm:state:hermes:current` is NOT touched by this WO. The deployment-truth correction (distinguish
canonical-SHA `adc21c4d` from deployed-SHA `71ea3bd`; retire stale `deployed_runtime=f69df68`) is a
separate HELM state-truth correction WO (**FW-16**). This WO only documents the dependency.

## Runtime non-mutation (read-only)

No image built, no container replaced/restarted, no deploy, no config install, no evidence mount, no
shadow enable, no Redis/SQL/migration, no reconnect-authority/limiter/cooldown change, no recovery-request
consumption, no other app edited, no `/etc`/host-global write. Live runtime (`71ea3bd` /
`d80018037b7f` / `c5fc2a62f424`, restarts 0, `consumer_live=false`) is unchanged. `IMAGE_BUILT=false`.
No new third-party dependency (tools are stdlib-only; verified by `test_tools_are_stdlib_only`).
