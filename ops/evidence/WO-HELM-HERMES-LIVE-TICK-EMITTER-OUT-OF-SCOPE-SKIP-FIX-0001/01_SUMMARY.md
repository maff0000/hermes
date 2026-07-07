# WO-HELM-HERMES-LIVE-TICK-EMITTER-OUT-OF-SCOPE-SKIP-FIX-0001 — HELM evidence

## Result: GREEN_LIVE_TICK_EMITTER_OUT_OF_SCOPE_SKIP_FIX_PR_READY_FOR_R2D2_AUDIT

## Defect (from activation AMBER)
Activation proved the LIVE canonical tick mechanism (key hermes:ticks:XAU_USD:latest:v1 went live+fresh+refreshing,
atomic gate==emitter==catalog, 6 runners, quote dark). BUT the OANDA stream carries many instruments (XPT_USD,
XAG_USD, GBP_USD, SPX500_USD, WTICO_USD, XCU_USD, ...) and main.py calls the emitter per-tick for ALL of them; the
emitter FAULTED + logged a WARN (GOV-HERMES-TICK-034) on each non-XAU tick (~10/s, unbounded fault counter). Safe
(non-XAU never published) but operationally unclean. Rolled back to dark. This PR fixes it.

## PR / branch / SHA
- branch: wo/WO-HELM-HERMES-LIVE-TICK-EMITTER-OUT-OF-SCOPE-SKIP-FIX-0001
- base SHA: b35687ac0d3cb3ccd775bf47cbd73f35b55b899e (origin/main, PR #78 merge)
- code head SHA: 308f9b502131bf03861cc705a2984b344f07c233

## Fix (utils/tick_live_emitter_v1.py)
- emit_tick SCOPE-SKIPS first: if _tick_instrument(tick) not in allowed_instruments -> return {"emitted": False,
  "reason": "OUT_OF_SCOPE", "instrument": ...} BEFORE any attempt/envelope/write. No Redis write, no fault, no WARN.
- skipped_out_of_scope counter (surfaced in status()) tracks the NORMAL skips; faults now reserved for genuine errors.
- new in_scope() + _tick_instrument() helpers (cheap read, no envelope build).
- GOV-HERMES-TICK-034 RETAINED in build_envelope as DEFENCE-IN-DEPTH (still raises if a direct/unsafe path bypasses the skip).

## Out-of-scope behaviour (exact)
- non-XAU instrument -> OUT_OF_SCOPE: no envelope, no Redis write, no fault counter increment, no WARN log
- in-scope XAU_USD -> build envelope + write canonical hermes:ticks:XAU_USD:latest:v1 (EX=10) when gates enabled

## Preserved (unchanged)
- canonical-only (XAU_USD -> hermes:ticks:XAU_USD:latest:v1, EX=10); no XAUUSD path; no duplicate hermes:tick:*
- LIVE gates HERMES_TICK_PUBLISH_ENABLED/AUTHORISED/INSTRUMENTS; enabled-without-authorised -> SystemExit(101); missing/invalid/XAUUSD scope -> fail loud
- dark by default (no client/no I/O); quote dark; tick catalog keystone + no split-brain; tick NOT a supervisor runner; shadow behaviour unchanged
- malformed XAU still fails loud; ask>=bid unchanged

## Tests
26 passed in 0.04s
- multi-instrument stream: only XAU publishes, non-XAU return OUT_OF_SCOPE, no Redis write, fault counter=0, ZERO WARN logs (spy logger)
- skip-before-envelope proof (BoomClient never touched; build_envelope defence-in-depth still raises 034)
- valid XAU still publishes EX=10; malformed XAU still fails loud; status exposes skip counter
- 151 focused pass; full suite 1067 pass; zero new failures vs pristine main

## Runtime untouched (read-only)
- runtime rolled back/dark: container 5c6710cae260 image 91aeaca36b1b; supervisor 6 runners; feed-health live; quote dark; canonical tick key ABSENT; market_map untouched; no cross-app

## Zero-runtime-mutation certification
CODE-ONLY. No deploy/restart/recreate; no env/config change; no Redis/SQL writes; tick NOT activated (dark); quote blocked; no cross-app; no secrets/config-in-code.
