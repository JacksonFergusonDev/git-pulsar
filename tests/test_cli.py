import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from git_pulsar import cli


def test_setup_repo_initializes_git(
    tmp_path: Path, mocker: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ensure git init is called and registry is updated."""
    monkeypatch.chdir(tmp_path)

    # 1. Mock subprocess for the initial 'git init'
    mock_run = mocker.patch("subprocess.run")

    # 2. Mock GitRepo for subsequent operations
    mock_git_cls = mocker.patch("git_pulsar.cli.GitRepo")
    mock_repo = mock_git_cls.return_value

    fake_registry = tmp_path / ".registry"
    cli.setup_repo(registry_path=fake_registry)

    # Assert 'git init' called
    mock_run.assert_any_call(["git", "init"], check=True)

    # Assert we switched branches using the wrapper
    # Note: setup_repo likely calls repo.checkout(BACKUP_BRANCH)
    assert mock_repo.checkout.called

    # Assert registry updated
    assert fake_registry.exists()
    assert str(tmp_path) in fake_registry.read_text()


def test_main_triggers_bootstrap(mocker: MagicMock) -> None:
    """Ensure --env flag calls ops.bootstrap_env."""
    mock_bootstrap = mocker.patch("git_pulsar.cli.ops.bootstrap_env")

    mocker.patch("sys.argv", ["git-pulsar", "--env"])
    cli.main()

    mock_bootstrap.assert_called_once()


def test_main_default_behavior(mocker: MagicMock) -> None:
    """Ensure running without flags defaults to setup_repo."""
    mock_setup = mocker.patch("git_pulsar.cli.setup_repo")
    mocker.patch("sys.argv", ["git-pulsar"])

    cli.main()

    mock_setup.assert_called_once()


def test_finalize_command(mocker: MagicMock) -> None:
    """Ensure 'finalize' command calls ops.finalize_work."""
    mock_finalize = mocker.patch("git_pulsar.cli.ops.finalize_work")
    mocker.patch("sys.argv", ["git-pulsar", "finalize"])

    cli.main()

    mock_finalize.assert_called_once()


def test_restore_command(mocker: MagicMock) -> None:
    """Ensure 'restore' command calls ops.restore_file."""
    mock_restore = mocker.patch("git_pulsar.cli.ops.restore_file")
    mocker.patch("sys.argv", ["git-pulsar", "restore", "file.py"])

    cli.main()

    mock_restore.assert_called_once_with("file.py", False)


def test_pause_command(tmp_path: Path, mocker: MagicMock) -> None:
    """Test pausing creates the pause file (direct CLI logic)."""
    (tmp_path / ".git").mkdir()
    os.chdir(tmp_path)

    # Mock GitRepo to pass the "is git repo" check if strictly enforced,
    # though pause/resume might just use pathlib.
    # Based on previous code, set_pause_state used pathlib directly.

    cli.set_pause_state(paused=True)
    assert (tmp_path / ".git" / "pulsar_paused").exists()

    cli.set_pause_state(paused=False)
    assert not (tmp_path / ".git" / "pulsar_paused").exists()


def test_status_reports_pause_state(
    tmp_path: Path, capsys: pytest.CaptureFixture, mocker: MagicMock
) -> None:
    """Ensure 'git-pulsar status' explicitly reports the PAUSED state."""
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "pulsar_paused").touch()
    os.chdir(tmp_path)

    # Mock GitRepo
    mock_cls = mocker.patch("git_pulsar.cli.GitRepo")
    mock_repo = mock_cls.return_value
    mock_repo.get_last_commit_time.return_value = "15 minutes ago"
    mock_repo.status_porcelain.return_value = []

    # Mock systemctl/launchctl check
    mocker.patch("subprocess.run")

    cli.show_status()

    captured = capsys.readouterr()
    assert "⏸️  PAUSED" in captured.out


def test_diff_shows_untracked_files(
    tmp_path: Path, capsys: pytest.CaptureFixture, mocker: MagicMock
) -> None:
    """Ensure 'git-pulsar diff' lists untracked files."""
    (tmp_path / ".git").mkdir()
    os.chdir(tmp_path)

    mock_cls = mocker.patch("git_pulsar.cli.GitRepo")
    mock_repo = mock_cls.return_value
    mock_repo.get_untracked_files.return_value = ["new_script.py"]

    cli.show_diff()

    captured = capsys.readouterr()
    assert "Untracked (New) Files" in captured.out
    assert "+ new_script.py" in captured.out


def test_cli_full_cycle(tmp_path: Path) -> None:
    """
    Black-box test: Run the actual CLI command in a subprocess.
    """
    # 1. Create a fake repo
    repo_dir = tmp_path / "my_project"
    repo_dir.mkdir()

    result = subprocess.run(
        [sys.executable, "-m", "git_pulsar.cli", "status"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "System Status" in result.stdout
