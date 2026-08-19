set shell := ["bash", "-euc", "-o", "pipefail"]
set quiet

# --- ANSI Colors ---
blue := '\033[1;34m'
green := '\033[1;32m'
yellow := '\033[1;33m'
nc := '\033[0m'

# Show available commands
default:
    @just --list

# Show this help menu
help:
    @just --list

# Sync/install dependencies using uv
sync:
    uv sync --quiet

# Install dependencies with dev & all extras
install:
    @printf "\n{{ blue }}=== Installing Dependencies ==={{ nc }}\n"
    uv sync --all-extras --dev
    @printf "{{ green }}✔ Dependencies installed{{ nc }}\n"

# Auto-format Python code using Ruff
format: sync
    @printf "\n{{ blue }}=== Formatting Code ==={{ nc }}\n"
    uv run ruff check --fix .
    uv run ruff format .
    @printf "{{ green }}✔ Formatting complete{{ nc }}\n"

# Run linters (Ruff and Markdown)
lint: sync
    @printf "\n{{ blue }}=== Running Linters ==={{ nc }}\n"
    uv run ruff check .
    uv run ruff format --check .
    if command -v markdownlint-cli2 >/dev/null 2>&1; then \
        markdownlint-cli2 "**/*.md" "#.venv" "#.pytest_cache" "#.mypy_cache" "#.ruff_cache" "#htmlcov"; \
    elif command -v markdownlint >/dev/null 2>&1; then \
        markdownlint "**/*.md" --ignore ".venv" --ignore ".pytest_cache" --ignore ".mypy_cache" --ignore ".ruff_cache" --ignore "htmlcov"; \
    elif command -v npx >/dev/null 2>&1; then \
        npx --yes markdownlint-cli2 "**/*.md" "#.venv" "#.pytest_cache" "#.mypy_cache" "#.ruff_cache" "#htmlcov"; \
    else \
        printf "{{ yellow }}⚠ markdownlint not found. Skipping markdown linting.{{ nc }}\n"; \
    fi
    @printf "{{ green }}✔ Linting passed{{ nc }}\n"

# Run static type checking with Mypy
typecheck: sync
    @printf "\n{{ blue }}=== Running Type Checks ==={{ nc }}\n"
    uv run mypy .
    @printf "{{ green }}✔ Type checking passed{{ nc }}\n"

# Run Tier 1 unit tests
test-unit: sync
    @printf "\n{{ blue }}=== Running Tier 1: Unit Tests ==={{ nc }}\n"
    uv run pytest
    @printf "{{ green }}✔ Unit tests passed{{ nc }}\n"

# Run unit tests with coverage
test-cov: sync
    @printf "\n{{ blue }}=== Running Tests with Coverage ==={{ nc }}\n"
    uv run pytest --cov
    @printf "{{ green }}✔ Coverage run complete{{ nc }}\n"

# Generate detailed coverage reports
test-cov-report: sync
    @printf "\n{{ blue }}=== Generating Coverage Reports ==={{ nc }}\n"
    uv run pytest --cov --cov-report=term-missing --cov-report=html:htmlcov | tee coverage_report.txt
    @printf "{{ green }}✔ Coverage reports generated in htmlcov/ and coverage_report.txt{{ nc }}\n"

# Run Tier 2 distributed sandbox tests
test-dist: sync
    @printf "\n{{ blue }}=== Running Tier 2: Distributed Sandbox ==={{ nc }}\n"
    bash scripts/test_distributed.sh
    @printf "{{ green }}✔ Distributed sandbox tests complete{{ nc }}\n"

# Spawn Tier 3 Multipass VM cluster for OS field testing
test-cluster:
    @printf "\n{{ blue }}=== Provisioning Tier 3: Field Test Cluster ==={{ nc }}\n"
    bash scripts/spawn_cluster.sh

# Run all automated testing tiers (1 & 2)
test: test-unit test-dist
    @printf "\n{{ green }}✔ All automated test tiers passed successfully.{{ nc }}\n"

# Run the local CI pipeline before pushing
ci: install lint typecheck test-cov test-dist
    @printf "\n{{ green }}✔ Local CI pipeline completed successfully. Clear to push!{{ nc }}\n"

