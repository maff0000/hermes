# Evidence index — WO-HELM-HERMES-SHARED-STREAM-RECOVERY-CONTRACT-DESIGN-0001

- Architecture doc (all 24 tasks incl. decision matrix §13, acceptance criteria §14, risk register §24):
  ../../../docs/design/shared_stream_recovery/architecture_v1.md
- Decision-envelope JSON schema: ../../../schemas/shared_stream_recovery/decision_envelope.v1.schema.json
- Inert pure decision core: ../../../design/shared_stream_recovery_contract_v1.py
- Fixture tests (20+ incl. July 16): ../../../tests/test_shared_stream_recovery_contract_v1.py
- current_recovery_path_trace.md — the defect call/state chain (grounded 71ea3bd)
- july16_reconnect_chronology.md — the fixture-of-record timeline
- inertness_proof.txt — grep proof: core not imported by any runtime path
- test_output.txt — pytest -v capture (25 passed)
- verdict.txt — GREEN
