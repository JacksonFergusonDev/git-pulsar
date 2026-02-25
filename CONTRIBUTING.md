# Contributing to Git Pulsar

First off, thanks for taking the time to contribute! 🎉

## How to Contribute

### Reporting Bugs

1. Check if the issue has already been reported.
2. Open a new issue with a clear title and description.
3. Include relevant logs (`git-pulsar log`) or reproduction steps.

### Development Setup

This project uses [uv](https://github.com/astral-sh/uv) for dependency management and Python 3.12+.

#### 1. Fork & Clone

   Fork the repo and clone it locally:

   ```bash
   git clone https://github.com/jacksonfergusondev/git-pulsar.git
   cd git-pulsar
   ```

#### 2. Environment Setup

   We use a `Makefile` to orchestrate dev workflows. Install the environment and dependencies:

   ```bash
   make install
   ```

   *Optional: If you use `direnv`, allow the automatically generated configuration:*

   ```bash
   direnv allow
   ```

#### 3. Install Hooks

   Set up pre-commit hooks to handle linting (Ruff) and type checking (Mypy) automatically.

   ```bash
   pre-commit install
   ```

### Running Tests

We utilize a multi-tiered testing architecture to validate behavior safely.

#### Tier 1: Unit Tests

Standard unit testing for core logic.

```bash
make test-unit
```

#### Tier 2: Distributed Sandbox

Tests distributed system logic (syncing, drift detection, shadow commits) locally by simulating two isolated machines interacting with a bare remote.

```bash
make test-dist
```

*Tip: Run `make test` to execute both Tier 1 and Tier 2 automatically.*

#### Tier 3: Field Testing (Linux VM)

If you are modifying OS-level daemon logic, battery polling, or doing highly destructive testing, spin up a fully isolated Ubuntu VM. Requires [Multipass](https://multipass.run/).

```bash
make test-cluster
```

This provisions a VM, mounts your local source code as read-only, and drops you into a safe `~/playground` repository. Your local Mac repository remains 100% untouched. If you edit code on your Mac, simply run `reload-pulsar` inside the VM to instantly fetch your latest changes.

### Pull Requests

#### 1. Create a Branch

   ```bash
   git checkout -b feature/my-amazing-feature
   ```

#### 2. Make Changes

   Write code and add tests for your changes.

#### 3. Verify

   Ensure your code passes the linters and automated test tiers locally.

   ```bash
   make lint
   make test
   ```

#### 4. Commit & Push

   Please use clear commit messages.

   ```bash
   git commit -m "feat: add support for solar flares"
   git push origin feature/my-amazing-feature
   ```

#### 5. Open a Pull Request

   Submit your PR against the `main` branch.
