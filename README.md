# 🔭 Git Pulsar (v0.6.0)

[![Tests](https://github.com/jacksonfergusondev/git-pulsar/actions/workflows/ci.yml/badge.svg)](https://github.com/jacksonfergusondev/git-pulsar/actions)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit)](https://github.com/pre-commit/pre-commit)

**Automated, paranoid git backups for students and casual coding.**

Git Pulsar is a background daemon that wakes up every 15 minutes, commits your work to a secluded `wip/pulsar` branch, and pushes it to your remote. It ensures that even if your laptop dies, your uncommitted work is safe on the server—without polluting your main history.

---

## ⚡ Features

- **Set & Forget:** Runs silently in the background via `launchd` (macOS) or `systemd` (Linux).
- **Non-Intrusive:** Commits are pushed to a separate branch (`wip/pulsar`). Your `main` branch stays clean.
- **Smart Checks:**
  - Detects if the repo is busy (merging/rebasing) and yields.
  - Prevents accidental upload of large files (>100MB).
  - Auto-generates `.gitignore` for Python/LaTeX projects if missing.
- **System Integration:** Native desktop notifications on success or failure.

## 📦 Installation

### macOS (Recommended)
Install via Homebrew to handle the daemon registration automatically.

```bash
brew tap jacksonfergusondev/tap
brew install git-pulsar
brew services start git-pulsar
```

### Linux / Generic
Install via `uv` (or `pipx`) to isolate the environment, then register the systemd service.

```bash
uv tool install git-pulsar
git-pulsar install-service --interval 300  # Optional: Run every 5 mins (default: 900s)
```

## 🚀 Usage

### 1. Activate a Repository
Navigate to any project you want to back up and initialize Pulsar.

```bash
cd ~/University/Astro401
git-pulsar
```
*This registers the path in the local registry (`~/.git_pulsar_registry`) and ensures the `wip/pulsar` branch exists.*

### 2. Work Normally
You do not need to do anything else. Pulsar wakes up every 15 minutes to:
1. Check for changes.
2. Commit them to `wip/pulsar`.
3. Push to `origin`.

### 3. Manual Backup
If you want to force a backup immediately (e.g., right before closing your laptop), run:

```bash
git-pulsar now
```

### 4. Restore a File
If you messed up a file and need to grab the latest version from the backup:

```bash
git-pulsar restore src/main.py
```
*This pulls the file from `wip/pulsar` into your working directory. It will warn you if you have uncommitted changes.*

### 5. Finalize Your Work
When you are ready to submit or merge your work back to main:

```bash
git-pulsar finalize
```

*This automates the squashing process: it switches to `main`, merges your backup history into a single commit, and resets the backup branch so you are ready for the next assignment.*

## ⚙️ Configuration

You can customize global behavior by creating `~/.config/git-pulsar/config.toml`.

**Default Configuration:**
```toml
[core]
backup_branch = "wip/pulsar"
remote_name = "origin"

[limits]
# 5MB log rotation
max_log_size = 5242880
# Skip files larger than 100MB
large_file_threshold = 104857600
```

## 🛑 Stopping the Service

To deregister the background daemon and stop all backups:

```bash
git-pulsar uninstall-service
```

## 🔧 Requirements

- **Python 3.12+**
- **Headless Auth:** Your git authentication must be non-interactive (SSH keys or a cached Credential Helper). Pulsar runs in the background and cannot prompt for passwords.

## 🛠️ Development

This project uses modern Python tooling.

1. **Clone and Install Dependencies:**
   ```bash
   git clone https://github.com/jacksonferguson/git-pulsar.git
   cd git-pulsar
   uv sync
   ```

2. **Setup Pre-commit Hooks:**
   ```bash
   pre-commit install
   ```

3. **Run Tests:**
   We use `pytest` for testing and `hypothesis` for property-based testing.
   ```bash
   uv run pytest
   ```

## 📂 Project Structure

```text
.
├── LICENSE
├── pyproject.toml
├── README.md
├── uv.lock
├── src
│   ├── __init__.py
│   ├── cli.py         # Entry point & repo setup logic
│   ├── daemon.py      # Core backup loop & git operations
│   └── service.py     # Background service installer (launchd/systemd)
└── tests
    ├── test_cli.py
    ├── test_daemon.py
    └── test_properties.py
```

## 📄 License

This project is licensed under the [MIT License](LICENSE).
