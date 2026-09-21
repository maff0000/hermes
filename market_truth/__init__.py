"""
market_truth — HMT-1 Canonical Market Event & Replay Foundation.

HERMES "Market Truth v2" programme. Implements, for the first time as running code, the
architecture ruled in the HMT-0 documentation pack at
`docs/architecture/hmt0-market-truth-v2/`:

    identical governed source bytes + identical canonicaliser/mapping/schema versions
    = identical canonical events + event identities + research partitions + partition hashes,
      on every replay.

Scope (HMT-1 only — see docs/architecture/hmt0-market-truth-v2/hmt1-provisional-scope.md):
  - Provider-neutral canonical event contracts (contracts.py).
  - A single deterministic fixture provider (providers/fixture.py) — no live network/credential
    dependency anywhere in this package or its tests.
  - A governed event-identity algorithm (identity.py).
  - A GC actual-contract identity + explicit provider->canonical symbology mapping (futures.py).
  - A research-partition writer/reader over local disposable storage (partition.py).
  - A deterministic replay harness (replay.py) and evidence manifest (evidence.py).
  - A derived-fact identity/provenance SKELETON ONLY (derived_fact_identity.py) — no real P0
    microstructure fact (volume-at-price/POC/VAH/VAL/delta/CVD/absorption/etc.) is computed here.

Explicitly NOT in scope for this package (see the HMT-1 work order for the full list): the HMT-2
research corpus and its acquisition, any live provider network call or credential, any Vantage,
MBO, DARWIN, or ATHENA work of any kind, legacy candle-table retirement, and PROD promotion.
"""
