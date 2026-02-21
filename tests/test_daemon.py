"""Tests for the background daemon process and backup logic."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from git_pulsar import daemon
from git_pulsar.config import Config
from git_pulsar.constants import BACKUP_NAMESPACE


@pytest.fixture
def mock_config(mocker: MagicMock) -> Config:
    """Creates a default Config object and mocks Config.load to return it."""
    conf = Config()
    conf.daemon.commit_interval = 0
    conf.daemon.push_interval = 0
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

    # Simulate parent resolution (Head exists, Backup doesn't)
    repo.rev_parse.side_effect = [None, "head_sha"]

    daemon.run_backup(str(tmp_path))

    # Assert plumbing usage
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
    mock_set_state.assert_called_once_with(tmp_path.resolve(), current_time, 5000)
