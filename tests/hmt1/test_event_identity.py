"""HMT-1 — identity.py: the one governed event-identity algorithm."""
import inspect

from market_truth.identity import EventIdentityMaterial, compute_event_identity


def _material(**overrides):
    base = dict(
        event_family="MARKET_TRADE",
        provider_id="fixture-provider:hmt1-fixture-provider-v1",
        dataset_id="hmt1-test-dataset",
        source_artifact_id="gc-mbp1-0006",
        canonical_instrument_id="COMEX:GC:2026-12",
        source_record_identity="gc-mbp1-0006",
        source_sequence=1006,
        sequence_domain="FIXTURE_PROVIDER_V1_NATIVE_SEQUENCE",
        source_event_time_text="2026-09-15T13:30:00.600Z",
        event_ordinal=0,
    )
    base.update(overrides)
    return EventIdentityMaterial(**base)


def test_identical_material_produces_identical_identity():
    m1 = _material()
    m2 = _material()
    assert m1 is not m2
    assert compute_event_identity(m1) == compute_event_identity(m2)


def test_identity_is_a_sha256_hex_digest():
    identity = compute_event_identity(_material())
    assert len(identity) == 64
    int(identity, 16)  # raises ValueError if not valid hex


def test_different_event_ordinal_produces_different_identity():
    """The ordinal/subtype mechanism (WO §6): one source record producing two canonical events
    (e.g. a trade + its companion book-transition) must get two distinct identities."""
    m_trade = _material(event_ordinal=0, event_family="MARKET_TRADE")
    m_tob = _material(event_ordinal=1, event_family="TOP_OF_BOOK")
    assert compute_event_identity(m_trade) != compute_event_identity(m_tob)


def test_different_source_sequence_produces_different_identity():
    a = _material(source_sequence=1006)
    b = _material(source_sequence=1007)
    assert compute_event_identity(a) != compute_event_identity(b)


def test_none_source_sequence_is_distinguishable_from_any_int_value():
    a = _material(source_sequence=None, sequence_domain="NOT_AVAILABLE")
    b = _material(source_sequence=0, sequence_domain="NOT_AVAILABLE")
    assert compute_event_identity(a) != compute_event_identity(b)


def test_identity_material_has_no_wallclock_pid_or_random_fields():
    """Structural proof (not just discipline) that identity material cannot include a
    wall-clock/process/random field: those fields do not exist on the dataclass at all."""
    field_names = set(EventIdentityMaterial.__dataclass_fields__.keys())
    forbidden_substrings = ("now", "pid", "uuid", "random", "path", "replay_time", "wall_clock")
    for name in field_names:
        lowered = name.lower()
        assert not any(bad in lowered for bad in forbidden_substrings), f"suspicious identity field: {name}"


def test_canonical_serialization_is_explicit_field_order_not_json_default_ordering():
    source = inspect.getsource(EventIdentityMaterial.canonical_serialization)
    assert "json.dumps" not in source
    assert "sort_keys" not in source
