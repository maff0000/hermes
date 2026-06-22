# Work Order — HERMES Production Shadow Burn Execution

**WO id:** `WO-HERMES-PROD-SHADOW-BURN-001`
**Title:** Production Shadow Burn — M5/H1 engine against LIVE production traffic, canonical truth DARK
**Status:** **DRAFT — NOT AUTHORISED FOR EXECUTION**
**Persona / lane:** Helm (HERMES — standalone market-data service)
**Independent validation lane:** R2D2-HERMES
**Base SHA:** `b170335` (`origin/main` canonical HEAD at draft time)
**Runtime code-state (for reference only — NOT a deploy target of this WO):** bare systemd `@ ed07173`
**Date (UTC):** 2026-06-22
**Author:** Helm (HERMES)

---

> ## SAFETY BANNER — READ FIRST
>
> **This is a DRAFT Work Order for review. It authorises NOTHING.**
> No deployment, no systemd change, no SSH, no Redis write, no env mutation is performed or implied by
> the act of writing this document. Execution is **blocked** until (1) the configurable shadow-prefix
> code prerequisite (§5.1) is implemented **and** R2D2-audited, (2) the **H1 independent validation**
> (still OPEN — §6, §8) is GREEN, and (3) the Architect grants **explicit, separate** authorisation for
> writes against the **production** Redis keyspace (§8). Canonical `hermes:candles:*` **MUST remain
> completely DARK** for the entire burn (§4). The shadow keyspace prefix this WO targets
> (`hermes:shadow:prod:candles:*`) **does not yet exist in the writer** — see §5.1.

---

## 1. Header summary

| Field | Value |
|-------|-------|
| WO | `WO-HERMES-PROD-SHADOW-BURN-001` |
| Type | Governed execution WO (DRAFT) |
| Lane | Helm / HERMES |
| Base SHA | `b170335` (`origin/main`) |
| Sink mode | `shadow` ONLY (canonical sink remains DARK) |
| Target Redis | **production** `proteus-redis` — host port **6379** (docker-proxy), non-loopback |
| Target keyspace | `hermes:shadow:prod:candles:{instrument}:{M5|H1}:latest:v1` |
| Canonical keyspace | `hermes:candles:*` — **DARK / untouched / count must stay 0** |
| Burn horizon | default **48h**, time-bounded, auto-expiring (§3) |
| Prerequisites | configurable shadow prefix (NOT done), H1 independent validation (OPEN), explicit prod authorisation (NOT given) |

---

## 2. Purpose & context

The HERMES shadow candle pipeline forwards closed M5/H1 candles (DIRECT_FROM_SOURCE, OANDA
full-precision float) to a shadow Redis keyspace while the canonical `hermes:candles:*` namespace stays
**DARK** — a true zero-blast-radius shadow path. It was **activated and validated in DEV-SHADOW only**.

**Consolidated dev baseline** (`helm:evidence:hermes:candle_shadow_activate_dev:consolidated:v1`):

- **140 / 140 zero-drop bucket parity** (M5 = 126, H1 = 14), **0 drops / 0 extras**, across **14
  instruments** (AUD_USD, EUR_GBP, EUR_USD, GBP_USD, NZD_USD, SPX500_USD, USD_CAD, USD_CHF, USD_JPY,
  WTICO_USD, XAG_USD, XAU_USD, XCU_USD, XPT_USD).
- **Max OHLCV delta = 5.0e-06**, exactly the **half-ULP of the legacy `decimal(12,5)` SQL columns**. The
  shadow preserves OANDA full-precision float; the legacy column rounds to 5dp. The shadow is therefore
  the **higher-fidelity** record — this residual is a storage-representation difference, not a fault.
- **Telemetry: 0 `CANDLE_VALIDATE_FAIL` / 0 `CANDLE_EMIT_FAIL` / 0 `_log_fail`** to the SIEM.
- **R2D2 independent validation:** M5 leg `GREEN_M5_INDEPENDENTLY_VERIFIED_ZERO_FAULT`
  (`r2d2:audit:hermes:shadow_candle_independent_validation_m5:20260622:v1`). **H1 independent validation
  is OPEN.**

**Why a production burn.** Dev traffic is thin. Before any canonical cutover the engine must be exercised
against **LIVE production OANDA market volume** — full instrument mix, real top-of-hour H1 crossovers,
real tick rates and burst patterns — to measure hot-path latency overhead, OANDA float-precision drift at
scale, and Redis memory behaviour over a sustained window. This WO defines that burn **with canonical
truth held DARK throughout**: production consumers see nothing change; only the shadow keyspace fills.

---

## 3. Burn horizon

A **strict, time-bounded, auto-expiring** window. There are **no open-ended runs**.

