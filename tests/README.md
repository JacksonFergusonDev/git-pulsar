# 🛡️ Verification Strategy: Engineering Safety

Because Git Pulsar operates on the user's active working directory, our testing philosophy prioritizes **Non-Interference** and **Data Integrity** above all else. We use a multi-layered verification strategy to ensure the daemon never corrupts the staging area or the commit history.

## Testing Layers

### 1. Property-Based Fuzzing (`test_properties.py`)
Standard unit tests often miss edge cases in file handling. We use [Hypothesis](https://hypothesis.readthedocs.io/) to "fuzz" our critical registry logic.
* **The Invariant:** The registry pruning algorithm must *never* delete a path that wasn't explicitly targeted, regardless of whitespace, encoding, or list size.
* **The Mechanism:** Hypothesis generates thousands of semi-random file paths and registry states to attempt to break the `prune_registry` function.

### 2. Plumbing & Isolation Verification (`test_daemon.py`)
This suite verifies the **Zero-Interference** architecture and **Decoupled Cycles**.
* **Mocking the Environment:** We strictly enforce that the daemon cannot run unless `GIT_INDEX_FILE` is set to a temporary path.
* **Plumbing Assertions:** We spy on the `subprocess` calls to ensure that *only* low-level plumbing commands (`git write-tree`, `git commit-tree`) are used. This proves that the user's high-level state (`git status`) remains untouched.
* **Cycle Independence:** Verifies that local commits and remote pushes occur on independent intervals, ensuring high-frequency snapshots without battery-draining network calls.

### 3. Platform Identity Matrix (`test_system.py`)
Pulsar relies on stable machine identity to manage distributed sessions.
* **The Problem:** macOS uses `IOPlatformUUID`, Linux uses `/etc/machine-id`, and fallback behavior is flaky.
* **The Solution:** We mock low-level system calls (`ioreg`, file reads) to simulate specific OS environments, ensuring that a "Session Handoff" works correctly regardless of the OS topology.

### 4. Topology Logic (`test_ops.py`)
Verifies the "State Reconciliation" engine.
* **Octopus Merges:** Simulates complex multi-head merge scenarios (e.g., merging 3 different machine streams into `main`) to ensure the DAG (Directed Acyclic Graph) is constructed correctly without conflicts.

### 5. Configuration Hierarchy (`test_config.py`)
Ensures the **Cascading Configuration** system behaves deterministically.
* **Priority Resolution:** Verifies that Local config (`pulsar.toml`) overrides Global config (`config.toml`), and list values (like `ignore`) are appended rather than replaced.
* **Preset Logic:** Tests that abstract presets (e.g., `paranoid`, `lazy`) correctly expand into concrete integer intervals for the daemon.

---

## Running Tests

**Run the full suite:**
```bash
uv run pytest
```

**Run only the Fuzzing engine:**
```bash
uv run pytest tests/test_properties.py
```
