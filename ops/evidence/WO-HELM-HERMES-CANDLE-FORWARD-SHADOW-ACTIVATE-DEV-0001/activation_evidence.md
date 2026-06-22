# Activation Evidence — HERMES Shadow Candle Pipeline (DEV)

**WO:** `WO-HELM-HERMES-CANDLE-FORWARD-SHADOW-ACTIVATE-DEV-0001`
**Artifact:** official activation evidence / development performance baseline
**Author:** Helm (HERMES lane) · **Independent validation:** R2D2-HERMES
**Compiled:** 2026-06-22 (UTC) · **Code state:** `ed07173` (= `origin/main`)
**Scope:** **DEV-SHADOW ONLY.** Canonical `hermes:candles:*` remained **DARK** throughout. This is a
development baseline — it is **not** a "candle truth restored" claim and does **not** retire the legacy
pipeline.

---

## 1. Purpose

Permanently record the pristine development performance baseline of the HERMES shadow candle pipeline,
captured at first activation, before the codebase expands further. The pipeline forwards closed M5/H1
candles (DIRECT_FROM_SOURCE) to dev Redis `hermes:shadow:candles:*` while the canonical namespace stays
dark — a true shadow run with zero blast radius on the live signal path.

## 2. Activation parameters

| Item | Value |
|------|-------|
| Deployed code | `ed07173` (fast-forward to `origin/main`; includes PR#31 `normalise_utc` fix) |
| Service | `signal-service-dev.service` (bare systemd, `/srv-dev/tradingSignals`) |
| Boot result | clean — `[CANDLE_FORWARD_BOOT] seam=ShadowCandleForwardSeam enabled=True`, no fail-loud |
| Sink | `shadow` → `proteus-redis-dev` `127.0.0.1:6380` db0, `DEV_SHADOW=true` |
| Activation (UTC) | 2026-06-22T10:17:30Z |
| Env override | 8× `HERMES_CANDLE_FORWARD_*` in `.env` (gitignored; `.env.*.bak` rollback retained) |
| Supported timeframes | M5, H1 only (DIRECT_NATIVE); M1/M15/D1/H4/D skipped UNSUPPORTED_TIMEFRAME |

## 3. Forward-window drift audit — HELM-measured

**Window:** 2026-06-22T10:17:30Z → 11:03:13Z (last poll), crossing the 11:00Z top-of-hour H1 close.
**Method:** poll all `hermes:shadow:candles:*:latest:v1` every 20s → dedupe to closed-bucket set; compare
against the legacy SQL baseline (`candles_M5`/`candles_H1`) scoped to closes inside the window; deltas
computed per (instrument, timeframe, bucket). 14 instruments enabled in dev.

| timeframe | legacy buckets | shadow buckets | **drops** | extras | **coverage** | max OHLCV Δ |
|-----------|---------------:|---------------:|----------:|-------:|-------------:|------------:|
| M5 | 126 | 126 | **0** | 0 | **100.00%** | 5.00e-06 |
| H1 | 14 | 14 | **0** | 0 | **100.00%** | 5.00e-06 |
| **Total** | **140** | **140** | **0** | **0** | **100.00%** | **5.00e-06** |

**Zero-drop bucket parity: 140 / 140.** Every legacy bucket across both timeframes produced a matching
shadow key; zero dropped, zero extra.

### Half-ULP rounding mechanics (the only OHLCV residual)

Across all 140 buckets the **maximum absolute OHLCV delta is exactly 5.0e-06** — precisely the half-ULP
of the legacy `decimal(12,5)` columns. The shadow contract preserves OANDA's full-precision float; the
legacy SQL column rounds to 5 decimal places. Instruments whose prices carry a 6th-decimal half-pipette
show Δ = 5.0e-06 (pure storage rounding); instruments without that artefact are bit-identical (Δ ≈ 1e-13).
**This is a representation difference, not a drop, calculation error, or UTC fault — and the shadow is the
higher-fidelity record** (correct, since HERMES owns raw market truth).

**14 verified instruments:** AUD_USD, EUR_GBP, EUR_USD, GBP_USD, NZD_USD, SPX500_USD, USD_CAD, USD_CHF,
USD_JPY, WTICO_USD, XAG_USD, XAU_USD, XCU_USD, XPT_USD.
Bit-identical (Δ≈0): SPX500_USD, USD_JPY, WTICO_USD, XAG_USD, XAU_USD, XPT_USD. Remainder: Δ ≤ 5.0e-06
(5dp storage rounding only).

## 4. Telemetry — silent Graylog SIEM

| event | count over full window |
|-------|-----------------------:|
| `CANDLE_VALIDATE_FAIL` | **0** |
| `CANDLE_EMIT_FAIL` | **0** |
| `_log_fail` routed to Graylog SIEM | **0** |

140 successful emits, **zero** failures shipped to the SIEM. The PR#31 `normalise_utc` fix processes
silently across both M5 and the H1 top-of-hour crossover. GELF resolves via the deployed
`resolve_gelf_target()` (legacy `GRAYLOG_HOST=192.168.11.10:12201` fallback; new `SIEM_GRAYLOG_IP` unset).

## 5. Independent validation — R2D2-HERMES

**Scope: M5 leg, independently cold-re-validated.** R2D2 did not trust HELM counters; it re-validated all
14 M5 keys in its own process and recomputed OHLCV deltas itself.

- **Verdict:** `GREEN_M5_INDEPENDENTLY_VERIFIED_ZERO_FAULT` (`r2d2:audit:hermes:shadow_candle_independent_validation_m5:20260622:v1`).
- Structural (14/14 pass), time-boundary (UTC-aware — the #31 fix holds; TTL `redis_ex_seconds(M5)=360` confirmed, no premature eviction), OHLCV parity: **global max abs delta 5.000e-06 = decimal(12,5) half-ULP**; definitive test `legacy == round(shadow, 5dp)` ROUND_HALF_UP **TRUE** for all non-identical buckets; **real faults: 0**. HELM thesis "confirmed to the number."
- Guardrails: canonical 6379 candles **0** (canonical DARK confirmed), independent re-validation failures **0**.
- R2D2 self-correction noted: its first-pass classifier false-RED'd 4 buckets via a strict float-boundary
  comparison at exactly half-ULP, corrected with a `Decimal` round-trip test — rigour cut both ways.

> **Honest scope caveat (recorded, not glossed):** R2D2's independent shadow-candle validation covers the
> **M5 leg (126 buckets)**. The **H1 leg (14 buckets) is HELM-measured only** — no independent R2D2 H1/
> consolidated validation key exists in fabric at compile time. The 140/140 figure is therefore
> **HELM-measured**, with **independent R2D2 corroboration on the M5 portion**. An H1 independent
> validation remains an open item (§8).

## 6. Audit lineage — AUDITED-GREEN & gate exception

- `retrospective_diff_ed07173:20260622:v1` first returned **AMBER** — the `eda0e1a..ed07173` range carried
  PR#32/#33 (container isolation + env-blind SIEM GELF) merged **without prior R2D2 audit** (process drift).
  It confirmed the candle path itself was unchanged beyond PR#31 and untracked tree was clean.
- `retro_audit_pr32_pr33:20260622:v1` then closed that gap: **`GREEN_PR32_PR33_RETRO_AUDITED_CLEAN` —
  audit gap CLOSED**, `main` code-state returns to **AUDITED-GREEN**. PR#33 `resolve_gelf_target` fail-loud
  STRUCTURAL ✓, backward-compat rock-solid ✓; PR#32 Dockerfile multi-stage/non-root/zero-leak ✓; compose
  mariadb air-gap + resource caps + no-new-privileges ✓.
- **Gate exception:** the #32/#33 fast-track (merge-before-audit) is **officially recorded in the
  Architect's ledger** as a formal gate exception; the technical drift was vindicated by execution quality.
  (Process reinforcement: design → PR → R2D2 audit → merge → deploy remains the standing order.)

## 7. LOW hardening notes — non-blocking technical debt

Classified by the Architect as non-blocking; to be swept in a fast follow-up refactor:

- **LOW-1** — `resolve_gelf_target()` does not `.strip()` the host; a whitespace-only host (`' '`) is
  truthy and bypasses the GOV-LOG-012 fail-loud (GELF is non-blocking UDP, so no boot crash). Fix:
  `host = (… or …).strip()`.
- **LOW-2** — compose `logging.gelf-address` falls back to `127.0.0.1` (loopback) as last resort, i.e.
  **fail-soft**, whereas the app `resolve_gelf_target()` is **fail-loud** (no loopback). Align: drop the
  loopback fallback or make it explicit.

## 8. Scope boundaries & remaining gates (NOT closed by this artifact)

- **Dev-shadow only**; canonical `hermes:candles:*` **DARK**; **legacy pipeline NOT retired** (Architect's call).
- Open: **H1 independent (R2D2) validation**; canonical `hermes:candles:*` publish; Falcon consumer cutover;
  legacy retirement; H4/M30 derivation; D anchor; backfill.
- Deployment reality: PR#32/#33 hardening exists **in design on `main`, not deployed** — runtime is still
  bare systemd `@ ed07173`; live dev MariaDB is still `proteus-mariadb-dev` host-exposed on `:3307`. The
  container air-gap is `DESIGN_COMPLETE_DEPLOYMENT_PENDING`, not live.

## 9. Provenance

| Source | Fabric key / path |
|--------|-------------------|
| HELM consolidated M5+H1 | `helm:evidence:hermes:candle_shadow_activate_dev:consolidated:v1` |
| HELM M5 leg | `helm:evidence:hermes:candle_shadow_activate_dev:m5_leg:v1` |
| R2D2 independent validation (M5) | `r2d2:audit:hermes:shadow_candle_independent_validation_m5:20260622:v1` |
| R2D2 retrospective (drift→AMBER) | `r2d2:audit:hermes:retrospective_diff_ed07173:20260622:v1` |
| R2D2 retro audit (#32/#33→GREEN) | `r2d2:audit:hermes:retro_audit_pr32_pr33:20260622:v1` |
| Raw capture (1,764 snapshots) | `/srv-dev/tradingSignals/ops/evidence/candle_shadow_activate/` (dell) |

> Note on the brief: the originally-referenced key
> `r2d2:audit:hermes:shadow_candle_independent_validation_consolidated:20260622:v1` does **not** exist; the
> authoritative R2D2 source is the **M5** key above. This artifact cites only keys that were actually read.

## 10. Sign-off

| Role | Party | Status |
|------|-------|--------|
| Build & activation | Helm (HERMES) | COMPLETE — evidence compiled |
| Independent validation (M5) | R2D2-HERMES | GREEN — zero-fault, corroborated to the number |
| H1 independent validation | R2D2-HERMES | **OPEN** |
| Gate exception (#32/#33) | Architect (Matt) | RECORDED in ledger |
| Legacy retirement authorisation | Architect (Matt) | **NOT GIVEN** (out of scope here) |

**Baseline statement:** at `ed07173`, the HERMES shadow candle pipeline achieved **140/140 zero-drop
bucket parity** (HELM-measured; M5 independently R2D2-verified) with **OHLCV agreement to within the legacy
`decimal(12,5)` storage precision** (max Δ = 5.0e-06 half-ULP) and **zero failure telemetry** to the
Graylog SIEM, across 14 instruments. This is the recorded development performance baseline.
