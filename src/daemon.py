import datetime
import os
import socket
import subprocess
import sys
import time
import tomllib
from pathlib import Path

from .constants import LOG_FILE, REGISTRY_FILE
from .git_wrapper import GitRepo
from .system import get_system

SYSTEM = get_system()

DEFAULT_CONFIG = {
    "core": {
        "backup_branch": "wip/pulsar",
        "remote_name": "origin",
    },
    "limits": {
        "max_log_size": 5 * 1024 * 1024,
        "large_file_threshold": 100 * 1024 * 1024,
    },
    "daemon": {
        "min_battery_percent": 10,
        "eco_mode_percent": 20,
    },
}


def load_config() -> dict:
    config_path = Path.home() / ".config/git-pulsar/config.toml"
    config = DEFAULT_CONFIG.copy()

    if config_path.exists():
        try:
            with open(config_path, "rb") as f:
                user_config = tomllib.load(f)

            # recursive update for sections
            for section, values in user_config.items():
                if section in config and isinstance(values, dict):
                    # Assign to local variable so mypy can narrow the type
                    target_section = config[section]
                    if isinstance(target_section, dict):
                        target_section.update(values)

        except Exception as e:
            # Fallback to defaults on parse error, but log it
            print(f"Config Error: {e}", file=sys.stderr)

    return config


# Load once at module level
CONFIG = load_config()


def get_remote_host(repo_path: Path, remote_name: str) -> str | None:
    """Extracts hostname from git remote URL (SSH or HTTPS)."""
    try:
        url = subprocess.check_output(
            ["git", "remote", "get-url", remote_name], cwd=repo_path, text=True
        ).strip()

        # Handle SSH: git@github.com:user/repo.git
        if "@" in url:
            return url.split("@")[1].split(":")[0]
        # Handle HTTPS: https://github.com/user/repo.git
        if "://" in url:
            return url.split("://")[1].split("/")[0]
        return None
    except Exception:
        return None


def is_remote_reachable(host: str) -> bool:
    """Quick TCP check to see if remote is online (Port 443 or 22)."""
    if not host:
        return False  # Can't check, assume offline or broken

    for port in [443, 22]:
        try:
            # 3 second timeout is plenty for a simple SYN check
            with socket.create_connection((host, port), timeout=3):
                return True
        except OSError:
            continue
    return False


def log(message: str, interactive: bool = False) -> None:
    """Logs to file and stderr, rotating if too large."""
    max_size = CONFIG["limits"]["max_log_size"]
    if LOG_FILE.exists() and LOG_FILE.stat().st_size > max_size:
        try:
            os.remove(LOG_FILE)
        except OSError:
            pass

    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted_msg = f"[{timestamp}] {message}"

    if interactive:
        print(formatted_msg)  # Print to stdout for user visibility
        return  # Skip file writing in interactive mode

    # 1. Write to internal log file
    try:
        with open(LOG_FILE, "a") as f:
            f.write(f"{formatted_msg}\n")
    except OSError:
        pass

    # 2. Echo to stderr (so Homebrew/Systemd captures it)
    print(formatted_msg, file=sys.stderr)


def is_repo_busy(repo_path: Path, interactive: bool = False) -> bool:
    git_dir = repo_path / ".git"

    # 1. Check for operational locks
    critical_files = [
        "MERGE_HEAD",
        "REBASE_HEAD",
        "CHERRY_PICK_HEAD",
        "BISECT_LOG",
        "rebase-merge",
        "rebase-apply",
    ]
    for f in critical_files:
        if (git_dir / f).exists():
            return True

    # 2. Check for index.lock (Race Condition Handler)
    lock_file = git_dir / "index.lock"
    if lock_file.exists():
        # A. Check for stale lock (> 24 hours)
        try:
            mtime = lock_file.stat().st_mtime
            age_hours = (time.time() - mtime) / 3600
            if age_hours > 24:
                msg = f"Stale lock detected in {repo_path.name} ({age_hours:.1f}h old)."
                log(f"WARNING: {msg}")
                if interactive:
                    print(f"⚠️  {msg}\n   Run 'rm {lock_file}' to fix.")
                else:
                    notify("Pulsar Warning", f"Stale lock in {repo_path.name}")
                return True
        except OSError:
            pass  # File vanished

        # B. Wait-and-see (Micro-retry)
        time.sleep(1.0)
        if lock_file.exists():
            return True

    return False


def has_large_files(repo_path: Path) -> bool:
    """
    Scans for files larger than GitHub's 100MB limit.
    Returns True if a large file is found (and notifies user).
    """
    limit = CONFIG["limits"]["large_file_threshold"]

    # Only scan files git knows about or sees as untracked
    try:
        cmd = ["git", "ls-files", "--others", "--modified", "--exclude-standard"]
        candidates = subprocess.check_output(cmd, cwd=repo_path, text=True).splitlines()
    except subprocess.CalledProcessError:
        return False

    for name in candidates:
        file_path = repo_path / name
        try:
            if file_path.stat().st_size > limit:
                log(
                    f"WARNING {repo_path.name}: Large file detected ({name}). "
                    "Backup aborted."
                )
                notify("Backup Aborted", f"File >100MB detected: {name}")
                return True
        except OSError:
            continue

    return False


def prune_registry(original_path_str: str) -> None:
    if not REGISTRY_FILE.exists():
        return
    try:
        with open(REGISTRY_FILE, "r") as f:
            lines = f.readlines()

        target = original_path_str.strip()

        with open(REGISTRY_FILE, "w") as f:
            for line in lines:
                clean_line = line.strip()
                # Skip empty lines and the target path (ignoring whitespace)
                if clean_line and clean_line != target:
                    f.write(line)
        repo_name = Path(original_path_str).name
        log(f"PRUNED: {original_path_str} removed from registry.")
        notify("Backup Stopped", f"Removed missing repo: {repo_name}")
    except OSError as e:
        log(f"ERROR: Could not prune registry. {e}")


def _should_skip(repo_path: Path, interactive: bool) -> str | None:
    if not repo_path.exists():
        return "Path missing"

    if (repo_path / ".git" / "pulsar_paused").exists():
        return "Paused by user"

    if not interactive:
        if SYSTEM.is_under_load():
            return "System under load"

        # Simple battery check (example of accessing system strategy)
        pct, plugged = SYSTEM.get_battery()
        if not plugged and pct < 10:
            return "Battery critical"

    return None


def run_backup(original_path_str: str, interactive: bool = False) -> None:
    repo_path = Path(original_path_str).resolve()

    # 1. Guard Clauses
    if reason := _should_skip(repo_path, interactive):
        if reason == "Path missing":
            prune_registry(original_path_str)
        return

    # 2. Repo Interactions
    try:
        repo = GitRepo(repo_path)
        if repo.current_branch() != CONFIG["core"]["backup_branch"]:
            return

        if not repo.status_porcelain():
            return

        # 3. Action
        repo.add_all()
        repo.commit(f"Pulsar auto-backup: {datetime.datetime.now()}")
        _attempt_push(repo, interactive)  # Extracted push logic

    except Exception as e:
        log(f"CRITICAL {repo_path.name}: {e}", interactive)


def main(interactive: bool = False) -> None:
    if not REGISTRY_FILE.exists():
        if interactive:
            print("Registry empty. Run 'git-pulsar' in a repo to register it.")
        return

    with open(REGISTRY_FILE, "r") as f:
        repos = [line.strip() for line in f if line.strip()]

    for repo_str in set(repos):
        run_backup(repo_str, interactive=interactive)


if __name__ == "__main__":
    main()
