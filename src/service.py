import shutil
import subprocess
import sys
from pathlib import Path

from .constants import LOG_FILE

APP_LABEL = "com.jacksonferguson.gitpulsar"


def get_executable() -> str:
    """Locates the installed daemon executable."""
    exe = shutil.which("git-pulsar-daemon")
    if not exe:
        print(
            "❌ Error: Could not find 'git-pulsar-daemon'. Ensure the package is "
            "installed."
        )
        sys.exit(1)
    return exe


def get_paths() -> tuple[Path, Path]:
    """Returns (service_file_path, log_path) based on OS."""
    home = Path.home()
    if sys.platform == "darwin":
        return (
            home / f"Library/LaunchAgents/{APP_LABEL}.plist",
            LOG_FILE,
        )
    elif sys.platform.startswith("linux"):
        return (
            home / f".config/systemd/user/{APP_LABEL}.service",
            LOG_FILE,
        )
    else:
        print(f"❌ OS {sys.platform} not supported for auto-scheduling yet.")
        sys.exit(1)


def install_macos(
    plist_path: Path, log_path: Path, executable: str, interval: int
) -> None:
    content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{APP_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{executable}</string>
    </array>
    <key>StartInterval</key>
    <key>StartInterval</key>
    <integer>{interval}</integer>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{log_path}</string>
    <key>StandardErrorPath</key>
    <string>{log_path}</string>
</dict>
</plist>"""

    plist_path.parent.mkdir(parents=True, exist_ok=True)
    with open(plist_path, "w") as f:
        f.write(content)

    subprocess.run(["launchctl", "unload", str(plist_path)], stderr=subprocess.DEVNULL)
    subprocess.run(["launchctl", "load", str(plist_path)], check=True)
    print(f"✅ Pulsar background service active (macOS).\nLogs: {log_path}")


def install_linux(
    unit_path: Path, log_path: Path, executable: str, interval: int
) -> None:
    base_dir = unit_path.parent
    base_dir.mkdir(parents=True, exist_ok=True)

    service_file = base_dir / f"{APP_LABEL}.service"
    timer_file = base_dir / f"{APP_LABEL}.timer"

    service_content = f"""[Unit]
Description=Git Pulsar Backup Daemon

[Service]
ExecStart={executable}
"""
    timer_content = f"""[Unit]
Description=Run Git Pulsar every {interval} seconds

[Timer]
OnBootSec=5min
OnUnitActiveSec={interval}s
Unit={APP_LABEL}.service

[Install]
WantedBy=timers.target
"""

    with open(service_file, "w") as f:
        f.write(service_content)
    with open(timer_file, "w") as f:
        f.write(timer_content)

    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(
        ["systemctl", "--user", "enable", "--now", f"{APP_LABEL}.timer"], check=True
    )
    print(
        f"✅ Pulsar systemd timer active (Linux).\n"
        f"Check status: systemctl --user status {APP_LABEL}.timer"
    )


def install(interval: int = 900) -> None:
    exe = get_executable()
    path, log = get_paths()

    print(f"Installing background service (interval: {interval}s)...")
    if sys.platform == "darwin":
        install_macos(path, log, exe, interval)
    elif sys.platform.startswith("linux"):
        install_linux(path, log, exe, interval)


def uninstall() -> None:
    path, _ = get_paths()
    if sys.platform == "darwin":
        subprocess.run(["launchctl", "unload", str(path)], stderr=subprocess.DEVNULL)
        if path.exists():
            path.unlink()
    elif sys.platform.startswith("linux"):
        timer_name = f"{APP_LABEL}.timer"
        subprocess.run(
            ["systemctl", "--user", "disable", "--now", timer_name],
            stderr=subprocess.DEVNULL,
        )

        # Remove .service and .timer
        timer_path = path.parent / timer_name
        if path.exists():
            path.unlink()
        if timer_path.exists():
            timer_path.unlink()

        subprocess.run(["systemctl", "--user", "daemon-reload"])

    print("✅ Service uninstalled.")
