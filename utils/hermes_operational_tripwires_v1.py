"""HERMES operational tripwires v1 — Redis capacity, container restart-storm, hostname drift.
WO-HELM-HERMES-DEV-PRE-PROD-RECOVERY-GATE-CLOSURE-0001.

Host-side detection tool. Not baked into the HERMES application container — these three checks
belong to the host (Docker container state, the Redis process, /etc/hostname), not the app runtime,
so keeping them host-side means zero change to the HERMES image or its startup path. Writes into the
EXISTING `hermes_incidents` table via `utils.watchdog.HealthPersistence` — no new incident-writing
implementation, no new monitoring platform.

DETECTION ONLY. Nothing here restarts, repairs, or mutates the Redis instance, the containers it
inspects, or the host hostname. A capacity/restart/identity problem is made loud by opening (and, once
resolved, closing) an incident in the existing table — never fixed automatically. The identity gate
itself stays fail-closed; this tool never touches EXPECTED_HOSTNAME or /etc/hostname.

Execution: intended as a governed host-side systemd timer (see ops/systemd/hermes-tripwires.timer).
Each run is independent and idempotent; state between runs (last-seen restart counts) lives in a small
local JSON file this tool owns — never in the monitored containers/Redis/host identity.
"""
from __future__ import annotations
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

# ---- severity bands (WO-mandated) mapped onto the EXISTING HERMES severity vocabulary
# (WARNING/CRITICAL/FATAL — FATAL already used by utils.watchdog for HERMES_RECOVERY_EXHAUSTED-class
# conditions). WO's <70/>=70/>=80/>=90 bands: <70 no incident; >=70 WARNING; >=80 CRITICAL; >=90 FATAL.
CAPACITY_WARNING_PCT = 70.0
CAPACITY_CRITICAL_PCT = 80.0
CAPACITY_FATAL_PCT = 90.0

SEVERITY_WARNING = "WARNING"
SEVERITY_CRITICAL = "CRITICAL"
SEVERITY_FATAL = "FATAL"

STATE_PATH_ENV = "HERMES_TRIPWIRE_STATE_PATH"
CEILING_ENV = "HERMES_REDIS_CAPACITY_CEILING_BYTES"


# =============================================================================================
# Pure evaluation functions — no I/O, fully unit-testable
# =============================================================================================
def resolve_effective_capacity_bytes(*, redis_maxmemory, cgroup_limit_bytes, governed_ceiling_bytes):
    """The effective ceiling to evaluate utilisation against, in priority order:
    1. Redis's own configured `maxmemory` (its own enforced ceiling), if set (>0) — both DEV and PROD
       set this explicitly (PROD's redis.conf: `maxmemory 1536mb` post-recovery, matching the cgroup
       cap byte-for-byte — verified live, not assumed; an earlier draft of this comment incorrectly
       claimed PROD had no `maxmemory` set, corrected under WO-HELM-HERMES-PROD-MECHANICAL-RECOVERY-0001);
    2. the container's cgroup memory limit, as a fallback for any deployment that only sets the cgroup
       cap and leaves Redis's own `maxmemory` at its default (0/unlimited);
    3. HERMES_REDIS_CAPACITY_CEILING_BYTES — an explicit external-config escape hatch for a context
       where neither of the above is introspectable. No config in application code: this is the ONLY
       place a ceiling number may come from outside Redis/cgroup introspection, and it is an env var.
    Fail loud if none resolve — a capacity check with no ceiling to compare against is not a check."""
    if redis_maxmemory and redis_maxmemory > 0:
        return int(redis_maxmemory)
    if cgroup_limit_bytes and cgroup_limit_bytes > 0:
        return int(cgroup_limit_bytes)
    if governed_ceiling_bytes and governed_ceiling_bytes > 0:
        return int(governed_ceiling_bytes)
    raise ValueError("GOV-CAPACITY-TRIPWIRE-001: no effective Redis capacity ceiling resolvable "
                     "(no maxmemory, no cgroup limit, no HERMES_REDIS_CAPACITY_CEILING_BYTES) — "
                     "refusing to report a capacity percentage against an unknown ceiling")


