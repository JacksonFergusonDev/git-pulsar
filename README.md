# Git Pulsar 🔭

Automated, paranoid git backups for students and casual coding.

Pulsar wakes up every 15 minutes, commits your work to a `wip/pulsar` branch, and pushes it to your remote.

## Installation

### macOS (Recommended)
Use Homebrew to install the daemon and register the background service automatically:

```bash
brew tap jacksonfergusondev/tap
brew install git-pulsar
brew services start git-pulsar
```

### Linux / Non-Homebrew
Use `pipx` to install, then register the service manually:

```bash
pipx install git-pulsar
git-pulsar install-service
```
*Note: This creates a Systemd timer (Linux) or LaunchAgent (macOS) to run the daemon.*

## Usage

1. Go to any folder you want to back up:

```bash
cd ~/University/Astro401
```

2. Activate Pulsar:

```bash
git-pulsar
```

3. Work as normal. Pulsar handles the rest.

To stop the background service:
```bash
git-pulsar uninstall-service
```

## Requirements
* **SSH Keys:** Your git authentication must be "headless" (SSH keys or Credential Helper). Pulsar cannot type your password for you in the background.