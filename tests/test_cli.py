import os
from pathlib import Path
from unittest.mock import MagicMock

from src import cli


def test_setup_repo_initializes_git(tmp_path: Path, mocker: MagicMock) -> None:
    """Ensure git init is called and registry is updated."""
    os.chdir(tmp_path)

    # 1. Mock subprocess for the initial 'git init'
    mock_run = mocker.patch("subprocess.run")

    # 2. Mock GitRepo for subsequent operations
    mock_git_cls = mocker.patch("src.cli.GitRepo")
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
    mock_bootstrap = mocker.patch("src.cli.ops.bootstrap_env")

    mocker.patch("sys.argv", ["git-pulsar", "--env"])
    cli.main()

    mock_bootstrap.assert_called_once()


def test_main_default_behavior(mocker: MagicMock) -> None:
    """Ensure running without flags defaults to setup_repo."""
    mock_setup = mocker.patch("src.cli.setup_repo")
    mocker.patch("sys.argv", ["git-pulsar"])

    cli.main()

    mock_setup.assert_called_once()


def test_finalize_command(mocker: MagicMock) -> None:
    """Ensure 'finalize' command calls ops.finalize_work."""
    mock_finalize = mocker.patch("src.cli.ops.finalize_work")
    mocker.patch("sys.argv", ["git-pulsar", "finalize"])

    cli.main()

    mock_finalize.assert_called_once()


def test_restore_command(mocker: MagicMock) -> None:
    """Ensure 'restore' command calls ops.restore_file."""
    mock_restore = mocker.patch("src.cli.ops.restore_file")
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
