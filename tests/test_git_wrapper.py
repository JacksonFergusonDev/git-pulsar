import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from git_pulsar.git_wrapper import GitRepo


def test_list_refs_logs_error_on_failure(
    mocker: MagicMock, caplog: MagicMock, tmp_path: Path
) -> None:
    """Verifies that git failures are logged instead of passing silently."""
    # Mock subprocess to raise an exception
    mocker.patch("subprocess.run", side_effect=Exception("Git is broken"))

    # Create a fake .git directory so GitRepo accepts the path
    (tmp_path / ".git").mkdir()
    repo = GitRepo(tmp_path)

    # Run the method
    results = repo.list_refs("refs/heads/*")

    # Assert it handled the error gracefully
    assert results == []

    # Assert it logged the warning
    assert "Git error listing refs" in caplog.text


def test_run_diff_with_file_targeting(mocker: MagicMock, tmp_path: Path) -> None:
    """Verifies that run_diff correctly appends the file boundary double-dash."""
    (tmp_path / ".git").mkdir()
    repo = GitRepo(tmp_path)
    mock_run = mocker.patch.object(repo, "_run")

    # Diff against target without file
    repo.run_diff("HEAD")
    mock_run.assert_called_with(["diff", "HEAD"], capture=False)

    # Diff against target with specific file
    repo.run_diff("refs/backup/main", file="src/main.py")
    mock_run.assert_called_with(
        ["diff", "refs/backup/main", "--", "src/main.py"], capture=False
    )


def test_diff_shortstat_regex_parsing(mocker: MagicMock, tmp_path: Path) -> None:
    """Verifies that shortstat parses correctly, handling missing clauses."""
    (tmp_path / ".git").mkdir()
    repo = GitRepo(tmp_path)
    mock_run = mocker.patch.object(repo, "_run")

    # Case 1: Standard output with all three metrics
    mock_run.return_value = " 3 files changed, 25 insertions(+), 4 deletions(-)"
    assert repo.diff_shortstat("main", "backup_ref") == (3, 25, 4)

    # Case 2: Missing deletions clause
    mock_run.return_value = " 1 file changed, 10 insertions(+)"
    assert repo.diff_shortstat("main", "backup_ref") == (1, 10, 0)

    # Case 3: Missing insertions clause
    mock_run.return_value = " 2 files changed, 12 deletions(-)"
    assert repo.diff_shortstat("main", "backup_ref") == (2, 0, 12)

    # Case 4: Empty diff (branch is up to date)
    mock_run.return_value = ""
    assert repo.diff_shortstat("main", "backup_ref") == (0, 0, 0)


def test_git_plumbing_and_porcelain(tmp_path: Path) -> None:
    """Verifies the execution of high-level and plumbing git commands."""
    # Initialize a real local repository
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True)

    # Configure dummy identity for CI environments
    subprocess.run(
        ["git", "config", "user.name", "Test Runner"], cwd=tmp_path, check=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True
    )

    repo = GitRepo(tmp_path)

    assert repo.current_branch() == "main"

    # Test tracking and porcelain output
    test_file = tmp_path / "orbit_kinematics.py"
    test_file.write_text("import numpy as np\n")

    status = repo.status_porcelain()
    assert len(status) == 1
    assert "??" in status[0]

    repo.add_all()
    assert "A " in repo.status_porcelain()[0]

    repo.commit("Add initial orbital kinematic models")
    assert len(repo.status_porcelain()) == 0

    # Test plumbing functions (Tree and Commit object creation)
    test_file.write_text("import numpy as np\n# Added perturbed orbit logic\n")
    repo.add_all()

    tree_sha = repo.write_tree()
    assert len(tree_sha) == 40  # Standard SHA-1 length

    parent_sha = repo.rev_parse("HEAD")
    assert parent_sha is not None

    commit_sha = repo.commit_tree(tree_sha, [parent_sha], "Shadow backup commit")
    assert len(commit_sha) == 40

    # Test checkout mechanics
    repo.checkout(commit_sha)
    assert repo.rev_parse("HEAD") == commit_sha


