"""Out-of-band, fail-silent Discord alerting for the recovery guard.
WO-HERMES-AUTOMATED-BACKFILL-RECOVERY.

Ring-fenced: pure stdlib (urllib/json/re), NO env_config, NO requests — so an HTTP timeout, a network
error, or a missing/invalid webhook can NEVER raise into (and crash) the boot/recovery loop. The caller
resolves the webhook URL (via env_config) and passes it in; this module only dispatches, fail-silent.
"""
import json
import re
import sys
import urllib.request

# Accept only genuine Discord webhook URLs (https + discord host + /api/webhooks/<id>/<token>).
_WEBHOOK_RE = re.compile(
    r"^https://(?:canary\.|ptb\.)?discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9_-]+$")


def is_valid_webhook_url(url):
    """True only for a well-formed Discord webhook URL. Empty/None/malformed -> False (never raises)."""
    return isinstance(url, str) and bool(_WEBHOOK_RE.match(url.strip()))


def dispatch_rogue_alert(webhook_url, diagnostic_code, summary):
    """Dispatch an isolated, fail-silent execution warning to #the-rogue-alliance.

    Returns True on a confirmed 204, else False. NEVER raises — a muted/failed dispatch must not affect
    the recovery decision (idempotency + the 24h bound are enforced independently of notification).
    """
    if not is_valid_webhook_url(webhook_url):
        print(f"[ALERT-MUTE] No valid ROGUE_ALLIANCE webhook configured ({diagnostic_code}); "
              "notification skipped (recovery guardrails are unaffected).", file=sys.stderr)
        return False

    payload = {
        "username": "Hermes Recovery Guard",
        "embeds": [{
            "title": f"⚠️ Automated Backfill Triggered: {diagnostic_code}",
            "description": summary,
            "color": 16753920,  # Amber/Orange
            "footer": {"text": "System State: DEPLOYED_MAIN_GATED"},
        }],
    }
    try:
        req = urllib.request.Request(
            webhook_url.strip(),
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "HermesGuard-v1.0"},
        )
        with urllib.request.urlopen(req, timeout=5.0) as response:
            status = getattr(response, "status", None) or response.getcode()
            if status != 204:
                print(f"[ALERT-WARN] Discord returned non-canonical status: {status}", file=sys.stderr)
                return False
        return True
    except Exception as e:  # noqa: BLE001 — fail-silent by contract; telemetry dropouts never crash caller
        print(f"[ALERT-MUTE] Webhook dispatch bypassed due to transport error: {e}", file=sys.stderr)
        return False
