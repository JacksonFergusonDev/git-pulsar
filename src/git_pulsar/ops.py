import contextlib
import json
import logging
import os
import shutil
import subprocess
import sys
import textwrap
import time
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from . import system
from .config import Config
from .constants import APP_NAME, BACKUP_NAMESPACE
from .git_wrapper import GitRepo

console = Console()
logger = logging.getLogger(APP_NAME)


def get_backup_ref(branch: str) -> str:
    """
    Constructs the fully qualified backup reference for the current machine and branch.

    Args:
        branch (str): The name of the branch to back up.

    Returns:
        str: The namespaced ref string (e.g., refs/heads/wip/pulsar/slug/branch).
    """
    slug = system.get_identity_slug()
    return f"refs/heads/{BACKUP_NAMESPACE}/{slug}/{branch}"


def get_remote_drift_state(repo_path: Path) -> tuple[bool, int, str, str]:
    """Checks if another machine has a newer backup session for the current branch.

    Args:
        repo_path (Path): Path to the local git repository.

    Returns:
        tuple[bool, int, str, str]: A tuple containing:
            - bool: True if divergence/drift is detected, False otherwise.
            - int: The Unix timestamp of the newest remote session (0 if none/error).
            - str: The machine slug that pushed the newest session (empty if none).
            - str: A human-readable warning message (empty if no drift).
    """
    try:
        repo = GitRepo(repo_path)
        current_branch = repo.current_branch()
        if not current_branch:
            return False, 0, "", ""

        # Lightweight fetch of backup refs for the current branch
        try:
            repo._run(
                [
                    "fetch",
                    "origin",
                    f"refs/heads/{BACKUP_NAMESPACE}/*/{current_branch}:refs/heads/{BACKUP_NAMESPACE}/*/{current_branch}",
                ],
                capture=True,
            )
        except Exception as e:
            logger.debug(f"Fetch failed during drift check: {e}")
            return False, 0, "", ""  # Silently fail if offline or remote is unreachable

        candidates = repo.list_refs(f"refs/heads/{BACKUP_NAMESPACE}/*/{current_branch}")
        if not candidates:
            return False, 0, "", ""

        my_slug = system.get_identity_slug()
        my_backup_ref = get_backup_ref(current_branch)

        # Determine our local latest timestamp (backup ref or HEAD)
        local_ts = 0
        try:
            if my_backup_ref in candidates:
                local_ts = int(
                    repo._run(["log", "-1", "--format=%ct", my_backup_ref]).strip()
                )
            else:
                local_ts = int(repo._run(["log", "-1", "--format=%ct", "HEAD"]).strip())
        except Exception as e:
            logger.debug(f"Failed to get local timestamp: {e}")

        newest_ts = 0
        newest_machine = ""
        # Dynamically calculate the machine index in the ref string
        machine_index = 2 + len(BACKUP_NAMESPACE.split("/"))

        for ref in candidates:
            try:
                ts = int(repo._run(["log", "-1", "--format=%ct", ref]).strip())
                if ts > newest_ts:
                    newest_ts = ts
                    parts = ref.split("/")
                    if len(parts) > machine_index:
                        newest_machine = parts[machine_index]
            except Exception as e:
                logger.debug(f"Failed to process ref {ref}: {e}")
                continue

        if newest_ts > local_ts and newest_machine and newest_machine != my_slug:
            minutes_ago = int((time.time() - newest_ts) / 60)
            warning = (
                f"Divergence Risk: '{newest_machine}' pushed a newer session "
                f"~{minutes_ago} mins ago. Consider running 'git pulsar sync'."
            )
            return True, newest_ts, newest_machine, warning

    except Exception as e:
        logger.debug(f"Drift check failed: {e}")

    return False, 0, "", ""


def get_drift_state(repo_path: Path) -> tuple[float, int]:
    """Retrieves the cached state for remote drift detection.

    Args:
        repo_path (Path): The path to the repository.

    Returns:
        tuple[float, int]: A tuple containing:
            - float: The Unix timestamp of the last time a drift check was performed.
            - int: The Unix timestamp of the newest remote session the user was warned about.
    """
    state_file = repo_path / ".git" / "pulsar_drift_state"
    if not state_file.exists():
        return 0.0, 0

    try:
        content = state_file.read_text().strip()
        if not content:
            return 0.0, 0

        data = json.loads(content)
        return float(data.get("last_check_ts", 0.0)), int(
            data.get("warned_remote_ts", 0)
        )
    except (OSError, ValueError, json.JSONDecodeError) as e:
        logger.debug(f"Failed to read drift state: {e}")
        return 0.0, 0


