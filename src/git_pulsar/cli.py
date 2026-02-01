import argparse
import os
import subprocess
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import daemon, ops, service
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
    """Helper to resolve the namespaced backup ref for the current repo state."""
    return ops.get_backup_ref(repo.current_branch())


def show_status() -> None:
    # 1. Daemon Health
    is_running = False
    if PID_FILE.exists():
        try:
            with open(PID_FILE, "r") as f:
                pid = int(f.read().strip())
            os.kill(pid, 0)
            is_running = True
        except (ValueError, OSError):
            is_running = False

    status_style = "bold green" if is_running else "bold red"
    status_text = "Active" if is_running else "Stopped"

    system_content = Text()
    system_content.append("Daemon: ", style="bold")
    system_content.append(status_text, style=status_style)

    # Usage: console (instance), not Console (class)
    console.print(Panel(system_content, title="System Status", expand=False))

    # 2. Repo Status (if we are in one)
    if Path(".git").exists():
        repo = GitRepo(Path.cwd())
        ref = _get_ref(repo)

        try:
            time_str = repo.get_last_commit_time(ref)
        except Exception:
            time_str = "None (No backup found)"

        count = len(repo.status_porcelain())
        is_paused = (Path(".git") / "pulsar_paused").exists()

        repo_content = Text()
        repo_content.append(f"Last Backup: {time_str}\n")
        repo_content.append(f"Pending:     {count} files changed\n")

        if is_paused:
            repo_content.append("Mode:        PAUSED", style="bold yellow")
        else:
            repo_content.append("Mode:        Active", style="green")

        console.print(Panel(repo_content, title="Repository Status", expand=False))

    # 3. Global Summary (if not in a repo)
    elif REGISTRY_FILE.exists():
        with open(REGISTRY_FILE) as f:
            count = len([line for line in f if line.strip()])
        console.print(f"[dim]Watching {count} repositories.[/dim]")


def show_diff() -> None:
    if not Path(".git").exists():
        console.print("[bold red]Not a git repository.[/bold red]")
        sys.exit(1)

    repo = GitRepo(Path.cwd())

    # 1. Standard Diff (tracked files)
    ref = _get_ref(repo)

    console.print(f"[bold]Diff vs {ref}:[/bold]\n")
    repo.run_diff(ref)

    # 2. Untracked Files
    if untracked := repo.get_untracked_files():
        console.print("\n[bold green]Untracked (New) Files:[/bold green]")
        for line in untracked:
            console.print(f"   + {line}", style="green")


def list_repos() -> None:
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
    console.print("[bold]Pulsar Doctor[/bold]\n")

    # 1. Registry Hygiene
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

    # 2. Daemon Status
    with console.status("[bold blue]Checking Daemon...", spinner="dots"):
        is_running = False
        if sys.platform == "darwin":
            res = subprocess.run(["launchctl", "list"], capture_output=True, text=True)
            # Check Homebrew label instead of internal label
            is_running = HOMEBREW_LABEL in res.stdout
        elif sys.platform.startswith("linux"):
            res = subprocess.run(
                ["systemctl", "--user", "is-active", f"{APP_LABEL}.timer"],
                capture_output=True,
                text=True,
            )
            is_running = res.stdout.strip() == "active"

        if is_running:
            console.print("   [green]✔ Daemon is active.[/green]")
        else:
            console.print(
                "   [red]✘ Daemon is STOPPED.[/red] Run 'git pulsar install-service'."
            )

    # 3. Connectivity
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


def add_ignore_cli(pattern: str) -> None:
    if not Path(".git").exists():
        console.print("[bold red]Not a git repository.[/bold red]")
        return
    ops.add_ignore(pattern)


def tail_log() -> None:
    if not LOG_FILE.exists():
        console.print(f"[red]No log file found yet at {LOG_FILE}.[/red]")
        return

    console.print(f"Tailing [bold cyan]{LOG_FILE}[/bold cyan] (Ctrl+C to stop)...")
    try:
        subprocess.run(["tail", "-f", str(LOG_FILE)])
    except KeyboardInterrupt:
        console.print("\nStopped.", style="dim")


def set_pause_state(paused: bool) -> None:
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
    cwd = Path.cwd()
    # Removed incorrect "Not a git repository" print here

    # 1. Ensure it's a git repo
    if not (cwd / ".git").exists():
        console.print(
            f"[bold blue]Git Pulsar:[/bold blue] activating "
            f"for [cyan]{cwd.name}[/cyan]..."
        )
        subprocess.run(["git", "init"], check=True)

    repo = GitRepo(cwd)

    # 2. Check/Create .gitignore
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

    # 3. Add to Registry
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

    ignore_parser = subparsers.add_parser("ignore", help="Add pattern to .gitignore")
    ignore_parser.add_argument("pattern", help="File pattern (e.g. '*.log')")

    prune_parser = subparsers.add_parser("prune", help="Clean up old backup refs")
    prune_parser.add_argument(
        "--days", type=int, default=30, help="Age in days (default: 30)"
    )

    args = parser.parse_args()

    # 1. Handle Environment Setup (Flag)
    if args.env:
        ops.bootstrap_env()

    # 2. Handle Subcommands
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

    # 3. Default Action (if no subcommand is run, or after --env)
    # We always run setup_repo unless a service command explicitly exited.
    setup_repo()


if __name__ == "__main__":
    main()