def evaluate_capacity(*, used_memory_bytes, effective_limit_bytes, observed_utc):
    """used/limit -> {pct, severity_or_None, ...evidence}."""
    if effective_limit_bytes <= 0:
        raise ValueError("GOV-CAPACITY-TRIPWIRE-002: effective_limit_bytes must be positive")
    pct = round(100.0 * used_memory_bytes / effective_limit_bytes, 2)
    if pct >= CAPACITY_FATAL_PCT:
        severity = SEVERITY_FATAL
    elif pct >= CAPACITY_CRITICAL_PCT:
        severity = SEVERITY_CRITICAL
    elif pct >= CAPACITY_WARNING_PCT:
        severity = SEVERITY_WARNING
    else:
        severity = None
    return {
        "used_memory_bytes": used_memory_bytes, "effective_limit_bytes": effective_limit_bytes,
        "utilisation_pct": pct, "severity": severity, "observed_utc": observed_utc.isoformat(),
    }


def evaluate_restart_storm(*, container, current_restart_count, current_status, current_health,
                           last_known_count, observed_utc):
    """Two independent signals, either raises:
    - the restart COUNT increased since this tool's own last check (a real, new restart happened);
    - the container is observed 'restarting' or health='unhealthy' AT this instant (crash-loop caught
      in the act, regardless of whether the count ticked between polls)."""
    delta = None if last_known_count is None else current_restart_count - last_known_count
    fault_code = None
    severity = None
    if delta is not None and delta > 0:
        fault_code = "CONTAINER_RESTART_STORM"
        severity = SEVERITY_CRITICAL if delta >= 3 else SEVERITY_WARNING
    elif current_status == "restarting" or current_health == "unhealthy":
        fault_code = "CONTAINER_UNHEALTHY_PROLONGED"
        severity = SEVERITY_CRITICAL
    return {
        "container": container, "current_restart_count": current_restart_count,
        "last_known_count": last_known_count, "delta_since_last_check": delta,
        "status": current_status, "health": current_health,
        "fault_code": fault_code, "severity": severity, "observed_utc": observed_utc.isoformat(),
    }


def evaluate_hostname_drift(*, expected_hostname, persisted_hostname, transient_hostname, observed_utc):
    """`persisted_hostname` is whatever HERMES's own deployment-identity gate would read (the
    /etc/hostname the container mounts) — the same source of truth the gate fails closed against, so
    this detects the SAME class of drift independently of a restart surfacing it as IDENT-HOST-MISMATCH.
    `transient_hostname` (kernel hostname) is optional context: a static/transient disagreement is
    itself evidence of an unapplied manual edit — exactly INC-HERMES-2026-09-PROD-HOSTNAME-DRIFT's
    signature (static=TRADING-1, transient=HERMES, no reboot in between)."""
    persisted_mismatch = persisted_hostname != expected_hostname
    transient_mismatch = transient_hostname is not None and transient_hostname != persisted_hostname
    drift = persisted_mismatch or transient_mismatch
    return {
        "expected_hostname": expected_hostname, "persisted_hostname": persisted_hostname,
        "transient_hostname": transient_hostname, "persisted_mismatch": persisted_mismatch,
        "transient_mismatch": transient_mismatch,
        "fault_code": "HOST_IDENTITY_DRIFT" if drift else None,
        "severity": SEVERITY_FATAL if drift else None,  # this exact class of drift caused the PROD incident
        "observed_utc": observed_utc.isoformat(),
    }


# =============================================================================================
# Evidence gathering — real I/O, kept thin and separate from the pure logic above
# =============================================================================================
def read_redis_info(redis_client):
    info = redis_client.info()
    return {"used_memory": info.get("used_memory", 0), "maxmemory": info.get("maxmemory", 0)}


def read_cgroup_memory_limit():
    """Best-effort cgroup v2 then v1 read. Returns None (never raises) if neither is available —
    callers fall through resolve_effective_capacity_bytes's other sources."""
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            raw = Path(path).read_text().strip()
            if raw == "max":
                continue
            val = int(raw)
            if 0 < val < (1 << 62):   # cgroup v1 reports a huge sentinel for "unlimited"
                return val
        except (OSError, ValueError):
            continue
    return None


def docker_inspect(container):
    """Returns {restart_count, status, health} for a container name via `docker inspect`, or None if
    the container doesn't exist. Read-only; never starts/stops/restarts anything."""
    fmt = '{"RestartCount":{{.RestartCount}},"Status":"{{.State.Status}}","Health":"{{if .State.Health}}{{.State.Health.Status}}{{end}}"}'
    try:
        out = subprocess.run(["docker", "inspect", "--format", fmt, container],
                             capture_output=True, text=True, timeout=10, check=True)
        return json.loads(out.stdout.strip())
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None


