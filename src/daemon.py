import datetime
import os
import signal
import socket
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from types import FrameType

from .constants import LOG_FILE, REGISTRY_FILE
from .git_wrapper import GitRepo
from .system import get_machine_id, get_system

SYSTEM = get_system()


@dataclass
class CoreConfig:
    backup_branch: str = "wip/pulsar"
    remote_name: str = "origin"


@dataclass
class LimitsConfig:
    max_log_size: int = 5 * 1024 * 1024
    large_file_threshold: int = 100 * 1024 * 1024


@dataclass
class DaemonConfig:
    min_battery_percent: int = 10
    eco_mode_percent: int = 20


@dataclass
class Config:
    core: CoreConfig = field(default_factory=CoreConfig)
    limits: LimitsConfig = field(default_factory=LimitsConfig)
    daemon: DaemonConfig = field(default_factory=DaemonConfig)

    @classmethod
    def load(cls) -> "Config":
        config_path = Path.home() / ".config/git-pulsar/config.toml"
        instance = cls()

        if config_path.exists():
            try:
                with open(config_path, "rb") as f:
                    data = tomllib.load(f)

                # Selective update
                if "core" in data:
                    instance.core = CoreConfig(**data["core"])
                if "limits" in data:
                    instance.limits = LimitsConfig(**data["limits"])
                if "daemon" in data:
                    instance.daemon = DaemonConfig(**data["daemon"])
            except Exception as e:
                print(f"Config Error: {e}", file=sys.stderr)

        return instance


CONFIG = Config.load()


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
    max_size = CONFIG.limits.max_log_size
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
                    SYSTEM.notify("Pulsar Warning", f"Stale lock in {repo_path.name}")
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
    limit = CONFIG.limits.large_file_threshold

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
                SYSTEM.notify("Backup Aborted", f"File >100MB detected: {name}")
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
        SYSTEM.notify("Backup Stopped", f"Removed missing repo: {repo_name}")
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


def _attempt_push(repo: GitRepo, refspec: str, interactive: bool) -> None:
    # 1. Eco Mode Check
    percent, plugged = SYSTEM.get_battery()
    if not plugged and percent < CONFIG.daemon.eco_mode_percent:
        log(f"ECO MODE {repo.path.name}: Committed. Push skipped.", interactive)
        return

    # 2. Network Check
    remote_name = CONFIG.core.remote_name
    host = get_remote_host(repo.path, remote_name)
    if host and not is_remote_reachable(host):
        log(f"OFFLINE {repo.path.name}: Committed. Push skipped.", interactive)
        return

    # 3. Push
    try:
        env = os.environ.copy()
        env["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes"

        # Push specific refspec
        repo._run(["push", remote_name, refspec], capture=False, env=env)
        log(f"SUCCESS {repo.path.name}: Pushed.", interactive)
    except Exception as e:
        log(f"PUSH ERROR {repo.path.name}: {e}", interactive)


def run_backup(original_path_str: str, interactive: bool = False) -> None:
    repo_path = Path(original_path_str).resolve()

    # 1. Guard Clauses
    if reason := _should_skip(repo_path, interactive):
        if reason == "Path missing":
            prune_registry(original_path_str)
        elif reason == "System under load":
            pass  # Silent skip
        else:
            log(f"SKIPPED {repo_path.name}: {reason}", interactive)
        return

    # 2. Shadow Commit Logic
    try:
        repo = GitRepo(repo_path)
        current_branch = repo.current_branch()
        if not current_branch:
            return  # Detached HEAD or weird state

        # Construct Namespaced Ref: refs/heads/wip/pulsar/{machine_id}/{branch}
        machine_id = get_machine_id()
        backup_ref = f"refs/heads/wip/pulsar/{machine_id}/{current_branch}"

        # 3. Isolation: Use a temporary index
        temp_index = repo_path / ".git" / "pulsar_index"
        env = os.environ.copy()
        env["GIT_INDEX_FILE"] = str(temp_index)

        try:
            # Stage current working directory into temp index
            repo._run(["add", "."], env=env)

            # Write Tree
            tree_oid = repo.write_tree(env=env)

            # Determine Parents (Synthetic Merge)
            # Parent 1: Previous backup (to keep backup history linear-ish)
            # Parent 2: Current HEAD (to link to project history)
            parents = []
            if parent_backup := repo.rev_parse(backup_ref):
                parents.append(parent_backup)
            if parent_head := repo.rev_parse("HEAD"):
                parents.append(parent_head)

            # Check if we actually have changes compared to last backup
            # (Optimization: Don't spam commits if tree is identical to parent_backup)
            if parent_backup:
                # Get tree of previous backup
                prev_tree = repo._run(["rev-parse", f"{parent_backup}^{{tree}}"])
                if prev_tree == tree_oid:
                    # No changes since last backup
                    return

            # Commit Tree
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            commit_oid = repo.commit_tree(
                tree_oid, parents, f"Shadow backup {timestamp}", env=env
            )

            # Update Ref
            repo.update_ref(backup_ref, commit_oid, parent_backup)

            # 4. Push
            # Push specifically this ref
            _attempt_push(repo, f"{backup_ref}:{backup_ref}", interactive)

        finally:
            # Cleanup temp index
            if temp_index.exists():
                temp_index.unlink()

    except Exception as e:
        log(f"CRITICAL {repo_path.name}: {e}", interactive)


def main(interactive: bool = False) -> None:
    if not REGISTRY_FILE.exists():
        if interactive:
            print("Registry empty. Run 'git-pulsar' in a repo to register it.")
        return

    with open(REGISTRY_FILE, "r") as f:
        repos = [line.strip() for line in f if line.strip()]

    # Set a timeout handler for stalled mounts
    def timeout_handler(_signum: int, _frame: FrameType | None) -> None:
        raise TimeoutError("Repo access timed out")

    signal.signal(signal.SIGALRM, timeout_handler)

    for repo_str in set(repos):
        try:
            # 5 second timeout per repo to prevent hanging on network drives
            signal.alarm(5)
            run_backup(repo_str, interactive=interactive)
            signal.alarm(0)  # Disable alarm
        except TimeoutError:
            log(f"TIMEOUT {repo_str}: Skipped (possible stalled mount).", interactive)
        except Exception as e:
            log(f"LOOP ERROR {repo_str}: {e}", interactive)
            signal.alarm(0)


if __name__ == "__main__":
    main()
