"""Exceptional-envelope apply/verify/restore/verify state machine: never leaves the exception
active, requires explicit per-session authorization, never auto-escalates beyond the configured
ceiling."""
import pytest

from market_truth.acquisition.orchestration import exceptional_envelope as ee

CEILING = 24 * 1024 ** 3  # 24 GiB, matching the Trinity-authorised ceiling


def _auth(**overrides):
    defaults = dict(
        session_id="GC-2022-10-21",
        memory_high_bytes=20 * 1024 ** 3,
        memory_max_bytes=24 * 1024 ** 3,
        authorised_by="architect",
        authorised_utc="2026-09-27T00:00:00Z",
        reason="diagnostic reproduction",
    )
    defaults.update(overrides)
    return ee.EnvelopeAuthorization(**defaults)


def test_validate_authorization_accepts_a_well_formed_request():
    ee.validate_authorization(_auth(), authorised_ceiling_memory_max_bytes=CEILING)


def test_validate_authorization_rejects_bad_session_id_grammar():
    with pytest.raises(ee.EnvelopeError, match="grammar"):
        ee.validate_authorization(_auth(session_id="not-a-session"), authorised_ceiling_memory_max_bytes=CEILING)


def test_validate_authorization_rejects_high_greater_or_equal_to_max():
    with pytest.raises(ee.EnvelopeError, match="memory_high_bytes"):
        ee.validate_authorization(
            _auth(memory_high_bytes=24 * 1024 ** 3, memory_max_bytes=24 * 1024 ** 3),
            authorised_ceiling_memory_max_bytes=CEILING,
        )


def test_validate_authorization_never_auto_escalates_beyond_ceiling():
    with pytest.raises(ee.EnvelopeError, match="ceiling"):
        ee.validate_authorization(
            _auth(memory_max_bytes=CEILING + 1), authorised_ceiling_memory_max_bytes=CEILING
        )


def test_validate_authorization_requires_authorised_by_and_utc():
    with pytest.raises(ee.EnvelopeError, match="authorised_by"):
        ee.validate_authorization(_auth(authorised_by=""), authorised_ceiling_memory_max_bytes=CEILING)
    with pytest.raises(ee.EnvelopeError, match="authorised_utc"):
        ee.validate_authorization(_auth(authorised_utc=""), authorised_ceiling_memory_max_bytes=CEILING)


def test_preflight_passes_when_all_conditions_healthy():
    result = ee.preflight(
        min_memavailable_kb=40 * 1024 * 1024,
        psi_stall_threshold=5.0,
        mem_available_fn=lambda: 55 * 1024 * 1024,
        psi_fn=lambda: 0.0,
        slice_is_idle_fn=lambda: True,
    )
    assert result.ok is True
    assert result.reasons == []


@pytest.mark.parametrize(
    "mem_fn,psi_fn,idle_fn,expected_fragment",
    [
        (lambda: 10 * 1024 * 1024, lambda: 0.0, lambda: True, "MemAvailable"),
        (lambda: 55 * 1024 * 1024, lambda: None, lambda: True, "unreadable"),
        (lambda: 55 * 1024 * 1024, lambda: 6.0, lambda: True, "PSI"),
        (lambda: 55 * 1024 * 1024, lambda: 0.0, lambda: False, "idle"),
    ],
)
def test_preflight_fails_closed_on_each_condition(mem_fn, psi_fn, idle_fn, expected_fragment):
    result = ee.preflight(
        min_memavailable_kb=40 * 1024 * 1024, psi_stall_threshold=5.0,
        mem_available_fn=mem_fn, psi_fn=psi_fn, slice_is_idle_fn=idle_fn,
    )
    assert result.ok is False
    assert any(expected_fragment in r for r in result.reasons)


class _FakeCgroup:
    """Tiny in-memory stand-in for cgroupfs memory.high/memory.max, driven by a fake
    `run_systemctl` so tests never touch a real systemd instance."""

    def __init__(self):
        self.values = {"memory.high": None, "memory.max": None}
        self.systemctl_calls = []

    def run_systemctl(self, argv):
        self.systemctl_calls.append(argv)
        for token in argv:
            if token.startswith("MemoryHigh="):
                self.values["memory.high"] = int(token.split("=", 1)[1])
            elif token.startswith("MemoryMax="):
                self.values["memory.max"] = int(token.split("=", 1)[1])

    def read_cgroup_int_fn(self, name):
        return self.values.get(name)


