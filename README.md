# 🔭 Git Pulsar (v0.12.1)

[![Tests](https://github.com/jacksonfergusondev/git-pulsar/actions/workflows/ci.yml/badge.svg)](https://github.com/jacksonfergusondev/git-pulsar/actions)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Style: Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Uses Rich](https://img.shields.io/badge/uses-rich-0A0A0A?logo=<SIMPLEICONS_SLUG>&logoColor=white)](https://github.com/Textualize/rich)

**Paranoid, invisible backups for students and distributed developers.**

Git Pulsar is a background daemon that wakes up every 15 minutes to snapshot your work. Unlike standard autosave tools, Pulsar uses **Shadow Commits**—it writes directly to the git object database without touching your staging area, index, or active branch.

It ensures that even if your laptop dies (or you forget to push before leaving the library), your work is safe on the server and accessible from any other machine.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="demo/demo_dark.gif">
  <source media="(prefers-color-scheme: light)" srcset="demo/demo_light.gif">
  <img alt="Pulsar demo"
       src="demo/demo_light.gif"
       width="700"
       style="max-width:100%; height:auto;">
</picture>

## ⚡ Features

* **👻 Ghost Mode (Shadow Commits):** Backups are stored in a configured namespace (default: `refs/heads/wip/pulsar/...`). Your `git status`, `git branch`, and `git log` remain completely clean.
* **🌍 Roaming Profiles:** Hop between your laptop, desktop, and university lab computer. Pulsar tracks sessions per machine and lets you `sync` to pick up exactly where you left off.
* **🛡 Zero-Interference:**
    * Uses a temporary index so it never messes up your partial `git add`.
    * Detects if you are rebasing or merging and waits for you to finish.
    * Prevents accidental upload of large binaries (>100MB).
* **🐙 Grand Unification:** When you are done, `finalize` merges the backup history from *all* your devices into your main branch in one clean squash commit.

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
Navigate to your project. The first time you run Pulsar, it will ask for a **Machine ID** (e.g., `macbook`, `lab-pc`) to namespace your backups.

```bash
cd ~/University/Astro401
git pulsar
```
*You are now protected. The daemon will silently snapshot your work every 15 minutes.*

### 2. The "Session Handoff" (Sync)
You worked on your **Desktop** all night but forgot to push. You open your **Laptop** at class.

```bash
git pulsar sync
```
*Pulsar checks the remote, finds the newer session from `desktop`, and asks to fast-forward your working directory to match it. You just recovered your homework.*

### 3. Restore a File
Mess up a script? Grab the version from 15 minutes ago.

```bash
# Restore specific file from the latest shadow backup
git pulsar restore src/main.py
```

### 4. Finalize Your Work
When you are ready to submit or merge to `main`:

```bash
git pulsar finalize
```
*This performs an **Octopus Merge**. It pulls the backup history from your Laptop, Desktop, and Lab PC, squashes them all together, and stages the result on `main`.*

---

## 🛠 Command Reference

### Backup Management
| Command | Description |
| :--- | :--- |
| `git pulsar` | **Default.** Registers the current repo and ensures the daemon is watching it. |
| `git pulsar now` | Force an immediate backup (e.g., before closing lid). |
| `git pulsar sync` | Pull the latest session from *any* machine to your current directory. |
| `git pulsar restore <file>` | Restore a specific file from the latest backup. |
| `git pulsar diff` | See what has changed since the last backup. |
| `git pulsar finalize` | Squash-merge all backup streams into `main`. |

### Repository Control
| Command | Description |
| :--- | :--- |
| `git pulsar list` | Show all watched repositories and their status. |
| `git pulsar pause` | Temporarily suspend backups for this repo. |
| `git pulsar resume` | Resume backups. |
| `git pulsar remove` | Stop tracking this repository entirely (keeps files). |
| `git pulsar ignore <glob>` | Add a pattern to `.gitignore` (and untrack it if needed). |

### Maintenance
| Command | Description |
| :--- | :--- |
| `git pulsar status` | Show detailed daemon state (Running/Idle) and repo backup status. |
| `git pulsar doctor` | Run deep diagnostics (logs, stuck repos) and clean up the registry. |
| `git pulsar prune` | Delete old backup history (>30 days). Runs automatically weekly. |
| `git pulsar log` | View recent log history (last 1000 lines) and tail new entries. |

### Service
| Command | Description |
| :--- | :--- |
| `git pulsar install-service` | Register the background daemon (LaunchAgent/Systemd). |
| `git pulsar uninstall-service` | Remove the background daemon. |

---

## ⚙️ Configuration

You can customize behavior via `~/.config/git-pulsar/config.toml`.

```toml
[core]
remote_name = "origin"

[daemon]
# Don't backup if battery is below 20% and unplugged
eco_mode_percent = 20

[limits]
# Prevent git from choking on massive files
large_file_threshold = 104857600  # 100MB
```

## 🧩 Architecture: How it works

Pulsar separates **Data Safety** from **Git History**.

1.  **Isolation:** When the daemon wakes up, it sets `GIT_INDEX_FILE=.git/pulsar_index`. It stages your files *there*, leaving your actual staging area untouched.
2.  **Plumbing:** It uses low-level commands (`write-tree`, `commit-tree`) to create a commit object.
3.  **Namespacing:** This commit is pushed to a custom refspec:
    `refs/heads/wip/pulsar/<machine-id>/<branch-name>`
4.  **Topology:** Each backup commit has two parents: the previous backup (for history) and your current `HEAD` (for context), creating a "Zipper" graph that tracks your work alongside the project evolution.

---

## 🛑 Development

1.  **Clone & Sync:**
    ```bash
    git clone https://github.com/jacksonfergusondev/git-pulsar.git
    cd git-pulsar
    uv sync
    ```

2. **Set Up Pre-Commit Hooks**
   ```bash
   pre-commit install
   ```

3.  **Run Tests:**
    ```bash
    uv run pytest
    ```

## 📄 License

MIT © [Jackson Ferguson](https://github.com/jacksonfergusondev)
