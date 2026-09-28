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
        config_mod.ENV_SCRATCH_DIR: "/host-a/scratch",
        config_mod.ENV_EVIDENCE_ROOT: "/host-a/evidence",
        config_mod.ENV_DISK_GUARD_MOUNT: "/host-a",
        config_mod.ENV_SLICE_NAME: "hmt2.slice",
    }
    env.update(overrides)
    return env


def test_missing_required_path_env_vars_raise_config_error_naming_each_one():
    """No path field may fall back to a plausible-looking-but-wrong default: with a completely
    empty environment, every host-specific path field is unset, and `from_env` must fail loud,
    naming every missing environment variable, rather than silently resolving to some other
    host's real path (the bug this test replaces used to assert `repo_dir` silently defaulted
    to a literal Trinity path, `/srv/hmt-code/hermes`, here)."""
    with pytest.raises(config_mod.ConfigError) as exc_info:
        config_mod.Hmt2OpsConfig.from_env({})
    message = str(exc_info.value)
    for env_name in (
        config_mod.ENV_REPO_DIR,
        config_mod.ENV_VENV_PYTHON,
        config_mod.ENV_LAUNCHER_PATH,
        config_mod.ENV_ACQUIRE_LAUNCHER_PATH,
        config_mod.ENV_SCRATCH_DIR,
        config_mod.ENV_EVIDENCE_ROOT,
        config_mod.ENV_DISK_GUARD_MOUNT,
    ):
        assert env_name in message


def test_missing_a_single_required_path_env_var_is_named_even_when_others_are_set():
    """Setting every required path env var except one must raise, naming exactly that one --
    proving the check is per-field, not an all-or-nothing gate."""
    env = _base_env()
    del env[config_mod.ENV_EVIDENCE_ROOT]
    with pytest.raises(config_mod.ConfigError, match=config_mod.ENV_EVIDENCE_ROOT):
        config_mod.Hmt2OpsConfig.from_env(env)


def test_generic_policy_fields_keep_host_agnostic_defaults_when_only_paths_are_set():
    """Path fields must never guess a default; generic policy/tuning fields are the opposite --
    they are not host-identifying, so a documented default is safe and expected to apply as long
    as the required paths are supplied."""
    cfg = config_mod.Hmt2OpsConfig.from_env(_base_env())
    assert cfg.slice_name == "hmt2.slice"
    assert cfg.per_session_timeout_sec == 900
    assert cfg.pre_session_min_memavailable_kb == 40 * 1024 * 1024
    assert cfg.in_session_min_memavailable_kb == 24 * 1024 * 1024
    assert cfg.psi_full_avg10_stall_threshold == 5.0
    assert cfg.disk_guard_warn_pct == 70
    assert cfg.disk_guard_stop_pct == 80
    assert cfg.max_canonical_chunk_sessions == 10


def test_full_config_with_all_required_env_vars_set_resolves_correctly():
    """Regression protection for the fail-loud fix itself: a real, fully-populated deployment
    environment (every required path set, exactly as a real /etc/hmt2/hmt2-ops.env would) must
    still resolve a complete, correct, non-raising config -- the fix must reject *absence*, not
    make a well-configured host newly unable to start."""
    env = _base_env(
        **{
            config_mod.ENV_REPO_DIR: "/srv/rogue-hermes/worktrees/hmt2-governed-gc-mbp1-corpus",
            config_mod.ENV_VENV_PYTHON: "/srv/rogue-hermes/hmt2-venv/bin/python3",
            config_mod.ENV_LAUNCHER_PATH: "/usr/local/sbin/hmt2-run.sh",
            config_mod.ENV_ACQUIRE_LAUNCHER_PATH: "/usr/local/sbin/hmt2-acquire-run.sh",
            config_mod.ENV_CANONICAL_RESEARCH_ROOT: "/srv/rogue-hermes/hmt2-proof/canonical",
            config_mod.ENV_SCRATCH_DIR: "/srv/rogue-hermes/hmt2-proof/scratch",
            config_mod.ENV_EVIDENCE_ROOT: "/srv/rogue-hermes/hmt2-proof/evidence",
            config_mod.ENV_ACQUIRE_SECRET_PATH: "/srv/rogue-hermes/hmt2-proof/secrets/databento.key",
            config_mod.ENV_DISK_GUARD_MOUNT: "/srv",
        }
    )

    cfg = config_mod.Hmt2OpsConfig.from_env(env)

    assert cfg.repo_dir == "/srv/rogue-hermes/worktrees/hmt2-governed-gc-mbp1-corpus"
    assert cfg.venv_python == "/srv/rogue-hermes/hmt2-venv/bin/python3"
    assert cfg.launcher_path == "/usr/local/sbin/hmt2-run.sh"
    assert cfg.acquire_launcher_path == "/usr/local/sbin/hmt2-acquire-run.sh"
    assert cfg.canonical_research_root == "/srv/rogue-hermes/hmt2-proof/canonical"
    assert cfg.scratch_dir == "/srv/rogue-hermes/hmt2-proof/scratch"
    assert cfg.evidence_root == "/srv/rogue-hermes/hmt2-proof/evidence"
    assert cfg.acquire_secret_path == "/srv/rogue-hermes/hmt2-proof/secrets/databento.key"
    assert cfg.disk_guard_mount == "/srv"
    assert cfg.research_hmt2_dir() == "/srv/rogue-hermes/worktrees/hmt2-governed-gc-mbp1-corpus/research/hmt2"


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
    raise; this config module never touches the credential itself. (Unlike the required path
    fields, `acquire_secret_path` is genuinely optional -- its absence is a normal, unconfigured
    state, not a host-portability hazard, so it is deliberately exempt from
    `_REQUIRED_NO_DEFAULT_FIELDS` and must stay that way.)"""
    cfg = config_mod.Hmt2OpsConfig.from_env(_base_env())
    assert cfg.acquire_secret_path is None
    cfg2 = config_mod.Hmt2OpsConfig.from_env(_base_env(**{config_mod.ENV_ACQUIRE_SECRET_PATH: "/etc/secret/path"}))
    assert cfg2.acquire_secret_path == "/etc/secret/path"