def set_drift_state(
    repo_path: Path, last_check_ts: float, warned_remote_ts: int
) -> None:
    """Persists the drift detection state to disk atomically.

    Args:
        repo_path (Path): The path to the repository.
        last_check_ts (float): The Unix timestamp of the current check.
        warned_remote_ts (int): The Unix timestamp of the remote session warned about.
    """
    state_file = repo_path / ".git" / "pulsar_drift_state"
    tmp_file = state_file.with_suffix(".tmp")

    data = {
        "last_check_ts": last_check_ts,
        "warned_remote_ts": warned_remote_ts,
    }

    try:
        with open(tmp_file, "w") as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())  # Force hardware write

        # Atomic pointer swap at the filesystem level
        os.replace(tmp_file, state_file)
    except OSError as e:
        logger.debug(f"Failed to write drift state: {e}")
        if tmp_file.exists():
            with contextlib.suppress(OSError):
                tmp_file.unlink()


def bootstrap_env() -> None:
    """Bootstraps a Python development environment on macOS.

    This function scaffolds the environment using `uv` for package management,
    `direnv` for environment switching, and configures VS Code settings.

    Note:
        This workflow is currently optimized for macOS.
    """
    if sys.platform != "darwin":
        console.print(
            "[bold red]ERROR:[/bold red] The --env workflow is "
            "currently optimized for macOS."
        )
        return

    cwd = Path.cwd()
    console.print(
        f"[bold blue]SETUP:[/bold blue] Setting up dev environment in {cwd.name}..."
    )

    # 1. Dependency Check
    missing = []
    if not shutil.which("uv"):
        missing.append("uv")
    if not shutil.which("direnv"):
        missing.append("direnv")

    if missing:
        console.print(
            f"[bold red]ERROR:[/bold red] Missing tools: {', '.join(missing)}"
        )
        console.print("   Please run:")
        install_cmd = f"brew install {' '.join(missing)}"
        if not shutil.which("brew"):
            install_cmd = f"(Check your package manager) install {' '.join(missing)}"

        console.print(f"     {install_cmd}")
        sys.exit(1)

    # 2. Project Scaffold (uv)
    if not (cwd / "pyproject.toml").exists():
        console.print("[bold blue]INIT:[/bold blue] Initializing Python project...")
        # 'uv init' creates a standard pyproject.toml.
        subprocess.run(["uv", "init", "--no-workspace", "--python", "3.12"], check=True)
    else:
        console.print("   Existing pyproject.toml found. Skipping init.")

    # 3. Direnv Configuration
    envrc_path = cwd / ".envrc"
    if not envrc_path.exists():
        console.print("[bold blue]CONFIG:[/bold blue] Creating .envrc...")
        envrc_content = textwrap.dedent("""\
            # Auto-generated by git-pulsar
            if [ ! -d ".venv" ]; then
                echo "Creating virtual environment..."
                uv sync
            fi
            source .venv/bin/activate
            
            source_env_if_exists .envrc.local
        """)
        with open(envrc_path, "w") as f:
            f.write(envrc_content)

        subprocess.run(["direnv", "allow"], check=True)
    else:
        console.print("   .envrc exists. Skipping.")

    # 4. VS Code Settings
    vscode_dir = cwd / ".vscode"
    settings_path = vscode_dir / "settings.json"

    if not settings_path.exists():
        vscode_dir.mkdir(exist_ok=True)
        console.print("[bold blue]CONFIG:[/bold blue] Configuring VS Code...")
        settings_content = textwrap.dedent("""\
            {
                "python.defaultInterpreterPath": ".venv/bin/python",
                "python.terminal.activateEnvironment": true,
                "files.exclude": {
                    "**/__pycache__": true,
                    "**/.ipynb_checkpoints": true,
                    "**/.DS_Store": true,
                    "**/.venv": true
                },
                "search.exclude": {
                    "**/.venv": true
                }
            }
        """)
        with open(settings_path, "w") as f:
            f.write(settings_content)

    console.print("\n[bold green]SUCCESS:[/bold green] Environment ready.")

    if "DIRENV_DIR" not in os.environ:
        console.print("\n[bold yellow]ACTION REQUIRED:[/bold yellow] Enable direnv")
        console.print("   1. Open your config:")
        console.print("      code ~/.zshrc  (or nano ~/.zshrc)")
        console.print("   2. Add this line to the bottom:")
        console.print('      eval "$(direnv hook zsh)"')
        console.print("   3. Reload:")
        console.print("      source ~/.zshrc")


