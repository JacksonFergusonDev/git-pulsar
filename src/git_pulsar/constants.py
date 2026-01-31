import os
from pathlib import Path

# --- Identity ---
APP_NAME = "git-pulsar"
APP_LABEL = "com.jacksonferguson.gitpulsar"
BACKUP_NAMESPACE = "wip/pulsar"

# --- Paths ---
_XDG_STATE = os.environ.get("XDG_STATE_HOME")
_BASE_STATE = Path(_XDG_STATE) if _XDG_STATE else Path.home() / ".local/state"

STATE_DIR = _BASE_STATE / "git-pulsar"
# Ensure state dir exists on import
STATE_DIR.mkdir(parents=True, exist_ok=True)

REGISTRY_FILE = STATE_DIR / "registry"
LOG_FILE = STATE_DIR / "daemon.log"

# --- Configuration Paths ---
CONFIG_DIR: Path = Path.home() / ".config/git-pulsar"
CONFIG_FILE: Path = CONFIG_DIR / "config.toml"
MACHINE_ID_FILE: Path = CONFIG_DIR / "machine_id"

# --- Git / Logic Constants ---
DEFAULT_IGNORES = [
    "__pycache__/",
    "*.ipynb_checkpoints",
    "*.pdf",
    "*.aux",
    "*.log",
    ".DS_Store",
]

GIT_LOCK_FILES = [
    "MERGE_HEAD",
    "REBASE_HEAD",
    "CHERRY_PICK_HEAD",
    "BISECT_LOG",
    "rebase-merge",
    "rebase-apply",
]

PID_FILE = REGISTRY_FILE.parent / "daemon.pid"
