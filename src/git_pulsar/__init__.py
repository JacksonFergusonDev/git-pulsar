"""Git Pulsar: Automated background synchronization for git repositories.

This package provides the command-line interface, background daemon, and core
operational logic for creating high-frequency, non-intrusive "shadow backups"
of local git repositories.
"""

import contextlib
import importlib.metadata

with contextlib.suppress(importlib.metadata.PackageNotFoundError):
    __version__ = importlib.metadata.version("git-pulsar")

from . import (
    cli,
    config,
    constants,
    daemon,
    git_wrapper,
    ops,
    service,
    system,
    types,
)

__all__ = [
    "cli",
    "config",
    "constants",
    "daemon",
    "git_wrapper",
    "ops",
    "service",
    "system",
    "types",
]
