"""Git Pulsar: Automated background synchronization for git repositories.

This package provides the command-line interface, background daemon, and core
operational logic for creating high-frequency, non-intrusive "shadow backups"
of local git repositories.
"""

from . import (
    cli,
    config,
    constants,
    daemon,
    git_wrapper,
    ops,
    service,
    system,
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
]
