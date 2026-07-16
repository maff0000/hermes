# HERMES XAU Market-Transition Evidence Validator (v1)

**WO:** WO-HELM-HERMES-MARKET-TRANSITION-EVIDENCE-VALIDATOR-0001
**Status:** INERT / read-only / NOT wired into any runtime, compose, systemd, or `main.py`.
**Owner lane:** HELM (HERMES market-data).

## Purpose

Turn a raw XAU market-transition observer log (produced by
`xau_transition_observer.sh`) into a **stable, deterministic, machine-readable
audit verdict**, so the weekly-break / daily-rollover transition can be
**reproducibly signed off**. It does **not** replace, modify, or wire into the
observer — it is a downstream, offline auditor.

## Doctrine

- **INERT.** Reads only: the observer log, `config/market_hours_schedule.v1.json`,
  and an optional runtime-identity JSON. Never writes to the observer, runtime,
  Redis, SQL, or the network. Pure Python stdlib.
- **Never infer missing evidence as success.** Every acceptance condition is one
  of `PASS` / `FAIL` / `INDETERMINATE` / `NOT_OBSERVED`.
- **The governed closure window is computed here, independently and DST-aware,**
  from the schedule config. Boundary values that appear inside the log are **not
  trusted** — the log is audited *against* the computed governed window.
- UTC only. Project-relative paths. No secrets.

## CLI

```bash
python3 tools/hermes_transition_evidence_validator_v1.py \
  --log <observer.log> \
  --schedule-config config/market_hours_schedule.v1.json \
  --instrument XAU_USD \
  --date 2026-07-16 \
  --cadence-seconds 60 \
  [--grace-seconds 300] \
  [--runtime-identity runtime_identity.json] \
  [--output verdict.json] [--pretty]
```

Exit code: `0` = PASS, `1` = INDETERMINATE, `2` = FAIL. Verdict JSON is printed
to stdout (and optionally written to `--output`). The output validates against
`schemas/transition_evidence/transition_evidence_verdict.v1.schema.json`.

`--grace-seconds` defaults to the schedule's `reopening_grace_seconds`.

## Governed window computation (DST-aware)

For `XAU_USD` the schedule resolves to the `metals` named schedule with a daily
break `17:00–18:00` in `America/New_York`. The validator localises the break to
the requested `--date` using `zoneinfo` and converts to UTC:

| Date season | Local break | Closure (UTC) | Reopening | Grace end (+300s) |
|-------------|-------------|---------------|-----------|-------------------|
| July (EDT, UTC−4) | 17:00–18:00 | 21:00–22:00 | 22:00 | 22:05 |
| January (EST, UTC−5) | 17:00–18:00 | 22:00–23:00 | 23:00 | 23:05 |

Fail-loud instruments (`WTICO_USD`, `SPX500_USD`) resolve to **no governed
schedule** (`FAIL_CLOSED_NONE`); the validator computes no closure window for
them and treats any log claim that they are *closed* as a fault (see below).

## Canonical observer-log line grammar (v1)

Every non-blank line begins with an outer UTC bracket `[<UTC>]`. `<UTC>` is
either a full ISO-8601 instant (`2026-07-16T21:00:00Z`, authoritative) or a bare
`HH:MM:SSZ` wall-clock combined with `--date`.

**Marker lines**

```
[<UTC>] OBSERVER START (pid <N>). <free text>
[<UTC>] === ENTERING OBSERVATION WINDOW ===
[<UTC>] === WINDOW COMPLETE ===
[<UTC>] D1 latest after seal: <ts> <FRESHNESS>
[<UTC>] OBSERVER END (pid <N>).
[<UTC>] HEALTH RESTORED <free text>                                  (optional)
[<UTC>] FAIL-LOUD <INSTR>: schedule=<S> state=<STATE> reason=<REASON> (optional)
```

**Sample lines** (five pipe-delimited groups)

```
[<UTC>] <HH:MM:SSZ> | phase=<STATE> truth_expected=<bool> reason=<REASON> \
  | M1=<ts> <FRESHNESS> \
  | quote=<STATUS>/<FRESHNESS> age=<float> \
  | feed=<STATUS>/<FRESHNESS> conn=<CONN> fault=<STATE> M1age=<int> \
  | incidents=<int> reconnect=<int> recovery=<int> false_close_log=<int>
```

