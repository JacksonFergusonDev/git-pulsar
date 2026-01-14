# Git Pulsar 🔭

Automated, paranoid git backups for students and casual coding.

Pulsar wakes up every 15 minutes, commits your work to a `wip/pulsar` branch, and pushes it to your remote. It keeps your history safe without cluttering your main branch.

## Installation

```bash
brew tap yourname/tap
brew install git-pulsar
brew services start git-pulsar
```

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

## Recovery / Merging

When you finish an assignment:

```bash
git checkout main
git merge --squash wip/pulsar
git commit -m "Finished Assignment 1"
git push
```