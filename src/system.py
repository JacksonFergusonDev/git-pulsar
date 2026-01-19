import os
import socket
import subprocess
import sys
from pathlib import Path

from constants import MACHINE_ID_FILE


class SystemStrategy:
    def get_battery(self) -> tuple[int, bool]:
        """Returns (percentage, is_plugged_in)."""
        return 100, True

    def is_under_load(self) -> bool:
        """Returns True if 1-minute load average > 2.5x CPU count."""
        if not hasattr(os, "getloadavg"):
            return False
        try:
            load_1m, _, _ = os.getloadavg()
            cpu_count = os.cpu_count() or 1
            return load_1m > (cpu_count * 2.5)
        except OSError:
            return False

    def notify(self, title: str, message: str) -> None:
        pass


class MacOSStrategy(SystemStrategy):
    def get_battery(self) -> tuple[int, bool]:
        try:
            out = subprocess.check_output(["pmset", "-g", "batt"], text=True)
            is_plugged = "AC Power" in out
            import re

            match = re.search(r"(\d+)%", out)
            percent = int(match.group(1)) if match else 100
            return percent, is_plugged
        except Exception:
            return 100, True

    def notify(self, title: str, message: str) -> None:
        clean_msg = message.replace('"', "'")
        script = f'display notification "{clean_msg}" with title "{title}"'
        try:
            subprocess.run(["osascript", "-e", script], stderr=subprocess.DEVNULL)
        except Exception:
            pass


class LinuxStrategy(SystemStrategy):
    def get_battery(self) -> tuple[int, bool]:
        try:
            bat_path = Path("/sys/class/power_supply/BAT0")
            if not bat_path.exists():
                bat_path = Path("/sys/class/power_supply/BAT1")

            if bat_path.exists():
                with open(bat_path / "capacity", "r") as f:
                    percent = int(f.read().strip())
                with open(bat_path / "status", "r") as f:
                    is_plugged = f.read().strip() != "Discharging"
                return percent, is_plugged
        except Exception:
            pass
        return 100, True

    def notify(self, title: str, message: str) -> None:
        try:
            subprocess.run(["notify-send", title, message], stderr=subprocess.DEVNULL)
        except FileNotFoundError:
            pass


def get_system() -> SystemStrategy:
    if sys.platform == "darwin":
        return MacOSStrategy()
    elif sys.platform.startswith("linux"):
        return LinuxStrategy()
    else:
        return SystemStrategy()


def get_machine_id_file() -> Path:
    return Path(MACHINE_ID_FILE)


def get_machine_id() -> str:
    """
    Returns the persistent machine ID.
    Falls back to hostname if not configured (legacy behavior).
    """
    id_file = get_machine_id_file()
    if id_file.exists():
        return id_file.read_text().strip()
    return socket.gethostname()
