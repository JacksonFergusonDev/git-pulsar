import subprocess
import sys
from . import service
from pathlib import Path

REGISTRY_FILE = Path.home() / ".git_pulsar_registry"
BACKUP_BRANCH = "wip/pulsar"


def setup_repo() -> None:
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
    if not REGISTRY_FILE.exists():
        REGISTRY_FILE.touch()

    with open(REGISTRY_FILE, "r+") as f:
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
            "⚠️  WARNING: Git push failed. Ensure you have SSH keys set up or credentials cached."
        )
        print(
            "   Background backups will fail if authentication requires a password prompt."
        )

    print("1. Add remote: git remote add origin <url>")
    print("2. Work loop: code -> code (auto-commits happen)")
    print(f"3. Milestone: git checkout main -> git merge --squash {BACKUP_BRANCH}")


def main() -> None:
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "install-service":
            service.install()
            return
        elif cmd == "uninstall-service":
            service.uninstall()
            return

    setup_repo()


if __name__ == "__main__":
    main()
