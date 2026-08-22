"""Tests for the background daemon process and backup logic."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from git_pulsar import daemon
from git_pulsar.config import Config
from git_pulsar.constants import BACKUP_NAMESPACE
from git_pulsar.types import BackupOptions, CommitTreeParams, DriftState


@pytest.fixture
def mock_config(mocker: MagicMock) -> Config:
    """Creates a default Config object and mocks Config.load to return it."""
    conf = Config()
    conf.daemon.commit_interval = 0
    conf.daemon.push_interval = 0
    conf.daemon.sync_enabled = True
    mocker.patch("git_pulsar.daemon.Config.load", return_value=conf)
    return conf


def test_run_backup_shadow_commit_flow(
    tmp_path: Path, mocker: MagicMock, mock_config: Config
) -> None:
    """Verifies the standard backup workflow, ensuring isolation and plumbing usage.

    Args:
        tmp_path (Path): Pytest fixture for a temporary directory.
        mocker (MagicMock): Pytest fixture for mocking.
        mock_config (Config): The mocked configuration fixture.
    """
    (tmp_path / ".git").mkdir()

    # Mock system dependencies
    mocker.patch("git_pulsar.daemon.SYSTEM.is_under_load", return_value=False)
    mocker.patch("git_pulsar.daemon.SYSTEM.get_battery", return_value=(100, True))

    # Mock the slug function
    mocker.patch("git_pulsar.system.get_identity_slug", return_value="test-unit--1234")

    # Mock has_large_files to avoid subprocess/git errors
    mocker.patch("git_pulsar.ops.has_large_files", return_value=False)

    # Mock GitRepo
    mock_cls = mocker.patch("git_pulsar.daemon.GitRepo")
    repo = mock_cls.return_value
    repo.current_branch.return_value = "main"

    # Simulate ref timestamps to ensure Push triggers:
    mocker.patch("git_pulsar.daemon._get_ref_timestamp", side_effect=[0, 100, 0])

    # Simulate parent resolution (Head exists, Backup doesn't) and push resolution
    repo.rev_parse.side_effect = [None, "head_sha", "new_backup_sha", None]

    daemon.run_backup(str(tmp_path))

    # Assert plumbing usage
    repo.add_all.assert_not_called()
    repo._run.assert_any_call(["add", "."], env=mocker.ANY)
    repo.write_tree.assert_called_once()
    repo.commit_tree.assert_called_once()

    # Verify ref update
    repo.update_ref.assert_called()
    args, _ = repo.update_ref.call_args

    # Assert the ref contains the FULL SLUG (test-unit--1234)
    assert f"refs/heads/{BACKUP_NAMESPACE}/test-unit--1234/main" == args[0]

    # Verify push
    repo._run.assert_any_call(
        ["push", "origin", mocker.ANY], capture=True, env=mocker.ANY
    )


def test_run_backup_decoupled_push(
    tmp_path: Path, mocker: MagicMock, mock_config: Config
) -> None:
    """Verifies that commits can happen without pushing if the interval is not met."""
    (tmp_path / ".git").mkdir()

    mocker.patch("git_pulsar.daemon.SYSTEM.is_under_load", return_value=False)

    # Configure: Commit often, Push rarely
    mock_config.daemon.commit_interval = 60
    mock_config.daemon.push_interval = 3600

    mocker.patch("git_pulsar.daemon.SYSTEM.get_battery", return_value=(100, True))
    mocker.patch("git_pulsar.system.get_identity_slug", return_value="id--1234")
    mocker.patch("git_pulsar.ops.has_large_files", return_value=False)

    mock_cls = mocker.patch("git_pulsar.daemon.GitRepo")
    repo = mock_cls.return_value
    repo.current_branch.return_value = "main"

    # Mock Time: 1000s passed since commit (should commit),
    # but only 1000s passed since push (should NOT push).
    now = 10000
    mocker.patch("time.time", return_value=now)

    def get_timestamp_side_effect(repo: MagicMock, ref: str) -> int:
        if "remotes" in ref:
            return now - 1000  # Last push was 1000s ago (Interval 3600 -> Skip)
        return now - 1000  # Last commit was 1000s ago (Interval 60 -> Commit)

    mocker.patch(
        "git_pulsar.daemon._get_ref_timestamp", side_effect=get_timestamp_side_effect
    )

    daemon.run_backup(str(tmp_path))

    # Assert Commit happened
    repo.commit_tree.assert_called_once()

    # Assert Push did NOT happen
    for call_args in repo._run.call_args_list:
        args = call_args[0][0]
        assert "push" not in args, "Push should have been skipped!"


def test_run_backup_drift_detection_throttled(
    tmp_path: Path, mocker: MagicMock, mock_config: Config
) -> None:
    """Verifies that the daemon respects the 15-minute polling interval."""
    (tmp_path / ".git").mkdir()
    mocker.patch("git_pulsar.daemon.SYSTEM.is_under_load", return_value=False)
    mocker.patch("git_pulsar.daemon.SYSTEM.get_battery", return_value=(100, True))
    mocker.patch("git_pulsar.ops.has_large_files", return_value=False)

    mock_repo = mocker.patch("git_pulsar.daemon.GitRepo").return_value
    mock_repo.current_branch.return_value = "main"

    # Set last check to exactly 10 minutes ago (600 seconds), interval requires 900
    current_time = 10000.0
    mocker.patch("time.time", return_value=current_time)
    mocker.patch("git_pulsar.ops.get_drift_state", return_value=(current_time - 600, 0))

    mock_get_host = mocker.patch("git_pulsar.daemon.get_remote_host")

    daemon.run_backup(str(tmp_path), interactive=False)

    # Assert network host was never checked because it was throttled
    mock_get_host.assert_not_called()


def test_run_backup_drift_detection_triggers_notification(
    tmp_path: Path, mocker: MagicMock, mock_config: Config
) -> None:
    """Verifies that unacknowledged drift triggers an OS notification and updates state."""
    (tmp_path / ".git").mkdir()
    mocker.patch("git_pulsar.daemon.SYSTEM.is_under_load", return_value=False)
    mocker.patch("git_pulsar.daemon.SYSTEM.get_battery", return_value=(100, True))
    mocker.patch("git_pulsar.ops.has_large_files", return_value=False)

    mock_repo = mocker.patch("git_pulsar.daemon.GitRepo").return_value
    mock_repo.current_branch.return_value = "main"

    # Simulate 20 minutes since last check (exceeds 900s throttle)
    current_time = 10000.0
    mocker.patch("time.time", return_value=current_time)
    mocker.patch(
        "git_pulsar.ops.get_drift_state", return_value=(current_time - 1200, 0)
    )

    mocker.patch("git_pulsar.daemon.get_remote_host", return_value="github.com")
    mocker.patch("git_pulsar.daemon.is_remote_reachable", return_value=True)

    # Simulate finding newer drift
    warning_msg = "Divergence Risk: 'desktop' pushed newer session"
    mocker.patch(
        "git_pulsar.ops.get_remote_drift_state",
        return_value=(True, 5000, "desktop", warning_msg),
    )

    mock_notify = mocker.patch("git_pulsar.daemon.SYSTEM.notify")
    mock_set_state = mocker.patch("git_pulsar.ops.set_drift_state")

    daemon.run_backup(str(tmp_path), interactive=False)

    # Assert OS interrupt was fired
    mock_notify.assert_called_once_with("Pulsar Drift Detected", warning_msg)

    # Assert state was updated so we don't spam the user again for timestamp 5000
    mock_set_state.assert_called_once_with(
        tmp_path.resolve(), DriftState(current_time, 5000)
    )


def test_main_signal_alarm_reset_on_exception(
    tmp_path: Path, mocker: MagicMock
) -> None:
    """Verifies that signal.alarm(0) is executed even when run_backup raises an error."""
    mock_repo = tmp_path / "repo1"
    mocker.patch("git_pulsar.system.get_registered_repos", return_value=[mock_repo])
    mocker.patch(
        "git_pulsar.daemon.run_backup", side_effect=RuntimeError("Simulated failure")
    )
    mocker.patch("git_pulsar.daemon.run_maintenance")
    mocker.patch("git_pulsar.daemon.setup_logging")

    mock_alarm = mocker.patch("signal.alarm")

    daemon.main(interactive=False)

    # Verify signal.alarm(5) was set and signal.alarm(0) was cleared
    assert mock_alarm.call_args_list == [
        mocker.call(5),
        mocker.call(0),
    ]


def test_run_backup_deduplicates_identical_parents(
    tmp_path: Path, mocker: MagicMock, mock_config: Config
) -> None:
    """Verifies that parents passed to commit_tree contains no duplicates when HEAD == backup."""
    (tmp_path / ".git").mkdir()
    mocker.patch("git_pulsar.daemon.SYSTEM.is_under_load", return_value=False)
    mocker.patch("git_pulsar.daemon.SYSTEM.get_battery", return_value=(100, True))
    mocker.patch("git_pulsar.system.get_identity_slug", return_value="test-slug--1234")
    mocker.patch("git_pulsar.ops.has_large_files", return_value=False)

    mock_cls = mocker.patch("git_pulsar.daemon.GitRepo")
    repo = mock_cls.return_value
    repo.current_branch.return_value = "main"

    # Both backup and HEAD point to "same_sha"
    repo.rev_parse.side_effect = ["same_sha", "same_sha", "old_tree"]
    repo.write_tree.return_value = "new_tree"
    mocker.patch("git_pulsar.daemon._get_ref_timestamp", return_value=0)

    daemon.run_backup(str(tmp_path))

    # Assert commit_tree was called with parents=["same_sha"] (no duplicate)
    repo.commit_tree.assert_called_once()
    args, kwargs = repo.commit_tree.call_args
    if args and isinstance(args[0], CommitTreeParams):
        assert args[0].parents == ["same_sha"]
    else:
        assert kwargs.get("parents") == ["same_sha"]


def test_run_backup_push_uses_oid_comparison(
    tmp_path: Path, mocker: MagicMock, mock_config: Config
) -> None:
    """Verifies that pushes trigger when local_oid != remote_oid regardless of timestamps."""
    (tmp_path / ".git").mkdir()
    mocker.patch("git_pulsar.daemon.SYSTEM.is_under_load", return_value=False)
    mocker.patch("git_pulsar.daemon.SYSTEM.get_battery", return_value=(100, True))
    mocker.patch("git_pulsar.system.get_identity_slug", return_value="test-slug--1234")
    mocker.patch("git_pulsar.ops.has_large_files", return_value=False)

    mock_cls = mocker.patch("git_pulsar.daemon.GitRepo")
    repo = mock_cls.return_value
    repo.current_branch.return_value = "main"

    # Backup already exists and matches current tree -> skip commit
    repo.rev_parse.side_effect = [
        "local_sha_123",  # local backup ref parse in commit phase
        "local_sha_123",  # head parse in commit phase
        "tree_1",  # prev_tree
        "local_sha_123",  # local backup ref parse in push phase
        "remote_sha_456",  # remote backup ref parse in push phase
    ]
    repo.write_tree.return_value = "tree_1"

    # Both timestamps are 0 (identical)
    mocker.patch("git_pulsar.daemon._get_ref_timestamp", return_value=0)

    daemon.run_backup(str(tmp_path))

    # Verify push was attempted because local_sha_123 != remote_sha_456
    repo._run.assert_any_call(
        ["push", "origin", mocker.ANY], capture=True, env=mocker.ANY
    )


def test_temporary_index_and_is_repo_busy_in_worktree(tmp_path: Path) -> None:
    """Verifies that temporary_index and is_repo_busy function correctly in a git worktree."""
    import subprocess

    main_repo = tmp_path / "main_repo"
    main_repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=main_repo, check=True)
    subprocess.run(
        ["git", "config", "user.name", "Test Runner"], cwd=main_repo, check=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=main_repo, check=True
    )
    (main_repo / "file.txt").write_text("Hello\n")
    subprocess.run(["git", "add", "file.txt"], cwd=main_repo, check=True)
    subprocess.run(["git", "commit", "-m", "Initial"], cwd=main_repo, check=True)

    worktree = tmp_path / "wt"
    subprocess.run(
        ["git", "worktree", "add", "-b", "wt-branch", str(worktree)],
        cwd=main_repo,
        check=True,
    )

    # temporary_index in worktree
    with daemon.temporary_index(worktree) as env:
        assert "GIT_INDEX_FILE" in env
        assert Path(env["GIT_INDEX_FILE"]).parent.exists()

    # is_repo_busy in worktree
    assert not daemon.is_repo_busy(worktree)


def test_should_skip_paused_repo(tmp_path: Path) -> None:
    """Verifies that _should_skip returns 'Paused by user' when the paused sentinel exists."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "pulsar_paused").touch()

    conf = Config()
    assert (
        daemon._should_skip(tmp_path, BackupOptions(config=conf, interactive=False))
        == "Paused by user"
    )
    assert (
        daemon._should_skip(tmp_path, BackupOptions(config=conf, interactive=True))
        == "Paused by user"
    )


