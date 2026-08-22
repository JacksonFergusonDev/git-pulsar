"""Domain types, enumerations, and type aliases for Git Pulsar.

This module provides strongly typed domain concepts (Enums for fixed sets of values,
NewType definitions for distinct identifiers and validated units, and structured
dataclasses/NamedTuples) to replace raw primitive usage across the codebase.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import NamedTuple, NewType

# --- Enums (Fixed Sets of Values) ---


class Preset(StrEnum):
    """Configuration presets for backup intensity."""

    PARANOID = "paranoid"
    AGGRESSIVE = "aggressive"
    BALANCED = "balanced"
    LAZY = "lazy"


class RepoStatus(StrEnum):
    """Repository operational status in listing and diagnostics."""

    ACTIVE = "Active"
    PAUSED = "Paused"
    MISSING = "Missing"
    ERROR = "Error"
    UNKNOWN = "Unknown"


class DaemonStatus(StrEnum):
    """Daemon process status."""

    RUNNING = "Active (Running)"
    IDLE = "Active (Idle)"
    STOPPED = "Stopped"


class SkipReason(StrEnum):
    """Reasons why a backup iteration for a repository may be skipped."""

    PATH_MISSING = "Path missing"
    PAUSED = "Paused by user"
    SYSTEM_UNDER_LOAD = "System under load"
    BATTERY_CRITICAL = "Battery critical"


class ConfigSection(StrEnum):
    """Configuration file sections."""

    CORE = "core"
    DAEMON = "daemon"
    LIMITS = "limits"
    FILES = "files"


# --- Distinct Identifiers and Units (NewTypes) ---

MachineId = NewType("MachineId", str)
"""str: Unique persistent machine/hardware identifier."""

MachineName = NewType("MachineName", str)
"""str: User-defined human-readable machine name."""

MachineSlug = NewType("MachineSlug", str)
"""str: Composite identity slug formatted as '{machine_name}--{short_id}'."""

GitRef = NewType("GitRef", str)
"""str: Fully-qualified or relative Git reference (e.g. 'refs/heads/wip/pulsar/...')."""

BranchName = NewType("BranchName", str)
"""str: Git branch name (e.g. 'main', 'feature/login')."""

GitOID = NewType("GitOID", str)
"""str: Generic Git object identifier (SHA-1 hash)."""

CommitSHA = NewType("CommitSHA", GitOID)
"""str: 40-character SHA-1 hash for a Git commit object."""

TreeSHA = NewType("TreeSHA", GitOID)
"""str: 40-character SHA-1 hash for a Git tree object."""

Seconds = NewType("Seconds", int)
"""int: Time interval measured in seconds."""

ByteSize = NewType("ByteSize", int)
"""int: Memory or file size measured in bytes."""


# --- Structured Domain Return / State Types ---


class BatteryStatus(NamedTuple):
    """Battery telemetry containing charge percentage and power connection status."""

    percent: int
    is_plugged: bool


class DiffStat(NamedTuple):
    """Summary of changes between two git references."""

    files_changed: int
    insertions: int
    deletions: int


class DriftState(NamedTuple):
    """Persisted timestamps for roaming radar drift detection."""

    last_check_ts: float
    warned_remote_ts: int


class RemoteDriftResult(NamedTuple):
    """Result of querying remote backup sessions for divergence."""

    drift_detected: bool
    newest_ts: int
    newest_machine: MachineSlug | str
    warning: str


@dataclass(frozen=True)
class BackupRefInfo:
    """Parsed representation of a Git Pulsar backup reference.

    Attributes:
        ref (GitRef): The full git ref (e.g. 'refs/heads/wip/pulsar/mac--123/main').
        slug (MachineSlug): The composite machine slug (e.g. 'mac--123').
        machine_name (MachineName): The human-readable machine name (e.g. 'mac').
        branch (BranchName): The branch name (e.g. 'main' or 'feature/login').
    """

    ref: GitRef
    slug: MachineSlug
    machine_name: MachineName
    branch: BranchName
