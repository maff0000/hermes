# HERMES Recovery Runbook

**Service:** signal-service-dev (HERMES)
**Host:** dell-debian (192.168.11.10)
**Port:** 8211
**Database:** tradingSignals on 127.0.0.1:3307

---

## 1. Health Check

```bash
curl -s http://localhost:8211/health | python3 -m json.tool
```

Key fields:
- `health_state` — GREEN / AMBER / RED
- `stream_state` — FLOWING / STALE / DISCONNECTED / CONNECTED_UNPROVEN
- `tick_age_seconds` — seconds since last tick (< 2s when healthy)
- `fault_code` — active fault or null
- `unresolved_gap_count` — historical gaps not yet repaired

**If RED during market hours → go to section 2.**
**If GREEN but `unresolved_gap_count > 0` → go to section 5.**

---

## 2. Stale Stream Incident

### Recognition

`/health` shows one or more of:
- `health_state: RED`
- `stream_state: STALE` or `DISCONNECTED`
- `fault_code: HERMES_STREAM_STALE_TICK` or `HERMES_STREAM_STALE_CANDLE`
- `tick_age_seconds` > 120

### Immediate triage

```bash
# Check systemd
systemctl status signal-service-dev.service --no-pager -l | head -20

# Check latest journal (skip IRIS noise)
journalctl -u signal-service-dev.service --since '5 minutes ago' --no-pager | grep -ivE 'IRIS|healthcheck' | tail -20

# Check latest M1 candle in DB
PW=$(grep DEV_DB_PASSWORD /srv-dev/tradingSignals/.env | cut -d= -f2)
mysql -h 127.0.0.1 -P 3307 -u root -p"$PW" tradingSignals -e "SELECT MAX(timestamp) AS latest_m1, NOW() AS db_time FROM candles_M1 WHERE instrument='XAU_USD'"
```

### Decision tree

| Condition | Action |
|-----------|--------|
| Process dead | `systemctl restart signal-service-dev` |
| Process alive, no ticks, no errors | Restart — likely zombie stream |
| Process alive, reconnect errors looping | Check OANDA API status, then restart |
| Watchdog shows `HERMES_RECOVERY_EXHAUSTED` | Service should have self-terminated. Check systemd restart count. If stuck, manual restart. |
| `/health` returns `{"status":"ok"}` (old stub) | **WO-1 not deployed.** Deploy from local main. |

### After restart

Wait 30s (proof window), then:

```bash
curl -s http://localhost:8211/health | python3 -m json.tool
```

Expect: `health_state: GREEN`, `stream_state: FLOWING`, `tick_age_seconds < 2`.

If still RED after restart → escalate to Matt.

---

## 3. Gap Scan

After any stale event, scan for damage:

```bash
cd /srv-dev/tradingSignals

# Scan specific window
python3 utils/gap_scanner.py scan \
  --instrument XAU_USD \
  --start 2026-03-31T02:55 \
  --end 2026-03-31T07:41

# Scan last N hours (all instruments, all timeframes)
python3 utils/gap_scanner.py verify \
  --recent 24

# Scan specific timeframe
python3 utils/gap_scanner.py verify \
  --instrument XAU_USD \
  --timeframe M1 \
  --recent 12
```

Exit code: 0 = clean, 1 = gaps found.

### Interpreting output

```
GAP [CANDLE] XAU_USD M1: 2026-03-31 02:55:00 → 2026-03-31 07:41:00 (286min, 287 missing)
```

- `CANDLE` or `SIGNAL` — what's missing
- Instrument + timeframe
- UTC window (inclusive)
- Duration and count

### Persisting gaps to ledger

The `scan` command automatically persists to `hermes_data_gaps` table. Use `--dry-run` to preview without writing.

---

## 4. Recovery

### Recover a specific window

