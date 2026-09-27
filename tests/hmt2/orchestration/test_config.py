"""Config-driven portability: the same code, pointed at different external config, resolves
different, correct paths/policy per environment -- no value hardcoded outside documented
defaults."""
import pytest

from market_truth.acquisition.orchestration import config as config_mod


def _base_env(**overrides):
    env = {
        config_mod.ENV_REPO_DIR: "/host-a/hermes",
        config_mod.ENV_VENV_PYTHON: "/host-a/venv/bin/python3",
        config_mod.ENV_LAUNCHER_PATH: "/host-a/sbin/hmt2-run.sh",
        config_mod.ENV_ACQUIRE_LAUNCHER_PATH: "/host-a/sbin/hmt2-acquire-run.sh",
        config_mod.ENV_SLICE_NAME: "hmt2.slice",
    }
    env.update(overrides)
    return env


def test_defaults_apply_when_env_is_empty():
    cfg = config_mod.Hmt2OpsConfig.from_env({})
    assert cfg.repo_dir == "/srv/hmt-code/hermes"
    assert cfg.slice_name == "hmt2.slice"
    assert cfg.per_session_timeout_sec == 900
    assert cfg.pre_session_min_memavailable_kb == 40 * 1024 * 1024
    assert cfg.in_session_min_memavailable_kb == 24 * 1024 * 1024
    assert cfg.psi_full_avg10_stall_threshold == 5.0
    assert cfg.disk_guard_warn_pct == 70
    assert cfg.disk_guard_stop_pct == 80
    assert cfg.max_canonical_chunk_sessions == 10


def test_two_different_hosts_resolve_two_different_configs():
    """The same `Hmt2OpsConfig.from_env` call, pointed at two different environments (simulating
    Trinity vs dell-debian), must resolve two genuinely different, correct configs -- proving
    the config is truly externalised, not secretly hardcoded anywhere."""
    trinity_env = _base_env(
        **{
            config_mod.ENV_REPO_DIR: "/srv/hmt-code/hermes",
            config_mod.ENV_CANONICAL_RESEARCH_ROOT: "/srv/hmt-data/canonical",
            config_mod.ENV_ROUTINE_MEMORY_HIGH_BYTES: str(12 * 1024 ** 3),
            config_mod.ENV_ROUTINE_MEMORY_MAX_BYTES: str(16 * 1024 ** 3),
        }
    )
    dell_env = _base_env(
        **{
            config_mod.ENV_REPO_DIR: "/srv-dev-worktrees/hermes",
            config_mod.ENV_CANONICAL_RESEARCH_ROOT: "/srv/dell-hmt-data/canonical",
            config_mod.ENV_ROUTINE_MEMORY_HIGH_BYTES: str(8 * 1024 ** 3),
            config_mod.ENV_ROUTINE_MEMORY_MAX_BYTES: str(10 * 1024 ** 3),
        }
    )

    trinity_cfg = config_mod.Hmt2OpsConfig.from_env(trinity_env)
    dell_cfg = config_mod.Hmt2OpsConfig.from_env(dell_env)

    assert trinity_cfg.repo_dir == "/srv/hmt-code/hermes"
    assert dell_cfg.repo_dir == "/srv-dev-worktrees/hermes"
    assert trinity_cfg.canonical_research_root == "/srv/hmt-data/canonical"
    assert dell_cfg.canonical_research_root == "/srv/dell-hmt-data/canonical"
    assert trinity_cfg.routine_memory_high_bytes == 12 * 1024 ** 3
    assert dell_cfg.routine_memory_high_bytes == 8 * 1024 ** 3
    assert trinity_cfg != dell_cfg


def test_research_hmt2_dir_derives_from_repo_dir():
    cfg = config_mod.Hmt2OpsConfig.from_env(_base_env(**{config_mod.ENV_REPO_DIR: "/x/hermes"}))
    assert cfg.research_hmt2_dir() == "/x/hermes/research/hmt2"


@pytest.mark.parametrize(
    "override,message_fragment",
    [
        ({config_mod.ENV_IN_SESSION_MIN_MEMAVAILABLE_KB: str(50 * 1024 * 1024)}, "in_session_min_memavailable_kb"),
        ({config_mod.ENV_ROUTINE_MEMORY_HIGH_BYTES: str(20 * 1024 ** 3), config_mod.ENV_ROUTINE_MEMORY_MAX_BYTES: str(16 * 1024 ** 3)}, "routine_memory_high_bytes"),
        ({config_mod.ENV_DISK_GUARD_WARN_PCT: "90", config_mod.ENV_DISK_GUARD_STOP_PCT: "80"}, "disk_guard_warn_pct"),
        ({config_mod.ENV_MAX_CANONICAL_CHUNK_SESSIONS: "0"}, "max_canonical_chunk_sessions"),
        ({config_mod.ENV_EXCEPTIONAL_AUTHORISED_CEILING_MEMORY_MAX_BYTES: str(1)}, "exceptional_authorised_ceiling_memory_max_bytes"),
    ],
)
def test_invalid_policy_combinations_are_rejected(override, message_fragment):
    with pytest.raises(config_mod.ConfigError, match=message_fragment):
        config_mod.Hmt2OpsConfig.from_env(_base_env(**override))


def test_non_integer_env_value_raises_config_error():
    with pytest.raises(config_mod.ConfigError):
        config_mod.Hmt2OpsConfig.from_env(_base_env(**{config_mod.ENV_PER_SESSION_TIMEOUT_SEC: "not-a-number"}))


def test_acquire_secret_path_is_a_path_only_never_required():
    """No config field ever carries a secret VALUE -- only, optionally, a path. Absence must not
    raise; this config module never touches the credential itself."""
    cfg = config_mod.Hmt2OpsConfig.from_env({})
    assert cfg.acquire_secret_path is None
    cfg2 = config_mod.Hmt2OpsConfig.from_env({config_mod.ENV_ACQUIRE_SECRET_PATH: "/etc/secret/path"})
    assert cfg2.acquire_secret_path == "/etc/secret/path"
