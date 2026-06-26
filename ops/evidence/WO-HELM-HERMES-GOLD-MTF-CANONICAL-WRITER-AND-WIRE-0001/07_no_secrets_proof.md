# No-Secrets Proof

- No secrets in code, tests, evidence, or fabric. The canonical bus host/port/db are **env-driven
  pointers**, never literal secrets, and no credential is required for a Redis `SET`.
- Evidence payload samples are synthetic OHLC (2000/2010/1995/2005) — no real market data, no tokens.
- Scan: `grep -rniE 'password|secret|token|webhook|api[_-]?key|PRIVATE KEY'` over the diff + evidence
  returns only the benign test node id `test_forbidden_field_token_rejected` (the word "token" in a test
  name), no actual secret value.
