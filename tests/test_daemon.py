from pathlib import Path
from unittest.mock import MagicMock

from git_pulsar import daemon
from git_pulsar.constants import BACKUP_NAMESPACE
from git_pulsar.daemon import Config


def test_run_backup_shadow_commit_flow(tmp_path: Path, mocker: MagicMock) -> None:
    """
    Verifies the standard backup workflow,
    ensuring isolation and correct git plumbing usage.

    This test checks that:
    1. A temporary index is used (via GIT_INDEX_FILE).
    2. Git plumbing commands (write-tree, commit-tree) are invoked.
    3. The backup reference is updated and pushed.

    Args:
        tmp_path (Path): Pytest fixture for a temporary directory.
        mocker (MagicMock): Pytest fixture for mocking.
    """
    (tmp_path / ".git").mkdir()

    # Mock system dependencies and identity resolution.
    mocker.patch("git_pulsar.daemon.SYSTEM.is_under_load", return_value=False)
    mocker.patch("git_pulsar.daemon.SYSTEM.get_battery", return_value=(100, True))
    mocker.patch("git_pulsar.daemon.get_machine_id", return_value="test-unit")
    mocker.patch("socket.gethostname", return_value="test-unit")

    # Mock configuration to prevent reading from user's disk.
    mocker.patch("git_pulsar.daemon.CONFIG", Config())

    # Mock GitRepo and its interactions.
    mock_cls = mocker.patch("git_pulsar.daemon.GitRepo")
    repo = mock_cls.return_value
    repo.path = tmp_path
    repo.current_branch.return_value = "main"

    # Setup mock return values for git plumbing commands.
    repo.write_tree.return_value = "tree_sha"
    repo.commit_tree.return_value = "commit_sha"
    repo.rev_parse.side_effect = lambda x: "parent_sha" if "HEAD" in x else None

    # Mock network connectivity checks.
    mocker.patch("git_pulsar.daemon.get_remote_host", return_value="github.com")
    mocker.patch("git_pulsar.daemon.is_remote_reachable", return_value=True)

    # ACTION
    daemon.run_backup(str(tmp_path))

    # VERIFICATION

    # Verify that operations used the temporary index.
    add_call = repo._run.call_args_list[0]
    args, kwargs = add_call
    assert args[0] == ["add", "."]
    assert "GIT_INDEX_FILE" in kwargs["env"]
    assert "pulsar_index" in kwargs["env"]["GIT_INDEX_FILE"]

    # Verify git plumbing commands were called.
    repo.write_tree.assert_called_once()
    repo.commit_tree.assert_called_once()

    # Verify the local backup reference was updated.
    repo.update_ref.assert_called_once()
    assert (
        f"refs/heads/{BACKUP_NAMESPACE}/test-unit/main"
        in repo.update_ref.call_args[0][0]
    )

    # Verify the backup reference was pushed to the remote.
    push_call = repo._run.call_args_list[-1]
    cmd = push_call[0][0]
    assert "push" in cmd
    assert (
        f"refs/heads/{BACKUP_NAMESPACE}/test-unit/main:refs/heads/{BACKUP_NAMESPACE}/test-unit/main"
        in cmd
    )


def test_run_backup_skips_if_no_changes(tmp_path: Path, mocker: MagicMock) -> None:
    """Verifies that the backup process aborts early if no changes are detected.

    If the current working directory tree matches the tree of the previous backup,
    no new commit should be created.

    Args:
        tmp_path (Path): Pytest fixture for a temporary directory.
        mocker (MagicMock): Pytest fixture for mocking.
    """
    (tmp_path / ".git").mkdir()

    mocker.patch("git_pulsar.daemon.SYSTEM.is_under_load", return_value=False)
    mocker.patch("git_pulsar.daemon.get_machine_id", return_value="test-unit")

    # Mock configuration.
    mocker.patch("git_pulsar.daemon.CONFIG", Config())
    mock_cls = mocker.patch("git_pulsar.daemon.GitRepo")
    repo = mock_cls.return_value
    repo.current_branch.return_value = "main"

    # Simulate existing backup with identical tree.
    repo.rev_parse.return_value = "backup_sha"
    repo.write_tree.return_value = "tree_sha_X"
    repo._run.return_value = "tree_sha_X"  # matches parent

    daemon.run_backup(str(tmp_path))

    # Ensure no commit or push occurred.
    repo.commit_tree.assert_not_called()
    repo.update_ref.assert_not_called()
