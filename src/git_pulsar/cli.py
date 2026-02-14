import argparse
import datetime
import os
import subprocess
import sys
import time
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import daemon, ops, service, system
from .config import CONFIG_FILE, Config
from .constants import (
    APP_LABEL,
    DEFAULT_IGNORES,
    HOMEBREW_LABEL,
    LOG_FILE,
    PID_FILE,
    REGISTRY_FILE,
)
from .git_wrapper import GitRepo

console = Console()


def _get_ref(repo: GitRepo) -> str:
    """Resolves the namespaced backup reference for the current repository state.

    Args:
        repo (GitRepo): The repository instance to analyze.

    Returns:
        str: The fully qualified backup reference string based on the current branch.
    """
    return ops.get_backup_ref(repo.current_branch())


def _is_service_enabled() -> bool:
    """Checks if the system service is currently loaded and active.

    Supports both launchd (macOS) and systemd (Linux).

    Returns:
        bool: True if the service is active/loaded, False otherwise.
    """
    if sys.platform == "darwin":
        res = subprocess.run(["launchctl", "list"], capture_output=True, text=True)
        return HOMEBREW_LABEL in res.stdout
    elif sys.platform.startswith("linux"):
        res = subprocess.run(
            ["systemctl", "--user", "is-active", f"{APP_LABEL}.timer"],
            capture_output=True,
            text=True,
        )
        return res.stdout.strip() == "active"
    return False


def _analyze_logs(hours: int = 24) -> list[str]:
    """
    Scans the daemon log for error messages that occurred within a recent time window.

    Args:
        hours (int, optional): The number of hours to look back. Defaults to 24.

    Returns:
        list[str]: A list of error or critical log lines found within the time window.
    """
    if not LOG_FILE.exists():
        return []

    errors = []
    threshold = datetime.datetime.now() - datetime.timedelta(hours=hours)

    try:
        # Read the last 50KB of the log file
        # to capture recent context without parsing the whole file.
        file_size = LOG_FILE.stat().st_size
        read_size = min(file_size, 50 * 1024)

        with open(LOG_FILE, "r") as f:
            if file_size > read_size:
                f.seek(file_size - read_size)
            lines = f.readlines()

        for line in lines:
            if "ERROR" in line or "CRITICAL" in line:
                # Attempt to parse the timestamp [YYYY-MM-DD HH:MM:SS].
                # If parsing fails,
                # assume it is a related traceback line and include it.
                try:
                    if line.startswith("["):
                        ts_str = line[1:20]
                        line_dt = datetime.datetime.strptime(
                            ts_str, "%Y-%m-%d %H:%M:%S"
                        )
                        if line_dt < threshold:
                            continue  # Skip errors older than the threshold.
                    errors.append(line.strip())
                except ValueError:
                    pass
    except Exception as e:
        return [f"Error reading log file: {e}"]

    return errors


def _check_repo_health(path: Path) -> str | None:
    """
    Evaluates the health of a repository, checking for stale backups or stalled states.

    Args:
        path (Path): The file system path to the repository.

    Returns:
        str | None: A warning message if an issue is detected,
                    or None if the repository is healthy.
    """
    try:
        repo = GitRepo(path)
        # Check if the repository is explicitly paused.
        if (path / ".git" / "pulsar_paused").exists():
            return None

        # If the working directory is clean, no backup is required.
        if not repo.status_porcelain():
            return None

        # Verify the freshness of the last backup.
        ref = _get_ref(repo)
        try:
            # Retrieve the raw Unix timestamp of the backup reference.
            ts_str = repo._run(["log", "-1", "--format=%ct", ref])
            last_backup_ts = int(ts_str.strip())
        except Exception:
            return "Has changes, but NO backup found."

        # Check against the stale threshold (e.g., 2 hours).
        # If changes are pending and no backup has occurred recently,
        # the daemon may be stalled.
        if time.time() - last_backup_ts > 7200:
            return "Stalled: Changes pending > 2 hours."

    except Exception as e:
        return f"Unable to verify git status: {e}"

    return None


def open_config() -> None:
    """Opens the global configuration file in the system default editor."""
    if not CONFIG_FILE.exists():
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, "w") as f:
            f.write(
                "# Git Pulsar Configuration\n\n"
                "[daemon]\n"
                "# Options: paranoid, aggressive, balanced, lazy\n"
                '# preset = "balanced"\n'
            )

    editor = os.environ.get("EDITOR")
    if not editor:
        if sys.platform == "darwin":
            editor = "open"
        else:
            editor = "nano"

    console.print(f"Opening [cyan]{CONFIG_FILE}[/cyan]...")

    try:
        if editor == "open":
            subprocess.run(["open", str(CONFIG_FILE)])
        else:
            subprocess.run([editor, str(CONFIG_FILE)])
    except Exception as e:
        console.print(f"[red]Could not open editor: {e}[/red]")


