import copy
import logging
import re
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

PRESETS: dict[str, dict[str, int]] = {
    "paranoid": {"commit_interval": 300, "push_interval": 300},
    "aggressive": {"commit_interval": 600, "push_interval": 600},
    "balanced": {"commit_interval": 900, "push_interval": 3600},
    "lazy": {"commit_interval": 3600, "push_interval": 14400},
}


def parse_size(value: int | str) -> int:
    """Converts human-readable size strings (e.g., '100MB') to bytes."""
    if isinstance(value, int):
        return value
    match = re.match(r"^(\d+(?:\.\d+)?)\s*([kmg]b?)$", str(value).strip().lower())
    if not match:
        raise ValueError(f"Invalid size format '{value}'")
    num, unit = float(match.group(1)), match.group(2)
    multiplier = {
        "k": 1024,
        "kb": 1024,
        "m": 1024**2,
        "mb": 1024**2,
        "g": 1024**3,
        "gb": 1024**3,
    }
    return int(num * multiplier[unit])


def parse_time(value: int | str) -> int:
    """Converts human-readable time strings (e.g., '1hr', '30m') to seconds."""
    if isinstance(value, int):
        return value
    match = re.match(
        r"^(\d+(?:\.\d+)?)\s*(s|sec|m|min|h|hr)s?$", str(value).strip().lower()
    )
    if not match:
        raise ValueError(f"Invalid time format '{value}'")
    num, unit = float(match.group(1)), match.group(2)
    multiplier = {"s": 1, "sec": 1, "m": 60, "min": 60, "h": 3600, "hr": 3600}
    return int(num * multiplier[unit])


@dataclass(frozen=True)
class ConfigFieldMeta:
    """Metadata describing a configuration setting.

    Attributes:
        key (str): The configuration field key.
        description (str): Human-readable comment explaining the setting.
        example_value (str): The example value displayed in configuration templates.
        parser (Any): Optional parser function for human-readable values.
    """

    key: str
    description: str
    example_value: str
    parser: Any = None


# Single source of truth for configuration metadata and template generation
CONFIG_SECTIONS_METADATA: dict[str, list[ConfigFieldMeta]] = {
    "core": [
        ConfigFieldMeta(
            key="backup_branch",
            description="The namespace used for backup references.",
            example_value=f'"{BACKUP_NAMESPACE}"',
        ),
        ConfigFieldMeta(
            key="remote_name",
            description="The git remote to push backups to.",
            example_value='"origin"',
        ),
    ],
    "daemon": [
        ConfigFieldMeta(
            key="preset",
            description=f"Configuration preset. Options: {', '.join(PRESETS.keys())}",
            example_value='"balanced"',
        ),
        ConfigFieldMeta(
            key="commit_interval",
            description="Time between local backup commits",
            example_value='"10m"',
            parser=parse_time,
        ),
        ConfigFieldMeta(
            key="push_interval",
            description="Time between pushing to remote",
            example_value='"1h"',
            parser=parse_time,
        ),
        ConfigFieldMeta(
            key="min_battery_percent",
            description="Battery percentage floor for commits (pauses backups if battery is lower)",
            example_value="10",
        ),
        ConfigFieldMeta(
            key="eco_mode_percent",
            description="Battery percentage floor for pushing to remotes (pauses pushes if battery is lower)",
            example_value="20",
        ),
    ],
    "limits": [
        ConfigFieldMeta(
            key="max_log_size",
            description="Max bytes for log files before rotation",
            example_value='"5MB"',
            parser=parse_size,
        ),
        ConfigFieldMeta(
            key="large_file_threshold",
            description="Max bytes for a file before triggering a warning (and ignoring it)",
            example_value='"100MB"',
            parser=parse_size,
        ),
    ],
    "files": [
        ConfigFieldMeta(
            key="ignore",
            description="List of glob patterns to ignore for backups (appended to defaults)",
            example_value="[]",
        ),
        ConfigFieldMeta(
            key="manage_gitignore",
            description="Whether the daemon is allowed to modify .gitignore automatically",
            example_value="true",
        ),
    ],
}