| Item | Specification |
|------|---------------|
| Default duration | **48 hours** (configurable per execution authorisation) |
| Anchoring | **UTC only.** `BURN_START_UTC` and `BURN_STOP_UTC` are explicit ISO-8601 Z timestamps recorded at arm time. No `NOW()`, no naive datetimes, no broker-time. |
| Auto-expiry | The burn **must self-terminate** at `BURN_STOP_UTC` regardless of operator presence. A watchdog disarms the forward seam (sets `HERMES_CANDLE_FORWARD_ENABLED=false` + restart) at `BURN_STOP_UTC`; absence of a live disarm is itself a fail-loud condition. The window cannot silently extend. |
| Arm authority | **Helm** arms, on **explicit Architect authorisation only** (§8). Arm = set the env contract (§5.2) and restart the service. |
| Disarm authority | **Helm** disarms (manual kill-switch §7) **or** the watchdog auto-disarms at `BURN_STOP_UTC`. R2D2 may signal NO-GO mid-burn (§6) which obligates Helm to disarm immediately. |
| Feed routing | Live incoming **OANDA** feeds pipe into **production** Redis `:6379` **ENTIRELY** under the shadow keyspace prefix `hermes:shadow:prod:candles:*`. Canonical `hermes:candles:*` receives nothing. |
| Start criteria | All §5 prerequisites GREEN; canonical pre-burn key count = 0; R2D2 pre-burn GO. |
| Stop criteria | `BURN_STOP_UTC` reached (normal), **or** any invariant breach (§4), **or** R2D2 NO-GO (§6), **or** manual kill-switch (§7). |

---

## 4. Invariant boundary (CRITICAL)

**The canonical namespace `hermes:candles:*` MUST remain completely DARK and untouched by this engine for
the entire burn.** This is the master invariant; its breach is an immediate, non-negotiable STOP.

**Enforcement (layered, fail-loud):**

1. **Sink = `shadow` only.** `HERMES_CANDLE_FORWARD_SINK=shadow`. The canonical sink path is never
   selected. Any attempt by this engine to write a `hermes:candles:*` key must raise the canonical-sink
   fail-loud fault **`FAULT_WRITE_FORBIDDEN`** and abort the emit (and the burn), not silently degrade.
2. **Prefix isolation.** Every key written carries the `hermes:shadow:prod:candles:` prefix (§5.1). The
   writer constructs keys from the configured prefix only; there is no code path from the shadow seam to
   the canonical prefix.
3. **Continuous invariant check.** A separate monitor polls `DBSIZE`-scoped canonical cardinality:
   **`COUNT(hermes:candles:*)` must stay exactly 0** for the entire window. The first non-zero reading is
   a fail-loud RED → immediate disarm (§7) and burn invalidation. The pre-burn baseline count (0) is
   recorded at arm time; any drift from 0 is a breach regardless of source.
4. **Read-back discipline.** Production Redis writes to the shadow prefix are themselves
   owner-authorisation-gated (§5.4); the engine never deletes or mutates anything outside its own shadow
   prefix.

> The burn proves the engine against live volume **without ever touching production truth**. If the
> canonical count leaves 0 for any reason, the run is void and is treated as a real fault to be
> root-caused, not waved through.

---

## 5. Prerequisites (code + config — NOT yet done)

> **None of the following is implemented or authorised by this WO.** They are gating prerequisites the
> WO records honestly. Execution cannot begin until 5.1 ships + is R2D2-audited, 5.3/H1 validation is
> GREEN, and 5.4 explicit authorisation is granted.

### 5.1 Configurable shadow-prefix parameter — **REQUIRED CODE PREREQUISITE (NOT IMPLEMENTED)**

**Design gap — flagged honestly.** The burn targets the **NEW** shadow keyspace prefix
`hermes:shadow:prod:candles:*`, but **the current writer emits `hermes:shadow:candles:*`** (no `prod`
segment — see dev baseline keys `hermes:shadow:candles:{instrument}:{M5|H1}:latest:v1`). The `prod`
segment **does not exist** in the writer today.

- **Required change:** introduce a **configurable shadow key-prefix parameter** (e.g. an env
  `HERMES_CANDLE_FORWARD_SHADOW_KEY_PREFIX` consumed by the writer when constructing keys), defaulting to
  the existing `hermes:shadow:candles` for backward compatibility, settable to `hermes:shadow:prod:candles`
  for this burn.
- **Status:** **NOT IMPLEMENTED.** This WO does **not** implement it. It must be built under its own WO,
  then **R2D2-audited** (structural + key-construction + backward-compat) **before** the burn is armed.
- **Acceptance for the prerequisite:** writer constructs keys solely from the configured prefix; default
  preserves dev behaviour bit-for-bit; canonical prefix remains unreachable from the shadow seam
  (`FAULT_WRITE_FORBIDDEN` still holds).

