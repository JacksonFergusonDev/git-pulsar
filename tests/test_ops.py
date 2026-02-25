import json
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
    1. Execution of `uv init` with configured python version.
    2. Creation of `.envrc` and VS Code settings using configured venv_dir.
    3. Injection of venv_dir into .gitignore via add_ignore to prevent index bloat.

    Args:
        tmp_path (Path): Pytest fixture for a temporary directory.
        mocker (MagicMock): Pytest fixture for mocking.
    """
    mocker.patch("sys.platform", "darwin")
    os.chdir(tmp_path)
    mocker.patch("shutil.which", return_value="/usr/bin/fake")
    mock_run = mocker.patch("subprocess.run")
    mocker.patch("git_pulsar.ops.console")

    # 1. Mock the Config payload to use a custom venv_dir and python version
    mock_config = mocker.patch("git_pulsar.ops.Config").load.return_value
    mock_config.env.python_version = "3.12"
    mock_config.env.venv_dir = ".custom_venv"
    mock_config.env.generate_direnv = True
    mock_config.env.generate_vscode_settings = True

    # 2. Mock add_ignore so we can verify it gets called
    mock_add_ignore = mocker.patch("git_pulsar.ops.add_ignore")

    ops.bootstrap_env()

    # Assert `uv init` used the configured python version
    mock_run.assert_any_call(
        ["uv", "init", "--no-workspace", "--python", "3.12"], check=True
    )

    # Assert .envrc was created with the custom venv directory
    envrc = tmp_path / ".envrc"
    assert envrc.exists()
    assert "source .custom_venv/bin/activate" in envrc.read_text()

    # Assert VS Code settings were created with the custom venv directory
    settings = tmp_path / ".vscode" / "settings.json"
    assert settings.exists()
    assert ".custom_venv/bin/python" in settings.read_text()

    # Assert our new safety measure triggered correctly
    mock_add_ignore.assert_called_once_with(".custom_venv/")


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


def test_restore_dirty_cancels(tmp_path: Path, mocker: MagicMock) -> None:
    """Verifies that selecting [C]ancel exits cleanly with code 0.

    Args:
        tmp_path (Path): Pytest fixture for a temporary directory.
        mocker (MagicMock): Pytest fixture for mocking.
    """
    os.chdir(tmp_path)
    (tmp_path / "script.py").touch()

    mock_cls = mocker.patch("git_pulsar.ops.GitRepo")
    mock_repo = mock_cls.return_value
    mock_repo.status_porcelain.return_value = ["M script.py"]
    mocker.patch("git_pulsar.ops.get_backup_ref", return_value="refs/backup")
    mocker.patch("git_pulsar.ops.console")

    # Mock the prompt to return 'c' for cancel
    mocker.patch("git_pulsar.ops.Prompt.ask", return_value="c")

    with pytest.raises(SystemExit) as excinfo:
        ops.restore_file("script.py")

    assert excinfo.value.code == 0
    mock_repo.checkout.assert_not_called()


def test_restore_dirty_overwrites(tmp_path: Path, mocker: MagicMock) -> None:
    """Verifies that selecting [O]verwrite breaks the loop and restores the file.

    Args:
        tmp_path (Path): Pytest fixture for a temporary directory.
        mocker (MagicMock): Pytest fixture for mocking.
    """
    os.chdir(tmp_path)
    (tmp_path / "script.py").touch()

    mock_cls = mocker.patch("git_pulsar.ops.GitRepo")
    mock_repo = mock_cls.return_value
    mock_repo.status_porcelain.return_value = ["M script.py"]
    mocker.patch("git_pulsar.ops.get_backup_ref", return_value="refs/backup")
    mocker.patch("git_pulsar.ops.console")

    # Mock the prompt to return 'o' for overwrite
    mocker.patch("git_pulsar.ops.Prompt.ask", return_value="o")

    ops.restore_file("script.py")

    mock_repo.checkout.assert_called_once_with("refs/backup", file="script.py")


def test_restore_dirty_views_diff(tmp_path: Path, mocker: MagicMock) -> None:
    """Verifies that selecting [V]iew Diff executes run_diff and re-prompts.

    Args:
        tmp_path (Path): Pytest fixture for a temporary directory.
        mocker (MagicMock): Pytest fixture for mocking.
    """
    os.chdir(tmp_path)
    (tmp_path / "script.py").touch()

    mock_cls = mocker.patch("git_pulsar.ops.GitRepo")
    mock_repo = mock_cls.return_value
    mock_repo.status_porcelain.return_value = ["M script.py"]
    mocker.patch("git_pulsar.ops.get_backup_ref", return_value="refs/backup")
    mocker.patch("git_pulsar.ops.console")

    # Mock the prompt to return 'v' (view), then 'c' (cancel) on the second pass
    mocker.patch("git_pulsar.ops.Prompt.ask", side_effect=["v", "c"])

    with pytest.raises(SystemExit):
        ops.restore_file("script.py")

    mock_repo.run_diff.assert_called_once_with("refs/backup", file="script.py")
    mock_repo.checkout.assert_not_called()


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
    """Verifies that `finalize_work` performs an octopus squash merge of backup streams."""
    repo = mocker.patch("git_pulsar.ops.GitRepo").return_value
    repo.status_porcelain.return_value = []
    repo.current_branch.return_value = "feature-branch"
    repo.rev_parse.side_effect = ["sha", None]  # main exists, master doesn't
    repo.diff_shortstat.return_value = (2, 10, 5)

    # Provide a string so rich doesn't panic
    repo.get_last_commit_time.return_value = "2 hours ago"

    mocker.patch("git_pulsar.ops.console")

    # Mock the new pre-flight confirmation to proceed
    mocker.patch("git_pulsar.ops.Confirm.ask", return_value=True)

    # Simulate finding 3 backup streams.
    repo.list_refs.return_value = ["ref_A", "ref_B", "ref_C"]

    ops.finalize_work()

    # 1. Verify target branch switch happened
    repo.checkout.assert_called_with("main")

    # 2. Verify Octopus Merge of all streams.
    repo.merge_squash.assert_called_with("ref_A", "ref_B", "ref_C")

    # 3. Verify Interactive Commit trigger.
    repo.commit_interactive.assert_called_once()


def test_finalize_aborts_on_user_decline(mocker: MagicMock) -> None:
    """Verifies that declining the pre-flight checklist exits cleanly without checking out."""
    repo = mocker.patch("git_pulsar.ops.GitRepo").return_value
    repo.status_porcelain.return_value = []
    repo.current_branch.return_value = "feature-branch"
    repo.rev_parse.side_effect = ["sha", None]
    repo.diff_shortstat.return_value = (2, 10, 5)

    # Provide a string so rich doesn't panic
    repo.get_last_commit_time.return_value = "2 hours ago"

    mocker.patch("git_pulsar.ops.console")

    # Mock the pre-flight confirmation to abort
    mocker.patch("git_pulsar.ops.Confirm.ask", return_value=False)
    repo.list_refs.return_value = ["ref_A", "ref_B"]

    with pytest.raises(SystemExit) as excinfo:
        ops.finalize_work()

    assert excinfo.value.code == 0

    # Crucially, verify we never switched branches or merged
    repo.checkout.assert_not_called()
    repo.merge_squash.assert_not_called()


# --- Roaming Radar & State Tests ---


def test_get_remote_drift_state_no_branch(tmp_path: Path, mocker: MagicMock) -> None:
    mock_cls = mocker.patch("git_pulsar.ops.GitRepo")
    repo = mock_cls.return_value
    repo.current_branch.return_value = ""

    drift, ts, machine, warning = ops.get_remote_drift_state(tmp_path)
    assert not drift
    assert ts == 0


def test_get_remote_drift_state_fetch_fails(tmp_path: Path, mocker: MagicMock) -> None:
    mock_cls = mocker.patch("git_pulsar.ops.GitRepo")
    repo = mock_cls.return_value
    repo.current_branch.return_value = "main"
    repo._run.side_effect = Exception("Network offline")

    drift, ts, machine, warning = ops.get_remote_drift_state(tmp_path)
    assert not drift


def test_get_remote_drift_state_local_is_newer(
    tmp_path: Path, mocker: MagicMock
) -> None:
    mock_cls = mocker.patch("git_pulsar.ops.GitRepo")
    repo = mock_cls.return_value
    repo.current_branch.return_value = "main"

    mocker.patch("git_pulsar.system.get_identity_slug", return_value="laptop--123")
    mocker.patch(
        "git_pulsar.ops.get_backup_ref",
        return_value="refs/heads/wip/pulsar/laptop--123/main",
    )

    repo.list_refs.return_value = [
        "refs/heads/wip/pulsar/desktop--456/main",
        "refs/heads/wip/pulsar/laptop--123/main",
    ]

    def mock_run_side_effect(cmd: list[str], **kwargs: Any) -> str:
        if cmd[0] == "fetch":
            return ""
        if cmd[0] == "log":
            if "desktop" in cmd[-1]:
                return "1000"
            if "laptop" in cmd[-1]:
                return "2000"
        return "0"

    repo._run.side_effect = mock_run_side_effect

    drift, ts, machine, warning = ops.get_remote_drift_state(tmp_path)
    assert not drift
    assert ts == 0


def test_get_remote_drift_state_remote_is_newer(
    tmp_path: Path, mocker: MagicMock
) -> None:
    mock_cls = mocker.patch("git_pulsar.ops.GitRepo")
    repo = mock_cls.return_value
    repo.current_branch.return_value = "main"

    mocker.patch("git_pulsar.system.get_identity_slug", return_value="laptop--123")
    mocker.patch(
        "git_pulsar.ops.get_backup_ref",
        return_value="refs/heads/wip/pulsar/laptop--123/main",
    )

    repo.list_refs.return_value = [
        "refs/heads/wip/pulsar/desktop--456/main",
        "refs/heads/wip/pulsar/laptop--123/main",
    ]

    def mock_run_side_effect(cmd: list[str], **kwargs: Any) -> str:
        if cmd[0] == "fetch":
            return ""
        if cmd[0] == "log":
            if "desktop" in cmd[-1]:
                return "2000"
            if "laptop" in cmd[-1]:
                return "1000"
        return "0"

    repo._run.side_effect = mock_run_side_effect
    mocker.patch("time.time", return_value=2900.0)

    drift, ts, machine, warning = ops.get_remote_drift_state(tmp_path)
    assert drift is True
    assert ts == 2000
    assert machine == "desktop--456"
    assert "15 mins" in warning


def test_get_drift_state_empty(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    last_check, warned_ts = ops.get_drift_state(tmp_path)
    assert last_check == 0.0
    assert warned_ts == 0


def test_get_drift_state_valid(tmp_path: Path) -> None:
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    state_file = git_dir / "pulsar_drift_state"
    state_file.write_text(json.dumps({"last_check_ts": 500.5, "warned_remote_ts": 100}))

    last_check, warned_ts = ops.get_drift_state(tmp_path)
    assert last_check == 500.5
    assert warned_ts == 100


def test_set_drift_state_atomic(tmp_path: Path) -> None:
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    state_file = git_dir / "pulsar_drift_state"

    ops.set_drift_state(tmp_path, 999.9, 200)

    assert state_file.exists()
    data = json.loads(state_file.read_text())
    assert data["last_check_ts"] == 999.9
    assert data["warned_remote_ts"] == 200


def test_has_large_files_uses_config_limit(tmp_path: Path, mocker: MagicMock) -> None:
    """Verifies that `has_large_files` uses the configured threshold.

    Args:
        tmp_path (Path): Pytest fixture for a temporary directory.
        mocker (MagicMock): Pytest fixture for mocking.
    """
    from git_pulsar.config import Config

    mock_config = Config()
    # Set a custom small limit (500 bytes)
    mock_config.limits.large_file_threshold = 500

    # Mock the get_system factory directly in the ops module.
    # This completely isolates the test and prevents REAL desktop notifications
    # from firing on macOS or Linux.
    mock_strat = mocker.patch("git_pulsar.ops.system.get_system").return_value

    # Mock git ls-files to return a file
    mocker.patch("subprocess.check_output", return_value="big_file.txt")

    # Create the 'large' file in the isolated temp directory
    (tmp_path / "big_file.txt").write_text("a" * 600)  # 600 bytes > 500 limit

    result = ops.has_large_files(tmp_path, mock_config)

    assert result is True
    # Verify the mock strategy intercepted the call
    mock_strat.notify.assert_called_with("Backup Aborted", mocker.ANY)