# Remove caches, artifacts, and temporary files
clean:
    @printf "\n{{ blue }}=== Cleaning Workspace ==={{ nc }}\n"
    rm -rf \
        .pytest_cache \
        .mypy_cache \
        .ruff_cache \
        htmlcov \
        .coverage \
        coverage.xml \
        dist \
        build \
        .cache
    rm -f coverage_report.txt
    find . -type d -name "__pycache__" -exec rm -rf {} +
    @printf "{{ green }}✔ Workspace cleaned{{ nc }}\n"

# Bump project version (part: major, minor, patch), sync lockfile, commit, tag, and atomic push
bump part: lint typecheck test-unit
    #!/usr/bin/env bash

    echo "Ensuring local repository is up to date..."
    git pull --ff-only

    echo "Checking for pre-existing uncommitted changes..."
    if [[ -n "$(git status --porcelain --untracked-files=no -- pyproject.toml uv.lock)" ]]; then
        echo "Error: pyproject.toml or uv.lock already has uncommitted changes. Commit or stash them first." >&2
        exit 1
    fi

    VERSION=$(uv run https://raw.githubusercontent.com/JacksonFergusonDev/ci-cd-tooling/refs/heads/main/scripts/bump.py {{ part }})
    NEW_TAG="v$VERSION"

    echo "Checking tag $NEW_TAG does not already exist..."
    if git rev-parse "$NEW_TAG" >/dev/null 2>&1; then
        echo "Error: tag $NEW_TAG already exists." >&2
        git checkout -- pyproject.toml
        exit 1
    fi

    echo "Updating lockfile for $NEW_TAG..."
    uv sync

    echo "Staging changes and creating commit..."
    git add pyproject.toml uv.lock
    git commit -m "chore: bump version to $VERSION"
    git tag -a "$NEW_TAG" -m "Bump version to $NEW_TAG"

    echo "Shipping atomically to remote..."
    git push origin HEAD --tags

# Drop into an isolated macOS sandbox shell with a freshly built local git-pulsar on $PATH
sandbox *args: sync
    #!/usr/bin/env bash
    set -euo pipefail

    REPO_ROOT="{{ invocation_directory() }}"
    SANDBOX_DIR="$(mktemp -d /tmp/pulsar-macos-XXXXXX)"
    MOCK_HOME="$SANDBOX_DIR/home"
    WORKSPACE="$SANDBOX_DIR/workspace"
    SANDBOX_VENV="$SANDBOX_DIR/venv"

    mkdir -p "$MOCK_HOME" "$WORKSPACE"

    cleanup() {
        printf "\n{{ yellow }}Cleaning up macOS sandbox...{{ nc }}\n"
        rm -rf "$SANDBOX_DIR"
        printf "{{ green }}✔ Sandbox wiped.{{ nc }}\n"
    }
    trap cleanup EXIT INT TERM

    printf "\n{{ blue }}=== Building Fresh git-pulsar Sandbox Environment ==={{ nc }}\n"

    uv venv "$SANDBOX_VENV" --quiet
    VIRTUAL_ENV="$SANDBOX_VENV" uv pip install \
        --no-cache \
        --reinstall-package git-pulsar \
        -e "$REPO_ROOT" --quiet

    printf "{{ green }}✔ git-pulsar built fresh from local source tree{{ nc }}\n"
    printf "{{ yellow }}Mocked HOME:{{ nc }} %s\n" "$MOCK_HOME"
    printf "{{ yellow }}Workspace:  {{ nc }} %s\n" "$WORKSPACE"
    printf "{{ yellow }}Binary:     {{ nc }} %s\n\n" "$SANDBOX_VENV/bin/git-pulsar"

    cd "$WORKSPACE"

    RAW_ARGS="{{ args }}"

    if [[ -n "$RAW_ARGS" ]]; then
        HOME="$MOCK_HOME" PATH="$SANDBOX_VENV/bin:$PATH" git-pulsar {{ args }}
    else
        printf "{{ blue }}Entering interactive sandbox shell (type 'exit' or Ctrl+D when done):{{ nc }}\n\n"
        HOME="$MOCK_HOME" PATH="$SANDBOX_VENV/bin:$PATH" PULSARSANDBOX=1 $SHELL -i
    fi
