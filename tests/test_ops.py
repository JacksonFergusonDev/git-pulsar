import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from git_pulsar import ops
from git_pulsar.constants import BACKUP_NAMESPACE


def test_bootstrap_env_enforces_macos(mocker: MagicMock) -> None:
    """Verifies that `bootstrap_env` exits early on non-macOS platforms.

    Args:
        mocker (MagicMock): Pytest fixture for mocking.
    """
    mocker.patch("sys.platform", "linux")
    mock_console = mocker.patch("git_pulsar.ops.console")

    ops.bootstrap_env()

    mock_console.print.assert_called_with(
        "[bold red]ERROR:[/bold red] The --env "
        "workflow is currently optimized for macOS."
    )


def test_bootstrap_env_checks_dependencies(tmp_path: Path, mocker: MagicMock) -> None:
    """Verifies that `bootstrap_env` raises SystemExit if required tools are missing.

    Args:
        tmp_path (Path): Pytest fixture for a temporary directory.
        mocker (MagicMock): Pytest fixture for mocking.
    """
    mocker.patch("sys.platform", "darwin")
    os.chdir(tmp_path)
    mocker.patch("shutil.which", return_value=None)
    mocker.patch("git_pulsar.ops.console")

    with pytest.raises(SystemExit):
        ops.bootstrap_env()


def test_bootstrap_env_scaffolds_files(tmp_path: Path, mocker: MagicMock) -> None:
    """Verifies that `bootstrap_env` creates the necessary configuration files.

    Checks for:
    1. Execution of `uv init`.
    2. Creation of `.envrc` with activation logic.

    Args:
        tmp_path (Path): Pytest fixture for a temporary directory.
        mocker (MagicMock): Pytest fixture for mocking.
    """
    mocker.patch("sys.platform", "darwin")
    os.chdir(tmp_path)
    mocker.patch("shutil.which", return_value="/usr/bin/fake")
    mock_run = mocker.patch("subprocess.run")
    mocker.patch("git_pulsar.ops.console")

    ops.bootstrap_env()

    mock_run.assert_any_call(
        ["uv", "init", "--no-workspace", "--python", "3.12"], check=True
    )

    envrc = tmp_path / ".envrc"
    assert envrc.exists()
    assert "source .venv/bin/activate" in envrc.read_text()


# Restore / Sync Tests


def test_restore_clean(mocker: MagicMock) -> None:
    """Verifies that `restore_file` checks out the file when the working tree is clean.

    Args:
        mocker (MagicMock): Pytest fixture for mocking.
    """
    mock_cls = mocker.patch("git_pulsar.ops.GitRepo")
    mock_repo = mock_cls.return_value
    mock_repo.status_porcelain.return_value = []

    mocker.patch("git_pulsar.ops.console")

    # Mock get_identity_slug
    mock_repo.current_branch.return_value = "main"
    mocker.patch("git_pulsar.system.get_identity_slug", return_value="my-mac--1234")

    ops.restore_file("script.py")

    # Expect namespaced ref with the slug
    expected_ref = f"refs/heads/{BACKUP_NAMESPACE}/my-mac--1234/main"
    mock_repo.checkout.assert_called_with(expected_ref, file="script.py")


def test_restore_dirty_fails(tmp_path: Path, mocker: MagicMock) -> None:
    """Verifies that `restore_file` aborts if the target file has uncommitted changes.

    Args:
        tmp_path (Path): Pytest fixture for a temporary directory.
        mocker (MagicMock): Pytest fixture for mocking.
    """
    os.chdir(tmp_path)
    (tmp_path / "script.py").touch()

    mock_cls = mocker.patch("git_pulsar.ops.GitRepo")
    mock_repo = mock_cls.return_value
    mock_repo.status_porcelain.return_value = ["M script.py"]
    mock_repo.current_branch.return_value = "main"

    mocker.patch("git_pulsar.ops.console")

    with pytest.raises(SystemExit):
        ops.restore_file("script.py")


def test_sync_session_success(mocker: MagicMock) -> None:
    """
    Verifies that `sync_session` identifies the latest backup and resets the workspace.

    Args:
        mocker (MagicMock): Pytest fixture for mocking.
    """
    mocker.patch("git_pulsar.ops.GitRepo")
    repo = mocker.patch("git_pulsar.ops.GitRepo").return_value
    repo.current_branch.return_value = "main"

    # Mock user confirmation 'y'.
    mock_console = mocker.patch("git_pulsar.ops.console")
    mock_console.input.return_value = "y"

    # 1. Setup candidate refs from multiple machines.
    repo.list_refs.return_value = [
        f"refs/heads/{BACKUP_NAMESPACE}/laptop/main",
        f"refs/heads/{BACKUP_NAMESPACE}/desktop/main",
    ]

    # 2. Setup timestamp logic (desktop is newer).
    def mock_run(cmd: list[str], *args: Any, **kwargs: Any) -> str:
        # Check if "desktop" or "laptop" is in the command arguments.
        cmd_str = " ".join(cmd)
        if cmd[0] == "log" and "desktop" in cmd_str:
            return "2000"
        if cmd[0] == "log" and "laptop" in cmd_str:
            return "1000"
        return ""

    repo._run.side_effect = mock_run

    # 3. Setup tree diff (simulate remote tree != local tree).
    repo.write_tree.return_value = "local_tree"

    ops.sync_session()

    # Verify fetch of specific branch only
    repo._run.assert_any_call(
        [
            "fetch",
            "origin",
            f"refs/heads/{BACKUP_NAMESPACE}/*/main:refs/heads/{BACKUP_NAMESPACE}/*/main",
        ],
        capture=True,
    )

    # Verify checkout of the newer 'desktop' ref.
    # We inspect the call history to find the checkout command.
    checkout_call = [
        c for c in repo._run.call_args_list if c[0][0] and c[0][0][0] == "checkout"
    ]
    assert checkout_call, "Checkout was never called!"

    cmd_args = checkout_call[0][0][0]  # extract the list passed to _run
    assert f"refs/heads/{BACKUP_NAMESPACE}/desktop/main" in cmd_args


# Finalize Tests


def test_finalize_octopus_merge(mocker: MagicMock) -> None:
    """Verifies that `finalize_work` performs an octopus squash merge of backup streams.

    Args:
        mocker (MagicMock): Pytest fixture for mocking.
    """
    repo = mocker.patch("git_pulsar.ops.GitRepo").return_value
    repo.status_porcelain.return_value = []
    repo.current_branch.return_value = "main"

    mocker.patch("git_pulsar.ops.console")

    # Simulate finding 3 backup streams.
    repo.list_refs.return_value = ["ref_A", "ref_B", "ref_C"]

    ops.finalize_work()

    # 1. Verify Fetch.
    repo._run.assert_any_call(
        [
            "fetch",
            "origin",
            f"refs/heads/{BACKUP_NAMESPACE}/*:refs/heads/{BACKUP_NAMESPACE}/*",
        ],
        capture=True,
    )

    # 2. Verify Octopus Merge of all streams.
    repo.merge_squash.assert_called_with("ref_A", "ref_B", "ref_C")

    # 3. Verify Interactive Commit trigger.
    repo.commit_interactive.assert_called_once()