def test_should_skip_missing_path(tmp_path: Path) -> None:
    """Verifies that _should_skip returns 'Path missing' when the repo directory does not exist."""
    missing = tmp_path / "nonexistent_repo"
    conf = Config()
    assert (
        daemon._should_skip(missing, BackupOptions(config=conf, interactive=False))
        == "Path missing"
    )


def test_should_skip_battery_critical(tmp_path: Path, mocker: MagicMock) -> None:
    """Verifies that _should_skip returns 'Battery critical' in background mode when below min threshold."""
    (tmp_path / ".git").mkdir()
    conf = Config()
    conf.daemon.min_battery_percent = 15

    mocker.patch("git_pulsar.daemon.SYSTEM.is_under_load", return_value=False)
    mocker.patch("git_pulsar.daemon.SYSTEM.get_battery", return_value=(10, False))

    # Background mode should skip
    assert (
        daemon._should_skip(tmp_path, BackupOptions(config=conf, interactive=False))
        == "Battery critical"
    )
    # Interactive mode should proceed regardless of battery
    assert (
        daemon._should_skip(tmp_path, BackupOptions(config=conf, interactive=True))
        is None
    )


def test_should_skip_system_under_load(tmp_path: Path, mocker: MagicMock) -> None:
    """Verifies that _should_skip returns 'System under load' in background mode."""
    (tmp_path / ".git").mkdir()
    conf = Config()

    mocker.patch("git_pulsar.daemon.SYSTEM.is_under_load", return_value=True)
    assert (
        daemon._should_skip(tmp_path, BackupOptions(config=conf, interactive=False))
        == "System under load"
    )
    assert (
        daemon._should_skip(tmp_path, BackupOptions(config=conf, interactive=True))
        is None
    )


