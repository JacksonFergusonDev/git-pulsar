# 🏗️ Architecture: The Daemon Core

The `src/` directory contains the package source code. The architecture strictly separates **OS-Level Mechanics** (Service management, Identity) from **Git Plumbing** (Object manipulation).

## Module Map

### 1. The Core Loop (State Management)
* **`git_pulsar/daemon.py`**: The background process.
    * **Role:** The "Heartbeat." It wakes up, checks system constraints (Battery, CPU Load), and triggers the backup logic.
    * **Logic:** Decouples "Saving" (Commit) from "Publishing" (Push) using independent intervals to optimize for battery life.
    * **Safety:** Implements `GIT_INDEX_FILE` isolation to ensure it never locks or corrupts the user's active git index.
* **`git_pulsar/ops.py`**: High-level Business Logic.
    * **Role:** The "Controller." It orchestrates complex multi-step operations like `finalize` (Octopus Merges) and `restore`.
    * **Logic:** Calculates the "Zipper Graph" topology to merge shadow commits back into the main branch.
* **`git_pulsar/config.py`**: Configuration Engine.
    * **Role:** The "Source of Truth."
    * **Logic:** Implements a cascading hierarchy (Defaults → Global → Local) to merge settings from `~/.config/git-pulsar/config.toml` and project-level `pulsar.toml` or `pyproject.toml`.

### 2. The Abstraction Layer (Plumbing)
* **`git_pulsar/git_wrapper.py`**: The Git Interface.
    * **Role:** A strict wrapper around `subprocess`.
    * **Philosophy:** **No Porcelain.** This module primarily uses git *plumbing* commands (`write-tree`, `commit-tree`, `update-ref`) rather than user-facing commands (`commit`, `add`) to ensure deterministic behavior.
* **`git_pulsar/system.py`**: OS Abstraction.
    * **Role:** Identity & Environment.
    * **Logic:** Handles the chaos of cross-platform identity (mapping `IOPlatformUUID` on macOS vs `/etc/machine-id` on Linux) to ensure stable "Roaming Profiles."

### 3. Service Management (Lifecycle)
* **`git_pulsar/service.py`**: The Installation Engine.
    * **Role:** Interface with the host init system.
    * **Logic:** Generates and registers `systemd` user timers (Linux) or instructions for `launchd` (macOS/Homebrew).

### 4. The Interface
* **`git_pulsar/cli.py`**: The User Entry Point.
    * **Role:** Argument parsing and UI rendering.
    * **Tech:** Uses `rich` for terminal visualization. It delegates all logic to `ops.py` or `daemon.py`.

---

## Key Invariants

1.  **Index Isolation:** The `daemon` module MUST ALWAYS set `os.environ["GIT_INDEX_FILE"]` to a temporary path before performing write operations.
2.  **Zero-Destruction:** The `prune` logic in `ops.py` relies on strictly namespaced refspecs (`refs/heads/wip/pulsar/...`) and never touches standard heads.
3.  **Identity Stability:** The `system` module guarantees that a Machine ID persists across reboots, preventing "Split Brain" backup histories.
4.  **Configuration Precedence:** Local project configuration MUST always override global user settings to ensure repo-specific constraints (e.g., large file limits) are respected.