### 5.2 Env contract for a prod-6379 shadow target (exact names)

| Env var | Burn value | Notes |
|---------|-----------|-------|
| `HERMES_CANDLE_FORWARD_ENABLED` | `true` | master seam enable; flips to `false` to disarm (§7) |
| `HERMES_CANDLE_FORWARD_SINK` | `shadow` | shadow ONLY — canonical sink never selected (§4) |
| `HERMES_CANDLE_FORWARD_SHADOW_REDIS_HOST` | `<prod redis host>` | **production** `proteus-redis`, **non-loopback** docker-proxy host |
| `HERMES_CANDLE_FORWARD_SHADOW_REDIS_PORT` | `6379` | production Redis host port |
| `HERMES_CANDLE_FORWARD_SHADOW_REDIS_DB` | `<db index>` | shadow db on prod instance |
| `HERMES_CANDLE_FORWARD_SHADOW_AUTHORISED` | `true` | **must be true** — hard gate |
| `HERMES_CANDLE_FORWARD_SHADOW_TREAT_AS_PRODUCTION` | `true` | **set deliberately** — this is a real production target; declare it as such, do not leave the default `false` |
| `HERMES_CANDLE_FORWARD_SHADOW_DEV_SHADOW` | `false` (default) | **NOT set** — only required when host is loopback |
| `HERMES_CANDLE_FORWARD_SHADOW_KEY_PREFIX` | `hermes:shadow:prod:candles` | **depends on 5.1** (NOT yet implemented) |

**Loopback guard `GOV-CANDLE-PUB-SHADOW-003`:** rejects a loopback host when `TREAT_AS_PRODUCTION` is set
without `DEV_SHADOW`. Production Redis on `:6379` is a **real, non-loopback** docker-proxy target, so this
guard is **not triggered** by the burn config. `TREAT_AS_PRODUCTION=true` is nonetheless set **deliberately**
to declare the production nature of the target (the opposite of the dev run, which used a loopback host +
`DEV_SHADOW=true` to satisfy the guard).

### 5.3 TTL sizing

`:latest:v1` keys are bounded by `redis_ex_seconds` (dev: **M5 = 360s**, **H1 = 3900s**). For the prod
burn, TTLs must be **re-sized for production cadence and reviewed** so that (a) keys self-expire (bounding
cardinality and memory — §6c), (b) no premature eviction inside a bucket period (R2D2 confirmed no
premature eviction in dev at M5=360 / H1=3900). The chosen prod TTLs are recorded at arm time and audited.

### 5.4 Separate explicit owner authorisation

Writes (and any deletes) against the **production** Redis keyspace are **guarded**. The burn requires a
**separate, explicit Architect authorisation** for production-keyspace writes — distinct from authorising
this WO. No production write occurs on the strength of this document alone. (Standing rule: deletes/writes
to prod keyspace are owner-gated; no silent global fallback.)

---

## 6. R2D2 validation criteria

R2D2-HERMES holds **independent** measurement authority. R2D2 does not trust HELM counters; it re-measures
in its own process. The dev pattern (cold re-validate keys, recompute deltas) carries forward. R2D2 must
also close the **still-OPEN H1 independent validation** (§8) as part of accepting the burn.

### (a) Latency overhead of the shadow emit on the hot path

- **Measure:** per-candle **emit duration** on the tick→candle hot path, reported as **p50 / p95 / p99**
  (and max), separated by timeframe (M5, H1).
- **Impact:** measure the burn-induced delta on **tick→candle processing latency** — baseline (seam
  disabled) vs burn (seam enabled) on the same live volume window.
- **Budget:** define a hot-path overhead **delta budget** (e.g. a bounded p99 increase) agreed with the
  Architect at arm time; p99 emit overhead exceeding budget, or any measurable degradation of canonical
  tick→candle processing, is **NO-GO**. The shadow emit must never starve the live signal path.

### (b) OANDA float-precision drift

- **Reuse the dev baseline.** Per-bucket OHLCV deltas of shadow vs the legacy `decimal(12,5)` record must
  stay **within the half-ULP = 5.0e-06**.
- **Definitive canonical test:** `legacy == round(shadow, 5dp)` using **ROUND_HALF_UP** must be **TRUE**
  for every non-bit-identical bucket. (In dev R2D2's first-pass strict float comparison false-RED'd 4
  buckets at exactly half-ULP; the `Decimal` round-trip is the authoritative test.)
- **Track:** **exact-match count** vs **rounding-explained count**; **any delta exceeding half-ULP is a
  real fault** (RED), not storage rounding.

### (c) Memory saturation

