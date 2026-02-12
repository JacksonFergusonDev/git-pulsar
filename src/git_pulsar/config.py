import logging
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .constants import (
    APP_NAME,
    BACKUP_NAMESPACE,
    CONFIG_FILE,
)

logger = logging.getLogger(APP_NAME)


@dataclass
class CoreConfig:
    """Core application settings.

    Attributes:
        backup_branch (str): The namespace used for backup refs.
        remote_name (str): The git remote to push backups to.
    """

    backup_branch: str = BACKUP_NAMESPACE
    remote_name: str = "origin"


@dataclass
class LimitsConfig:
    """Resource limitation settings.

    Attributes:
        max_log_size (int): Max bytes for log files before rotation.
        large_file_threshold (int): Max bytes for a file before triggering a warning.
    """

    max_log_size: int = 5 * 1024 * 1024
    large_file_threshold: int = 100 * 1024 * 1024


@dataclass
class FilesConfig:
    """File management settings.

    Attributes:
        ignore (list[str]): List of patterns to ignore (appended to defaults).
    """

    ignore: list[str] = field(default_factory=list)


@dataclass
class DaemonConfig:
    """Daemon operational settings.

    Attributes:
        commit_interval (int): Seconds between local commits.
        push_interval (int): Seconds between remote pushes.
        min_battery_percent (int): Battery floor for commits.
        eco_mode_percent (int): Battery floor for pushes.
        preset (str | None): A configuration preset name (e.g. 'paranoid').
    """

    commit_interval: int = 600
    push_interval: int = 3600
    min_battery_percent: int = 10
    eco_mode_percent: int = 20
    preset: str | None = None

    def apply_preset(self) -> None:
        """Overwrites intervals based on the selected preset."""
        if self.preset == "paranoid":
            self.commit_interval = 300  # 5 mins
            self.push_interval = 300  # 5 mins
        elif self.preset == "aggressive":
            self.commit_interval = 600  # 10 mins
            self.push_interval = 600  # 10 mins
        elif self.preset == "balanced":
            self.commit_interval = 900  # 15 mins
            self.push_interval = 3600  # 1 hour
        elif self.preset == "lazy":
            self.commit_interval = 3600  # 1 hour
            self.push_interval = 14400  # 4 hours


@dataclass
class Config:
    """Global configuration aggregator.

    Attributes:
        core (CoreConfig): Core settings.
        limits (LimitsConfig): Resource limits.
        files (FilesConfig): File handling settings.
        daemon (DaemonConfig): Daemon behavior settings.
    """

    core: CoreConfig = field(default_factory=CoreConfig)
    limits: LimitsConfig = field(default_factory=LimitsConfig)
    files: FilesConfig = field(default_factory=FilesConfig)
    daemon: DaemonConfig = field(default_factory=DaemonConfig)

    @classmethod
    def load(cls, repo_path: Path | None = None) -> "Config":
        """Loads and merges configuration from defaults, global, and local sources.

        Args:
            repo_path (Path | None): The repository root to search for local config.

        Returns:
            Config: The fully merged configuration object.
        """
        instance = cls()

        # 1. Load Global Config
        if CONFIG_FILE.exists():
            instance._merge_from_file(CONFIG_FILE)

        # 2. Load Local Config (if applicable)
        if repo_path:
            local_toml = repo_path / "pulsar.toml"
            pyproject = repo_path / "pyproject.toml"

            if local_toml.exists():
                instance._merge_from_file(local_toml)
            elif pyproject.exists():
                instance._merge_from_file(pyproject, section="tool.pulsar")

        return instance

    def _merge_from_file(self, path: Path, section: str | None = None) -> None:
        """Parses a TOML file and merges it into the current instance.

        Args:
            path (Path): Path to the TOML file.
            section (str | None): Dot-separated section path (e.g., 'tool.pulsar').
        """
        try:
            with open(path, "rb") as f:
                data = tomllib.load(f)

            # Navigate to the specific section if requested (for pyproject.toml)
            if section:
                for key in section.split("."):
                    data = data.get(key, {})

            if not data:
                return

            # Merge Logic
            if "core" in data:
                self.core = self._update_dataclass(self.core, data["core"])
            if "limits" in data:
                self.limits = self._update_dataclass(self.limits, data["limits"])
            if "daemon" in data:
                self.daemon = self._update_dataclass(self.daemon, data["daemon"])
                self.daemon.apply_preset()
            if "files" in data:
                new_ignores = data["files"].get("ignore", [])
                if new_ignores:
                    self.files.ignore.extend(new_ignores)
                    self.files.ignore = list(dict.fromkeys(self.files.ignore))

        except tomllib.TOMLDecodeError as e:
            logger.error(f"Config syntax error in {path}: {e}")
        except Exception as e:
            logger.warning(f"Failed to load config from {path}: {e}")

    @staticmethod
    def _update_dataclass(instance: Any, updates: dict) -> Any:
        """Updates a dataclass instance with a dictionary of values."""
        valid_keys = instance.__dataclass_fields__.keys()
        filtered_updates = {k: v for k, v in updates.items() if k in valid_keys}
        return replace(instance, **filtered_updates)
