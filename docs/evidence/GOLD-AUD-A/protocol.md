# GOLD-AUD-A: production Redis read-only protocol (PROPOSAL for HELM review)

**Status: PROPOSED, NOT RUN.** This file is the exact command allow-list and plan the HELM-lane operator is asked to review. It has not been executed. No Redis was contacted while writing it. After the run, this file is completed with the executed command log summary and counts (section 12); until then every count and observed value is `PENDING HELM PROTOCOL`.

Authority: PID `pids/GOLD-AUD-A.md` sections S1 to S9 (the allow-list below restates S1 and S2 and adds nothing to them except where marked **PROPOSED EXTENSION**, each of which needs HELM and PO approval and has a fallback). Production read access is separately authorised by the owner (dated record held privately) and additionally needs HELM to verify the instance and database, confirm a read-only connection and review this protocol. No instance address, credential or secret key name appears in this public text; they are supplied to the operator privately.

## 1. Who does what

| Actor | Does | Does not |
|---|---|---|
| HELM-lane operator | verifies the instance and logical database, confirms read-only access, reviews and approves this protocol, runs the wrapper, keeps raw output private | share raw output, an address or a credential with FORGE |
| FORGE PL (this lane) | authored this protocol, the offline allow-list checker and the sanitisation plan; receives the sanitised bundle only | connect to production, receive an address or credential |
| ROGUE | supervises, runs the independent public-safety and verdict audits | connect to production |
| Second operator or auditor | repeats the inventory scan and a 10-key sample after the first operator has finished (section 9) | run concurrently with the first |

## 2. Preconditions (all must be recorded before the run)

1. Owner permission recorded and quoted in the evidence PR description with a timestamp earlier than bundle capture.
2. HELM identifies the authoritative instance and logical database by evidence that does not depend on this protocol's own reads (for example the deployment record and the build identity of the running service). If two candidates disagree or the target is ambiguous: stop (decision D2).
3. HELM confirms the approved connection is read-only by HELM's own means. The wrapper below denies writes as defence in depth; it is not the only control. No credential, access rule or principal is created, copied, rotated or tested by this audit.
4. The wrapper is reviewed against section 4 and exercised against a **non-production fake client** using `scripts/test_protocol_allowlist.py` (offline tests) before any production connection.
5. The allowed-diff set (section 8) is fixed and recorded before the run.
6. No deploy or recreate of the HERMES service is scheduled inside the window (decision D5); if one occurs mid-window, stop and re-run.
7. The operator advises whether to avoid the publish-critical minutes; if so, scan phases run outside the minutes the operator names.

## 3. Wrapper requirements

The only way to talk to the instance is a wrapper that:

- opens exactly one connection and uses it sequentially (no pipelining, no second connection, no pub/sub, no transactions);
- rejects, before sending, any command that fails `check_command` in `scripts/protocol_allowlist.py` (deny by default);
- enforces at most 10 commands per second, at least 1 second between scan pages, no single reply above 64 KiB, total reply bytes at most 50 MB (it stops itself at 40 MB), and aborts on any latency or memory warning;
- obtains authentication, if the approved read path needs any, from its own process environment only; it never places a credential on a command line, in a log, in an error message or in evidence;
- writes one JSON line per command (`conn`, `utc`, `argv`, `reply_kind`, `reply_size`, small `reply_value` for TYPE, STRLEN, HLEN, ZCARD, SCARD) to a private 0600 log; `argv` never contains a credential;
- for `INFO server`, `INFO clients`, `INFO persistence`: stores the raw reply only in the private bundle and extracts for the public record only `uptime_in_seconds` and `run_id` (server) and nothing else.

### 3.1 Key rules (content reads are restricted to governed key patterns)

Content reads (`GET`, `HGET`, `HGETALL`, `ZRANGE`, `SMEMBERS`, `LRANGE`) are refused unless the whole key name matches a governed family pattern for the instrument (candle latest and history, indicator latest and history, features, sessions, levels, quote, feed health, catalog, gaps, backfill status, tick, plus the five exact global control names), or a legacy family under a legacy prefix that **HELM has confirmed** (none by default; legacy keys are therefore unreadable until HELM supplies the prefix). Metadata commands (`TYPE`, `TTL`, `PTTL`, `EXISTS`, `STRLEN`, `HLEN`, `ZCARD`, `SCARD`) are additionally allowed on any key under the governed prefix that carries the instrument segment or its alias form, so alias or unexpected keys can be detected without reading their content. Keys of other systems (for example a key such as `billing:XAU_USD`) and other instruments are never touched. The patterns are implemented in `scripts/protocol_allowlist.py` and tested, including negative cases. A bare substring match is not used.

## 4. Allow-list (exhaustive; identical to PID S1)