- **Redis memory growth** under the `hermes:shadow:prod:candles:*` prefix over the full 48h.
- **Cardinality bound:** keys are `:latest:` semantics, so cardinality is bounded by
  **instruments × timeframes** (with TTL self-expiry). Expected steady-state ≈ (N instruments × 2
  timeframes) live keys, **not** unbounded growth. Any monotonic upward drift in key count or memory is a
  RED (points to a leak / TTL miss / prefix collision).
- **Headroom:** track host/container memory headroom against the **4 GB cap**; growth must stay well
  inside it with comfortable margin for the whole window.

### Zero-drop parity, telemetry, and GO/NO-GO

- **Bucket parity:** target **100%** zero-drop (as in dev) — every closed legacy bucket has a matching
  shadow key, 0 drops / 0 extras, across the live instrument set.
- **Telemetry:** must stay **0 `CANDLE_VALIDATE_FAIL` / 0 `CANDLE_EMIT_FAIL` / 0 `_log_fail`**.

**GO / NO-GO matrix:**

| Criterion | GO | NO-GO |
|-----------|----|-------|
| Canonical invariant (§4) | `COUNT(hermes:candles:*) == 0` whole window | any non-zero reading |
| Bucket parity | 100% zero-drop, 0 extras | any drop or extra |
| OHLCV precision | all deltas ≤ 5.0e-06; `legacy == round(shadow,5dp)` HALF_UP TRUE | any delta > half-ULP |
| Hot-path latency | p99 emit overhead within agreed budget; no tick→candle degradation | budget breach / measurable degradation |
| Memory | bounded cardinality + memory inside 4 GB cap with margin; no monotonic growth | unbounded growth / cap pressure |
| Telemetry | 0 / 0 / 0 failures | any failure shipped to SIEM |
| H1 independent validation | R2D2 H1 leg GREEN (closes the OPEN item) | H1 validation absent or RED |

**Any single NO-GO obligates immediate disarm (§7) and burn invalidation.**

---

## 7. Rollback / kill-switch

**Disarm is instant and always available.**

1. **Env toggle to disabled:** set `HERMES_CANDLE_FORWARD_ENABLED=false` (sink falls inert) and **restart**
   the service. The forward seam stops emitting immediately; the live signal path is unaffected (the seam
   is additive — disabling it removes only the shadow emit).
2. **Auto-expiry watchdog:** at `BURN_STOP_UTC` the watchdog performs the same disarm automatically, so the
   burn cannot outlive its window even if no operator acts (§3).
3. **TTL-based self-cleaning:** shadow keys carry `redis_ex_seconds` TTLs (§5.3); after disarm the
   `hermes:shadow:prod:candles:*` keys **self-expire** and the keyspace drains on its own — no manual delete
   against the production instance is required (and any delete there is owner-gated regardless, §5.4).
4. **Trigger conditions:** invariant breach (§4), any GO/NO-GO NO-GO (§6), Architect instruction, or
   operator judgement. Kill-switch first, root-cause second.

---

## 8. Sign-off

| Role | Party | Status |
|------|-------|--------|
| Build (engine, shadow seam, configurable prefix §5.1) | **Helm (HERMES)** | **PENDING** — configurable shadow-prefix parameter NOT yet implemented; this WO is a DRAFT only |
| Independent validation — M5 leg | **R2D2-HERMES** | dev baseline `GREEN_M5_INDEPENDENTLY_VERIFIED_ZERO_FAULT` (carries into burn acceptance) |
| Independent validation — **H1 leg** | **R2D2-HERMES** | **OPEN** — must be closed GREEN before/within burn acceptance |
| Production-keyspace write authorisation | **Architect (Matt)** | **NOT GIVEN** — separate explicit authorisation required (§5.4) |
| Burn execution authorisation | **Architect (Matt)** | **NOT GIVEN** — this document authorises nothing |

**Outstanding before execution (explicit):**
1. Configurable shadow-prefix parameter (`hermes:shadow:prod:candles:*`) **implemented and R2D2-audited** — NOT done (§5.1).
2. **H1 independent (R2D2) validation** — still **OPEN** (§6, baseline §8).
3. **Architect authorisation** for the burn AND for production-keyspace writes — **NOT GIVEN** (§5.4, §8).

---

*Provenance:* `helm:evidence:hermes:candle_shadow_activate_dev:consolidated:v1` (dev baseline 140/140) ·
`r2d2:audit:hermes:shadow_candle_independent_validation_m5:20260622:v1` (M5 independent GREEN; H1 OPEN) ·
dev activation evidence `ops/evidence/WO-HELM-HERMES-CANDLE-FORWARD-SHADOW-ACTIVATE-DEV-0001/activation_evidence.md`.
Base SHA `b170335`. **DRAFT — NOT AUTHORISED FOR EXECUTION.**
