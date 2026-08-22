import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from git_pulsar import ops
from git_pulsar.config import Config
from git_pulsar.constants import BACKUP_NAMESPACE
from git_pulsar.types import DriftState

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
    mocker.patch("git_pulsar.ops.Config.load").return_value.daemon.sync_enabled = True
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
    def mock_get_commit_timestamp(ref: str) -> int:
        if "desktop" in ref:
            return 2000
        if "laptop" in ref:
            return 1000
        return 0

    repo.get_commit_timestamp.side_effect = mock_get_commit_timestamp
    repo.get_last_commit_time.return_value = "5 minutes ago"

    # 3. Setup tree diff (simulate remote tree != local tree).
    repo.write_tree.return_value = "local_tree"
    repo._run.return_value = "remote_tree"

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


def test_finalize_aborts_on_merge_conflict(mocker: MagicMock) -> None:
    """Verifies that merge conflicts during octopus squash abort with exit code 1."""
    repo = mocker.patch("git_pulsar.ops.GitRepo").return_value
    repo.status_porcelain.return_value = []
    repo.current_branch.return_value = "feature-branch"
    repo.rev_parse.side_effect = ["sha", None]
    repo.diff_shortstat.return_value = (2, 10, 5)
    repo.get_last_commit_time.return_value = "2 hours ago"

    mocker.patch("git_pulsar.ops.console")
    mocker.patch("git_pulsar.ops.Confirm.ask", return_value=True)
    repo.list_refs.return_value = ["ref_A"]
    repo.merge_squash.side_effect = RuntimeError("Conflict detected")

    with pytest.raises(SystemExit) as excinfo:
        ops.finalize_work()

    assert excinfo.value.code == 1
    repo.commit_interactive.assert_not_called()


# --- Roaming Radar & State Tests ---


def test_get_remote_drift_state_no_branch(tmp_path: Path, mocker: MagicMock) -> None:
    mock_cls = mocker.patch("git_pulsar.ops.GitRepo")
    repo = mock_cls.return_value
    repo.current_branch.return_value = ""

    drift, ts, _machine, _warning = ops.get_remote_drift_state(tmp_path)
    assert not drift
    assert ts == 0


def test_get_remote_drift_state_fetch_fails(tmp_path: Path, mocker: MagicMock) -> None:
    mock_cls = mocker.patch("git_pulsar.ops.GitRepo")
    repo = mock_cls.return_value
    repo.current_branch.return_value = "main"
    repo._run.side_effect = Exception("Network offline")

    drift, _ts, _machine, _warning = ops.get_remote_drift_state(tmp_path)
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

    drift, ts, _machine, _warning = ops.get_remote_drift_state(tmp_path)
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

    def mock_get_commit_timestamp(ref: str) -> int:
        if "desktop" in ref:
            return 2000
        if "laptop" in ref:
            return 1000
        return 0

    repo.get_commit_timestamp.side_effect = mock_get_commit_timestamp
    repo._run.return_value = ""
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

    ops.set_drift_state(tmp_path, DriftState(999.9, 200))

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


def test_restore_file_failure_exits_1(mocker: MagicMock) -> None:
    """Verifies that restore_file exits with code 1 when checkout fails."""
    mock_cls = mocker.patch("git_pulsar.ops.GitRepo")
    mock_repo = mock_cls.return_value
    mock_repo.status_porcelain.return_value = []
    mock_repo.checkout.side_effect = RuntimeError("Checkout failed")
    mocker.patch("git_pulsar.ops.console")

    with pytest.raises(SystemExit) as excinfo:
        ops.restore_file("script.py")

    assert excinfo.value.code == 1


def test_restore_file_force_overwrites(mocker: MagicMock) -> None:
    """Verifies that restore_file with force=True bypasses the interactive prompt."""
    mock_cls = mocker.patch("git_pulsar.ops.GitRepo")
    mock_repo = mock_cls.return_value
    mock_repo.status_porcelain.return_value = ["M script.py"]
    mocker.patch("git_pulsar.ops.console")
    mock_prompt = mocker.patch("git_pulsar.ops.Prompt.ask")

    ops.restore_file("script.py", force=True)

    mock_prompt.assert_not_called()
    mock_repo.checkout.assert_called_once()


def test_sync_session_disabled_exits_1(mocker: MagicMock) -> None:
    """Verifies that sync_session exits with code 1 if sync is disabled in config."""
    mocker.patch("git_pulsar.ops.Config.load").return_value.daemon.sync_enabled = False
    mocker.patch("git_pulsar.ops.console")

    with pytest.raises(SystemExit) as excinfo:
        ops.sync_session()

    assert excinfo.value.code == 1