Tokens: `FRESHNESS ∈ {FRESH, STALE, NONE}`; quote `STATUS ∈ {OK, ERROR, MISSING}`;
feed `STATUS ∈ {OK, STALE, DOWN}`; `CONN ∈ {UP, DOWN}`; `fault ∈ {NONE, STALE, DOWN}`.
Counters are cumulative. `M1=-` / `M1=NONE` denotes no candle. Unknown
`phase`/`reason` tokens are preserved verbatim; a sample line that fails the
five-group shape is recorded as a `malformed_line`, never silently dropped.

### Derived quantities

- **Effective tick instant** `= outer_ts − age`. A genuinely frozen feed keeps
  this constant (age grows with wall clock). A fabricated refresh (age reset)
  advances it — the fabrication signal.
- **Window partition** of each sample by governed boundaries: `pre_close`,
  `closure`, `grace`, `post_grace`.

## Acceptance conditions

`overall = PASS` requires **all** required conditions `PASS` and **no** condition
`FAIL`. Any `FAIL` ⇒ `overall = FAIL`. Otherwise any required
`INDETERMINATE`/`NOT_OBSERVED` ⇒ `overall = INDETERMINATE`.

| Condition | PASS means | FAIL means |
|-----------|------------|------------|
| `END_MARKER_PRESENT` | OBSERVER END seen | *(absent ⇒ INDETERMINATE — incomplete, never PASS)* |
| `REQUIRED_WINDOW_COVERED` | samples span pre-close → closure → past grace_end | coverage incomplete ⇒ INDETERMINATE |
| `SAMPLE_CONTINUITY` | monotonic, no gap > 3× cadence | gap > 3× cadence or non-monotonic |
| `CLOSURE_CLASSIFICATION` | every closure sample `MARKET_CLOSED_EXPECTED` / `truth_expected=false` | any closure sample marked open |
| `NO_FABRICATED_FRESHNESS` | M1 candle & effective tick frozen through closure | candle or tick timestamp advanced during closure |
| `NO_INACTIVITY_INCIDENT_DURING_CLOSURE` | incident counter unchanged across closure | incident fired during governed closure |
| `NO_CLOSURE_DRIVEN_RECONNECT` | reconnect counter unchanged across closure | reconnect churn during closure |
| `CORRECT_GRACE_TIMING` | no escalation before `grace_end` | escalation fired inside the grace window |
| `NO_FALSE_GREEN_DURING_GRACE` | no healthy/GREEN claimed in grace without genuine flow | premature GREEN while M1 still frozen |
| `GENUINE_RESUMED_FLOW_OR_ESCALATION` | flow resumes (Path A) **or** correct post-grace escalation (Path B) | neither — silent stall |
| `RUNTIME_IDENTITY_CONTINUITY` | observer pid stable (and identity file start==end) | pid changed mid-window or identity snapshot changed |
| `REST_QUOTE_VS_STREAM_SEPARATION` | no FRESH REST quote masking a STALE stream with no fault | REST freshness masks a dead stream |
| `WTICO_FAIL_LOUD` | `WTICO_USD` fails loud (`schedule=None`, not closed) | guessed a schedule / marked closed |
| `SPX500_FAIL_LOUD` | `SPX500_USD` fails loud (`schedule=None`, not closed) | guessed a schedule / marked closed |

`WTICO_FAIL_LOUD` / `SPX500_FAIL_LOUD` are `NOT_OBSERVED` when no `FAIL-LOUD`
evidence line exists — `NOT_OBSERVED` does not block `overall`, but a `FAIL`
(a guessed-closed claim) does.

## Runtime identity

Continuity is checked from the OBSERVER START vs END `pid` (a mid-window change
means the container/process restarted). Optionally a `--runtime-identity` JSON
`{"start": {...}, "end": {...}}` is compared for equality; any difference ⇒ FAIL.

## Fixtures & tests

`tests/fixtures/transition_evidence_v1/*.log` — 16 synthetic scenario logs
(one healthy baseline + one defect each), regenerable via
`tests/fixtures/transition_evidence_v1/_generate.py`.
`tests/test_transition_evidence_validator_v1.py` asserts the overall verdict and
the specific target condition for each of the 16 scenarios, plus DST-window,
parsing, determinism, and schema-field-coverage unit tests.

Run:

```bash
python3 -m pytest tests/test_transition_evidence_validator_v1.py -q
```