def restore_file(path_str: str, force: bool = False) -> None:
    """Restores a specific file from the latest backup of the current branch.

    Args:
        path_str (str): The relative path to the file to restore.
        force (bool): If True, overwrites uncommitted local changes. Defaults to False.
    """
    repo = GitRepo(Path.cwd())
    path = Path(path_str)

    current_branch = repo.current_branch()
    backup_ref = get_backup_ref(current_branch)

    # 1. Safety Check: Verify if the file is dirty.
    if not force and path.exists() and repo.status_porcelain(path_str):
        console.print(
            f"[bold yellow]WARNING:[/bold yellow] '{path_str}' has uncommitted changes."
        )

        while True:
            choice = Prompt.ask(
                "   [O]verwrite / [V]iew Diff / [C]ancel",
                choices=["o", "v", "c"],
                default="c",
            )

            if choice == "v":
                repo.run_diff(backup_ref, file=path_str)
                continue
            elif choice == "c":
                console.print("[bold red]ABORTED.[/bold red]")
                sys.exit(0)
            elif choice == "o":
                break

    # 2. Restore file from backup ref.
    console.print(
        f"[bold blue]RESTORING:[/bold blue] '{path_str}' from {backup_ref}..."
    )
    try:
        repo.checkout(backup_ref, file=path_str)
        console.print("[bold green]SUCCESS:[/bold green] Restore complete.")
    except Exception as e:
        logger.error(f"Failed to restore {path_str}: {e}")
        console.print(f"[bold red]ERROR:[/bold red] Failed to restore: {e}")
        sys.exit(1)


def sync_session() -> None:
    """Synchronizes the local workspace with the latest available backup session.

    This function scans backups from all machines for the current branch, identifies
    the most recent one, and (after confirmation) resets the local working directory
    to match it. This facilitates "Smart Handoff" between devices.
    """
    repo = GitRepo(Path.cwd())
    current_branch = repo.current_branch()

    # 1. Fetch backups from all sources.
    with console.status(
        f"[bold blue]Scanning for session on '{current_branch}'...[/bold blue]",
        spinner="dots",
    ):
        try:
            # Only fetch backups related to the current branch
            repo._run(
                [
                    "fetch",
                    "origin",
                    f"refs/heads/{BACKUP_NAMESPACE}/*/{current_branch}:refs/heads/{BACKUP_NAMESPACE}/*/{current_branch}",
                ],
                capture=True,
            )
        except Exception as e:
            logger.warning(f"Fetch error: {e}")
            console.print(
                "[yellow][bold]WARNING:[/bold] Fetch warning: network might be down "
                "(checking local cache).[/yellow]"
            )

    # 2. Find candidate refs (refs/heads/{namespace}/{machine}/{branch}).
    candidates = repo.list_refs(f"refs/heads/{BACKUP_NAMESPACE}/*/{current_branch}")

    if not candidates:
        console.print("[bold red]ERROR:[/bold red] No backups found anywhere.")
        return

    # 3. Sort candidates by commit timestamp (newest first).
    latest_ref = None
    latest_time = 0

    for ref in candidates:
        try:
            ts_str = repo._run(["log", "-1", "--format=%ct", ref])
            ts = int(ts_str.strip())
            if ts > latest_time:
                latest_time = ts
                latest_ref = ref
        except Exception as e:
            logger.warning(f"Failed to parse timestamp for backup ref '{ref}': {e}")
            continue

    if not latest_ref:
        console.print("[bold red]ERROR:[/bold red] Could not determine latest backup.")
        return

    # 4. Compare with local state.
    machine_name = latest_ref.split("/")[-2]
    human_time = repo._run(["log", "-1", "--format=%cr", latest_ref])

    console.print(
        Panel(
            f"[bold]Source:[/bold] {machine_name}\n[bold]Time:[/bold]   {human_time}",
            title="Latest Session Found",
            border_style="green",
            expand=False,
        )
    )

    # Check if the local tree already matches the remote tree.
    local_tree = repo.write_tree()
    remote_tree = repo._run(["rev-parse", f"{latest_ref}^{{tree}}"])

    if local_tree == remote_tree:
        console.print("[bold green]SUCCESS:[/bold green] You are already up to date.")
        return

    # 5. Confirm overwrite.
    console.print(
        "\n[bold yellow]WARNING:[/bold yellow] This will overwrite your local "
        "changes to match the backup."
    )
    confirm = console.input("   Proceed with sync? [y/N] ").lower()
    if confirm != "y":
        console.print("[bold red]ABORTED.[/bold red]")
        sys.exit(0)

    # 6. Execute sync.
    try:
        # Checkout the contents of the backup ref to the worktree without moving HEAD.
        repo._run(["checkout", latest_ref, "--", "."])
        console.print(
            "[bold green]SUCCESS:[/bold green] Session synced. You may resume work."
        )
    except Exception as e:
        logger.warning(f"Sync failed: {e}")
        console.print(f"[bold red]ERROR:[/bold red] Sync failed: {e}")
        sys.exit(1)


