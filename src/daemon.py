import datetime
import os
import subprocess
import sys
import tomllib
from pathlib import Path

APP_NAME = "git-pulsar"
REGISTRY_FILE = Path.home() / ".git_pulsar_registry"
LOG_FILE = Path.home() / ".git_pulsar_log"

DEFAULT_CONFIG = {
    "core": {
        "backup_branch": "wip/pulsar",
        "remote_name": "origin",
    },
    "limits": {
        "max_log_size": 5 * 1024 * 1024,
        "large_file_threshold": 100 * 1024 * 1024,
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


def notify(title: str, message: str) -> None:
    """Sends a desktop notification (macOS + Linux)."""
    clean_msg = message.replace('"', "'")

    # macOS
    if sys.platform == "darwin":
        script = (
            f'display notification "{clean_msg}" with title "{title}" '
            f'subtitle "{APP_NAME}"'
        )
        try:
            subprocess.run(["osascript", "-e", script], stderr=subprocess.DEVNULL)
        except Exception:
            pass

    # Linux
    elif sys.platform.startswith("linux"):
        try:
            subprocess.run(
                ["notify-send", title, clean_msg, "-a", APP_NAME],
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            pass


def is_repo_busy(repo_path: Path) -> bool:
    git_dir = repo_path / ".git"
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


def run_backup(original_path_str: str, interactive: bool = False) -> None:
    try:
        repo_path = Path(original_path_str).expanduser().resolve()
    except Exception as e:
        log(
            f"ERROR: Could not resolve path {original_path_str}: {e}",
            interactive=interactive,
        )
        return

    repo_name = repo_path.name

    if not repo_path.exists():
        log(
            f"MISSING {original_path_str}: Path not found. Pruning.",
            interactive=interactive,
        )
        prune_registry(original_path_str)
        return

    if not (repo_path / ".git").exists():
        log(f"SKIPPED {repo_name}: Not a git repo anymore.", interactive=interactive)
        return

    if is_repo_busy(repo_path):
        log(
            f"SKIPPED {repo_name}: Repo is busy (merge/rebase).",
            interactive=interactive,
        )
        return

    if has_large_files(repo_path):
        return

    backup_branch = CONFIG["core"]["backup_branch"]
    remote_name = CONFIG["core"]["remote_name"]

    try:
        # Check branch
        current_branch = subprocess.check_output(
            ["git", "branch", "--show-current"], cwd=repo_path, text=True
        ).strip()

        if current_branch != backup_branch:
            return

        # Check status
        status = subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=repo_path, text=True
        )
        if not status.strip():
            return

        # Commit
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        subprocess.run(["git", "add", "."], cwd=repo_path, check=True)
        subprocess.run(
            # --no-verify to skip pre-commit hooks
            ["git", "commit", "--no-verify", "-m", f"Pulsar auto-backup: {timestamp}"],
            cwd=repo_path,
            check=True,
            stdout=subprocess.DEVNULL,
        )

        # Push
        subprocess.run(
            ["git", "push", remote_name, backup_branch],  # Use remote_name here
            cwd=repo_path,
            check=True,
            timeout=45,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        log(f"SUCCESS {repo_name}: Pushed.", interactive=interactive)

    except subprocess.TimeoutExpired:
        log(f"TIMEOUT {repo_name}: Push timed out.", interactive=interactive)
    except subprocess.CalledProcessError as e:
        err_text = e.stderr.decode("utf-8") if e.stderr else "Unknown git error"
        log(f"ERROR {repo_name}: {err_text.strip()}", interactive=interactive)
        notify("Backup Failed", f"{repo_name}: Check logs.")
    except Exception as e:
        log(f"CRITICAL {repo_name}: {e}", interactive=interactive)
        notify("Pulsar Crash", f"{repo_name}: {e}")


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