def show_status() -> None:
    """Displays the current status of the daemon and the active repository."""
    # Check daemon process status.
    pid_running = False
    if PID_FILE.exists():
        try:
            with open(PID_FILE, "r") as f:
                pid = int(f.read().strip())
            os.kill(pid, 0)
            pid_running = True
        except (ValueError, OSError):
            pid_running = False

    # Check if the system service is scheduled/enabled.
    service_enabled = _is_service_enabled()

    if pid_running:
        status_text = "Active (Running)"
        status_style = "bold green"
    elif service_enabled:
        status_text = "Active (Idle)"
        status_style = "green"
    else:
        status_text = "Stopped"
        status_style = "bold red"

    system_content = Text()
    system_content.append("Daemon: ", style="bold")
    system_content.append(status_text, style=status_style)

    console.print(Panel(system_content, title="System Status", expand=False))

    # Display status for the current repository, if applicable.
    if Path(".git").exists():
        cwd = Path.cwd()

        # Check registration status.
        is_registered = False
        if REGISTRY_FILE.exists():
            with open(REGISTRY_FILE, "r") as f:
                registered = {line.strip() for line in f if line.strip()}
            if str(cwd) in registered:
                is_registered = True

        if not is_registered:
            console.print(
                Panel(
                    "This repository is not tracked by Git Pulsar.\n"
                    "Run [bold cyan]git pulsar[/bold cyan] to enable backups.",
                    title="Repository Status",
                    expand=False,
                    border_style="yellow",
                )
            )
            return

        repo = GitRepo(cwd)
        conf = Config.load(cwd)

        # Resolve Refs
        ref = _get_ref(repo)
        ref_name = ref.replace("refs/heads/", "")
        remote_ref = f"refs/remotes/{conf.core.remote_name}/{ref_name}"

        # Get Commit Time
        try:
            commit_ts = repo._run(["log", "-1", "--format=%ct", ref]).strip()
            last_commit_time = datetime.datetime.fromtimestamp(int(commit_ts))
            commit_str = last_commit_time.strftime("%Y-%m-%d %H:%M")
        except Exception:
            commit_str = "Never"

        # Get Push Time
        try:
            push_ts = repo._run(["log", "-1", "--format=%ct", remote_ref]).strip()
            last_push_time = datetime.datetime.fromtimestamp(int(push_ts))
            push_str = last_push_time.strftime("%Y-%m-%d %H:%M")
        except Exception:
            push_str = "Never"

        count = len(repo.status_porcelain())
        is_paused = (cwd / ".git" / "pulsar_paused").exists()

        repo_content = Text()
        repo_content.append(f"Last Commit: {commit_str}\n")
        repo_content.append(f"Last Push:   {push_str}\n", style="dim")
        repo_content.append(f"Pending:     {count} files changed\n")

        if is_paused:
            repo_content.append("Mode:        PAUSED", style="bold yellow")
        else:
            repo_content.append("Mode:        Active", style="green")

        console.print(Panel(repo_content, title="Repository Status", expand=False))

    # Display global repository count if not currently in a repository.
    elif REGISTRY_FILE.exists():
        with open(REGISTRY_FILE) as f:
            count = len([line for line in f if line.strip()])
        console.print(f"[dim]Watching {count} repositories.[/dim]")


def show_diff() -> None:
    """Displays the diff between the working directory and the last backup."""
    if not Path(".git").exists():
        console.print("[bold red]Not a git repository.[/bold red]")
        sys.exit(1)

    repo = GitRepo(Path.cwd())

    # Display standard diff for tracked files.
    ref = _get_ref(repo)

    console.print(f"[bold]Diff vs {ref}:[/bold]\n")
    repo.run_diff(ref)

    # List untracked files.
    if untracked := repo.get_untracked_files():
        console.print("\n[bold green]Untracked (New) Files:[/bold green]")
        for line in untracked:
            console.print(f"   + {line}", style="green")