def read_hostname_state(host_hostname_path="/etc/hostname"):
    persisted = Path(host_hostname_path).read_text().strip()
    transient = None
    try:
        out = subprocess.run(["hostname"], capture_output=True, text=True, timeout=5, check=True)
        transient = out.stdout.strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass
    return persisted, transient


def load_state(state_path):
    try:
        return json.loads(Path(state_path).read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state_path, state):
    Path(state_path).parent.mkdir(parents=True, exist_ok=True)
    Path(state_path).write_text(json.dumps(state, indent=2))


# =============================================================================================
# CLI runner
# =============================================================================================
def _reconcile(persistence, open_incidents, fault_code, severity, summary, diagnostic):
    """Open a new incident if fault_code fired and none is already open for it; close any open
    incident for this fault_code if the condition has cleared. Never touches other fault codes."""
    existing = [i for i in open_incidents if i["fault_code"] == fault_code]
    if severity:
        if not existing:
            persistence.open_incident(severity, fault_code, summary, json.dumps(diagnostic))
        return
    for inc in existing:
        persistence.close_incident(inc["incident_id"], json.dumps({"reason": "condition cleared", **diagnostic}))


def evaluate_all(state):
    """Evidence-gathering + evaluation only -- no database I/O, no persistence. Safe to run anywhere
    that can reach Redis and the Docker CLI/host filesystem (i.e. the host itself). Returns
    (results_dict, updated_state)."""
    from env_config import get_env, get_env_int
    import redis
    from utils.hermes_redis_auth_v1 import redis_auth_kwargs

    now = datetime.now(timezone.utc)
    results = {}

    # ---- Redis capacity ----
    try:
        r = redis.Redis(host=get_env("HERMES_CANDLE_CANONICAL_REDIS_HOST", required=True),
                        port=get_env_int("HERMES_CANDLE_CANONICAL_REDIS_PORT", required=True),
                        db=get_env_int("HERMES_CANDLE_CANONICAL_REDIS_DB", required=True),
                        socket_timeout=5, **redis_auth_kwargs())
        info = read_redis_info(r)
        ceiling_env = get_env(CEILING_ENV, default=None)
        limit = resolve_effective_capacity_bytes(
            redis_maxmemory=info["maxmemory"], cgroup_limit_bytes=read_cgroup_memory_limit(),
            governed_ceiling_bytes=int(ceiling_env) if ceiling_env else None)
        cap = evaluate_capacity(used_memory_bytes=info["used_memory"], effective_limit_bytes=limit, observed_utc=now)
        results["redis_capacity"] = cap
    except Exception as e:
        results["redis_capacity"] = {"error": str(e)}

    # ---- restart-storm (per configured container) ----
    containers = [c.strip() for c in get_env("HERMES_TRIPWIRE_CONTAINERS", default="hermes-signal,hermes-cache").split(",") if c.strip()]
    restart_results = {}
    for c in containers:
        d = docker_inspect(c)
        if d is None:
            continue
        last = state.get("restart_counts", {}).get(c)
        rs = evaluate_restart_storm(container=c, current_restart_count=d["RestartCount"],
                                    current_status=d["Status"], current_health=d["Health"] or None,
                                    last_known_count=last, observed_utc=now)
        restart_results[c] = rs
        state.setdefault("restart_counts", {})[c] = d["RestartCount"]
    results["restarts"] = restart_results

    # ---- hostname drift ----
    try:
        persisted, transient = read_hostname_state(get_env("HOST_HOSTNAME_PATH", default="/etc/hostname"))
        hd = evaluate_hostname_drift(expected_hostname=get_env("EXPECTED_HOSTNAME", required=True),
                                     persisted_hostname=persisted, transient_hostname=transient, observed_utc=now)
        results["hostname_drift"] = hd
    except Exception as e:
        results["hostname_drift"] = {"error": str(e)}

    return results, state