def test_git_dir_resolution_worktree(tmp_path: Path) -> None:
    """Verifies that GitRepo.git_dir resolves correctly in standard repos and worktrees."""
    main_repo = tmp_path / "main_repo"
    main_repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=main_repo, check=True)
    subprocess.run(
        ["git", "config", "user.name", "Test Runner"], cwd=main_repo, check=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=main_repo, check=True
    )
    (main_repo / "README.md").write_text("# Main\n")
    subprocess.run(["git", "add", "README.md"], cwd=main_repo, check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=main_repo, check=True)

    repo = GitRepo(main_repo)
    assert repo.git_dir == (main_repo / ".git").resolve()

    # Create a git worktree
    worktree_path = tmp_path / "wt"
    subprocess.run(
        ["git", "worktree", "add", "-b", "wt-branch", str(worktree_path)],
        cwd=main_repo,
        check=True,
    )

    wt_repo = GitRepo(worktree_path)
    assert wt_repo.git_dir.exists()
    assert "worktrees" in str(wt_repo.git_dir)


def test_status_porcelain_pathspec_double_dash(
    mocker: MagicMock, tmp_path: Path
) -> None:
    """Verifies that status_porcelain adds the '--' pathspec separator."""
    (tmp_path / ".git").mkdir()
    repo = GitRepo(tmp_path)
    mock_run = mocker.patch.object(repo, "_run", return_value="")

    repo.status_porcelain("file.txt")
    mock_run.assert_called_once_with(["status", "--porcelain", "--", "file.txt"])


def test_git_repo_raises_on_non_repo(tmp_path: Path) -> None:
    """Verifies that GitRepo raises ValueError if the directory is not a git repository."""
    with pytest.raises(ValueError, match=r"Not a git repository"):
        GitRepo(tmp_path)


def test_run_raises_runtime_error_on_git_failure(tmp_path: Path) -> None:
    """Verifies that _run translates CalledProcessError into RuntimeError with output context."""
    (tmp_path / ".git").mkdir()
    repo = GitRepo(tmp_path)

    with pytest.raises(RuntimeError, match=r"Git error"):
        repo._run(["log", "--invalid-option-xyz"])


def test_update_ref_with_old_oid(mocker: MagicMock, tmp_path: Path) -> None:
    """Verifies that update_ref includes the old_oid argument when supplied."""
    (tmp_path / ".git").mkdir()
    repo = GitRepo(tmp_path)
    mock_run = mocker.patch.object(repo, "_run")

    repo.update_ref("refs/heads/backup", "new_sha123", old_oid="old_sha456")
    mock_run.assert_called_once_with(
        [
            "update-ref",
            "-m",
            "Pulsar backup",
            "refs/heads/backup",
            "new_sha123",
            "old_sha456",
        ]
    )


def test_update_ref_raises_on_failure(
    mocker: MagicMock, caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    """Verifies that update_ref logs a warning and re-raises exceptions on failure."""
    (tmp_path / ".git").mkdir()
    repo = GitRepo(tmp_path)
    mocker.patch.object(repo, "_run", side_effect=RuntimeError("Lock error"))

    with pytest.raises(RuntimeError, match="Lock error"):
        repo.update_ref("refs/heads/backup", "new_sha123")

    assert "Failed to update ref refs/heads/backup" in caplog.text


def test_merge_squash_no_op_on_empty(mocker: MagicMock, tmp_path: Path) -> None:
    """Verifies that merge_squash returns early without invoking git when no branches are passed."""
    (tmp_path / ".git").mkdir()
    repo = GitRepo(tmp_path)
    mock_run = mocker.patch.object(repo, "_run")

    repo.merge_squash()
    mock_run.assert_not_called()


def test_checkout_force_flag(mocker: MagicMock, tmp_path: Path) -> None:
    """Verifies that checkout appends the -f flag when force is True."""
    (tmp_path / ".git").mkdir()
    repo = GitRepo(tmp_path)
    mock_run = mocker.patch.object(repo, "_run")

    repo.checkout("main", force=True)
    mock_run.assert_called_once_with(["checkout", "-f", "main"], capture=False)


def test_commit_tree_raises_on_failure(
    mocker: MagicMock, caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    """Verifies that commit_tree logs a warning and re-raises on subprocess errors."""
    (tmp_path / ".git").mkdir()
    repo = GitRepo(tmp_path)
    mocker.patch.object(repo, "_run", side_effect=RuntimeError("Tree invalid"))

    with pytest.raises(RuntimeError, match="Tree invalid"):
        repo.commit_tree("tree_sha", ["parent_sha"], "Test message")

    assert "Failed to commit tree tree_sha" in caplog.text