def test_attempt_push_skips_in_eco_mode(mocker: MagicMock) -> None:
    """Verifies that _attempt_push skips push when battery is below eco_mode_percent."""
    repo = mocker.MagicMock()
    repo.path = Path("/mock/repo")
    conf = Config()
    conf.daemon.eco_mode_percent = 25

    mocker.patch("git_pulsar.daemon.SYSTEM.get_battery", return_value=(20, False))
    daemon._attempt_push(repo, "ref:ref", BackupOptions(config=conf, interactive=False))

    repo._run.assert_not_called()


def test_attempt_push_skips_when_offline(mocker: MagicMock) -> None:
    """Verifies that _attempt_push skips push when remote host is unreachable."""
    repo = mocker.MagicMock()
    repo.path = Path("/mock/repo")
    conf = Config()

    mocker.patch("git_pulsar.daemon.SYSTEM.get_battery", return_value=(100, True))
    mocker.patch("git_pulsar.daemon.get_remote_host", return_value="github.com")
    mocker.patch("git_pulsar.daemon.is_remote_reachable", return_value=False)

    daemon._attempt_push(repo, "ref:ref", BackupOptions(config=conf, interactive=False))
    repo._run.assert_not_called()


def test_attempt_push_executes_successfully(mocker: MagicMock) -> None:
    """Verifies that _attempt_push executes git push with BatchMode SSH command."""
    repo = mocker.MagicMock()
    repo.path = Path("/mock/repo")
    conf = Config()

    mocker.patch("git_pulsar.daemon.SYSTEM.get_battery", return_value=(100, True))
    mocker.patch("git_pulsar.daemon.get_remote_host", return_value="github.com")
    mocker.patch("git_pulsar.daemon.is_remote_reachable", return_value=True)

    # Background mode
    daemon._attempt_push(repo, "ref:ref", BackupOptions(config=conf, interactive=False))
    repo._run.assert_called_once_with(
        ["push", "origin", "ref:ref"],
        capture=True,
        env=mocker.ANY,
    )
    assert repo._run.call_args[1]["env"]["GIT_SSH_COMMAND"] == "ssh -o BatchMode=yes"

    # Interactive mode
    repo.reset_mock()
    mocker.patch("git_pulsar.daemon.console.status")
    daemon._attempt_push(repo, "ref:ref", BackupOptions(config=conf, interactive=True))
    repo._run.assert_called_once()


