# Shadow Key Topology — versioned `:v1` only; canonical DARK

## Shadow keys (the only keys this PR writes, and only under explicit dev-shadow auth)
```
hermes:shadow:candles:{instrument}:{M1|M5|M15|H1}:latest:v1
```
Every key includes the `:v1` contract version and ends `:latest:v1`. Examples:
- `hermes:shadow:candles:XAU_USD:M1:latest:v1`
- `hermes:shadow:candles:XAU_USD:M5:latest:v1`
- `hermes:shadow:candles:XAU_USD:M15:latest:v1`
- `hermes:shadow:candles:XAU_USD:H1:latest:v1`

Proof: `test_every_shadow_key_is_versioned_v1` asserts every written key
`startswith("hermes:shadow:candles:")` **and** `endswith(":latest:v1")`, and that no canonical
`hermes:candles:*` key is written.

## Canonical stays DARK
`canonical_key()` still produces `hermes:candles:{instrument}:{tf}:latest:v1` for the **envelope's**
internal `key` field (the contract's identity), but the dev-shadow writer re-keys to the shadow prefix via
`to_shadow_key()` + `assert_shadow_key()` and **never** writes the canonical key. No canonical publish path
is wired or enabled by this PR. `signals:candle:*` (legacy Proteus) remains forbidden by
`GOV-CANDLE-CONTRACT-028`.

## Unversioned keys rejected
The publisher's `assert_shadow_key` guard (pre-existing, `GOV-CANDLE-PUB-KEY-*`) rejects any key not
matching the governed shadow shape, so an unversioned `hermes:shadow:candles:XAU_USD:M5:latest` can never
be written.
