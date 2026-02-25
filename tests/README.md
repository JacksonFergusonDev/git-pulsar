# 🛡️ Verification Strategy: Engineering Safety

Because Git Pulsar operates on the user's active working directory, our testing philosophy prioritizes **Non-Interference** and **Data Integrity** above all else. We use a multi-layered verification strategy to ensure the daemon never corrupts the staging area or the commit history.

## The Three-Tiered Architecture

### Tier 1: Unit & Integration (The `tests/` directory)

*Scope: Python-level logic, configuration parsing, CLI routing, and mocked git wrapper behaviors.*

#### 1. Property-Based Fuzzing (`test_properties.py`)

Standard unit tests often miss edge cases in file handling. We use [Hypothesis](https://hypothesis.readthedocs.io/) to "fuzz" our critical registry logic.

- **The Invariant:** The registry pruning algorithm must *never* delete a path that wasn't explicitly targeted, regardless of whitespace, encoding, or list size.
- **The Mechanism:** Hypothesis generates thousands of semi-random file paths and registry states to attempt to break the `prune_registry` function.

#### 2. Plumbing & Isolation Verification (`test_daemon.py`)

This suite verifies the **Zero-Interference** architecture and **Decoupled Cycles**.

- **Mocking the Environment:** We strictly enforce that the daemon cannot run unless `GIT_INDEX_FILE` is set to a temporary path.
- **Plumbing Assertions:** We spy on the `subprocess` calls to ensure that *only* low-level plumbing commands (`git write-tree`, `git commit-tree`) are used. This proves that the user's high-level state (`git status`) remains untouched.
- **Cycle Independence:** Verifies that local commits and remote pushes occur on independent intervals, ensuring high-frequency snapshots without battery-draining network calls.
- **Roaming Radar:** Tests the background event loop's network polling throttle (15-minute intervals) and verifies that cross-platform OS interrupts (`SYSTEM.notify`) fire correctly when unacknowledged remote drift is detected.

#### 3. Platform Identity Matrix (`test_system.py`)

Pulsar relies on stable machine identity to manage distributed sessions.

- **The Problem:** macOS uses `IOPlatformUUID`, Linux uses `/etc/machine-id`, and fallback behavior is flaky.
- **The Solution:** We mock low-level system calls (`ioreg`, file reads) to simulate specific OS environments, ensuring that a "Session Handoff" works correctly regardless of the OS topology.

#### 4. Topology Logic (`test_ops.py`)

Verifies the "State Reconciliation" engine and primitive operations.

- **Octopus Merges:** Simulates complex multi-head merge scenarios (e.g., merging 3 different machine streams into `main`) to ensure the DAG (Directed Acyclic Graph) is constructed correctly without conflicts.
- **Pre-Flight Checklist Negotiation:** Verifies the interactive dry-run table before finalizing, ensuring that declined merges cleanly abort without mutating the active working branch.
- **State Management:** Verifies atomic file I/O operations (`set_drift_state`) to ensure cross-process thread safety between the background daemon and foreground CLI.
- **Drift Detection:** Tests the core logic for identifying when remote sessions leapfrog local ones, simulating various network failures and detached HEAD states.
- **Pipeline Blockers:** Validates decoupled checks for oversized files (`has_large_files`), ensuring they safely abort operations and trigger system notifications without polluting the daemon's event loop.
- **Interactive State Machines:** Validates the `Prompt.ask` control loop during dirty file restorations, ensuring branching paths (Overwrite, View Diff, Cancel) execute the correct `GitRepo` methods and exit gracefully.

#### 5. Configuration Hierarchy (`test_config.py`)

Ensures the **Cascading Configuration** system behaves deterministically.

- **Priority Resolution:** Verifies that Local config (`pulsar.toml`) overrides Global config (`config.toml`), and list values (like `ignore`) are appended rather than replaced.
- **Preset Logic:** Tests that abstract presets (e.g., `paranoid`, `lazy`) correctly expand into concrete integer intervals for the daemon.

#### 6. Diagnostics & CLI Interaction (`test_cli.py`)

Validates the state-aware diagnostic engine and user-facing CLI commands.

- **Dashboard Observability:** Validates the `status` command's rendering of power telemetry (Eco-Mode vs. Critical), dynamic health thresholds, and zero-latency caching for drift/blocker warnings.
- **Interactive Resolution Queue:** Tests the `doctor` command's two-stage pipeline, ensuring execution loops correctly apply confirmed auto-fixes (e.g., stale index lock removal, ghost registry cleanup) and safely bypass declined ones.
- **State vs. Event Correlation:** Tests the `doctor` command by decoupling repository health (state) from daemon logs (events). We mock dynamic lookback windows to verify that naturally resolved transient anomalies are suppressed, while active correlated failures trigger alerts.
- **Environment Simulation & Guidance:** Uses `tmp_path` and `mocker` to synthesize restrictive `.git/hooks`, offline networks, and Linux `systemd` configurations (`loginctl`) without executing side effects on the host, verifying exact stdout formatting for manual interventions.
- **UI Determinism:** Ensures commands like `status` and `config` parse timestamps and route to standard system editors (`$EDITOR`, `nano`) correctly.

#### 7. Git Abstraction Layer (`test_git_wrapper.py`)

Ensures the Python-to-Git subprocess boundary remains secure and predictable.

- **Command Construction:** Verifies that dynamic arguments—such as file-level diff targeting—correctly append necessary boundary markers (`--`) to prevent Git from misinterpreting file paths as revision hashes.
- **Regex Parsing Determinism:** Validates the extraction of insertions, deletions, and changed files from variable `git diff --shortstat` output, ensuring the data pipeline doesn't break when Git omits empty clauses.
- **Error Handling:** Ensures low-level subprocess failures are caught and logged rather than causing silent upstream crashes.

---

### Tier 2: Distributed Sandbox (`scripts/test_distributed.sh`)

*Scope: Distributed system mechanics (session handoffs, drift detection, octopus merges).*

- **The Mechanism:** This bash script bypasses VMs entirely. It uses the `XDG_STATE_HOME` environment variable to simulate multiple isolated machines interacting with a local bare remote. It includes strict `trap` cleanup and parses the daemon logs to catch swallowed exceptions during shadow commits.

---

### Tier 3: Field Operations (`scripts/spawn_cluster.sh`)

*Scope: OS-level integrations (`systemd`, battery polling via `sysfs`, network timeouts) and destructive "Chaos Engineering."*

- **The Mechanism:** Fully automates the provisioning of an Ubuntu VM using Multipass. It safely mounts the local source code as read-only and generates an isolated `~/playground` repository for risk-free destructive testing on a live Linux filesystem.

---

## Running Tests

The test suite is centrally orchestrated via the `Makefile`.

**Run Tier 1 (Unit & Integration):**

```bash
make test-unit
```

**Run Tier 2 (Distributed Sandbox):**

```bash
make test-dist
```

**Run Tier 1 & Tier 2 sequentially (Default):**

```bash
make test
```

**Run the full CI Pipeline locally (Lint, Typecheck, Test):**

```bash
make ci
```

**Provision the Tier 3 VM Cluster:**

```bash
make test-cluster
```
