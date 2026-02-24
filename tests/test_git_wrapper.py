from pathlib import Path
from unittest.mock import MagicMock

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
