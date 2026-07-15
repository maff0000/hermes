# Recovery-relevant scope (the exact object that is hashed)

Fixture: H1 gap @06:00; one adjacent covered candle @05:00 (inside window).

```json
{
  "H1": {
    "retained_gaps": [
      [
        1784008800,
        1784012400
      ]
    ],
    "relevant_coverage": [
      [
        1784005200,
        1784008800
      ]
    ],
    "authority": [
      "governed_redis_history_index:RETENTION_BOUNDED:35d",
      "v1"
    ]
  }
}
```

Endpoints are UTC epoch seconds. `authority` = [provenance (encodes retention classification), contract_version].
Only timeframes with a retained gap appear; unrelated timeframes/candles are absent by construction.