def test_sync_session_no_backups_returns(mocker: MagicMock) -> None:
    """Verifies that sync_session exits early when no backups exist for current branch."""
    mocker.patch("git_pulsar.ops.Config.load").return_value.daemon.sync_enabled = True
    repo = mocker.patch("git_pulsar.ops.GitRepo").return_value
    repo.current_branch.return_value = "main"
    repo.list_refs.return_value = []
    mocker.patch("git_pulsar.ops.console")

    ops.sync_session()
    repo.write_tree.assert_not_called()


def test_sync_session_already_up_to_date(mocker: MagicMock) -> None:
    """Verifies that sync_session exits early without overwriting when trees match."""
    mocker.patch("git_pulsar.ops.Config.load").return_value.daemon.sync_enabled = True
    repo = mocker.patch("git_pulsar.ops.GitRepo").return_value
    repo.current_branch.return_value = "main"
    repo.list_refs.return_value = ["refs/heads/wip/pulsar/laptop/main"]

    def mock_run(cmd: list[str], *args: Any, **kwargs: Any) -> str:
        if cmd[0] == "rev-parse":
            return "matching_tree_sha"
        return ""

    repo._run.side_effect = mock_run
    repo.get_commit_timestamp.return_value = 1000
    repo.get_last_commit_time.return_value = "5 minutes ago"
    repo.write_tree.return_value = "matching_tree_sha"
    mocker.patch("git_pulsar.ops.console")

    ops.sync_session()

    # Verify no checkout occurred
    for call in repo._run.call_args_list:
        args = call[0][0]
        assert "checkout" not in args


def test_sync_session_user_declines_sync(mocker: MagicMock) -> None:
    """Verifies that declining the sync prompt aborts cleanly with exit code 0."""
    mocker.patch("git_pulsar.ops.Config.load").return_value.daemon.sync_enabled = True
    repo = mocker.patch("git_pulsar.ops.GitRepo").return_value
    repo.current_branch.return_value = "main"
    repo.list_refs.return_value = ["refs/heads/wip/pulsar/laptop/main"]

    def mock_run(cmd: list[str], *args: Any, **kwargs: Any) -> str:
        if cmd[0] == "rev-parse":
            return "remote_tree_sha"
        return ""

    repo._run.side_effect = mock_run
    repo.get_commit_timestamp.return_value = 1000
    repo.get_last_commit_time.return_value = "5 minutes ago"
    repo.write_tree.return_value = "local_tree_sha"

    mock_console = mocker.patch("git_pulsar.ops.console")
    mock_console.input.return_value = "n"

    with pytest.raises(SystemExit) as excinfo:
        ops.sync_session()

    assert excinfo.value.code == 0


def test_finalize_work_dirty_working_tree_aborts(mocker: MagicMock) -> None:
    """Verifies that finalize_work aborts with exit code 1 if uncommitted changes exist."""
    repo = mocker.patch("git_pulsar.ops.GitRepo").return_value
    repo.status_porcelain.return_value = ["M file.txt"]
    mocker.patch("git_pulsar.ops.console")

    with pytest.raises(SystemExit) as excinfo:
        ops.finalize_work()

    assert excinfo.value.code == 1


def test_finalize_work_no_backups_aborts(mocker: MagicMock) -> None:
    """Verifies that finalize_work aborts with exit code 1 if no backup refs exist."""
    repo = mocker.patch("git_pulsar.ops.GitRepo").return_value
    repo.status_porcelain.return_value = []
    repo.current_branch.return_value = "main"
    repo.list_refs.return_value = []
    mocker.patch("git_pulsar.ops.Config.load").return_value.daemon.sync_enabled = False
    mocker.patch("git_pulsar.ops.console")

    with pytest.raises(SystemExit) as excinfo:
        ops.finalize_work()

    assert excinfo.value.code == 1