def test_run_maintenance_respects_7day_interval(
    tmp_path: Path, mocker: MagicMock
) -> None:
    """Verifies that run_maintenance does not re-run if last_prune is younger than 7 days."""
    state_file = tmp_path / "last_prune"
    state_file.touch()

    mocker.patch("git_pulsar.daemon.REGISTRY_FILE", tmp_path / "registry")
    mock_prune = mocker.patch("git_pulsar.ops.prune_backups")

    daemon.run_maintenance(["/mock/repo1"])
    mock_prune.assert_not_called()


def test_run_maintenance_runs_when_stale_or_missing(
    tmp_path: Path, mocker: MagicMock
) -> None:
    """Verifies that run_maintenance triggers prune_backups when last_prune is missing."""
    mocker.patch("git_pulsar.daemon.REGISTRY_FILE", tmp_path / "registry")
    mock_prune = mocker.patch("git_pulsar.ops.prune_backups")

    daemon.run_maintenance(["/mock/repo1", "/mock/repo2"])

    assert mock_prune.call_count == 2
    assert (tmp_path / "last_prune").exists()


def test_get_remote_host_parsing(tmp_path: Path, mocker: MagicMock) -> None:
    """Verifies that get_remote_host parses both SSH and HTTPS remote URLs."""
    # Test SSH format
    mocker.patch(
        "subprocess.check_output",
        side_effect=[
            "git@github.com:org/repo.git\n",
            "https://gitlab.com/org/repo.git\n",
            "invalid_remote_format\n",
            RuntimeError("Subprocess failed"),
        ],
    )

    assert daemon.get_remote_host(tmp_path, "origin") == "github.com"
    assert daemon.get_remote_host(tmp_path, "origin") == "gitlab.com"
    assert daemon.get_remote_host(tmp_path, "origin") is None
    assert daemon.get_remote_host(tmp_path, "origin") is None


