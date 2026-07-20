# §6 Original secret-scanner self-match reproduction (value NOT disclosed)

WO-HELM-HERMES-PR114-FW08-EXACT-AUDIT-CORRECTIONS-AND-CANONICAL-PREFLIGHT-CLOSURE-0001

HELM independently reproduced the R2D2 self-match by running the PR#113 clean-context tool against the
EXACT canonical base `fe2037c5b4b4836d1038b4c0f734426ac9b3fa02`:

```
$ python3 tools/hermes_clean_build_context_v1.py \
    --source-sha fe2037c5b4b4836d1038b4c0f734426ac9b3fa02 --output-dir <tmp>/ctx --repo-dir .
result: FAIL
exit_code: 3
secret_findings: [{"path": "tools/hermes_clean_build_context_v1.py", "rule_id": "SEC-PRIVATE-KEY-PPK"}]
exported_file_count: 1198
```

- Rule: `SEC-PRIVATE-KEY-PPK`
- Path: `tools/hermes_clean_build_context_v1.py`
- Matched value: **NOT DISCLOSED** — the scanner's own PPK rule-pattern literal self-matches inside the
  scanner source file. This is a governed rule-definition self-match (a non-secret), NOT a credential. The
  value is never stored in code, evidence, or logs.
- Governed fingerprint (value-free): `7d787547a38db3584bdb6aa7955b9484262d93cffd738ca1dfee8c9f2049f716`