| Command | Allowed form | Limit or pre-check |
|---|---|---|
| `SCAN` | `SCAN <cursor> MATCH <pattern> COUNT <n>` only | `MATCH` must **equal** an approved pattern exactly (default list: `hermes:*XAU_USD*` and `hermes:*XAUUSD*`; the unprefixed forms and any other pattern are extensions needing approval, section 5); `COUNT` at most 200; at least 1 s between pages; no `TYPE` option |
| `TYPE`, `TTL`, `PTTL` | one key | none |
| `EXISTS` | exactly one key | none |
| `STRLEN` | one key | none |
| `GET` | one key | only after `TYPE` returned `string` and `STRLEN` returned at most 65536 for that key |
| `HLEN`, `HGET` | key (and field for `HGET`) | none |
| `HGETALL` | one key | only after `HLEN` at most 200; otherwise record `HLEN` and sample with `HGET` |
| `ZCARD` | one key | none |
| `ZRANGE` | `ZRANGE <key> <start> <stop>` with no options | start and stop non-negative, at most 100 elements, and only after `ZCARD` at most 200 |
| `SCARD` | one key | none |
| `SMEMBERS` | one key | only after `SCARD` at most 200 |
| `LRANGE` | `LRANGE <key> <start> <stop>` | non-negative, at most 100 elements |
| `INFO` | `INFO server`, `INFO clients` or `INFO persistence` | no other section |
| `DBSIZE`, `PING` | no arguments | none |

Forbidden (PID S2, enforced by the checker): `KEYS`; unbounded or un-narrowed `SCAN`; `INFO` with any other section including `keyspace`, `memory`, `stats`, `all`; `SMEMBERS` without the pre-check; every write (`SET`, `DEL`, `UNLINK`, `EXPIRE`, `PEXPIRE`, `PERSIST`, `RENAME`, `FLUSHDB`, `FLUSHALL`, and any other mutating command); `CONFIG` in any form; `ACL` in any form; `SUBSCRIBE`, `PSUBSCRIBE`; stream commands (`XREAD`, `XREADGROUP`, `XACK`, `XDEL`); blocking pops; `MONITOR`; `DEBUG`; `SLOWLOG`; `CLIENT`; `SAVE`, `BGSAVE`, `SHUTDOWN`; `SCRIPT`, `EVAL`, `FUNCTION`; `MIGRATE`; `REPLICAOF`; `PUBLISH`; `OBJECT`; `MEMORY`; and any container exec, restart or compose operation on the production server.

Consequences accepted: stream length (`XLEN`) and pub/sub channel listings are not in the allow-list, so the stream form of the tick design and pub/sub presence stay UNVERIFIED by design.

## 5. Phases, commands and budgets

Total wall clock at most 6 hours (PID S7). Inventory and schema in a single run of at most 30 minutes. Sampling at most 2 hours covering at least one full M15 and one full H1 boundary. Overrun is a stop condition, not an extension.

### Phase A: before-state (about 1 minute)

`PING`; `INFO server` (extract `uptime_in_seconds`, `run_id`); `INFO clients`; `INFO persistence`; `DBSIZE`. Five commands.

### Phase B: direct existence checks of source-named keys (about 15 seconds)

For each exact key the source names (the 19 governed families expanded over their timeframes and scopes, about 45 keys: six candle latest, six indicator, six feature, sessions, three level scopes, quote, feed health, catalog, gaps, backfill status, tick, five global control keys, eleven history indexes): `EXISTS`, then `TYPE`, then `PTTL`. About 135 commands at 10 per second.

### Phase C: inventory scans (bounded)

Each scan pass visits every slot of the keyspace once, so cost depends on `DBSIZE`, not on the number of matches. Pages per pass is about `ceil(DBSIZE / 200)` and time per pass is at least that many seconds. At most three passes in total across C1, C2 and C2b (the checker enforces this; if C2b is needed, HELM chooses which of its two patterns to run):

| Pass | Pattern | Purpose |
|---|---|---|
| C1 | `hermes:*XAU_USD*` (the prefix-qualified form of the PID pattern; every governed family and any legacy key under the governed prefix) | every key family for the instrument; names classified client-side |
| C2 | `hermes:*XAUUSD*` (**PROPOSED EXTENSION**, needs HELM and PO approval) | detect alias-form keys, which governed code never writes; counts only |
| C2b | `*XAU_USD*` and `*XAUUSD*` unprefixed (**PROPOSED EXTENSION**, needs HELM and PO approval; only if HELM suspects a deployment-specific prefix) | find legacy keys under another prefix; counts and names only, no content read until HELM confirms the prefix |
| C3 | none | the five global control names are covered by Phase B, not scanned |

