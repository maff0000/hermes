# WO-HELM-HERMES-QUOTE-PUBLISHER-CADENCE-FIX-0001 — HELM evidence

## Result: GREEN_QUOTE_PUBLISHER_CADENCE_FIX_PR_READY_FOR_R2D2_AUDIT

## Defect (from quote activation AMBER)
Quote activation mechanism proven (7 runners, correct fresh envelope, catalog quote RUNTIME_PUBLISHED, tick live) but
the quote key flapped: quote runner published on DEFAULT_INTERVAL_SECONDS=60 while QUOTE_TTL_SECONDS=15 -> key present
~15s, absent ~45s of every 60s (3/15 samples). Rolled back to dark. This PR fixes the cadence.

## PR / branch / SHA
- branch: wo/WO-HELM-HERMES-QUOTE-PUBLISHER-CADENCE-FIX-0001
- base SHA: cfe0f4af2442df4ea727407dd0d078e8843b95df (origin/main, PR #79 merge)
- code head SHA: 2deb58519423019f1a51a695486c8fd9d0c1347b

## Fix (utils/hermes_publisher_runtime_v1.py)
- QUOTE_TTL_SECONDS = 15 (hermes_quote_tick_contract_v1) — UNCHANGED
- OLD quote interval: DEFAULT_INTERVAL_SECONDS = 60 (>= TTL -> flapping)
- NEW quote interval: QUOTE_PUBLISH_INTERVAL_SECONDS = 5 (< TTL=15 -> continuously present)
- default_runner_specs appends quote with QUOTE_PUBLISH_INTERVAL_SECONDS; fail-loud guard raises GOV-HERMES-PUBRT-003 if interval >= QUOTE_TTL_SECONDS
- proof new_interval(5) < TTL(15): TRUE

## Preserved (unchanged)
- quote dark-by-default (no runner when gates absent); quote gates HERMES_QUOTE_PUBLISH_ENABLED/AUTHORISED/INSTRUMENTS + fail-loud (SystemExit 101 / QT-020/021)
- quote catalog RUNTIME_PUBLISHED-when-gated, consumer_live=false, no split-brain; tick activation independent + untouched; feed-health; other runners keep DEFAULT_INTERVAL_SECONDS (no regression); candle/H4/D1 unchanged

## Tests
33 passed in 0.04s
- quote runner interval == QUOTE_PUBLISH_INTERVAL_SECONDS, != 60, < QUOTE_TTL_SECONDS, in (5,10), never == 15
- module invariant interval < TTL; interval>=TTL fails loud (GOV-HERMES-PUBRT-003); other runners keep default cadence; quote still dark when gates absent
- 131 focused pass; full suite 1072 pass; zero new failures vs pristine main

## Runtime untouched (read-only)
- runtime rolled-back/dark: 6 runners; quote dark (CODE_PRESENT_DARK, key absent); tick LIVE (RUNTIME_PUBLISHED); feed-health live; live tick key fresh; market_map untouched

## Zero-runtime-mutation certification
CODE-ONLY. No deploy/restart/recreate; no env/config change; no Redis/SQL writes; quote NOT activated (dark); tick unchanged/live; no cross-app; no candle/H4/D1 change; no config-in-code (governed named constant); no secrets.