def finalize_work() -> None:
    """Consolidates backup streams into the main branch.

    This performs an 'Octopus Squash' merge of all backup streams for the current
    branch into the main/master branch, effectively finalizing the work session
    and updating the primary project history.
    """
    console.print("[bold blue]FINALIZING:[/bold blue] Finalizing work...")
    repo = GitRepo(Path.cwd())

    # 1. Ensure working directory is clean.
    if repo.status_porcelain():
        console.print(
            "[bold yellow]WARNING:[/bold yellow] You have uncommitted changes."
        )
        console.print("   Please commit or stash them before finalizing.")
        sys.exit(1)

    working_branch = repo.current_branch()

    try:
        # 2. Sync with Remote.
        with console.status(
            "[bold blue]Syncing with origin...[/bold blue]", spinner="dots"
        ):
            try:
                repo._run(["fetch", "origin", "main"], capture=True)
                repo._run(
                    [
                        "fetch",
                        "origin",
                        f"refs/heads/{BACKUP_NAMESPACE}/*:refs/heads/{BACKUP_NAMESPACE}/*",
                    ],
                    capture=True,
                )
            except Exception as e:
                console.print(
                    f"[yellow][bold]WARNING:[/bold] Fetch warning: {e}[/yellow]"
                )

        # 3. Identify Backup Candidates for the current branch.
        candidates = repo.list_refs(f"refs/heads/{BACKUP_NAMESPACE}/*/{working_branch}")

        if not candidates:
            console.print(
                "[bold red]ERROR:[/bold red] No backups found for this branch."
            )
            sys.exit(1)

        console.print(f"-> Found {len(candidates)} backup stream(s):")
        for c in candidates:
            console.print(f"   • {c}")

        # 4. Switch to the target branch (main/master).
        target = "main"
        if not repo.rev_parse("main") and repo.rev_parse("master"):
            target = "master"

        console.print(f"-> Switching to {target}...")
        repo.checkout(target)

        # 5. Perform Octopus Squash Merge.
        with console.status(
            f"[bold blue]Collapsing {len(candidates)} backup streams...[/bold blue]",
            spinner="dots",
        ):
            try:
                repo.merge_squash(*candidates)
            except RuntimeError:
                console.print(
                    "[bold red]CONFLICT:[/bold red] Merge conflicts detected. "
                    "Please resolve them, then commit."
                )
                sys.exit(0)

        # 6. Interactive Commit.
        console.print("-> Committing (opens editor)...")
        repo.commit_interactive()

        console.print("\n[bold green]SUCCESS:[/bold green] Work finalized!")
        console.print(f"   Your backup history remains in refs/{BACKUP_NAMESPACE}/...")

    except Exception as e:
        logger.error(f"Finalize failed: {e}")
        console.print(f"\n[bold red]ERROR:[/bold red] Error during finalize: {e}")
        sys.exit(1)