FIELD_PARSERS: dict[str, Any] = {
    meta.key: meta.parser
    for section_fields in CONFIG_SECTIONS_METADATA.values()
    for meta in section_fields
    if meta.parser is not None
}


def generate_default_config_template() -> str:
    """Generates the canonical TOML configuration template with descriptions and default examples."""
    lines = [
        "# Git Pulsar Global Configuration",
        "# Uncomment settings below to override their defaults.",
        "",
    ]
    for section, fields in CONFIG_SECTIONS_METADATA.items():
        lines.append(f"[{section}]")
        for f in fields:
            lines.append(f"# {f.description}")
            lines.append(f"# {f.key} = {f.example_value}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


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
        manage_gitignore (bool): Whether the daemon is allowed to modify .gitignore.
    """

    ignore: list[str] = field(default_factory=list)
    manage_gitignore: bool = True


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
    sync_enabled: bool = False

    def apply_preset(self) -> None:
        """Overwrites intervals based on the selected preset."""
        if self.preset and self.preset in PRESETS:
            preset_values = PRESETS[self.preset]
            self.commit_interval = preset_values["commit_interval"]
            self.push_interval = preset_values["push_interval"]


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

    # Cache for the base global configuration
    _global_cache: "Config | None" = None

    @classmethod
    def load(cls, repo_path: Path | None = None) -> "Config":
        """Loads and merges configuration from defaults, global, and local sources.

        Args:
            repo_path (Path | None): The repository root to search for local config.

        Returns:
            Config: The fully merged configuration object.
        """
        # 1. Load or Retrieve Global Config
        if cls._global_cache is None:
            instance = cls()
            if CONFIG_FILE.exists():
                instance._merge_from_file(CONFIG_FILE)
            cls._global_cache = instance

        # Start with a deep copy of the cached global config
        instance = copy.deepcopy(cls._global_cache)

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

            if section:
                for key in section.split("."):
                    data = data.get(key, {})

            if not data:
                return

            # Merge Logic
            if "core" in data:
                self.core = self._update_dataclass("core", self.core, data["core"])
            if "limits" in data:
                self.limits = self._update_dataclass(
                    "limits", self.limits, data["limits"]
                )
            if "daemon" in data:
                self.daemon = self._update_dataclass(
                    "daemon", self.daemon, data["daemon"]
                )
                self.daemon.apply_preset()
            if "files" in data:
                # Extract ignore list to prevent it from being overwritten during dataclass update
                new_ignores = data["files"].pop("ignore", [])
                self.files = self._update_dataclass("files", self.files, data["files"])
                if new_ignores:
                    self.files.ignore.extend(new_ignores)
                    self.files.ignore = list(dict.fromkeys(self.files.ignore))

        except tomllib.TOMLDecodeError as e:
            logger.error(f"Config syntax error in {path}: {e}")
        except Exception as e:
            logger.warning(f"Failed to load config from {path}: {e}")

    @staticmethod
    def _update_dataclass(
        section_name: str, instance: Any, updates: dict[str, Any]
    ) -> Any:
        """Updates a dataclass, warning on invalid keys and parsing human-readable formats."""
        valid_keys = instance.__dataclass_fields__.keys()
        filtered_updates = {}

        # 1. Catch and warn about typos / unknown keys
        invalid_keys = set(updates.keys()) - set(valid_keys)
        if invalid_keys:
            logger.warning(
                f"Unknown config keys in [{section_name}]: {', '.join(invalid_keys)}. Ignoring."
            )

        # 2. Process valid keys
        for k, v in updates.items():
            if k not in valid_keys:
                continue

            try:
                parser = FIELD_PARSERS.get(k)
                if parser is not None:
                    filtered_updates[k] = parser(v)
                else:
                    filtered_updates[k] = v
            except ValueError as e:
                logger.warning(
                    f"Config error in [{section_name}].{k}: {e}. Falling back to default."
                )

        return replace(instance, **filtered_updates)
