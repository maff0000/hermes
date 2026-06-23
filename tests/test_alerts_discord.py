"""Discord alert formatting-boundary + fail-silent tests.
WO-HERMES-AUTOMATED-BACKFILL-RECOVERY.

No network. Verifies webhook-URL boundary validation and that dispatch NEVER raises (transport errors,
non-204, missing/invalid URL all mute, returning False).
"""
import importlib.util
import os

_SPEC = importlib.util.spec_from_file_location(
    "alerts_discord", os.path.join(os.path.dirname(__file__), "..", "utils", "alerts", "discord.py"))
discord = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(discord)

_GOOD = "https://discord.com/api/webhooks/123456789012345678/AbC-dEf_123XyZ"


def test_webhook_format_boundaries_valid():
    for url in (_GOOD,
                "https://discordapp.com/api/webhooks/1/tokentoken",
                "https://canary.discord.com/api/webhooks/42/aB_cD-12",
                "  https://discord.com/api/webhooks/9/tok  "):   # surrounding whitespace tolerated
        assert discord.is_valid_webhook_url(url), url


def test_webhook_format_boundaries_invalid():
    for url in ("", None, 123, "discord.com/api/webhooks/1/tok",      # no scheme
                "http://discord.com/api/webhooks/1/tok",              # not https
                "https://evil.com/api/webhooks/1/tok",                # wrong host
                "https://discord.com/api/webhooks/abc/tok",           # non-numeric id
                "https://discord.com/api/webhooks/1/",                # missing token
                "https://discord.com/webhooks/1/tok"):                # wrong path
        assert not discord.is_valid_webhook_url(url), url


def test_dispatch_mutes_on_missing_or_invalid_webhook():
    # missing/invalid webhook -> muted, returns False, NEVER raises (no fall-through, no crash)
    assert discord.dispatch_rogue_alert("", "GOV-BACKFILL-001", "gap") is False
    assert discord.dispatch_rogue_alert(None, "GOV-BACKFILL-001", "gap") is False
    assert discord.dispatch_rogue_alert("https://evil.com/x", "GOV-BACKFILL-001", "gap") is False


def test_dispatch_is_fail_silent_on_transport_error(monkeypatch):
    def boom(*a, **k):
        raise OSError("network down")
    monkeypatch.setattr(discord.urllib.request, "urlopen", boom)
    # valid URL but transport fails -> must NOT raise, returns False
    assert discord.dispatch_rogue_alert(_GOOD, "GOV-BACKFILL-RECOVER", "x") is False