def list_repos() -> None:
    """Lists all repositories currently registered with Git Pulsar and their status."""
    if not REGISTRY_FILE.exists():
        console.print("[yellow]Registry is empty.[/yellow]")
        return

    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Repository", style="cyan")
    table.add_column("Status")
    table.add_column("Last Backup", justify="right", style="dim")

    with open(REGISTRY_FILE, "r") as f:
        lines = [line.strip() for line in f if line.strip()]

    for path_str in lines:
        path = Path(path_str)
        display_path = str(path).replace(str(Path.home()), "~")

        status_text = "Unknown"
        status_style = "white"
        last_backup = "-"

        if not path.exists():
            status_text = "Missing"
            status_style = "red"
        else:
            if (path / ".git" / "pulsar_paused").exists():
                status_text = "Paused"
                status_style = "yellow"
            else:
                status_text = "Active"
                status_style = "green"

            try:
                r = GitRepo(path)
                ref = _get_ref(r)
                last_backup = r.get_last_commit_time(ref)
            except Exception:
                if status_text == "Active":
                    try:
                        GitRepo(path)
                    except Exception:
                        status_text = "Error"
                        status_style = "bold red"

        table.add_row(
            display_path, f"[{status_style}]{status_text}[/{status_style}]", last_backup
        )

    console.print(table)


def unregister_repo() -> None:
    """Removes the current working directory from the Git Pulsar registry."""
    cwd = str(Path.cwd())
    if not REGISTRY_FILE.exists():
        console.print("Registry is empty.", style="yellow")
        return

    with open(REGISTRY_FILE, "r") as f:
        lines = [line.strip() for line in f if line.strip()]

    if cwd not in lines:
        console.print(
            f"Current path not registered: [cyan]{cwd}[/cyan]", style="yellow"
        )
        return

    with open(REGISTRY_FILE, "w") as f:
        for line in lines:
            if line != cwd:
                f.write(f"{line}\n")
    console.print(f"✔ Unregistered: [cyan]{cwd}[/cyan]", style="green")