def reconcile_all(results, *, persistence, open_incidents):
    """The only function that writes to hermes_incidents. Takes evaluate_all()'s output and applies
    the same open/close reconciliation regardless of where the evidence was gathered."""
    from utils.watchdog import FaultCode

    cap = results.get("redis_capacity", {})
    if "error" in cap:
        _reconcile(persistence, open_incidents, FaultCode.REDIS_UNAVAILABLE, SEVERITY_CRITICAL,
                  f"Redis capacity check failed: {cap['error']}", cap)
    else:
        fault_map = {SEVERITY_WARNING: FaultCode.REDIS_CAPACITY_WARNING,
                    SEVERITY_CRITICAL: FaultCode.REDIS_CAPACITY_CRITICAL,
                    SEVERITY_FATAL: FaultCode.REDIS_CAPACITY_FATAL}
        fired = fault_map.get(cap.get("severity"))
        for fc in fault_map.values():
            _reconcile(persistence, open_incidents, fc, fc == fired and cap.get("severity"),
                      f"Redis at {cap.get('utilisation_pct')}% of {cap.get('effective_limit_bytes')} bytes", cap)

    from utils.watchdog import FaultCode as FC2
    for c, rs in results.get("restarts", {}).items():
        fc = FC2.CONTAINER_RESTART_STORM if rs.get("fault_code") == "CONTAINER_RESTART_STORM" else FC2.CONTAINER_UNHEALTHY_PROLONGED
        _reconcile(persistence, [i for i in open_incidents if c in (i.get("fault_summary") or "")], fc,
                  rs.get("severity") if rs.get("fault_code") else None, f"[{c}] {rs.get('fault_code')}", rs)

    hd = results.get("hostname_drift", {})
    if "error" not in hd:
        from utils.watchdog import FaultCode as FC3
        _reconcile(persistence, open_incidents, FC3.HOST_IDENTITY_DRIFT,
                  hd.get("severity"), f"persisted={hd.get('persisted_hostname')} transient={hd.get('transient_hostname')} expected={hd.get('expected_hostname')}", hd)


def _reconcile_via_docker_exec(results, container):
    """Topology adaptation: on hosts where the DB is reachable only from inside the Docker network
    (e.g. PROD's hermes-db, deliberately unpublished to the host -- see docs/architecture/
    environment-model.md 'MariaDB remains internal/private'), this host-side process cannot open its
    own DB connection. Rather than publish a DB port to the host (an unauthorised, unrelated security
    change) or mount the Docker socket into the app container (rejected by this module's own design;
    see the module docstring), the ALREADY-network-connected app container performs the write, using
    its own already-correct DB config -- reachability changes, the incident-writing code and the
    hermes_incidents table do not."""
    import subprocess
    payload = json.dumps(results)
    proc = subprocess.run(
        ["docker", "exec", "-i", container, "python3", "-m", "utils.hermes_operational_tripwires_v1", "--reconcile-from-stdin"],
        input=payload, capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        raise RuntimeError(f"reconcile-via-docker-exec failed (rc={proc.returncode}): {proc.stderr[:500]}")
    return proc.stdout


def main(argv=None):
    from env_config import get_env, get_db_config
    from utils.watchdog import HealthPersistence

    argv = argv if argv is not None else __import__("sys").argv[1:]

    if "--reconcile-from-stdin" in argv:
        # Sink mode: invoked (via docker exec) from a process that gathered evidence but could not
        # reach the DB itself. Reads evaluate_all()'s JSON from stdin, writes incidents locally.
        results = json.loads(__import__("sys").stdin.read())
        persistence = HealthPersistence(get_db_config(), service_name="hermes-tripwires",
                                        environment=get_env("ENVIRONMENT", default="DEV"))
        open_incidents = persistence.get_open_incidents()
        reconcile_all(results, persistence=persistence, open_incidents=open_incidents)
        print(json.dumps({"reconciled": True}))
        return 0

    state_path = get_env(STATE_PATH_ENV, default="/var/lib/hermes/tripwire_state.json")
    state = load_state(state_path)
    results, state = evaluate_all(state)
    save_state(state_path, state)

    db_exec_container = get_env("HERMES_TRIPWIRE_DB_EXEC_CONTAINER", default=None)
    if db_exec_container:
        _reconcile_via_docker_exec(results, db_exec_container)
    else:
        persistence = HealthPersistence(get_db_config(), service_name="hermes-tripwires",
                                        environment=get_env("ENVIRONMENT", default="DEV"))
        open_incidents = persistence.get_open_incidents()
        reconcile_all(results, persistence=persistence, open_incidents=open_incidents)

    print(json.dumps(results, indent=2, default=str))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
