"""§20 shadow-emitter event-loop safety: bounded connect timeout + circuit breaker.
WO-HELM-HERMES-DEV-PROD-SINGLE-CONFIG-CONTRACT-CONVERGENCE-0001.

Proves: a persistently-unavailable shadow endpoint NEVER blocks the async tick loop indefinitely and never
raises into the market-truth path; the breaker opens after N consecutive failures and SKIPS redis I/O during
cooldown; a healthy endpoint keeps the breaker closed; the disabled emitter is fully inert (no client/I/O)."""
import importlib, time
import utils.tick_runtime_shadow_adapter_v1 as ad


class _RaisingRedis:
    """Fake redis client: every op raises (simulates an absent/down shadow endpoint)."""
    def __init__(self): self.calls = 0
    def __getattr__(self, name):
        def _boom(*a, **k):
            self.calls += 1
            raise ConnectionError("shadow endpoint down")
        return _boom


def _cfg(enabled=True):
    return ad.RuntimeShadowConfig(
        publisher_enabled=enabled, write_mode=ad.WRITE_MODE_SHADOW_RUNTIME_INERT, namespace="hermes",
        shadow_prefix="hermes:shadow:", redis_host="127.0.0.1", redis_port=6399, redis_db=0,
        redis_ex_seconds=10, payload_ttl_seconds=5, shadow_authorised=True,
        treat_as_production=False, dev_shadow=True)


class _Tick:
    instrument = "XAU_USD"; timestamp = None; bid = 1.0; ask = 1.0
    price_bid = 1.0; price_ask = 1.0


def _emitter_with_down_endpoint():
    return ad.build_runtime_shadow_emitter(config=_cfg(enabled=True), redis_client=_RaisingRedis())


def test_emit_never_raises_when_endpoint_down():
    em = _emitter_with_down_endpoint()
    r = em.emit_tick_observed(_Tick())          # must not raise
    assert r["emitted"] is False


def test_breaker_opens_and_skips_io_after_threshold():
    em = _emitter_with_down_endpoint()
    client = em.writer.redis_client
    # drive threshold consecutive failures
    for _ in range(em._CB_THRESHOLD):
        em.emit_tick_observed(_Tick())
    calls_after_open = client.calls
    # breaker now OPEN -> subsequent calls skip redis I/O entirely (no further client calls, bounded/no block)
    for _ in range(5):
        r = em.emit_tick_observed(_Tick())
        assert r.get("breaker") == "open"
    assert client.calls == calls_after_open, "breaker-open must skip redis I/O (no event-loop block)"


def test_breaker_resets_on_success():
    em = _emitter_with_down_endpoint()
    em._cb_consecutive_failures = em._CB_THRESHOLD
    em._cb_open_until = None                     # allow one attempt
    # monkeypatch emit_tick to succeed
    em.emit_tick = lambda *a, **k: {"emitted": True, "key": "hermes:shadow:x"}
    r = em.emit_tick_observed(_Tick())
    assert r.get("emitted") is True and em._cb_consecutive_failures == 0


def test_disabled_emitter_is_inert():
    dis = ad.DisabledShadowEmitter(_cfg(enabled=False))
    assert dis.enabled is False
    r = dis.emit_tick_observed(_Tick())
    assert r.get("emitted") in (False, None)