def test_prune_backups_deletes_old_refs(tmp_path: Path, mocker: MagicMock) -> None:
    """Verifies that prune_backups deletes refs older than cutoff and runs gc."""
    repo = mocker.patch("git_pulsar.ops.GitRepo").return_value
    repo.list_refs.return_value = [
        "refs/heads/wip/pulsar/laptop/main",
        "refs/heads/wip/pulsar/laptop/old_branch",
    ]

    current_time = 1000000.0
    mocker.patch("time.time", return_value=current_time)

    # First ref: 5 days old (keep), Second ref: 35 days old (delete)
    cutoff_ts_old = int(current_time - (35 * 86400))
    cutoff_ts_fresh = int(current_time - (5 * 86400))
    repo.get_commit_timestamp.side_effect = [
        cutoff_ts_fresh,  # ref 1
        cutoff_ts_old,  # ref 2
    ]
    repo._run.return_value = ""
    mocker.patch("git_pulsar.ops.console")

    ops.prune_backups(days=30, repo_path=tmp_path)

    repo._run.assert_any_call(
        ["update-ref", "-d", "refs/heads/wip/pulsar/laptop/old_branch"], capture=False
    )
    repo._run.assert_any_call(["gc", "--auto"], capture=True)


def test_prune_backups_no_stale_refs(tmp_path: Path, mocker: MagicMock) -> None:
    """Verifies that prune_backups does not trigger gc when no stale refs exist."""
    repo = mocker.patch("git_pulsar.ops.GitRepo").return_value
    repo.list_refs.return_value = ["refs/heads/wip/pulsar/laptop/main"]

    current_time = 1000000.0
    mocker.patch("time.time", return_value=current_time)
    cutoff_ts_fresh = str(int(current_time - (5 * 86400)))
    repo._run.side_effect = [cutoff_ts_fresh]
    mocker.patch("git_pulsar.ops.console")

    ops.prune_backups(days=30, repo_path=tmp_path)

    for call in repo._run.call_args_list:
        assert "gc" not in call[0][0]


def test_add_ignore_modifies_gitignore(tmp_path: Path, mocker: MagicMock) -> None:
    """Verifies that add_ignore appends pattern to .gitignore and handles already present."""
    mocker.patch.object(Path, "cwd", return_value=tmp_path)
    mock_config = Config()
    mock_config.files.manage_gitignore = True
    mocker.patch("git_pulsar.ops.Config.load", return_value=mock_config)
    mocker.patch("git_pulsar.ops.GitRepo").return_value._run.return_value = ""
    mocker.patch("git_pulsar.ops.console")

    ops.add_ignore("*.log")

    gitignore = tmp_path / ".gitignore"
    assert gitignore.exists()
    assert "*.log" in gitignore.read_text()

    # Running again should not duplicate
    ops.add_ignore("*.log")
    lines = gitignore.read_text().splitlines()
    assert lines.count("*.log") == 1


def test_add_ignore_skips_when_disabled(tmp_path: Path, mocker: MagicMock) -> None:
    """Verifies that add_ignore does not modify .gitignore if manage_gitignore is False."""
    mocker.patch.object(Path, "cwd", return_value=tmp_path)
    mock_config = Config()
    mock_config.files.manage_gitignore = False
    mocker.patch("git_pulsar.ops.Config.load", return_value=mock_config)
    mocker.patch("git_pulsar.ops.GitRepo").return_value._run.return_value = ""
    mocker.patch("git_pulsar.ops.console")

    ops.add_ignore("*.log")
    assert not (tmp_path / ".gitignore").exists()


def test_add_ignore_untracks_files_on_confirm(
    tmp_path: Path, mocker: MagicMock
) -> None:
    """Verifies that add_ignore runs git rm --cached if files are tracked and user confirms."""
    mocker.patch.object(Path, "cwd", return_value=tmp_path)
    mock_config = Config()
    mock_config.files.manage_gitignore = True
    mocker.patch("git_pulsar.ops.Config.load", return_value=mock_config)

    repo = mocker.patch("git_pulsar.ops.GitRepo").return_value
    repo._run.return_value = "app.log"
    mock_console = mocker.patch("git_pulsar.ops.console")
    mock_console.input.return_value = "y"

    ops.add_ignore("*.log")

    repo._run.assert_any_call(["rm", "--cached", "*.log"], capture=False)


def test_has_large_files_returns_false_on_small_or_error(
    tmp_path: Path, mocker: MagicMock
) -> None:
    """Verifies has_large_files returns False for small files and on subprocess errors."""
    import subprocess

    conf = Config()
    conf.limits.large_file_threshold = 1000

    (tmp_path / "small.txt").write_text("small")
    mocker.patch("subprocess.check_output", return_value="small.txt\n")

    assert not ops.has_large_files(tmp_path, conf)

    # Subprocess error
    mocker.patch(
        "subprocess.check_output",
        side_effect=subprocess.CalledProcessError(1, "git ls-files"),
    )
    assert not ops.has_large_files(tmp_path, conf)


