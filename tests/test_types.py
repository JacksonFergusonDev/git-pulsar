from pathlib import Path

from git_pulsar.config import Config
from git_pulsar.types import (
    BackupOptions,
    BackupRefInfo,
    BatteryStatus,
    BranchName,
    ByteSize,
    CommitSHA,
    CommitTreeParams,
    ConfigSection,
    DaemonStatus,
    DiffStat,
    DriftState,
    GitOID,
    GitRef,
    MachineId,
    MachineName,
    MachineSlug,
    Preset,
    RemoteDriftResult,
    RepoStatus,
    Seconds,
    ServiceUnitConfig,
    SkipReason,
    TreeSHA,
)


def test_preset_enum() -> None:
    """Verifies that Preset enum contains expected variants and string values."""
    assert Preset.PARANOID.value == "paranoid"
    assert Preset.AGGRESSIVE.value == "aggressive"
    assert Preset.BALANCED.value == "balanced"
    assert Preset.LAZY.value == "lazy"
    assert {p.value for p in Preset} == {"paranoid", "aggressive", "balanced", "lazy"}


def test_repo_status_enum() -> None:
    """Verifies RepoStatus enum variants and values."""
    assert RepoStatus.ACTIVE.value == "Active"
    assert RepoStatus.PAUSED.value == "Paused"
    assert RepoStatus.MISSING.value == "Missing"
    assert RepoStatus.ERROR.value == "Error"
    assert RepoStatus.UNKNOWN.value == "Unknown"


def test_daemon_status_enum() -> None:
    """Verifies DaemonStatus enum variants and values."""
    assert DaemonStatus.RUNNING.value == "Active (Running)"
    assert DaemonStatus.IDLE.value == "Active (Idle)"
    assert DaemonStatus.STOPPED.value == "Stopped"


def test_skip_reason_enum() -> None:
    """Verifies SkipReason enum variants and values."""
    assert SkipReason.PATH_MISSING.value == "Path missing"
    assert SkipReason.PAUSED.value == "Paused by user"
    assert SkipReason.SYSTEM_UNDER_LOAD.value == "System under load"
    assert SkipReason.BATTERY_CRITICAL.value == "Battery critical"


def test_config_section_enum() -> None:
    """Verifies ConfigSection enum variants and values."""
    assert ConfigSection.CORE.value == "core"
    assert ConfigSection.DAEMON.value == "daemon"
    assert ConfigSection.LIMITS.value == "limits"
    assert ConfigSection.FILES.value == "files"


def test_newtypes_runtime_behavior() -> None:
    """Verifies that NewTypes preserve underlying primitives at runtime."""
    machine_id = MachineId("uuid-1234")
    machine_name = MachineName("macbook")
    machine_slug = MachineSlug("macbook--uuid-1234")
    git_ref = GitRef("refs/heads/wip/pulsar/macbook--uuid-1234/main")
    branch = BranchName("main")
    oid = GitOID("a" * 40)
    commit = CommitSHA(oid)
    tree = TreeSHA(oid)
    seconds = Seconds(600)
    bytesize = ByteSize(1024)

    assert machine_id == "uuid-1234"
    assert machine_name == "macbook"
    assert machine_slug == "macbook--uuid-1234"
    assert git_ref.startswith("refs/heads/")
    assert branch == "main"
    assert len(commit) == 40
    assert len(tree) == 40
    assert seconds == 600
    assert bytesize == 1024


def test_battery_status_namedtuple() -> None:
    """Verifies BatteryStatus namedtuple indexing and attribute access."""
    status = BatteryStatus(85, False)
    assert status.percent == 85
    assert status.is_plugged is False
    # Verify tuple unpacking compatibility
    pct, plugged = status
    assert pct == 85
    assert plugged is False


def test_diff_stat_namedtuple() -> None:
    """Verifies DiffStat namedtuple indexing and attribute access."""
    diff = DiffStat(files_changed=3, insertions=20, deletions=5)
    assert diff.files_changed == 3
    assert diff.insertions == 20
    assert diff.deletions == 5
    # Verify tuple unpacking compatibility
    files, ins, dels = diff
    assert (files, ins, dels) == (3, 20, 5)


def test_drift_state_namedtuple() -> None:
    """Verifies DriftState namedtuple indexing and attribute access."""
    state = DriftState(last_check_ts=1700000000.5, warned_remote_ts=1700000000)
    assert state.last_check_ts == 1700000000.5
    assert state.warned_remote_ts == 1700000000
    # Verify tuple unpacking compatibility
    ts, warned = state
    assert ts == 1700000000.5
    assert warned == 1700000000


def test_remote_drift_result_namedtuple() -> None:
    """Verifies RemoteDriftResult namedtuple indexing and attribute access."""
    result = RemoteDriftResult(
        drift_detected=True,
        newest_ts=1700000000,
        newest_machine="macbook--1234",
        warning="Divergence detected",
    )
    assert result.drift_detected is True
    assert result.newest_ts == 1700000000
    assert result.newest_machine == "macbook--1234"
    assert result.warning == "Divergence detected"
    # Verify tuple unpacking compatibility
    detected, ts, machine, warn = result
    assert detected is True
    assert ts == 1700000000
    assert machine == "macbook--1234"
    assert warn == "Divergence detected"


def test_backup_ref_info_dataclass() -> None:
    """Verifies BackupRefInfo dataclass instantiation and fields."""
    info = BackupRefInfo(
        ref=GitRef("refs/heads/wip/pulsar/mac--123/main"),
        slug=MachineSlug("mac--123"),
        machine_name=MachineName("mac"),
        branch=BranchName("main"),
    )
    assert info.ref == "refs/heads/wip/pulsar/mac--123/main"
    assert info.slug == "mac--123"
    assert info.machine_name == "mac"
    assert info.branch == "main"


def test_service_unit_config_dataclass() -> None:
    """Verifies ServiceUnitConfig dataclass instantiation, default values, and field access."""
    cfg = ServiceUnitConfig(
        unit_path=Path("/tmp/pulsar.service"),
        executable="/usr/local/bin/git-pulsar-daemon",
    )
    assert cfg.unit_path == Path("/tmp/pulsar.service")
    assert cfg.executable == "/usr/local/bin/git-pulsar-daemon"
    assert cfg.interval == 900
    assert cfg.log_path is None

    cfg_custom = ServiceUnitConfig(
        unit_path=Path("/tmp/custom.service"),
        executable="/bin/daemon",
        interval=Seconds(300),
        log_path=Path("/var/log/pulsar.log"),
    )
    assert cfg_custom.interval == 300
    assert cfg_custom.log_path == Path("/var/log/pulsar.log")


def test_backup_options_dataclass() -> None:
    """Verifies BackupOptions dataclass instantiation and default values."""
    conf = Config()
    opts = BackupOptions(config=conf)
    assert opts.config is conf
    assert opts.interactive is False

    interactive_opts = BackupOptions(config=conf, interactive=True)
    assert interactive_opts.interactive is True


def test_commit_tree_params_dataclass() -> None:
    """Verifies CommitTreeParams dataclass instantiation and fields."""
    params = CommitTreeParams(
        tree=TreeSHA(GitOID("a" * 40)),
        parents=[CommitSHA(GitOID("b" * 40))],
        message="Test shadow commit",
        env={"GIT_INDEX_FILE": "/tmp/idx"},
    )
    assert params.tree == "a" * 40
    assert params.parents == ["b" * 40]
    assert params.message == "Test shadow commit"
    assert params.env == {"GIT_INDEX_FILE": "/tmp/idx"}