Time estimate (a pass is a scan from cursor 0 back to 0): DBSIZE 50 000 gives about 250 pages and about 4 to 5 minutes per pass; DBSIZE 250 000 gives about 1 250 pages and about 21 minutes per pass and would exceed the 30 minute inventory budget, in which case the operator stops and asks HELM and the PO whether to run C1 only. Abort if the projected time for a pass exceeds 15 minutes.

Client-side from the scan results, per family: count, key-name pattern, and (from the epoch embedded in history key names) first and last bar-open time and the number of bars present against the number expected for the window. History keys are counted but not individually inspected.

### Phase D: schema, TTL and depth samples

Sample set (deterministic): every latest and control key found (about 45 keys), and for each history family the newest bar, the oldest bar and three evenly spaced bars by epoch. At most 250 sample keys. Per key: `TYPE`, `STRLEN`, `GET` (strings, governed keys only), `PTTL`; hashes are legacy and follow the legacy rule below. For each history index: `ZCARD`; `ZRANGE` only where `ZCARD` is at most 200 (the daily index is expected to qualify; the others will not). First and last bar-open times for large indexes come from key names (section C), so no extension of `ZRANGE` is needed. Legacy hashes found by C1: `TYPE`, `HLEN` and `PTTL`; `HGETALL` (when `HLEN` is at most 200) only if HELM has confirmed the legacy prefix (section 3.1), otherwise they are counted and typed only.

Additional checks that test repository claims about production (AUDIT section 0.5): `PTTL` on every H4 and D1 history key found (at most about 285 commands) and on up to 20 evenly spaced keys of each other history family; for H4, compare the epochs present in key names against the expected 22/02/06/10/14/18 UTC schedule and compare the count of present keys with `ZCARD` of the index (the difference is the number of index members without an object); read `deployed_sha`, `run_env` and the identity fields from the manifest and heartbeat and compare them with both source revisions named in section 0.5. If the deployed revision matches neither, stop the interpretation step and ask ROGUE and HELM for a source-phase delta.

### Phase E: sampling period (at most 2 hours)

Sequential, single connection, roughly two commands per second on average.

| Group | Keys | Cadence | Per sample |
|---|---|---|---|
| Hot | tick, quote, heartbeat | every 5 s for the 3 minutes around the first M15 boundary and again around the first H1 boundary (from 60 s before to 120 s after); every 60 s otherwise | `STRLEN`, `GET`, `PTTL` |
| Per-timeframe | latest candle, indicator, feature key for each of the six timeframes (18 keys) | every 10 s from 30 s before to 150 s after each M15 and H1 boundary; every 60 s otherwise | `STRLEN`, `GET`, `PTTL` |
| Control | sessions, levels, feed health, catalog, gaps, backfill status, manifest, candle catalog, health | every 60 s | `STRLEN`, `GET`, `PTTL` |
| Legacy hashes found | up to three | every 60 s | `TYPE`, `HLEN`, `PTTL` always; `HGETALL` (if at most 200 fields) only under a HELM-confirmed legacy prefix, otherwise counted and typed only |

Payload estimate: about 45 keys, 3 KB average, 60 samples per hour for 2 hours is about 16 MB plus burst samples about 3 MB, well inside the 50 MB cap; the wrapper counts bytes and stops at 40 MB.

Per sample the client records separately and never merges: the bar-open time field, the publish time field, `valid_until_utc` where present, and its own receive time (UTC, millisecond). `PTTL` pairs from consecutive samples show decrement and renewal. An H4 boundary is included only if it falls inside the window and only if decision D3 agrees; there is no deliberate wait.

Natural-event observation (PID Scope 8): record only what occurs naturally, for example a weekend close or session gap if the window happens to contain one. No fault injection of any kind, no deliberate wait for one.

### Phase F: after-state (about 1 minute)

`INFO server` (extract `uptime_in_seconds`, `run_id`), `DBSIZE`, and for the Phase D sample set: `EXISTS`, `PTTL`, and `GET` (after the same `TYPE` and `STRLEN` pre-checks) to compute value hashes client-side.

## 6. Stop conditions

Stop at once, make no further commands, and report privately to HELM if any of these occurs:

1. the target is ambiguous, or two candidate instances disagree, or the instance does not match HELM's identification (decision D2);
2. excessive load: any latency warning or any memory warning the operator observes (memory is not readable through this protocol because `INFO memory` is forbidden, so this abort relies on the operator watching the instance by HELM's own means), a command timing out, the instance reporting loading or persistence activity not seen in the before-state, or the projected scan time exceeding the stated budget;
3. unexpected permissions: any write-capable behaviour is observed, authentication is required and none was supplied by the approved path, or a command is refused for a reason other than the allow-list (for example a permission message);
4. any protocol violation: a command outside the allow-list is attempted, a pre-check is skipped, a limit is exceeded, or the checker reports a violation on the running log;
5. an exposed secret or an active safety defect is observed (document nothing further about that item; report privately);
6. the time-box is exceeded, or a deploy or recreate of the service occurs mid-window (decide: re-run);
7. sanitisation cannot be proved (section 10).

## 7. Before and after proof (PID S8)

`run_id` before equal to after establishes no restart; it is **not** proof that no data was mutated. Mutation evidence is the sampled-key comparison: existence, `PTTL` and a value hash for each Phase D sample, before and after, excluding the allowed-diff fields. `DBSIZE` may change only by normal publishing and expiry and must be explained key by key. Note: reads can trigger lazy expiry of keys already past their TTL, so a key may vanish between the before and after samples because it was read; the comparison must treat a sampled key whose TTL elapsed in the interval as an explained difference, not a mutation.

## 8. Allowed-diff set (to be fixed and signed before the run)

A sampled key may differ before and after only in: publish-time fields (`generated_at_utc`, `updated_at_utc`, `published_at_utc`, legacy `updated_at`); validity and age fields (`valid_until_utc`, `age_seconds`, `age_seconds_by_tf`); status snapshots that follow the clock (`freshness_state`, `status`, `latest_key_freshness`, `last_publish_utc`); the bar-open fields and prices of a latest key when a new bar has closed; and `PTTL`. Newly published history keys and history keys that expired are listed individually. Any other difference is a FAIL.

## 9. Independent re-derivation (PID acceptance 5)

After the first operator has finished, never concurrently: the second operator repeats scan C1 and a 10-key sample under the same wrapper and limits. Pass: the key sets are equal except keys listed as expired or republished; each common sampled key has an equal value hash once the allowed-diff fields are excluded; any other difference is a FAIL.

## 10. Sanitisation (PID S5), planned

Raw output stays private, referenced in the PR by SHA-256 only. Before anything reaches the public repository: remove server names, addresses, ports other than the standard one, connection strings, container identifiers, account identifiers, user names and secrets; replace `provenance.runtime_instance`, the redacted connection target in the heartbeat and any service-identity machine-name part with `<REDACTED>`; keep public market values but publish only a bounded window; then run `gitleaks` and a text search for dotted-quad addresses, connection-string prefixes and the words listed in PID S5, all of which must return nothing, and record the empty result. Note for ROGUE: the repository has no `tools/secret-scan` script (the PID names one); the equivalent check must be agreed (finding D-A-12).

## 11. Offline checker and tests

`scripts/protocol_allowlist.py` verifies a command log against this allow-list: allow-list membership, forbidden commands, scan shape and count, pre-check ordering for `GET`, `HGETALL`, `ZRANGE`, `SMEMBERS`, `INFO` sections, exact scan-pattern allow-list, governed-key patterns for content reads, 10 commands per second (linear-time window), 1 second between scan pages, at most three scan passes, 6 hour time-box, 64 KiB per reply, 50 MB total, single connection; out-of-order timestamps and non-numeric reply sizes are recorded as violations. It exits non-zero on any violation, so it is the acceptance script for PID criterion 2 and is also meant to be imported by the wrapper. `scripts/test_protocol_allowlist.py` has eleven test groups that pass offline, including negative cases for scan patterns (`**`, `?*`, `[a-z]*`, `other:*`, `*`) and for keys of other systems (run from the `scripts` directory with `python3 -B test_protocol_allowlist.py`).

## 12. Executed record (to be completed after the run)

| Item | Value |
|---|---|
| Operator and reviewer | PENDING HELM PROTOCOL |
| Approval of this protocol (HELM) | PENDING HELM PROTOCOL |
| Extensions approved (C2 alias pass, C2b unprefixed scans, confirmed legacy prefix) | PENDING HELM PROTOCOL |
| Run start and end (UTC) | PENDING HELM PROTOCOL |
| `run_id` before / after | PENDING HELM PROTOCOL |
| `DBSIZE` before / after | PENDING HELM PROTOCOL |
| Commands run, by command (counts) | PENDING HELM PROTOCOL |
| Checker result on the executed log | PENDING HELM PROTOCOL |
| SHA-256 of the private bundle | PENDING HELM PROTOCOL |
| Stop conditions hit | PENDING HELM PROTOCOL |

## 13. Questions for HELM and the PO

1. Approve the alias pass `hermes:*XAUUSD*` and, if needed, the unprefixed scans, or restrict to `hermes:*XAU_USD*` only?
2. Confirm the legacy key prefix (if any) so legacy hashes may be read, or leave them unread?
3. Is a 3-key hot sample at 5 second cadence acceptable on this instance during the boundary bursts?
4. Which operator, which read path, and when (outside the publish-critical minutes if advised)?
5. D3: include an H4 boundary only if it falls in the window?