def test_ignore_pattern_exact_line_match(tmp_path: Path, mocker: MagicMock) -> None:
    """Verifies that ops.add_ignore checks exact lines instead of substring containment."""
    mocker.patch.object(Path, "cwd", return_value=tmp_path)
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("sub_foo.txt\n")

    mocker.patch("git_pulsar.ops.console")
    mock_git = mocker.patch("git_pulsar.ops.GitRepo")
    mock_git.return_value.status_porcelain.return_value = []

    ops.add_ignore("foo.txt")

    content = gitignore.read_text()
    assert "foo.txt" in content.splitlines()


def test_is_repo_paused_and_set_repo_paused(tmp_path: Path) -> None:
    """Verifies that is_repo_paused correctly checks and set_repo_paused toggles the sentinel."""
    (tmp_path / ".git").mkdir()

    # Initial state: not paused
    assert not ops.is_repo_paused(tmp_path)

    # Pause the repository
    ops.set_repo_paused(tmp_path, True)
    assert ops.is_repo_paused(tmp_path)
    assert (tmp_path / ".git" / "pulsar_paused").exists()

    # Resume the repository
    ops.set_repo_paused(tmp_path, False)
    assert not ops.is_repo_paused(tmp_path)
    assert not (tmp_path / ".git" / "pulsar_paused").exists()

    # Resuming when already not paused does not error
    ops.set_repo_paused(tmp_path, False)
    assert not ops.is_repo_paused(tmp_path)


def test_parse_backup_ref() -> None:
    """Verifies that parse_backup_ref extracts components correctly."""
    # Standard ref with compound slug
    ref1 = f"refs/heads/{BACKUP_NAMESPACE}/macbook-pro--a1b2c3d4/main"
    info1 = ops.parse_backup_ref(ref1)
    assert info1 is not None
    assert info1.ref == ref1
    assert info1.slug == "macbook-pro--a1b2c3d4"
    assert info1.machine_name == "macbook-pro"
    assert info1.branch == "main"

    # Multi-segment branch name (e.g. feature branch)
    ref2 = f"refs/heads/{BACKUP_NAMESPACE}/desktop/feature/login/oauth"
    info2 = ops.parse_backup_ref(ref2)
    assert info2 is not None
    assert info2.slug == "desktop"
    assert info2.machine_name == "desktop"
    assert info2.branch == "feature/login/oauth"

    # Non-matching ref prefix returns None
    assert ops.parse_backup_ref("refs/heads/main") is None
    assert ops.parse_backup_ref("refs/remotes/origin/main") is None
    assert (
        ops.parse_backup_ref(f"refs/heads/{BACKUP_NAMESPACE}/invalid_no_slash") is None
    )


def test_get_remote_backup_ref(mocker: MagicMock) -> None:
    """Verifies get_remote_backup_ref creates correct remote tracking reference."""
    mocker.patch("git_pulsar.system.get_identity_slug", return_value="my-device--99")
    remote_ref = ops.get_remote_backup_ref("main", remote_name="upstream")
    assert remote_ref == f"refs/remotes/upstream/{BACKUP_NAMESPACE}/my-device--99/main"


def test_fetch_backup_refs(tmp_path: Path, mocker: MagicMock) -> None:
    """Verifies fetch_backup_refs constructs the appropriate refspecs and handles errors."""
    (tmp_path / ".git").mkdir()
    mock_git = mocker.patch("git_pulsar.ops.GitRepo").return_value

    # Specific branch fetch
    mock_git._run.return_value = ""
    res = ops.fetch_backup_refs(mock_git, branch="dev", remote_name="origin")
    assert res is True
    mock_git._run.assert_called_with(
        [
            "fetch",
            "origin",
            f"refs/heads/{BACKUP_NAMESPACE}/*/dev:refs/heads/{BACKUP_NAMESPACE}/*/dev",
        ],
        capture=True,
    )

    # All branches fetch
    ops.fetch_backup_refs(mock_git, branch=None, remote_name="origin")
    mock_git._run.assert_called_with(
        [
            "fetch",
            "origin",
            f"refs/heads/{BACKUP_NAMESPACE}/*:refs/heads/{BACKUP_NAMESPACE}/*",
        ],
        capture=True,
    )

    # Network error handling
    mock_git._run.side_effect = RuntimeError("network offline")
    assert ops.fetch_backup_refs(mock_git, branch="dev") is False
