import os
from pathlib import Path

APP_NAME = "git-pulsar"
BACKUP_BRANCH = "wip/pulsar"


def _get_state_dir() -> Path:
    """Resolves XDG_STATE_HOME (default: ~/.local/state/git-pulsar)."""
    if xdg_state := os.environ.get("XDG_STATE_HOME"):
        base = Path(xdg_state)
    else:
        base = Path.home() / ".local" / "state"

    target = base / "git-pulsar"
    target.mkdir(parents=True, exist_ok=True)
    return target


_STATE_DIR = _get_state_dir()
REGISTRY_FILE = _STATE_DIR / "registry"
LOG_FILE = _STATE_DIR / "daemon.log"
