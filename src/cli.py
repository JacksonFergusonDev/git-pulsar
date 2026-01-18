import argparse
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

from . import daemon, service

REGISTRY_FILE = Path.home() / ".git_pulsar_registry"
BACKUP_BRANCH = "wip/pulsar"


def show_status() -> None:
    # 1. Daemon Health
    print("--- 🩺 System Status ---")
    is_running = False
    if sys.platform == "darwin":
        res = subprocess.run(["launchctl", "list"], capture_output=True, text=True)
        is_running = "com.jacksonferguson.gitpulsar" in res.stdout
    elif sys.platform.startswith("linux"):
        res = subprocess.run(
            ["systemctl", "--user", "is-active", "com.jacksonferguson.gitpulsar.timer"],
            capture_output=True,
            text=True,
        )
        is_running = res.stdout.strip() == "active"

    state_icon = "🟢 Running" if is_running else "🔴 Stopped"
    print(f"Daemon: {state_icon}")

    # 2. Repo Status (if we are in one)
    if Path(".git").exists():
        print("\n--- 📂 Repository Status ---")

        # Last Backup Time
        try:
            last_time = subprocess.check_output(
                ["git", "log", "-1", "--format=%cr", BACKUP_BRANCH],
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
        except subprocess.CalledProcessError:
            last_time = "Never"

        print(f"Last Backup: {last_time}")

        # Pending Changes
        status = subprocess.check_output(
            ["git", "status", "--porcelain"], text=True
        ).strip()
        count = len(status.splitlines()) if status else 0
        print(f"Pending:     {count} files changed")

        if (Path(".git") / "pulsar_paused").exists():
            print("Mode:        ⏸️  PAUSED")


def restore_file(path_str: str, force: bool = False) -> None:
    path = Path(path_str)
    if not path.exists():
        # It might be a deleted file we want to recover,
        # so we don't strictly require existence.
        pass

    # 1. Safety Check: Is the file dirty?
    if not force and path.exists():
        try:
            # git status --porcelain <path> returns output if modified/staged
            status = subprocess.check_output(
                ["git", "status", "--porcelain", path_str], text=True
            ).strip()
            if status:
                print(f"❌ Aborted: '{path_str}' has uncommitted changes.")
                print("   Use --force to overwrite them.")
                sys.exit(1)
        except subprocess.CalledProcessError:
            pass

    # 2. Restore
    print(f"🚑 Restoring '{path_str}' from {BACKUP_BRANCH}...")
    try:
        subprocess.run(["git", "checkout", BACKUP_BRANCH, "--", path_str], check=True)
        print("✅ Restore complete.")
    except subprocess.CalledProcessError as e:
        print(f"❌ Failed to restore: {e}")
        sys.exit(1)


def finalize_work() -> None:
    print("🚀 Finalizing work...")

    # 1. Check we are in a repo
    if not Path(".git").exists():
        print("❌ Not a git repository.")
        sys.exit(1)

    try:
        # 2. Ensure clean working state on current branch (usually wip/pulsar)
        status = subprocess.check_output(
            ["git", "status", "--porcelain"], text=True
        ).strip()
        if status:
            print("⚠️  You have uncommitted changes.")
            print("   Please commit or stash them before finalizing.")
            sys.exit(1)

        # 3. Checkout Main
        print("-> Switching to main...")
        subprocess.run(["git", "checkout", "main"], check=True)

        # 4. Merge Squash
        print(f"-> Squashing {BACKUP_BRANCH}...")
        subprocess.run(["git", "merge", "--squash", BACKUP_BRANCH], check=True)

        # 5. Commit (Interactive)
        print("-> Committing (opens editor)...")
        subprocess.run(["git", "commit"], check=True)

        # 6. Reset Backup Branch
        # We point wip/pulsar to the new main so the next session starts fresh.
        print(f"-> Resetting {BACKUP_BRANCH} to main...")
        subprocess.run(["git", "branch", "-f", BACKUP_BRANCH, "main"], check=True)

        print("\n✅ Work finalized!")
        print(
            f"   You are now on 'main'. "
            f"Run 'git-pulsar' to switch back to {BACKUP_BRANCH}."
        )

    except subprocess.CalledProcessError as e:
        print(f"\n❌ Error during finalize: {e}")
        sys.exit(1)


def bootstrap_env() -> None:
    """
    Scaffolds a macOS Python environment: uv, direnv, and VS Code.
    Triggered by `git-pulsar --env`.
    """
    if sys.platform != "darwin":
        print("❌ The --env workflow is currently optimized for macOS.")
        return

    cwd = Path.cwd()
    print(f"⚡ Setting up dev environment in {cwd.name}...")

    # 1. Dependency Check
    missing = []
    if not shutil.which("uv"):
        missing.append("uv")
    if not shutil.which("direnv"):
        missing.append("direnv")

    if missing:
        print(f"❌ Missing tools: {', '.join(missing)}")
        print("   Please run:")
        print(f"     brew install {' '.join(missing)}")
        sys.exit(1)

    # 2. Project Scaffold (uv)
    if not (cwd / "pyproject.toml").exists():
        print("📦 Initializing Python project...")
        # 'uv init' is safe; it creates a standard pyproject.toml
        subprocess.run(["uv", "init", "--no-workspace", "--python", "3.12"], check=True)
    else:
        print("   Existing pyproject.toml found. Skipping init.")

    # 3. Direnv Configuration
    envrc_path = cwd / ".envrc"
    if not envrc_path.exists():
        print("🔌 Creating .envrc...")
        # We use a simplified config that handles the venv creation
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
        print("   .envrc exists. Skipping.")

    # 4. VS Code Settings (Optimized for Students)
    vscode_dir = cwd / ".vscode"
    settings_path = vscode_dir / "settings.json"

    if not settings_path.exists():
        vscode_dir.mkdir(exist_ok=True)
        print("⚙️  Configuring VS Code...")
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

    print("\n✅ Environment ready.")

    # 5. The "Manual" Hook
    # Check if direnv is likely hooked by checking for the env var it sets
    if "DIRENV_DIR" not in os.environ:
        print("\n👉 Action Required: Enable direnv")
        print("   1. Open your config:")
        print("      code ~/.zshrc  (or nano ~/.zshrc)")
        print("   2. Add this line to the bottom:")
        print('      eval "$(direnv hook zsh)"')
        print("   3. Reload:")
        print("      source ~/.zshrc")


def set_pause_state(paused: bool) -> None:
    if not Path(".git").exists():
        print("❌ Not a git repository.")
        sys.exit(1)

    pause_file = Path(".git/pulsar_paused")
    if paused:
        pause_file.touch()
        print("⏸️  Pulsar paused. Backups suspended for this repo.")
    else:
        if pause_file.exists():
            pause_file.unlink()
        print("▶️  Pulsar resumed. Backups active.")


def setup_repo(registry_path: Path = REGISTRY_FILE) -> None:
    cwd = Path.cwd()
    print(f"🔭 Git Pulsar: activating for {cwd.name}...")

    # 1. Ensure it's a git repo
    if not (cwd / ".git").exists():
        print(f"Initializing git in {cwd}...")
        subprocess.run(["git", "init"], check=True)

    # 2. Check/Create .gitignore
    gitignore = cwd / ".gitignore"
    defaults = [
        "__pycache__/",
        "*.ipynb_checkpoints",
        "*.pdf",
        "*.aux",
        "*.log",
        ".DS_Store",
    ]

    if not gitignore.exists():
        print("Creating basic .gitignore...")
        with open(gitignore, "w") as f:
            f.write("\n".join(defaults) + "\n")
    else:
        print("Existing .gitignore found. Skipping creation.")

    # 3. Create/Switch to the backup branch
    print(f"Switching to {BACKUP_BRANCH}...")
    try:
        subprocess.run(
            ["git", "checkout", BACKUP_BRANCH], check=True, stderr=subprocess.DEVNULL
        )
    except subprocess.CalledProcessError:
        try:
            # Create orphan if main doesn't exist, or branch off current
            subprocess.run(["git", "checkout", "-b", BACKUP_BRANCH], check=True)
        except subprocess.CalledProcessError as e:
            print(f"❌ Error switching branches: {e}")
            sys.exit(1)

    # 4. Add to Registry
    print("Registering path...")
    if not registry_path.exists():
        registry_path.touch()

    with open(registry_path, "r+") as f:
        content = f.read()
        if str(cwd) not in content:
            f.write(f"{cwd}\n")
            print(f"Registered: {cwd}")
        else:
            print("Already registered.")

    print("\n✅ Pulsar Active.")

    try:
        # Check if we can verify credentials (only if remote exists)
        remotes = subprocess.check_output(["git", "remote"], cwd=cwd, text=True).strip()
        if remotes:
            print("Verifying git access...")
            subprocess.run(
                ["git", "push", "--dry-run"],
                cwd=cwd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
            )
    except subprocess.CalledProcessError:
        print(
            "⚠️  WARNING: Git push failed. Ensure you have SSH keys set up or "
            "credentials cached."
        )
        print(
            "   Background backups will fail if authentication requires a password "
            "prompt."
        )

    print("1. Add remote: git remote add origin <url>")
    print("2. Work loop: code -> code (auto-commits happen)")
    print(f"3. Milestone: git checkout main -> git merge --squash {BACKUP_BRANCH}")


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
        "finalize", help="Squash wip/pulsar into main and reset backup history"
    )

    subparsers.add_parser("pause", help="Suspend backups for current repo")
    subparsers.add_parser("resume", help="Resume backups for current repo")
    subparsers.add_parser("status", help="Show daemon and repo status")

    args = parser.parse_args()

    # 1. Handle Environment Setup (Flag)
    if args.env:
        bootstrap_env()

    # 2. Handle Subcommands
    if args.command == "install-service":
        service.install(interval=args.interval)
        return
    elif args.command == "uninstall-service":
        service.uninstall()
        return
    elif args.command == "now":
        daemon.main(interactive=True)
        return
    elif args.command == "restore":
        restore_file(args.path, args.force)
        return
    elif args.command == "finalize":
        finalize_work()
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

    # 3. Default Action (if no subcommand is run, or after --env)
    # We always run setup_repo unless a service command explicitly exited.
    setup_repo()


if __name__ == "__main__":
    main()
