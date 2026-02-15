"""Tests for the Command Line Interface (CLI) module."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from git_pulsar import cli
from git_pulsar.config import Config


def test_show_status_displays_timestamps(
    tmp_path: Path, capsys: pytest.CaptureFixture, mocker: MagicMock
) -> None:
    """Verifies that `show_status` displays both commit and push timestamps.

    Args:
        tmp_path (Path): Pytest fixture for a temporary directory.
        capsys (pytest.CaptureFixture): Pytest fixture for capturing stdout.
        mocker (MagicMock): Pytest fixture for mocking.
    """
    (tmp_path / ".git").mkdir()
    mocker.patch.object(Path, "cwd", return_value=tmp_path)

    # Mock Registry to include the current path
    registry_path = tmp_path / "registry"
    registry_path.write_text(str(tmp_path))
    mocker.patch("git_pulsar.system.REGISTRY_FILE", registry_path)

    # Mock Config loading
    mocker.patch("git_pulsar.config.Config.load", return_value=Config())

    # Mock GitRepo
    mock_cls = mocker.patch("git_pulsar.cli.GitRepo")
    repo = mock_cls.return_value
    repo.status_porcelain.return_value = []

    # Mock timestamp return values from git log
    # First call: Commit time, Second call: Push time
    repo._run.side_effect = ["1600000000", "1600000000"]

    cli.show_status()

    captured = capsys.readouterr()
    assert "Last Commit:" in captured.out
    assert "Last Push:" in captured.out
    assert "Active" in captured.out


def test_config_command_opens_editor(mocker: MagicMock) -> None:
    """Verifies that the `config` command attempts to open the editor.

    Args:
        mocker (MagicMock): Pytest fixture for mocking.
    """
    # Mock the editor environment variable
    mocker.patch.dict("os.environ", {"EDITOR": "nano"})

    # Mock subprocess to avoid actually running nano
    mock_run = mocker.patch("subprocess.run")

    # Mock the CONFIG_FILE object entirely to support .exists() and str()
    mock_config_path = mocker.MagicMock(spec=Path)
    mock_config_path.exists.return_value = True
    mock_config_path.__str__.return_value = "/mock/config.toml"

    mocker.patch("git_pulsar.cli.CONFIG_FILE", mock_config_path)

    cli.open_config()

    # Verify that the correct command was executed
    args = mock_run.call_args[0][0]
    assert args[0] == "nano"
    assert "/mock/config.toml" in str(args[1])


def test_main_runs_daemon_command(mocker: MagicMock) -> None:
    """Verifies that the `now` command invokes the daemon main loop.

    Args:
        mocker (MagicMock): Pytest fixture for mocking.
    """
    mocker.patch("sys.argv", ["git-pulsar", "now"])
    mock_daemon = mocker.patch("git_pulsar.daemon.main")

    cli.main()

    mock_daemon.assert_called_with(interactive=True)


def test_setup_repo_triggers_identity_config(tmp_path: Path, mocker: MagicMock) -> None:
    """Verifies that setting up a repo triggers identity configuration."""
    (tmp_path / ".git").mkdir()
    mocker.patch.object(Path, "cwd", return_value=tmp_path)

    # Use a fake registry so we don't pollute the real user's registry
    mock_registry = tmp_path / "registry"

    mocker.patch("git_pulsar.constants.REGISTRY_FILE", mock_registry)

    # Mock system.configure_identity
    mock_config_id = mocker.patch("git_pulsar.system.configure_identity")

    # Pass the mock registry explicitly
    cli.setup_repo(registry_path=mock_registry)

    # Assert it was called with a GitRepo instance
    mock_config_id.assert_called_once()
    args = mock_config_id.call_args[0]
    assert isinstance(args[0], cli.GitRepo)
