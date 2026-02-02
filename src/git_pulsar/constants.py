import os
from pathlib import Path

"""Global constants and configuration path definitions for Git Pulsar.

This module defines the filesystem layout (adhering to XDG standards where applicable),
application identifiers, and default Git configuration values used across the
application.
"""

# --- Identity ---
APP_NAME = "git-pulsar"
"""str: The human-readable application name."""

APP_LABEL = "com.jacksonferguson.gitpulsar"
"""str: The reverse-DNS style application identifier."""

HOMEBREW_LABEL = "homebrew.mxcl.git-pulsar"
"""str: The service label used when installed via Homebrew."""

BACKUP_NAMESPACE = "wip/pulsar"
"""str: The Git reference namespace used for storing backup commits."""

# --- Paths ---
_XDG_STATE = os.environ.get("XDG_STATE_HOME")
_BASE_STATE = Path(_XDG_STATE) if _XDG_STATE else Path.home() / ".local/state"

STATE_DIR = _BASE_STATE / "git-pulsar"
"""Path: The directory for runtime state data (logs, registry)."""

# Ensure state directory exists immediately upon module import.
STATE_DIR.mkdir(parents=True, exist_ok=True)

REGISTRY_FILE = STATE_DIR / "registry"
"""Path: The file path storing the list of registered repositories."""

LOG_FILE = STATE_DIR / "daemon.log"
"""Path: The file path for the daemon process logs."""

# --- Configuration Paths ---
CONFIG_DIR: Path = Path.home() / ".config/git-pulsar"
"""Path: The directory for user configuration files."""

CONFIG_FILE: Path = CONFIG_DIR / "config.toml"
"""Path: The main configuration file path."""

MACHINE_ID_FILE: Path = CONFIG_DIR / "machine_id"
"""Path: The file path storing the unique machine identifier."""

# --- Git / Logic Constants ---
DEFAULT_IGNORES = [
    "__pycache__/",
    "*.ipynb_checkpoints",
    "*.pdf",
    "*.aux",
    "*.log",
    ".DS_Store",
]
"""list[str]: Default file patterns added to .gitignore during repository setup."""

GIT_LOCK_FILES = [
    "MERGE_HEAD",
    "REBASE_HEAD",
    "CHERRY_PICK_HEAD",
    "BISECT_LOG",
    "rebase-merge",
    "rebase-apply",
]
"""
list[str]: Git internal files indicating an
active state (merge/rebase) that blocks backups.
"""

PID_FILE = REGISTRY_FILE.parent / "daemon.pid"
"""Path: The file path storing the daemon's process ID."""
