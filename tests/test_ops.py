import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from git_pulsar import ops
from git_pulsar.constants import BACKUP_NAMESPACE


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

    with pytest.raises(SystemExit):
        ops.bootstrap_env()


def test_bootstrap_env_scaffolds_files(tmp_path: Path, mocker: MagicMock) -> None:
    mocker.patch("sys.platform", "darwin")
    os.chdir(tmp_path)
    mocker.patch("shutil.which", return_value="/usr/bin/fake")
    mock_run = mocker.patch("subprocess.run")

    ops.bootstrap_env()

    mock_run.assert_any_call(
        ["uv", "init", "--no-workspace", "--python", "3.12"], check=True
    )

    envrc = tmp_path / ".envrc"
    assert envrc.exists()
    assert "source .venv/bin/activate" in envrc.read_text()


# Identity Tests


def test_configure_identity_creates_file(tmp_path: Path, mocker: MagicMock) -> None:
    """Should create machine_id file if missing."""
    mocker.patch("builtins.input", return_value="my-laptop")
    mock_id_file = tmp_path / "machine_id"
    mocker.patch("git_pulsar.ops.get_machine_id_file", return_value=mock_id_file)

    ops.configure_identity()

    assert mock_id_file.read_text() == "my-laptop"


def test_configure_identity_skips_existing(tmp_path: Path, mocker: MagicMock) -> None:
    """Should do nothing if file exists."""
    mock_id_file = tmp_path / "machine_id"
    mock_id_file.write_text("existing-id")
    mocker.patch("git_pulsar.ops.get_machine_id_file", return_value=mock_id_file)
    mock_input = mocker.patch("builtins.input")

    ops.configure_identity()

    mock_input.assert_not_called()


# Restore / Sync Tests


def test_restore_clean(mocker: MagicMock) -> None:
    """Should checkout the file if working tree is clean."""
    mock_cls = mocker.patch("git_pulsar.ops.GitRepo")
    mock_repo = mock_cls.return_value
    mock_repo.status_porcelain.return_value = []

    # Mock current branch and machine ID for ref construction
    mock_repo.current_branch.return_value = "main"
    mocker.patch("git_pulsar.ops.get_machine_id", return_value="test-unit")

    ops.restore_file("script.py")

    # Expect namespaced ref
    expected_ref = f"refs/heads/{BACKUP_NAMESPACE}/test-unit/main"
    mock_repo.checkout.assert_called_with(expected_ref, file="script.py")


def test_restore_dirty_fails(tmp_path: Path, mocker: MagicMock) -> None:
    os.chdir(tmp_path)
    (tmp_path / "script.py").touch()

    mock_cls = mocker.patch("git_pulsar.ops.GitRepo")
    mock_repo = mock_cls.return_value
    mock_repo.status_porcelain.return_value = ["M script.py"]
    mock_repo.current_branch.return_value = "main"

    with pytest.raises(SystemExit):
        ops.restore_file("script.py")


def test_sync_session_success(mocker: MagicMock) -> None:
    """Should find latest backup and checkout."""
    mocker.patch("git_pulsar.ops.GitRepo")
    repo = mocker.patch("git_pulsar.ops.GitRepo").return_value
    repo.current_branch.return_value = "main"

    # 1. Setup candidates
    repo.list_refs.return_value = [
        f"refs/heads/{BACKUP_NAMESPACE}/laptop/main",
        f"refs/heads/{BACKUP_NAMESPACE}/desktop/main",
    ]

    # 2. Setup timestamps (desktop is newer)
    def mock_run(cmd: list[str], *args: Any, **kwargs: Any) -> str:
        # Check if "desktop" or "laptop" is in any part of the command list
        cmd_str = " ".join(cmd)
        if cmd[0] == "log" and "desktop" in cmd_str:
            return "2000"
        if cmd[0] == "log" and "laptop" in cmd_str:
            return "1000"
        return ""

    repo._run.side_effect = mock_run

    # 3. Setup tree diff (simulate remote != local)
    repo.write_tree.return_value = "local_tree"

    mocker.patch("builtins.input", return_value="y")

    ops.sync_session()

    # Verify we fetched all namespaces
    repo._run.assert_any_call(
        [
            "fetch",
            "origin",
            f"refs/heads/{BACKUP_NAMESPACE}/*:refs/heads/{BACKUP_NAMESPACE}/*",
        ],
        capture=True,
    )

    # Verify we checked out the Desktop ref (newer)
    # Search for the checkout call in the mock history
    checkout_call = [
        c for c in repo._run.call_args_list if c[0][0] and "checkout" == c[0][0][0]
    ]
    assert checkout_call, "Checkout was never called!"

    cmd_args = checkout_call[0][0][0]  # extract the list passed to _run
    assert f"refs/heads/{BACKUP_NAMESPACE}/desktop/main" in cmd_args


# Finalize Tests


def test_finalize_octopus_merge(mocker: MagicMock) -> None:
    """Should squash merge multiple backup streams."""
    repo = mocker.patch("git_pulsar.ops.GitRepo").return_value
    repo.status_porcelain.return_value = []
    repo.current_branch.return_value = "main"

    # Found 3 backup streams
    repo.list_refs.return_value = ["ref_A", "ref_B", "ref_C"]

    ops.finalize_work()

    # 1. Verify Fetch
    repo._run.assert_any_call(
        [
            "fetch",
            "origin",
            f"refs/heads/{BACKUP_NAMESPACE}/*:refs/heads/{BACKUP_NAMESPACE}/*",
        ],
        capture=True,
    )

    # 2. Verify Octopus Merge
    repo.merge_squash.assert_called_with("ref_A", "ref_B", "ref_C")

    # 3. Verify Commit
    repo.commit_interactive.assert_called_once()
