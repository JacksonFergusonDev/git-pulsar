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
    mocker.patch("git_pulsar.daemon.get_machine_id", return_value="test-unit")

    # Mock has_large_files to avoid subprocess/git errors
    mocker.patch("git_pulsar.daemon.has_large_files", return_value=False)

    # Mock GitRepo
    mock_cls = mocker.patch("git_pulsar.daemon.GitRepo")
    repo = mock_cls.return_value
    repo.current_branch.return_value = "main"

    # Simulate ref timestamps to ensure Push triggers:
    # 1. Last Commit TS: 0 (triggers commit)
    # 2. Current Local TS: 100 (after commit)
    # 3. Last Remote TS: 0 (triggers push since 100 > 0)
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
    assert f"refs/heads/{BACKUP_NAMESPACE}/test-unit/main" == args[0]

    # Verify push
    repo._run.assert_any_call(
        ["push", "origin", mocker.ANY], capture=True, env=mocker.ANY
    )


def test_run_backup_decoupled_push(
    tmp_path: Path, mocker: MagicMock, mock_config: Config
) -> None:
    """Verifies that commits can happen without pushing if the interval is not met."""
    (tmp_path / ".git").mkdir()

    # Configure: Commit often, Push rarely
    mock_config.daemon.commit_interval = 60
    mock_config.daemon.push_interval = 3600

    mocker.patch("git_pulsar.daemon.SYSTEM.get_battery", return_value=(100, True))
    mocker.patch("git_pulsar.daemon.get_machine_id", return_value="id")
    mocker.patch("git_pulsar.daemon.has_large_files", return_value=False)

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


def test_has_large_files_uses_config_limit(
    tmp_path: Path, mocker: MagicMock, mock_config: Config
) -> None:
    """Verifies that `has_large_files` uses the configured threshold."""
    # Set a custom small limit (500 bytes)
    mock_config.limits.large_file_threshold = 500

    # Mock system notification
    mock_notify = mocker.patch("git_pulsar.daemon.SYSTEM.notify")

    # Mock git ls-files to return a file.
    mocker.patch("subprocess.check_output", return_value="big_file.txt")

    # Create the 'large' file
    (tmp_path / "big_file.txt").write_text("a" * 600)  # 600 bytes > 500 limit

    result = daemon.has_large_files(tmp_path, mock_config)

    assert result is True
    mock_notify.assert_called_with("Backup Aborted", mocker.ANY)
