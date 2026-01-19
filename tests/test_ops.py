import os
from pathlib import Path
from unittest.mock import MagicMock, call

import pytest

from src import ops
from src.constants import BACKUP_BRANCH


def test_bootstrap_env_enforces_macos(mocker: MagicMock) -> None:
    mocker.patch("sys.platform", "linux")
    mock_print = mocker.patch("builtins.print")

    ops.bootstrap_env()

    mock_print.assert_called_with(
        "❌ The --env workflow is currently optimized for macOS."
    )


def test_bootstrap_env_checks_dependencies(tmp_path: Path, mocker: MagicMock) -> None:
    mocker.patch("sys.platform", "darwin")
    os.chdir(tmp_path)
    mocker.patch("shutil.which", return_value=None)

    # Changed from RuntimeError to SystemExit
    with pytest.raises(SystemExit):
        ops.bootstrap_env()


def test_bootstrap_env_scaffolds_files(tmp_path: Path, mocker: MagicMock) -> None:
    mocker.patch("sys.platform", "darwin")
    os.chdir(tmp_path)
    mocker.patch("shutil.which", return_value="/usr/bin/fake")
    mock_run = mocker.patch("subprocess.run")

    ops.bootstrap_env()

    # Check uv init
    mock_run.assert_any_call(
        ["uv", "init", "--no-workspace", "--python", "3.12"], check=True
    )

    # Check .envrc
    envrc = tmp_path / ".envrc"
    assert envrc.exists()
    assert "source .venv/bin/activate" in envrc.read_text()


def test_restore_clean(mocker: MagicMock) -> None:
    """Should checkout the file if working tree is clean."""
    mock_cls = mocker.patch("src.ops.GitRepo")
    mock_repo = mock_cls.return_value
    mock_repo.status_porcelain.return_value = []  # Clean

    ops.restore_file("script.py")

    mock_repo.checkout.assert_called_with(BACKUP_BRANCH, file="script.py")


def test_restore_dirty_fails(tmp_path: Path, mocker: MagicMock) -> None:
    """Should abort if the file has local changes."""
    os.chdir(tmp_path)
    (tmp_path / "script.py").touch()  # File must exist for dirty check to trigger

    mock_cls = mocker.patch("src.ops.GitRepo")
    mock_repo = mock_cls.return_value
    # Simulate dirty status
    mock_repo.status_porcelain.return_value = ["M script.py"]

    with pytest.raises(SystemExit):
        ops.restore_file("script.py")


def test_restore_force(mocker: MagicMock) -> None:
    """Should overwrite local changes if --force is provided."""
    mock_cls = mocker.patch("src.ops.GitRepo")
    mock_repo = mock_cls.return_value
    mock_repo.status_porcelain.return_value = ["M script.py"]

    ops.restore_file("script.py", force=True)

    # Force should ignore the dirty check and pass force=True to checkout
    mock_repo.checkout.assert_called_with(BACKUP_BRANCH, file="script.py")


def test_finalize_success(mocker: MagicMock) -> None:
    """Should switch to main, squash merge, commit, and reset."""
    mock_cls = mocker.patch("src.ops.GitRepo")
    mock_repo = mock_cls.return_value
    mock_repo.status_porcelain.return_value = []

    ops.finalize_work()

    expected_calls = [
        call.checkout("main"),
        call.merge_squash(BACKUP_BRANCH),
        call.commit_interactive(),  # Updated method
        call.branch_reset(BACKUP_BRANCH, "main"),
    ]
    mock_repo.assert_has_calls(expected_calls, any_order=True)


def test_finalize_dirty_fails(mocker: MagicMock) -> None:
    mock_cls = mocker.patch("src.ops.GitRepo")
    mock_repo = mock_cls.return_value
    mock_repo.status_porcelain.return_value = ["?? new.py"]

    with pytest.raises(SystemExit):
        ops.finalize_work()