def test_is_remote_reachable(mocker: MagicMock) -> None:
    """Verifies socket reachability checks including empty host and socket errors."""
    assert not daemon.is_remote_reachable("")

    # Successful connection
    mock_conn = mocker.patch("socket.create_connection")
    assert daemon.is_remote_reachable("github.com")

    # Connection failure
    mock_conn.side_effect = OSError("Connection refused")
    assert not daemon.is_remote_reachable("github.com")


def test_is_repo_busy_checks(tmp_path: Path, mocker: MagicMock) -> None:
    """Verifies is_repo_busy detects operational locks and stale index locks."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir()

    # Clean repo is not busy
    assert not daemon.is_repo_busy(tmp_path)

    # Operational lock (e.g. MERGE_HEAD)
    merge_head = git_dir / "MERGE_HEAD"
    merge_head.touch()
    assert daemon.is_repo_busy(tmp_path)
    merge_head.unlink()

    # Fresh index.lock
    index_lock = git_dir / "index.lock"
    index_lock.touch()
    assert daemon.is_repo_busy(tmp_path)

    # Stale index.lock (>24h old) triggers notification in background mode
    import os
    import time

    old_time = time.time() - (25 * 3600)
    os.utime(index_lock, (old_time, old_time))

    mock_notify = mocker.patch("git_pulsar.daemon.SYSTEM.notify")
    assert daemon.is_repo_busy(tmp_path, interactive=False)
    mock_notify.assert_called_once_with(
        "Pulsar Warning", f"Stale lock in {tmp_path.name}"
    )


def test_run_backup_skips_when_paused_and_no_branch(
    tmp_path: Path, mocker: MagicMock
) -> None:
    """Verifies that run_backup cleanly returns when repo is paused or in detached HEAD."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "pulsar_paused").touch()

    mock_repo = mocker.patch("git_pulsar.daemon.GitRepo")
    daemon.run_backup(str(tmp_path))
    mock_repo.assert_not_called()

    (git_dir / "pulsar_paused").unlink()
    repo_instance = mock_repo.return_value
    repo_instance.current_branch.return_value = ""  # Detached HEAD

    daemon.run_backup(str(tmp_path))
    repo_instance.write_tree.assert_not_called()
