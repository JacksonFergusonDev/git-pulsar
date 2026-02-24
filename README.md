# 🔭 Git Pulsar (v0.15.0)

[![Tests](https://github.com/jacksonfergusondev/git-pulsar/actions/workflows/ci.yml/badge.svg)](https://github.com/jacksonfergusondev/git-pulsar/actions)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Style: Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Uses Rich](https://img.shields.io/badge/uses-rich-0A0A0A?logo=<SIMPLEICONS_SLUG>&logoColor=white)](https://github.com/Textualize/rich)

**Fault-tolerant state capture for distributed development.**

> **Standard `git commit` conflates two distinct actions: *saving your work* (frequency: high, noise: high) and *publishing a feature* (frequency: low, signal: high).**
>
> **Git Pulsar decouples them. It is a background daemon that provides high-frequency, out-of-band state capture, ensuring your work is immutable and recoverable without polluting your project history.**

## 📡 The Mission: Decoupling Signal from Noise

In a typical workflow, developers are forced to make "WIP" commits just to switch machines or save their progress. This introduces **entropy** into the commit log, requiring complex interactive rebases to clean up later.

**Git Pulsar** treats the working directory state as a continuous stream of data. It captures this "noise" in a dedicated namespace (`refs/heads/wip/...`), keeping your primary branch purely focused on "signal" (logical units of work).

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="demo/demo_dark.gif">
  <source media="(prefers-color-scheme: light)" srcset="demo/demo_light.gif">
  <img alt="Pulsar demo"
       src="demo/demo_light.gif"
       width="700"
       style="max-width:100%; height:auto;">
</picture>

---

## ⚙️ Engineering Philosophy: Non-Blocking Determinism

This system is designed to operate safely alongside standard Git commands without race conditions or index locking.

### 1. Out-of-Band Indexing (The "Shadow" Index)

Most autosave tools aggressively run `git add .`, which destroys the user's carefully staged partial commits.

- **The Invariant:** The user's `.git/index` must never be touched by the daemon.
- **The Implementation:** Pulsar sets the `GIT_INDEX_FILE` environment variable to a temporary location (`.git/pulsar_index`). It constructs the tree object using low-level plumbing commands (`git write-tree`), bypassing the porcelain entirely. This ensures **Zero-Interference** with your active workflow.

### 2. Distributed State Reconciliation (The "Zipper" Graph)

In a distributed environment (Laptop ↔ Desktop), state drift is inevitable.

- **The Mechanism:** Pulsar maintains a separate refspec for each machine ID.
- **The Topology:** When you run `git pulsar finalize`, the engine performs an **Octopus Merge**, traversing the DAG (Directed Acyclic Graph) of all machine streams and squashing them into a single, clean commit on `main`.

### 3. Fault Tolerance

- **The Problem:** Laptops die. SSH connections drop.
- **The Solution:** By decoupling commits from pushes, Pulsar can capture local state every few minutes while conserving battery by pushing to the remote at a lower frequency (e.g., hourly). This guarantees that the **Mean Time To Recovery (MTTR)** is minimized regardless of network availability or hardware failure.

---

## ⚡ Features

- **Decoupled Cycles:** Independent intervals for local commits and remote pushes. Save your battery while staying protected.
- **Smart Identity:** Automatically detects naming collisions with other devices on the remote, ensuring unique backup streams for every machine.
- **Roaming Radar:** The background daemon actively polls for topological drift, firing a cross-platform OS notification if another machine leapfrogs your local session so you can `sync` before conflicts arise.
- **Out-of-Band Indexing:** Backups are stored in a configured namespace (default: `refs/heads/wip/pulsar/...`). Your `git status`, `git branch`, and `git log` remain completely clean.
- **Distributed Sessions:** Hop between machines. Pulsar tracks sessions per device and lets you `sync` to pick up exactly where you left off.
- **State-Aware Diagnostics:** The `doctor` command correlates transient log events with active system health to prevent alert fatigue, proactively scans for pipeline blockers, and offers an interactive queue to safely auto-fix common issues.
- **Active Observability:** The `status` dashboard provides zero-latency power telemetry (e.g., Eco-Mode throttling) and immediately surfaces cached warnings for remote session drift and oversized files.
- **Zero-Interference:**
  - Uses a temporary index so it never messes up your partial `git add`.
  - Detects if you are rebasing or merging and waits for you to finish.
  - Prevents accidental upload of large binaries (configurable threshold).
- **Cascading Config:** Settings are merged from global defaults, `~/.config/git-pulsar/config.toml`, and local `pulsar.toml` or `pyproject.toml` files.

---

## 📦 Installation

### macOS

Install via Homebrew. This automatically manages the background service.

```bash
brew tap jacksonfergusondev/tap
brew install git-pulsar
brew services start git-pulsar
```

### Linux / Generic

Install via `uv` (or `pipx`) and use the built-in service manager to register the systemd timer.

```bash
uv tool install git-pulsar
# This generates and enables a systemd user timer
git pulsar install-service --interval 300
```

---

## 🚀 The Pulsar Workflow

Pulsar is designed to feel like a native git command.

### 1. Initialize & Identify

Navigate to your project. The first time you run Pulsar, it will register the repo, **check for naming collisions**, and start the background protection loop.

```bash
cd ~/University/Astro401
git pulsar
```

*The daemon will now silently snapshot your work based on your configured intervals.*

### 2. Configure Your Intensity

Need high-frequency protection for a critical project? Set a preset or fine-tune the intervals in your project root.

#### pulsar.toml

```toml
[daemon]
preset = "paranoid"  # 5min commits, 5min pushes
```

### 3. The "Session Handoff" (Sync)

You worked on your **Desktop** all night but forgot to push manually. You open your **Laptop** at class.

```bash
git pulsar sync
```

*Pulsar checks the remote, finds the newer session from `desktop`, and fast-forwards your working directory to match it.*

### 4. Restore a File

Mess up a script? Grab the version from your last shadow commit.

```bash
# Restore specific file from the latest shadow backup
git pulsar restore src/main.py
```

### 5. Finalize Your Work

When you are ready to submit or merge to `main`:

```bash
git pulsar finalize
```

*This performs an **Octopus Merge**. It pulls the backup history from your Laptop, Desktop, and Lab PC, squashes them all together, and stages the result on `main`.*

---

## 🧬 Environment Bootstrap (macOS)

Pulsar includes a one-click scaffolding tool to set up a modern, robust Python environment.

```bash
git pulsar --env
```

This bootstraps the current directory with:

- **uv:** Initializes a project with fast package management and Python 3.12+ pinning.

- **direnv:** Creates an .envrc for auto-activating virtual environments and hooking into the shell.

- **VS Code:** Generates a .vscode/settings.json pre-configured to exclude build artifacts and use the local venv.

---

## 🛠 Command Reference

### Backup Management

| Command | Description |
| :--- | :--- |
| `git pulsar` | **Default.** Registers the current repo and ensures the daemon is watching it. |
| `git pulsar now` | Force an immediate backup cycle (commit + push). |
| `git pulsar sync` | Pull the latest session from *any* machine to your current directory. |
| `git pulsar restore <file>` | Restore a specific file from the latest backup. |
| `git pulsar diff` | See what has changed since the last backup. |
| `git pulsar finalize` | Squash-merge all backup streams into `main` (includes pre-flight checklist). |

### Repository Control

| Command | Description |
| :--- | :--- |
| `git pulsar status` | Show real-time daemon telemetry, active health blockers, and repository status. |
| `git pulsar config` | Open the global configuration file in your default editor. |
| `git pulsar list` | Show all watched repositories and their status. |
| `git pulsar pause` | Temporarily suspend backups for this repo. |
| `git pulsar resume` | Resume backups. |
| `git pulsar remove` | Stop tracking this repository entirely (keeps files). |
| `git pulsar ignore <glob>` | Add a pattern to `.gitignore` (and untrack it if needed). |

### Maintenance

| Command | Description |
| :--- | :--- |
| `git pulsar doctor` | Run state-aware diagnostics and interactively auto-fix issues (logs, repo health, drift detection, hook interference). |
| `git pulsar prune` | Delete old backup history (>30 days). Runs automatically weekly. |
| `git pulsar log` | View recent log history (last 1000 lines) and tail new entries. |

### Service

| Command | Description |
| :--- | :--- |
| `git pulsar install-service` | Register the background daemon (LaunchAgent/Systemd). |
| `git pulsar uninstall-service` | Remove the background daemon. |

---

## ⚙️ Configuration

Settings cascade from Global → Local. Local list options (like `ignore`) append to global ones.

### Options

| Section | Key | Default | Description |
| :--- | :--- | :--- | :--- |
| `daemon` | `preset` | `None` | Use `paranoid`, `aggressive`, `balanced`, or `lazy`. |
| `daemon` | `commit_interval` | `600` | Seconds between local state captures. |
| `daemon` | `push_interval` | `3600` | Seconds between remote pushes. |
| `limits` | `large_file_threshold` | `100MB` | Max file size before aborting a backup. |

### Example `~/.config/git-pulsar/config.toml`

```toml
[daemon]
preset = "balanced"
eco_mode_percent = 25  # Throttles pushes if battery is low

[files]
ignore = ["*.tmp", "node_modules/"]
```

---

## 🗺 Roadmap

### Phase 1: The "Co-Pilot" Update (High Interactivity)

*Focus: Turning the tool from a blind script into a helpful partner that negotiates with you.*

- [x] **Smart Restore:** Replace hard failures on "dirty" files with a negotiation menu (Overwrite / View Diff / Cancel).
- [x] **Pre-Flight Checklists:** Display a summary table of incoming changes (machines, timestamps, file counts) before running destructive commands like `finalize`.
- [x] **Active Doctor:** Upgrade `git pulsar doctor` to not just diagnose issues (like stopped daemons), but offer to auto-fix them interactively.

### Phase 2: "Deep Thought" (Context & Intelligence)

*Focus: Leveraging data to make the tool feel alive and aware of your workflow.*

- [ ] **Semantic Shadow Logs:** Replace generic "Shadow backup" messages with auto-generated summaries (e.g., `backup: modified daemon.py (+15 lines)`).
- [x] **Roaming Radar:** Proactively detect if a different machine has pushed newer work to the same branch and notify the user to `sync`.
- [ ] **Decaying Retention:** Implement "Grandfather-Father-Son" pruning (keep all hourly backups for 24h, then daily summaries) to balance safety with disk space.

### Phase 3: The "TUI" Experience (Visuals)

*Focus: Making the invisible backup history tangible and explorable.*

- [ ] **Time Machine UI:** A terminal-based visual browser for `git pulsar restore` that lets you scroll through file history and view side-by-side diffs.
- [ ] **Universal Bootstrap:** Expand `git pulsar --env` to support Linux (apt/dnf) environments alongside macOS.

### Future Horizons

- [ ] **End-to-End Encryption:** Optional GPG encryption for shadow commits.
- [ ] **Windows Support:** Native support for PowerShell and Task Scheduler.

---

## 🤝 Contributing

We welcome contributions! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for details on how to set up the development environment, run tests, and submit pull requests.

## 📄 License

MIT © [Jackson Ferguson](https://github.com/jacksonfergusondev)