```bash
cd /srv-dev/tradingSignals

# Full recovery (all artifacts)
python3 utils/recovery_executor.py recover-window \
  -i XAU_USD \
  -s 2026-03-31T02:55 \
  -e 2026-03-31T07:41

# Candles only
python3 utils/recovery_executor.py recover-window \
  -i XAU_USD \
  -s 2026-03-31T02:55 \
  -e 2026-03-31T07:41 \
  --artifacts CANDLE_M1,CANDLE_M5,CANDLE_M15,CANDLE_H1

# Signals only (candle dependencies auto-included)
python3 utils/recovery_executor.py recover-window \
  -i XAU_USD \
  -s 2026-03-31T02:55 \
  -e 2026-03-31T07:41 \
  --artifacts SIGNAL_M5,SIGNAL_M15
```

### Recover from gap ledger

```bash
python3 utils/recovery_executor.py recover-gap-id --gap-id 1
```

### Post-recovery verification

```bash
python3 utils/recovery_executor.py verify-window \
  -i XAU_USD \
  -s 2026-03-31T02:55 \
  -e 2026-03-31T07:41
```

Exit code: 0 = repaired, 1 = gaps remain.

### Recovery is idempotent

Safe to rerun. Candles use `ON DUPLICATE KEY UPDATE`. Signals overwrite.

### If recovery fails

Check the job ledger:

```sql
SELECT job_id, status, fault_code, completed_steps, failed_steps
FROM hermes_recovery_jobs ORDER BY job_id DESC LIMIT 5;

SELECT job_item_id, artifact_code, status, fault_code, rows_written
FROM hermes_recovery_job_items WHERE job_id = <ID> ORDER BY execution_order;
```

---

## 5. Fault Code Reference

| Code | Meaning | Action |
|------|---------|--------|
| `HERMES_STREAM_STALE_TICK` | No tick received for > 120s during market hours | Watchdog detected. Check stream, restart if needed. |
| `HERMES_STREAM_STALE_CANDLE` | No M1 candle for > 180s during market hours | Tick-to-candle pipeline broken. Restart. |
| `HERMES_RECOVERY_FALSE_CONNECT` | Reconnect succeeded (auth OK) but no ticks flowed within 30s proof window | Stream is zombie. Watchdog will retry or exhaust. |
| `HERMES_RECOVERY_EXHAUSTED` | Max recovery attempts (5) reached | Service should self-terminate for systemd restart. If stuck, manual restart. |
| `HERMES_RECOVERY_BROKER_FETCH_FAILED` | OANDA REST API candle fetch failed | Check OANDA status. Check API key. Retry. |
| `HERMES_RECOVERY_SIGNAL_RECOMPUTE_FAILED` | Signal computation failed | Check candle dependency exists. Check SignalComputer. |
| `HERMES_RECOVERY_VALIDATION_FAILED` | Post-repair verification found remaining gaps | Partial repair. Investigate which artifacts failed. |
| `HERMES_RECOVERY_PARTIAL_FAILURE` | Some steps completed, some failed | Check job items for specific failures. |

---

## 6. Truth Semantics

### Health states

| State | Meaning | When |
|-------|---------|------|
| **GREEN** | Stream flowing, data current, no faults | Normal operation during market hours |
| **AMBER** | Infrastructure OK but no data expected | Market closed (weekends, after Friday 22:00 UTC) |
| **RED** | Data flow problem detected | Stale tick/candle, false reconnect, or service starting up |

### Key distinctions

| Condition | Health | Correct? |
|-----------|--------|----------|
| Flowing now, historically incomplete | GREEN + `unresolved_gap_count > 0` | Yes — current health is about now, not history |
| Market closed, no ticks | AMBER | Yes — not RED, no data expected |
| Process alive, no ticks, market open | RED | Yes — zombie state |
| Just restarted, proof window open | RED transitioning to GREEN | Yes — must prove flow before GREEN |

### Governance status labels

| Label | Meaning |
|-------|---------|
| **CLOSED GREEN** | PR merged to origin/main with full audit proof (PR URL, merge SHA, evidence on main) |
| **LOCAL MAIN COMPLETE** | Merged to local main, tests pass, but no remote push/PR yet |
| **REMOTE/PR CLOSURE PENDING** | GH outage or remote unavailable — needs remediation when available |

