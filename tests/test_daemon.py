from pathlib import Path
from unittest.mock import MagicMock

from src import daemon


def test_run_backup_skips_if_path_missing(mocker: MagicMock) -> None:
    mock_prune = mocker.patch("src.daemon.prune_registry")
    # Path that definitely doesn't exist
    daemon.run_backup("/non/existent/path")
    mock_prune.assert_called_once()


def test_run_backup_skips_if_paused(tmp_path: Path, mocker: MagicMock) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "pulsar_paused").touch()

    mock_repo_cls = mocker.patch("src.daemon.GitRepo")
    mock_log = mocker.patch("src.daemon.log")

    daemon.run_backup(str(tmp_path))

    # Should log "SKIPPED ... Paused by user"
    args, _ = mock_log.call_args
    assert "Paused by user" in args[0]
    # Should NOT instantiate GitRepo (optimization check)
    mock_repo_cls.assert_not_called()


def test_run_backup_happy_path(tmp_path: Path, mocker: MagicMock) -> None:
    """Daemon should commit and attempt push if all guards pass."""
    (tmp_path / ".git").mkdir()

    # 1. Mock System
    mocker.patch("src.daemon.SYSTEM.is_under_load", return_value=False)
    mocker.patch("src.daemon.SYSTEM.get_battery", return_value=(100, True))

    # 2. Mock GitRepo
    mock_cls = mocker.patch("src.daemon.GitRepo")
    repo = mock_cls.return_value
    repo.path = tmp_path

    # Setup repo state
    repo.current_branch.return_value = "wip/pulsar"
    repo.status_porcelain.return_value = ["M file.py"]

    # 3. Mock Network/Push helpers
    mocker.patch("src.daemon.get_remote_host", return_value="github.com")
    mocker.patch("src.daemon.is_remote_reachable", return_value=True)

    daemon.run_backup(str(tmp_path))

    # Verifications
    repo.add_all.assert_called_once()
    repo.commit.assert_called_once()
    # Check that the internal _run was called for push
    # (Since _attempt_push calls repo._run directly for environment injection)
    repo._run.assert_called()
    assert "push" in repo._run.call_args[0][0]


def test_run_backup_eco_mode(tmp_path: Path, mocker: MagicMock) -> None:
    """Should commit but skip push on low battery."""
    (tmp_path / ".git").mkdir()

    # Low battery, unplugged
    mocker.patch("src.daemon.SYSTEM.get_battery", return_value=(10, False))
    mocker.patch("src.daemon.SYSTEM.is_under_load", return_value=False)

    mock_cls = mocker.patch("src.daemon.GitRepo")
    repo = mock_cls.return_value
    repo.path = tmp_path
    repo.current_branch.return_value = "wip/pulsar"
    repo.status_porcelain.return_value = ["M file.py"]

    daemon.run_backup(str(tmp_path))

    repo.commit.assert_called_once()
    # Should NOT call push (which uses _run)
    repo._run.assert_not_called()
