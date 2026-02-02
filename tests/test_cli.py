import datetime
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from git_pulsar import cli


def test_setup_repo_initializes_git(
    tmp_path: Path, mocker: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verifies that `setup_repo` initializes a git repository and updates the registry.

    Args:
        tmp_path (Path): Pytest fixture for a temporary directory.
        mocker (MagicMock): Pytest fixture for mocking.
        monkeypatch (pytest.MonkeyPatch): Pytest fixture for modifying environment/cwd.
    """
    monkeypatch.chdir(tmp_path)

    # Mock subprocess for the initial 'git init' call.
    mock_run = mocker.patch("subprocess.run")

    # Mock GitRepo class to prevent actual git operations.
    mocker.patch("git_pulsar.cli.GitRepo")

    fake_registry = tmp_path / ".registry"
    cli.setup_repo(registry_path=fake_registry)

    # Assert 'git init' was called.
    mock_run.assert_any_call(["git", "init"], check=True)

    # Assert the registry file was created and contains the current path.
    assert fake_registry.exists()
    assert str(tmp_path) in fake_registry.read_text()


def test_main_triggers_bootstrap(mocker: MagicMock) -> None:
    """Verifies that the `--env` flag triggers environment bootstrapping.

    Args:
        mocker (MagicMock): Pytest fixture for mocking.
    """
    mock_bootstrap = mocker.patch("git_pulsar.cli.ops.bootstrap_env")
    mock_setup = mocker.patch("git_pulsar.cli.setup_repo")

    mocker.patch("sys.argv", ["git-pulsar", "--env"])
    cli.main()

    mock_bootstrap.assert_called_once()
    mock_setup.assert_called_once()


def test_main_default_behavior(mocker: MagicMock) -> None:
    """Verifies that running the CLI without arguments defaults to `setup_repo`.

    Args:
        mocker (MagicMock): Pytest fixture for mocking.
    """
    mock_setup = mocker.patch("git_pulsar.cli.setup_repo")
    mocker.patch("sys.argv", ["git-pulsar"])

    cli.main()

    mock_setup.assert_called_once()


def test_finalize_command(mocker: MagicMock) -> None:
    """Verifies that the `finalize` command calls `ops.finalize_work`.

    Args:
        mocker (MagicMock): Pytest fixture for mocking.
    """
    mock_finalize = mocker.patch("git_pulsar.cli.ops.finalize_work")
    mocker.patch("sys.argv", ["git-pulsar", "finalize"])

    cli.main()

    mock_finalize.assert_called_once()


def test_restore_command(mocker: MagicMock) -> None:
    """
    Verifies that the `restore` command calls `ops.restore_file` with correct arguments.

    Args:
        mocker (MagicMock): Pytest fixture for mocking.
    """
    mock_restore = mocker.patch("git_pulsar.cli.ops.restore_file")
    mocker.patch("sys.argv", ["git-pulsar", "restore", "file.py"])

    cli.main()

    mock_restore.assert_called_once_with("file.py", False)


def test_pause_command(
    tmp_path: Path, mocker: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verifies that the `pause` command creates and removes the lock file.

    Args:
        tmp_path (Path): Pytest fixture for a temporary directory.
        mocker (MagicMock): Pytest fixture for mocking.
        monkeypatch (pytest.MonkeyPatch): Pytest fixture for modifying cwd.
    """
    (tmp_path / ".git").mkdir()
    monkeypatch.chdir(tmp_path)

    cli.set_pause_state(paused=True)
    assert (tmp_path / ".git" / "pulsar_paused").exists()

    cli.set_pause_state(paused=False)
    assert not (tmp_path / ".git" / "pulsar_paused").exists()


def test_status_reports_pause_state(
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
    mocker: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verifies that `show_status` explicitly reports when a repository is paused.

    Args:
        tmp_path (Path): Pytest fixture for a temporary directory.
        capsys (pytest.CaptureFixture): Pytest fixture for capturing stdout/stderr.
        mocker (MagicMock): Pytest fixture for mocking.
        monkeypatch (pytest.MonkeyPatch): Pytest fixture for modifying cwd.
    """
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "pulsar_paused").touch()
    monkeypatch.chdir(tmp_path)

    # Register the repo so status checks proceed.
    registry = tmp_path / "registry_mock"
    registry.write_text(str(tmp_path))
    mocker.patch("git_pulsar.cli.REGISTRY_FILE", registry)

    # Mock GitRepo.
    mock_cls = mocker.patch("git_pulsar.cli.GitRepo")
    mock_repo = mock_cls.return_value
    mock_repo.get_last_commit_time.return_value = "15 minutes ago"
    mock_repo.status_porcelain.return_value = []

    # Mock systemctl/launchctl check.
    mocker.patch("git_pulsar.cli._is_service_enabled", return_value=True)

    cli.show_status()

    captured = capsys.readouterr()
    assert "PAUSED" in captured.out


def test_status_reports_idle(
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
    mocker: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Verifies that status reports 'Active (Idle)'
    when the service is enabled but not processing.

    Args:
        tmp_path (Path): Pytest fixture for a temporary directory.
        capsys (pytest.CaptureFixture): Pytest fixture for capturing stdout.
        mocker (MagicMock): Pytest fixture for mocking.
        monkeypatch (pytest.MonkeyPatch): Pytest fixture for modifying cwd.
    """
    (tmp_path / ".git").mkdir()
    monkeypatch.chdir(tmp_path)

    # Register repo.
    registry = tmp_path / "registry_mock"
    registry.write_text(str(tmp_path))
    mocker.patch("git_pulsar.cli.REGISTRY_FILE", registry)

    mock_cls = mocker.patch("git_pulsar.cli.GitRepo")
    mock_repo = mock_cls.return_value
    mock_repo.status_porcelain.return_value = []

    # Mock Service ON, PID OFF.
    mocker.patch("git_pulsar.cli._is_service_enabled", return_value=True)
    mocker.patch("git_pulsar.cli.PID_FILE", Path("/non/existent"))

    cli.show_status()

    captured = capsys.readouterr()
    assert "Active (Idle)" in captured.out


def test_doctor_detects_log_errors(
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
    mocker: MagicMock,
) -> None:
    """Verifies that `run_doctor` correctly identifies and reports recent log errors.

    Args:
        tmp_path (Path): Pytest fixture for a temporary directory.
        capsys (pytest.CaptureFixture): Pytest fixture for capturing stdout.
        mocker (MagicMock): Pytest fixture for mocking.
    """
    log_file = tmp_path / "daemon.log"

    # Create a log entry with a recent error timestamp.
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_content = f"[{now}] CRITICAL Something exploded\n"
    log_file.write_text(log_content)

    mocker.patch("git_pulsar.cli.LOG_FILE", log_file)
    mocker.patch("git_pulsar.cli._is_service_enabled", return_value=True)
    mocker.patch("git_pulsar.cli.REGISTRY_FILE", tmp_path / "empty_registry")

    cli.run_doctor()

    captured = capsys.readouterr()
    assert "Found 1 errors" in captured.out
    assert "CRITICAL Something exploded" in captured.out


def test_diff_shows_untracked_files(
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
    mocker: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verifies that `show_diff` lists untracked files in its output.

    Args:
        tmp_path (Path): Pytest fixture for a temporary directory.
        capsys (pytest.CaptureFixture): Pytest fixture for capturing stdout.
        mocker (MagicMock): Pytest fixture for mocking.
        monkeypatch (pytest.MonkeyPatch): Pytest fixture for modifying cwd.
    """
    (tmp_path / ".git").mkdir()
    monkeypatch.chdir(tmp_path)

    mock_cls = mocker.patch("git_pulsar.cli.GitRepo")
    mock_repo = mock_cls.return_value
    mock_repo.get_untracked_files.return_value = ["new_script.py"]

    cli.show_diff()

    captured = capsys.readouterr()
    assert "Untracked (New) Files" in captured.out
    assert "+ new_script.py" in captured.out


def test_cli_full_cycle(tmp_path: Path) -> None:
    """Performs a black-box test by invoking the CLI module in a subprocess.

    This ensures the module is executable and basic commands run without crashing.

    Args:
        tmp_path (Path): Pytest fixture for a temporary directory.
    """
    # Create a dummy repository structure.
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