def prune_backups(days: int, repo_path: Path | None = None) -> None:
    """Garbage collects backup references older than the specified retention period.

    Args:
        days (int): The retention period in days.
        repo_path (Path | None, optional): The path to the repository. Defaults to CWD.
    """
    repo = GitRepo(repo_path or Path.cwd())
    cutoff = time.time() - (days * 86400)

    console.print(
        f"[bold blue]MAINTENANCE:[/bold blue] "
        f"Scanning for backups older than {days} days..."
    )

    refs = repo.list_refs(f"refs/heads/{BACKUP_NAMESPACE}/")
    deleted_count = 0

    for ref in refs:
        try:
            ts_str = repo._run(["log", "-1", "--format=%ct", ref])
            ts = int(ts_str.strip())

            if ts < cutoff:
                age_days = (time.time() - ts) / 86400
                console.print(f"   Deleting {ref} (Age: {age_days:.1f} days)")
                repo._run(["update-ref", "-d", ref], capture=False)
                deleted_count += 1
        except Exception as e:
            logger.warning(f"Failed to process old backup ref '{ref}': {e}")
            continue

    if deleted_count == 0:
        console.print("[dim]No stale backups found.[/dim]")
    else:
        console.print(f"[bold red]Dropped {deleted_count} stale refs.[/bold red]")
        with console.status(
            "[bold blue]Running garbage collection (git gc)...[/bold blue]",
            spinner="dots",
        ):
            repo._run(["gc", "--auto"], capture=True)


def add_ignore(pattern: str) -> None:
    """Adds a file pattern to .gitignore and removes matching files from the index.

    If files matching the pattern are currently tracked, the user is prompted to
    stop tracking them (while keeping the files on disk).

    Args:
        pattern (str): The file pattern to ignore (e.g., '*.log').
    """
    cwd = Path.cwd()
    gitignore = cwd / ".gitignore"

    # 1. Append to .gitignore if not present.
    content = ""
    if gitignore.exists():
        with open(gitignore) as f:
            content = f.read()

    if pattern in content:
        console.print(f"[blue]INFO:[/blue] '{pattern}' is already in .gitignore.")
    else:
        with open(gitignore, "a") as f:
            prefix = "\n" if content and not content.endswith("\n") else ""
            f.write(f"{prefix}{pattern}\n")
        console.print(
            f"[bold green]SUCCESS:[/bold green] Added '{pattern}' to .gitignore."
        )

    # 2. Check if currently tracked and offer to remove from index.
    repo = GitRepo(cwd)
    try:
        tracked = repo._run(["ls-files", pattern])
        if tracked:
            console.print(
                f"[bold yellow]WARNING:[/bold yellow] "
                f"Files matching '{pattern}' are currently tracked by git."
            )
            confirm = console.input(
                "   Stop tracking them (keep local file)? [y/N] "
            ).lower()
            if confirm == "y":
                repo._run(["rm", "--cached", pattern], capture=False)
                console.print("   Removed from index (file preserved on disk).")
    except Exception as e:
        logger.warning(f"Failed to remove tracked files: {e}")
        pass


def has_large_files(repo_path: Path, config: Config) -> bool:
    """Scans untracked or modified files for sizes exceeding the limit.

    Args:
        repo_path (Path): The path to the repository.
        config (Config): The configuration instance for this repository.

    Returns:
        bool: True if a large file is found, False otherwise.
    """
    limit = config.limits.large_file_threshold

    # Only scan files git knows about or sees as untracked.
    try:
        cmd = ["git", "ls-files", "--others", "--modified", "--exclude-standard"]
        candidates = subprocess.check_output(cmd, cwd=repo_path, text=True).splitlines()
    except subprocess.CalledProcessError as e:
        logger.warning(f"Large file scan failed for {repo_path.name}: {e}")
        return False

    for name in candidates:
        file_path = repo_path / name
        try:
            if file_path.stat().st_size > limit:
                # Dynamic size formatting (Bytes -> MB)
                limit_mb = int(limit / (1024 * 1024))

                logger.warning(
                    f"WARNING {repo_path.name}: Large file detected ({name}). "
                    "Backup aborted."
                )
                system.get_system().notify(
                    "Backup Aborted", f"File >{limit_mb}MB detected: {name}"
                )
                return True
        except OSError as e:
            logger.warning(f"Failed to check size of file {name}: {e}")
            continue

    return False