def test_apply_and_restore_round_trip_verifies_live_values():
    cg = _FakeCgroup()
    ee.apply_envelope(
        slice_name="hmt2.slice", memory_high_bytes=20 * 1024 ** 3, memory_max_bytes=24 * 1024 ** 3,
        run_systemctl=cg.run_systemctl, read_cgroup_int_fn=cg.read_cgroup_int_fn,
    )
    assert cg.values == {"memory.high": 20 * 1024 ** 3, "memory.max": 24 * 1024 ** 3}

    ee.restore_routine_envelope(
        slice_name="hmt2.slice", routine_memory_high_bytes=12 * 1024 ** 3, routine_memory_max_bytes=16 * 1024 ** 3,
        run_systemctl=cg.run_systemctl, read_cgroup_int_fn=cg.read_cgroup_int_fn,
    )
    assert cg.values == {"memory.high": 12 * 1024 ** 3, "memory.max": 16 * 1024 ** 3}


def test_apply_envelope_raises_if_live_verify_disagrees():
    cg = _FakeCgroup()

    def lying_read(name):
        return 1  # never matches whatever was requested

    with pytest.raises(ee.EnvelopeError, match="apply verify failed"):
        ee.apply_envelope(
            slice_name="hmt2.slice", memory_high_bytes=20 * 1024 ** 3, memory_max_bytes=24 * 1024 ** 3,
            run_systemctl=cg.run_systemctl, read_cgroup_int_fn=lying_read,
        )


def test_restore_raises_stop_severity_if_live_verify_disagrees():
    cg = _FakeCgroup()

    def lying_read(name):
        return 1

    with pytest.raises(ee.EnvelopeError, match="RESTORE VERIFY FAILED"):
        ee.restore_routine_envelope(
            slice_name="hmt2.slice", routine_memory_high_bytes=12 * 1024 ** 3, routine_memory_max_bytes=16 * 1024 ** 3,
            run_systemctl=cg.run_systemctl, read_cgroup_int_fn=lying_read,
        )


def _context_manager_kwargs(cg, **overrides):
    kwargs = dict(
        slice_name="hmt2.slice",
        routine_memory_high_bytes=12 * 1024 ** 3,
        routine_memory_max_bytes=16 * 1024 ** 3,
        authorised_ceiling_memory_max_bytes=CEILING,
        preflight_min_memavailable_kb=40 * 1024 * 1024,
        psi_stall_threshold=5.0,
        run_systemctl=cg.run_systemctl,
        read_cgroup_int_fn=cg.read_cgroup_int_fn,
        mem_available_fn=lambda: 55 * 1024 * 1024,
        psi_fn=lambda: 0.0,
        slice_is_idle_fn=lambda: True,
    )
    kwargs.update(overrides)
    return kwargs


def test_exceptional_envelope_restores_routine_on_clean_exit():
    cg = _FakeCgroup()
    with ee.exceptional_envelope(_auth(), **_context_manager_kwargs(cg)):
        assert cg.values == {"memory.high": 20 * 1024 ** 3, "memory.max": 24 * 1024 ** 3}
    # Always restored afterwards -- never left exceptional.
    assert cg.values == {"memory.high": 12 * 1024 ** 3, "memory.max": 16 * 1024 ** 3}


def test_exceptional_envelope_restores_routine_even_if_worker_body_raises():
    """The structural guarantee: 'never leave the exceptional envelope active' holds even when
    the caller's own worker-launch code inside the `with` block raises."""
    cg = _FakeCgroup()
    with pytest.raises(RuntimeError, match="worker exploded"):
        with ee.exceptional_envelope(_auth(), **_context_manager_kwargs(cg)):
            assert cg.values == {"memory.high": 20 * 1024 ** 3, "memory.max": 24 * 1024 ** 3}
            raise RuntimeError("worker exploded")
    assert cg.values == {"memory.high": 12 * 1024 ** 3, "memory.max": 16 * 1024 ** 3}


def test_exceptional_envelope_never_applies_if_preflight_fails():
    cg = _FakeCgroup()
    with pytest.raises(ee.EnvelopeError, match="preflight failed"):
        with ee.exceptional_envelope(_auth(), **_context_manager_kwargs(cg, slice_is_idle_fn=lambda: False)):
            pytest.fail("must never enter the with-block body when preflight fails")
    # apply_envelope was never called at all.
    assert cg.systemctl_calls == []


def test_exceptional_envelope_never_applies_if_authorization_invalid():
    cg = _FakeCgroup()
    bad_auth = _auth(memory_max_bytes=CEILING + 1)
    with pytest.raises(ee.EnvelopeError, match="ceiling"):
        with ee.exceptional_envelope(bad_auth, **_context_manager_kwargs(cg)):
            pytest.fail("must never enter the with-block body for an invalid authorization")
    assert cg.systemctl_calls == []
