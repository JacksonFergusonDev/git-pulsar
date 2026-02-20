# Contributing to Git Pulsar

First off, thanks for taking the time to contribute! 🎉

## How to Contribute

### Reporting Bugs

1. Check if the issue has already been reported.
2. Open a new issue with a clear title and description.
3. Include relevant logs (`git-pulsar log`) or reproduction steps.

### Development Setup

This project uses [uv](https://github.com/astral-sh/uv) for dependency management and Python 3.12+.

1. **Fork & Clone**
   Fork the repo and clone it locally:

   ```bash
   git clone https://github.com/jacksonfergusondev/git-pulsar.git
   cd git-pulsar
   ```

2. **Environment Setup**
   We use `uv` to manage the virtual environment and dependencies.

   ```bash
   # Creates .venv and installs dependencies (including dev groups)
   uv sync
   ```

   *Optional: If you use `direnv`, allow the automatically generated configuration:*

   ```bash
   direnv allow
   ```

3. **Install Hooks**
   Set up pre-commit hooks to handle linting (Ruff) and type checking (Mypy) automatically.

   ```bash
   pre-commit install
   ```

### Running Tests

We use `pytest` for the test suite.

```bash
uv run pytest
```

### Pull Requests

1. **Create a Branch**

   ```bash
   git checkout -b feature/my-amazing-feature
   ```

2. **Make Changes**
   Write code and add tests for your changes.

3. **Verify**
   Ensure your code passes the linter and tests locally.

   ```bash
   uv run pytest
   ```

   (Pre-commit will also run `ruff` and `mypy` when you commit).

4. **Commit & Push**
   Please use clear commit messages.

   ```bash
   git commit -m "feat: add support for solar flares"
   git push origin feature/my-amazing-feature
   ```

5. **Open a Pull Request**
   Submit your PR against the `main` branch.
