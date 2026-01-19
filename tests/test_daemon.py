from pathlib import Path
from unittest.mock import MagicMock

from src import daemon


def test_run_backup_shadow_commit_flow(tmp_path: Path, mocker: MagicMock) -> None:
    """
    Ensure the daemon uses plumbing commands (write-tree, commit-tree)
    and isolates the index via GIT_INDEX_FILE.
    """
    (tmp_path / ".git").mkdir()

    # 1. Mock System & Identity
    mocker.patch("src.daemon.SYSTEM.is_under_load", return_value=False)
    mocker.patch("src.daemon.SYSTEM.get_battery", return_value=(100, True))
    mocker.patch("src.daemon.get_machine_id", return_value="test-unit")
    mocker.patch("socket.gethostname", return_value="test-unit")  # fallback check

    # 2. Mock GitRepo
    mock_cls = mocker.patch("src.daemon.GitRepo")
    repo = mock_cls.return_value
    repo.path = tmp_path
    repo.current_branch.return_value = "main"

    # Mock Plumbing Returns
    repo.write_tree.return_value = "tree_sha"
    repo.commit_tree.return_value = "commit_sha"
    repo.rev_parse.side_effect = lambda x: "parent_sha" if "HEAD" in x else None

    # 3. Mock Network
    mocker.patch("src.daemon.get_remote_host", return_value="github.com")
    mocker.patch("src.daemon.is_remote_reachable", return_value=True)

    # ACTION
    daemon.run_backup(str(tmp_path))

    # VERIFICATION

    # A. Check Isolation (GIT_INDEX_FILE)
    # The 'add' command must have run with the env var set
    add_call = repo._run.call_args_list[0]
    # args: (['add', '.'],), kwargs: {'env': ...}
    args, kwargs = add_call
    assert args[0] == ["add", "."]
    assert "GIT_INDEX_FILE" in kwargs["env"]
    assert "pulsar_index" in kwargs["env"]["GIT_INDEX_FILE"]

    # B. Check Plumbing Sequence
    repo.write_tree.assert_called_once()
    repo.commit_tree.assert_called_once()

    # C. Check Ref Update
    # Should update refs/heads/wip/pulsar/test-unit/main
    repo.update_ref.assert_called_once()
    assert "refs/heads/wip/pulsar/test-unit/main" in repo.update_ref.call_args[0][0]

    # D. Check Push
    # Should push the specific refspec
    push_call = repo._run.call_args_list[-1]
    cmd = push_call[0][0]
    assert "push" in cmd
    # Check that we pushed ref:ref
    assert (
        "refs/heads/wip/pulsar/test-unit/main:refs/heads/wip/pulsar/test-unit/main"
        in cmd
    )


def test_run_backup_skips_if_no_changes(tmp_path: Path, mocker: MagicMock) -> None:
    """Optimization check: Don't commit if tree matches parent backup."""
    (tmp_path / ".git").mkdir()

    mocker.patch("src.daemon.SYSTEM.is_under_load", return_value=False)
    mocker.patch("src.daemon.get_machine_id", return_value="test-unit")

    mock_cls = mocker.patch("src.daemon.GitRepo")
    repo = mock_cls.return_value
    repo.current_branch.return_value = "main"

    # Setup: Previous backup exists
    repo.rev_parse.return_value = "backup_sha"
    repo.write_tree.return_value = "tree_sha_X"

    # The crucial mock: The previous backup's tree matches current tree
    repo._run.return_value = "tree_sha_X"  # return from 'rev-parse backup^{tree}'

    daemon.run_backup(str(tmp_path))

    # Should NOT commit
    repo.commit_tree.assert_not_called()
    repo.update_ref.assert_not_called()
