# 🏗️ Architecture: The Daemon Core

The `src/` directory contains the package source code. The architecture strictly separates **OS-Level Mechanics** (Service management, Identity) from **Git Plumbing** (Object manipulation).

## Module Map

### 1. The Core Loop (State Management)

- **`git_pulsar/daemon.py`**: The background process.
  - **Role:** The "Heartbeat." It wakes up, checks system constraints (Battery, CPU Load), and triggers the backup logic.
  - **Logic:** Decouples "Saving" (Commit) from "Publishing" (Push) using independent intervals to optimize for battery life. Incorporates the "Roaming Radar" to poll for remote drift asynchronously.
  - **Safety:** Implements `GIT_INDEX_FILE` isolation to ensure it never locks or corrupts the user's active git index.
- **`git_pulsar/ops.py`**: High-level Business Logic.
  - **Role:** The "Controller." It orchestrates complex multi-step operations like `finalize` (Octopus Merges), `restore`, and drift detection.
  - **Logic:** Calculates the "Zipper Graph" topology to merge shadow commits back into the main branch, manages atomic file I/O for cross-process state tracking, and evaluates pipeline blockers (e.g., oversized files).
- **`git_pulsar/config.py`**: Configuration Engine.
  - **Role:** The "Source of Truth."
  - **Logic:** Implements a cascading hierarchy (Defaults → Global → Local) to merge settings from `~/.config/git-pulsar/config.toml` and project-level `pulsar.toml` or `pyproject.toml`.

### 2. The Abstraction Layer (Plumbing)

- **`git_pulsar/git_wrapper.py`**: The Git Interface.
  - **Role:** A strict wrapper around `subprocess`.
  - **Philosophy:** **No Porcelain.** This module primarily uses git *plumbing* commands (`write-tree`, `commit-tree`, `update-ref`) rather than user-facing commands (`commit`, `add`) to ensure deterministic behavior.
- **`git_pulsar/system.py`**: OS Abstraction.
  - **Role:** Identity & Environment.
  - **Logic:** Handles the chaos of cross-platform identity (mapping `IOPlatformUUID` on macOS vs `/etc/machine-id` on Linux) to ensure stable "Roaming Profiles."

### 3. Service Management (Lifecycle)

- **`git_pulsar/service.py`**: The Installation Engine.
  - **Role:** Interface with the host init system.
  - **Logic:** Generates and registers `systemd` user timers (Linux) or instructions for `launchd` (macOS/Homebrew).

### 4. The Interface

- **`git_pulsar/cli.py`**: The User Entry Point & Diagnostic Engine.
  - **Role:** Argument parsing, UI rendering, real-time observability, system health evaluation, and interactive issue resolution.
  - **Logic:** Uses `rich` for terminal visualization. Beyond routing subcommands to `ops.py` and `daemon.py`, it presents the `doctor` diagnostics and the zero-latency `status` dashboard (surfacing power telemetry, dynamic health constraints, and cached drift warnings). It correlates repository state against transient event logs and executes a two-stage diagnostic pipeline: scanning for host-environment pipeline blockers (e.g., strict git hooks, missing `systemd` linger), followed by an interactive resolution queue that prompts users to safely auto-fix specific issues (like stale index locks or ghost registry entries) or provides precise terminal commands for manual interventions.

---

## Key Invariants

1. **Index Isolation:** The `daemon` module MUST ALWAYS set `os.environ["GIT_INDEX_FILE"]` to a temporary path before performing write operations.
2. **Zero-Destruction:** The `prune` logic in `ops.py` relies on strictly namespaced refspecs (`refs/heads/wip/pulsar/...`) and never touches standard heads.
3. **Identity Stability:** The `system` module guarantees that a Machine ID persists across reboots, preventing "Split Brain" backup histories.
4. **Configuration Precedence:** Local project configuration MUST always override global user settings to ensure repo-specific constraints (e.g., large file limits) are respected.
5. **State Over Events (Zero-Latency):** The diagnostic engine (`cli.py`) MUST prioritize current repository state and local caches (e.g., `.git/pulsar_drift_state`) over historical log events or live network calls, ensuring the CLI never blocks the user's terminal while evaluating system health.
6. **Interactive Safety:** The diagnostic engine's interactive resolution queue MUST explicitly prompt the user for confirmation before executing any state-altering auto-fixes (e.g., deleting locks or modifying the registry).
