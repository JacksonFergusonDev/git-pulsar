from pathlib import Path
from unittest.mock import MagicMock

from git_pulsar import daemon
from git_pulsar.constants import BACKUP_NAMESPACE
from git_pulsar.daemon import Config


def test_run_backup_shadow_commit_flow(tmp_path: Path, mocker: MagicMock) -> None:
    """
    Ensure the daemon uses plumbing commands (write-tree, commit-tree)
    and isolates the index via GIT_INDEX_FILE.
    """
    (tmp_path / ".git").mkdir()

    # 1. Mock System & Identity
    mocker.patch("git_pulsar.daemon.SYSTEM.is_under_load", return_value=False)
    mocker.patch("git_pulsar.daemon.SYSTEM.get_battery", return_value=(100, True))
    mocker.patch("git_pulsar.daemon.get_machine_id", return_value="test-unit")
    mocker.patch("socket.gethostname", return_value="test-unit")

    # 2. Mock Configuration (CRITICAL: Ensure tests don't read ~/.config)
    # We provide a fresh default configuration
    mocker.patch("git_pulsar.daemon.CONFIG", Config())

    # 3. Mock GitRepo
    mock_cls = mocker.patch("git_pulsar.daemon.GitRepo")
    repo = mock_cls.return_value
    repo.path = tmp_path
    repo.current_branch.return_value = "main"

    # Mock Plumbing Returns
    repo.write_tree.return_value = "tree_sha"
    repo.commit_tree.return_value = "commit_sha"
    repo.rev_parse.side_effect = lambda x: "parent_sha" if "HEAD" in x else None

    # 4. Mock Network
    mocker.patch("git_pulsar.daemon.get_remote_host", return_value="github.com")
    mocker.patch("git_pulsar.daemon.is_remote_reachable", return_value=True)

    # ACTION
    daemon.run_backup(str(tmp_path))

    # VERIFICATION

    # A. Check Isolation (GIT_INDEX_FILE)
    add_call = repo._run.call_args_list[0]
    args, kwargs = add_call
    assert args[0] == ["add", "."]
    assert "GIT_INDEX_FILE" in kwargs["env"]
    assert "pulsar_index" in kwargs["env"]["GIT_INDEX_FILE"]

    # B. Check Plumbing Sequence
    repo.write_tree.assert_called_once()
    repo.commit_tree.assert_called_once()

    # C. Check Ref Update
    repo.update_ref.assert_called_once()
    assert (
        f"refs/heads/{BACKUP_NAMESPACE}/test-unit/main"
        in repo.update_ref.call_args[0][0]
    )

    # D. Check Push
    push_call = repo._run.call_args_list[-1]
    cmd = push_call[0][0]
    assert "push" in cmd
    assert (
        f"refs/heads/{BACKUP_NAMESPACE}/test-unit/main:refs/heads/{BACKUP_NAMESPACE}/test-unit/main"
        in cmd
    )


def test_run_backup_skips_if_no_changes(tmp_path: Path, mocker: MagicMock) -> None:
    """Optimization check: Don't commit if tree matches parent backup."""
    (tmp_path / ".git").mkdir()

    mocker.patch("git_pulsar.daemon.SYSTEM.is_under_load", return_value=False)
    mocker.patch("git_pulsar.daemon.get_machine_id", return_value="test-unit")

    # Mock Config here as well
    mocker.patch("git_pulsar.daemon.CONFIG", Config())
    mock_cls = mocker.patch("git_pulsar.daemon.GitRepo")
    repo = mock_cls.return_value
    repo.current_branch.return_value = "main"

    # Setup: Previous backup exists
    repo.rev_parse.return_value = "backup_sha"
    repo.write_tree.return_value = "tree_sha_X"
    repo._run.return_value = "tree_sha_X"  # matches parent

    daemon.run_backup(str(tmp_path))

    # Should NOT commit
    repo.commit_tree.assert_not_called()
    repo.update_ref.assert_not_called()