A WO is not CLOSED GREEN until remote proof exists. Local merge is operational truth, not governance completion.

---

## 7. Reference: March 31 2026 Incident

### Timeline

| Time (UTC) | Event |
|------------|-------|
| 02:25 | OANDA stream disconnected |
| 02:25 | False reconnect — control plane OK, no ticks |
| 02:54 | Last M1 candle written (buffered data exhausted) |
| 02:55-08:41 | **5h 46m of silence** — service alive, data dead |
| 08:30 | Trader noticed stale chart data |
| 08:41 | Helm restarted service, flow restored |
| 08:41 | Startup backfill recovered ~57 M5 candles |
| 09:06 | Second stale event — restarted again |
| ~10:08 | WO-1A deployed, watchdog verified GREEN |
| 10:18 | WO-4 recovered all missing data, gap RESOLVED |

### What was missing

| Artifact | Count |
|----------|-------|
| M1 candles | 287 |
| M5 candles | 0 (startup backfill covered) |
| M15 candles | 19-21 |
| H1 candles | 5-7 |
| M5 signals | 58 |
| M15 signals | 19 |

### M5 candle count note

WO-2 gap scan found zero M5 gaps because the startup backfill at 08:41 already recovered them. WO-4 Job #3 shows 59 M5 rows because the planner auto-included CANDLE_M5 as a SIGNAL_M5 dependency — these were idempotent overwrites (`ON DUPLICATE KEY UPDATE`) of already-existing data, not new recovery. The actual net new M5 recovery was done by the startup backfill, not by WO-4.

### Recovery jobs

| Job | Type | Steps | Rows | Status |
|-----|------|-------|------|--------|
| #1 | Candles | 4 | 0 | FAILED (OANDA API param bug) |
| #2 | Candles | 4 | 374 | COMPLETED |
| #3 | Signals + deps | 4 | 157 | COMPLETED |
| #4 | Rerun proof | 1 | 287 | COMPLETED (idempotent) |

### Post-repair

```
VERIFY PASS: no gaps in 02:55 → 07:40 window
Gap #1: RESOLVED at 2026-03-31 10:18:33 UTC
```

---

## 8. System Architecture Reference

```
HERMES Signal Service (dell-debian:8211)
├── OANDA Streaming Adapter (tick source)
├── CandleAggregator (M1/M5/M15/H1/D1)
├── SignalComputer (M5/M15 composite signals)
├── SignalPublisher (DB + Redis)
├── LevelEngine (PDH/PDL, Asia range, M15 swings)
├── Watchdog (WO-0001 + 0001A)
│   ├── In-memory tick/candle timestamps
│   ├── Cadence-driven DB persistence
│   ├── Proof window validation
│   └── Fatal exit on exhaustion
└── /health endpoint (authoritative truth)

Recovery toolchain:
├── gap_scanner.py (WO-0002) — detect missing windows
├── recovery_planner.py (WO-0003) — plan rebuild from metadata
└── recovery_executor.py (WO-0004) — execute + verify + record
```

### Config sources

| Config | Location |
|--------|----------|
| Service env | `/srv-dev/tradingSignals/.env` |
| Watchdog thresholds | `hermes_config` table (tradingSignals DB) |
| Signal lookback | `zeusv4_config` table (tradingProteus DB) |
| Recovery artifacts | `hermes_recovery_library` table |
| Recovery dependencies | `hermes_recovery_dependencies` table |
| Market hours | `trading_windows` table + forex rules |

### Key tables

| Table | Purpose |
|-------|---------|
| `hermes_service_health` | Authoritative runtime health |
| `hermes_incidents` | Incident audit trail |
| `hermes_data_gaps` | Gap detection ledger |
| `hermes_config` | HERMES-owned config |
| `hermes_recovery_library` | Recoverable artifact registry |
| `hermes_recovery_dependencies` | Artifact dependency graph |
| `hermes_recovery_jobs` | Recovery job execution ledger |
| `hermes_recovery_job_items` | Per-step execution detail |
