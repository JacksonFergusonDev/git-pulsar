from pathlib import Path
from unittest.mock import MagicMock, patch

from src import daemon


def test_is_repo_busy_detects_merge_head(tmp_path: Path) -> None:
    """Should return True if MERGE_HEAD exists in .git dir."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "MERGE_HEAD").touch()

    assert daemon.is_repo_busy(tmp_path) is True


def test_is_repo_busy_detects_index_lock(tmp_path: Path) -> None:
    """Should return True if index.lock exists (checking the race condition logic)."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "index.lock").touch()

    # Patch sleep so we don't actually wait 1.0s during tests
    with patch("time.sleep"):
        assert daemon.is_repo_busy(tmp_path) is True


def test_is_repo_busy_clean(tmp_path: Path) -> None:
    """Should return False if no lock files exist."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    assert daemon.is_repo_busy(tmp_path) is False


def test_run_backup_aborts_on_wrong_branch(tmp_path: Path, mocker: MagicMock) -> None:
    """Daemon should not backup if user is on 'main' instead of 'wip/pulsar'."""
    # 1. Setup real filesystem
    (tmp_path / ".git").mkdir()

    # 2. Patch Environment & Log
    mocker.patch("src.daemon.LOG_FILE", tmp_path / "test.log")
    # Ensure we pass the pre-flight checks
    mocker.patch("src.daemon.get_battery_status", return_value=(100, True))
    mocker.patch("src.daemon.is_system_under_load", return_value=False)

    # 3. Mock git calls
    mock_check_output = mocker.patch("subprocess.check_output")
    mock_run = mocker.patch("subprocess.run")

    # Simulate current branch = 'main'
    mock_check_output.side_effect = (
        lambda cmd, **kwargs: "main" if "branch" in cmd else ""
    )

    daemon.run_backup(str(tmp_path))

    # Assert we checked the branch
    assert mock_check_output.call_count > 0
    # Assert we NEVER called git commit or push
    mock_run.assert_not_called()


def test_run_backup_commits_dirty_state(tmp_path: Path, mocker: MagicMock) -> None:
    """Daemon should commit and push if conditions are perfect."""
    # 1. Setup real filesystem
    (tmp_path / ".git").mkdir()

    # 2. Patch Environment
    mocker.patch("src.daemon.LOG_FILE", tmp_path / "test.log")
    mocker.patch("src.daemon.is_repo_busy", return_value=False)
    mocker.patch("src.daemon.has_large_files", return_value=False)

    # Mock "Happy Path" environment
    mocker.patch("src.daemon.get_battery_status", return_value=(100, True))
    mocker.patch("src.daemon.is_system_under_load", return_value=False)
    mocker.patch("src.daemon.get_remote_host", return_value="github.com")
    mocker.patch("src.daemon.is_remote_reachable", return_value=True)

    # 3. Mocks
    mock_check_output = mocker.patch("subprocess.check_output")
    mock_run = mocker.patch("subprocess.run")

    def check_output_side_effect(cmd: list[str], **kwargs: object) -> str:
        if "branch" in cmd:
            return "wip/pulsar"
        if "status" in cmd:
            return "M  src/main.py"  # Simulate dirty file
        return ""

    mock_check_output.side_effect = check_output_side_effect

    daemon.run_backup(str(tmp_path))

    # Verify commit flow
    # 1. git add .
    mock_run.assert_any_call(["git", "add", "."], cwd=mocker.ANY, check=True)
    # 2. git push
    # Note: We check that 'git push' was called
    args, _ = mock_run.call_args
    assert args[0][:3] == ["git", "push", "origin"]


def test_run_backup_eco_mode_skips_push(tmp_path: Path, mocker: MagicMock) -> None:
    """Daemon should commit but SKIP push if battery is low (Eco Mode)."""
    (tmp_path / ".git").mkdir()
    mocker.patch("src.daemon.LOG_FILE", tmp_path / "test.log")
    mocker.patch("src.daemon.is_repo_busy", return_value=False)
    mocker.patch("src.daemon.has_large_files", return_value=False)
    mock_run = mocker.patch("subprocess.run")

    # Mock Battery: 15% and Unplugged (Eco threshold is 20%)
    mocker.patch("src.daemon.get_battery_status", return_value=(15, False))
    mocker.patch("src.daemon.is_system_under_load", return_value=False)

    mock_check_output = mocker.patch("subprocess.check_output")

    def check_output_side_effect(cmd: list[str], **kwargs: object) -> str:
        if "branch" in cmd:
            return "wip/pulsar"
        if "status" in cmd:
            return "M  src/main.py"
        return ""

    mock_check_output.side_effect = check_output_side_effect

    daemon.run_backup(str(tmp_path))

    # Assert commit happened
    mock_run.assert_any_call(["git", "add", "."], cwd=mocker.ANY, check=True)

    # Assert push did NOT happen
    for call in mock_run.call_args_list:
        args, _ = call
        if args[0][0] == "git" and args[0][1] == "push":
            assert False, "Should not push in Eco Mode"


def test_run_backup_offline_skips_push(tmp_path: Path, mocker: MagicMock) -> None:
    """Daemon should commit but SKIP push if remote is unreachable."""
    (tmp_path / ".git").mkdir()
    mocker.patch("src.daemon.LOG_FILE", tmp_path / "test.log")
    mocker.patch("src.daemon.is_repo_busy", return_value=False)
    mocker.patch("src.daemon.has_large_files", return_value=False)
    mock_run = mocker.patch("subprocess.run")

    # Mock Environment: Good battery, but network is dead
    mocker.patch("src.daemon.get_battery_status", return_value=(100, True))
    mocker.patch("src.daemon.is_system_under_load", return_value=False)
    mocker.patch("src.daemon.get_remote_host", return_value="github.com")
    mocker.patch("src.daemon.is_remote_reachable", return_value=False)

    mock_check_output = mocker.patch("subprocess.check_output")

    def check_output_side_effect(cmd: list[str], **kwargs: object) -> str:
        if "branch" in cmd:
            return "wip/pulsar"
        if "status" in cmd:
            return "M  src/main.py"
        return ""

    mock_check_output.side_effect = check_output_side_effect

    daemon.run_backup(str(tmp_path))

    # Assert commit happened
    mock_run.assert_any_call(["git", "add", "."], cwd=mocker.ANY, check=True)

    # Assert push did NOT happen
    for call in mock_run.call_args_list:
        args, _ = call
        if args[0][0] == "git" and args[0][1] == "push":
            assert False, "Should not push when offline"


def test_run_backup_respects_pause_file(tmp_path: Path, mocker: MagicMock) -> None:
    """Daemon should abort immediately if .git/pulsar_paused exists."""
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "pulsar_paused").touch()

    # Mock dependencies to verify we abort EARLY
    mock_busy = mocker.patch("src.daemon.is_repo_busy")
    mock_log = mocker.patch("src.daemon.log")

    daemon.run_backup(str(tmp_path))

    # Verify:
    # 1. We logged the pause event
    # 2. We did NOT check if repo is busy (proof we aborted early)
    assert "PAUSED" in mock_log.call_args[0][0]
    mock_busy.assert_not_called()