def run_doctor() -> None:
    """
    Diagnoses system health, cleans the registry, and checks connectivity and logs.
    """
    console.print("[bold]Pulsar Doctor[/bold]\n")

    # Verify and clean the registry.
    with console.status("[bold blue]Checking Registry...", spinner="dots"):
        if not REGISTRY_FILE.exists():
            console.print("   [green]✔ Registry empty/clean.[/green]")
        else:
            with open(REGISTRY_FILE, "r") as f:
                lines = [line.strip() for line in f if line.strip()]

            valid_lines = []
            fixed = False
            for line in lines:
                if Path(line).exists():
                    valid_lines.append(line)
                else:
                    fixed = True

            if fixed:
                with open(REGISTRY_FILE, "w") as f:
                    f.write("\n".join(valid_lines) + "\n")
                console.print(
                    "   [green]✔ Registry cleaned (ghost entries removed).[/green]"
                )
            else:
                console.print("   [green]✔ Registry healthy.[/green]")

    # Check daemon status.
    with console.status("[bold blue]Checking Daemon...", spinner="dots"):
        if _is_service_enabled():
            console.print("   [green]✔ Daemon is active.[/green]")
        else:
            console.print(
                "   [red]✘ Daemon is STOPPED.[/red] Run 'git pulsar install-service'."
            )

    # Check network/SSH connectivity.
    with console.status("[bold blue]Checking Connectivity...", spinner="dots"):
        try:
            res = subprocess.run(
                ["ssh", "-T", "git@github.com"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if "successfully authenticated" in res.stderr:
                console.print("   [green]✔ GitHub SSH connection successful.[/green]")
            else:
                console.print(
                    "   [yellow]⚠ GitHub SSH check returned "
                    "unexpected response.[/yellow]"
                )

        except Exception as e:
            console.print(f"   [red]✘ SSH Check failed: {e}[/red]")

    # Perform diagnostics on logs and repository freshness.
    console.print("\n[bold]Diagnostics[/bold]")

    # Check logs for recent errors.
    recent_errors = _analyze_logs(hours=24)
    if recent_errors:
        console.print(
            f"   [red]✘ Found {len(recent_errors)} errors in the last 24h:[/red]"
        )
        for err in recent_errors[-3:]:  # Show last 3
            console.print(f"     [dim]{err}[/dim]")
        if len(recent_errors) > 3:
            console.print("     ... (run 'git pulsar log' to see full history)")
    else:
        console.print("   [green]✔ Recent logs are clean.[/green]")

    # Check the health of registered repositories (Pulse Check).
    with console.status("[bold blue]Checking Repository Health...", spinner="dots"):
        if REGISTRY_FILE.exists():
            with open(REGISTRY_FILE, "r") as f:
                paths = [Path(line.strip()) for line in f if line.strip()]

            issues = []
            for p in paths:
                if p.exists():
                    if problem := _check_repo_health(p):
                        issues.append(f"{p.name}: {problem}")

            if issues:
                console.print(
                    f"   [yellow]⚠ Found {len(issues)} stalled repository(s):[/yellow]"
                )
                for issue in issues:
                    console.print(f"     - {issue}")
                console.print(
                    "     [dim](Check if daemon is running or "
                    "if files are too large)[/dim]"
                )
            else:
                console.print(
                    "   [green]✔ All repositories are healthy "
                    "(clean or backed up).[/green]"
                )


def add_ignore_cli(pattern: str) -> None:
    """Adds a file pattern to the repository's ignore list.

    Args:
        pattern (str): The file pattern to ignore (e.g., '*.log').
    """
    if not Path(".git").exists():
        console.print("[bold red]Not a git repository.[/bold red]")
        return
    ops.add_ignore(pattern)


def tail_log() -> None:
    """Follows the daemon log file in real-time."""
    if not LOG_FILE.exists():
        console.print(f"[red]No log file found yet at {LOG_FILE}.[/red]")
        return

    console.print(f"Tailing [bold cyan]{LOG_FILE}[/bold cyan] (Ctrl+C to stop)...")
    try:
        subprocess.run(["tail", "-n", "1000", "-f", str(LOG_FILE)])
    except KeyboardInterrupt:
        console.print("\nStopped.", style="dim")


def set_pause_state(paused: bool) -> None:
    """Toggles the backup state for the current repository.

    Args:
        paused (bool): True to pause backups, False to resume them.
    """
    if not Path(".git").exists():
        console.print("[bold red]Not a git repository.[/bold red]")
        sys.exit(1)

    pause_file = Path(".git/pulsar_paused")
    if paused:
        pause_file.touch()
        console.print(
            "Pulsar paused. Backups suspended for this repo.", style="bold yellow"
        )
    else:
        if pause_file.exists():
            pause_file.unlink()
        console.print("Pulsar resumed. Backups active.", style="bold green")


def setup_repo(registry_path: Path = REGISTRY_FILE) -> None:
    """Initializes Git Pulsar for the current repository and registers it.

    This ensures the directory is a git repository, sets up a default .gitignore,
    and adds the path to the global registry.

    Args:
        registry_path (Path, optional): Path to the registry file.
                                        Defaults to REGISTRY_FILE.
    """
    cwd = Path.cwd()

    # Ensure the directory is a git repository.
    if not (cwd / ".git").exists():
        console.print(
            f"[bold blue]Git Pulsar:[/bold blue] activating "
            f"for [cyan]{cwd.name}[/cyan]..."
        )
        subprocess.run(["git", "init"], check=True)

    repo = GitRepo(cwd)

    # Trigger Identity Configuration (with Sync)
    # We pass the repo so it can check 'origin' for collisions.
    system.configure_identity(repo)

    # Ensure a .gitignore file exists and contains default patterns.
    gitignore = cwd / ".gitignore"

    if not gitignore.exists():
        console.print("[dim]Creating basic .gitignore...[/dim]")
        with open(gitignore, "w") as f:
            f.write("\n".join(DEFAULT_IGNORES) + "\n")
    else:
        console.print(
            "Existing .gitignore found. Checking for missing defaults...", style="dim"
        )
        with open(gitignore, "r") as f:
            existing_content = f.read()

        missing_defaults = [d for d in DEFAULT_IGNORES if d not in existing_content]

        if missing_defaults:
            console.print(
                f"Appending {len(missing_defaults)} missing ignores...", style="dim"
            )
            with open(gitignore, "a") as f:
                f.write("\n" + "\n".join(missing_defaults) + "\n")
        else:
            console.print("All defaults present.", style="dim")

    # Register the repository path.
    console.print("Registering path...", style="dim")
    if not registry_path.exists():
        registry_path.touch()

    with open(registry_path, "r+") as f:
        content = f.read()
        if str(cwd) not in content:
            f.write(f"{cwd}\n")
            console.print(f"Registered: [cyan]{cwd}[/cyan]", style="green")
        else:
            console.print("Already registered.", style="dim")

    console.print("\n[bold green]✔ Pulsar Active.[/bold green]")

    try:
        remotes = repo._run(["remote"])
        if remotes:
            console.print("Verifying git access...", style="dim")
            repo._run(["push", "--dry-run"], capture=False)
    except Exception:
        console.print(
            "⚠ WARNING: Git push failed. Ensure you have "
            "SSH keys set up or credentials cached.",
            style="bold yellow",
        )


def main() -> None:
    """Main entry point for the Git Pulsar CLI."""
    parser = argparse.ArgumentParser(description="Git Pulsar CLI")

    # Global flags
    parser.add_argument(
        "--env",
        "-e",
        action="store_true",
        help="Bootstrap macOS Python environment (uv, direnv, VS Code)",
    )

    subparsers = parser.add_subparsers(
        dest="command", help="Service management commands"
    )

    # Subcommands
    install_parser = subparsers.add_parser(
        "install-service", help="Install the background daemon"
    )
    install_parser.add_argument(
        "--interval",
        type=int,
        default=900,
        help="Backup interval in seconds (default: 900)",
    )
    subparsers.add_parser("uninstall-service", help="Uninstall the background daemon")
    subparsers.add_parser("now", help="Run backup immediately (one-off)")

    # Restore Command
    restore_parser = subparsers.add_parser(
        "restore", help="Restore a file from the backup branch"
    )
    restore_parser.add_argument("path", help="Path to the file to restore")
    restore_parser.add_argument(
        "--force", "-f", action="store_true", help="Overwrite local changes"
    )

    subparsers.add_parser(
        "finalize", help="Squash backup stream into main and reset history"
    )

    subparsers.add_parser("pause", help="Suspend backups for current repo")
    subparsers.add_parser("resume", help="Resume backups for current repo")
    subparsers.add_parser("status", help="Show daemon and repo status")
    subparsers.add_parser("diff", help="Show changes between working dir and backup")
    subparsers.add_parser("list", help="List registered repositories")
    subparsers.add_parser("log", help="Tail the daemon log file")

    subparsers.add_parser("help", help="Show this help message")
    subparsers.add_parser("remove", help="Stop tracking current repo")
    subparsers.add_parser("sync", help="Sync with latest session")
    subparsers.add_parser("doctor", help="Clean registry and check health")
    subparsers.add_parser("config", help="Open global config file")

    ignore_parser = subparsers.add_parser("ignore", help="Add pattern to .gitignore")
    ignore_parser.add_argument("pattern", help="File pattern (e.g. '*.log')")

    prune_parser = subparsers.add_parser("prune", help="Clean up old backup refs")
    prune_parser.add_argument(
        "--days", type=int, default=30, help="Age in days (default: 30)"
    )

    args = parser.parse_args()

    # Handle Environment Setup (Flag)
    if args.env:
        ops.bootstrap_env()

    # Handle Subcommands
    if args.command == "install-service":
        with console.status("Installing background service...", spinner="dots"):
            service.install(interval=args.interval)
        console.print("[bold green]✔ Service installed.[/bold green]")
        return
    elif args.command == "help":
        parser.print_help()
        return
    elif args.command == "remove":
        unregister_repo()
        return
    elif args.command == "sync":
        with console.status("Syncing with latest session...", spinner="dots"):
            ops.sync_session()
        console.print("[bold green]✔ Sync complete.[/bold green]")
        return
    elif args.command == "doctor":
        run_doctor()
        return
    elif args.command == "ignore":
        add_ignore_cli(args.pattern)
        return
    elif args.command == "prune":
        with console.status("Pruning old backup refs...", spinner="dots"):
            ops.prune_backups(args.days)
        return
    elif args.command == "uninstall-service":
        with console.status("Uninstalling service...", spinner="dots"):
            service.uninstall()
        console.print("[bold green]✔ Service uninstalled.[/bold green]")
        return
    elif args.command == "now":
        daemon.main(interactive=True)
        return
    elif args.command == "restore":
        ops.restore_file(args.path, args.force)
        return
    elif args.command == "finalize":
        with console.status("Finalizing work (squashing backups)...", spinner="dots"):
            ops.finalize_work()
        return
    elif args.command == "pause":
        set_pause_state(True)
        return
    elif args.command == "resume":
        set_pause_state(False)
        return
    elif args.command == "status":
        show_status()
        return
    elif args.command == "diff":
        show_diff()
        return
    elif args.command == "list":
        list_repos()
        return
    elif args.command == "log":
        tail_log()
        return
    elif args.command == "config":
        open_config()
        return

    # Default Action (if no subcommand is run, or after --env)
    # We always run setup_repo unless a service command explicitly exited.
    setup_repo()


if __name__ == "__main__":
    main()
