"""
HERMES app-level container healthcheck probe — WP2.
WO-HELM-HERMES-CONTAINER-MVP-WP2-CANONICAL-BUILD-AND-EXTERNALISED-CONFIGURATION-0001.

Pure, stdlib-only decision helper shared by the tests AND the Dockerfile HEALTHCHECK. It GETs
http://127.0.0.1:${SIGNAL_PORT}/health and translates (HTTP status, health_state) into a container exit
code, biased to NOT flap during a short OANDA recovery:

  - 200 + GREEN                     -> 0 (healthy)
  - 200 + AMBER                     -> 0 (healthy; transient-recovery tolerant — do NOT flap)
  - 503 + RED                       -> 1 (dead/unhealthy)
  - connection-refused (None)       -> 1 (process not listening)
  - 200 but no health_state body    -> 1 (listening-but-dead: got a response but not the health contract)

`healthcheck_decode` is pure; `run_probe` performs the bounded (3s) stdlib HTTP GET and calls it.
NEVER logs or exposes a secret; reads only the /health JSON.
"""

import json
import os
import sys
import urllib.request
import urllib.error


# AMBER is treated as healthy so a short recovery window does not flap the container.
_HEALTHY_STATES = ("GREEN", "AMBER")


def healthcheck_decode(status_code, health_state) -> int:
    """Pure decision. status_code=None means connection-refused / no response at all.

    Returns 0 (healthy -> HEALTHCHECK exit 0) or 1 (unhealthy -> HEALTHCHECK exit 1)."""
    # Connection refused / no response: the process is not answering -> dead.
    if status_code is None:
        return 1

    if status_code == 200:
        # Listening-but-dead: we got a 200 but no recognisable health contract in the body.
        if health_state is None:
            return 1
        state = str(health_state).strip().upper()
        return 0 if state in _HEALTHY_STATES else 1

    # 503 (or any non-200): unhealthy (RED or service-initializing).
    return 1


def _fetch_health(url, timeout=3.0):
    """Bounded stdlib GET. Returns (status_code, health_state). status_code=None on connection failure.
    NEVER raises out of the probe path."""
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status_code = resp.getcode()
            raw = resp.read()
    except urllib.error.HTTPError as e:
        # A 503 (RED / initializing) still returns a status code + body.
        status_code = e.code
        try:
            raw = e.read()
        except Exception:
            raw = b""
    except Exception:
        # Connection refused / DNS / timeout: no response at all.
        return None, None

    health_state = None
    try:
        body = json.loads(raw.decode("utf-8"))
        if isinstance(body, dict):
            health_state = body.get("health_state")
    except Exception:
        health_state = None
    return status_code, health_state


def run_probe() -> int:
    """Perform the probe against the local /health endpoint; return the container exit code."""
    port = os.getenv("SIGNAL_PORT", "8211")
    url = f"http://127.0.0.1:{port}/health"
    status_code, health_state = _fetch_health(url, timeout=3.0)
    return healthcheck_decode(status_code, health_state)


if __name__ == "__main__":
    sys.exit(run_probe())
