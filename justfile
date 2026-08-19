# Default target: format, lint, typecheck, and test
default: all

# Show this help menu
help:
    @just --list

# Run format, lint, typecheck, and all automated tests
all: format lint typecheck test

# Install dependencies using uv
install:
    @printf "\n\033[1;34m=== Installing Dependencies ===\033[0m\n"
    uv sync --all-extras --dev

# Auto-format Python code using Ruff
format:
    @printf "\n\033[1;34m=== Formatting Code ===\033[0m\n"
    uv run ruff check --fix .
    uv run ruff format .

# Run linters (Ruff and Markdown)
lint:
    @printf "\n\033[1;34m=== Running Linters ===\033[0m\n"
    uv run ruff check .
    uv run ruff format --check .
    @if command -v markdownlint >/dev/null 2>&1; then \
        markdownlint "**/*.md" --ignore ".venv"; \
    elif command -v npx >/dev/null 2>&1; then \
        npx --yes markdownlint-cli "**/*.md" --ignore ".venv"; \
    else \
        printf "\033[1;33m⚠ 'markdownlint' and 'npx' not found. Skipping markdownlint. (Requires Node.js or markdownlint-cli)\033[0m\n"; \
    fi

# Run static type checking with Mypy
typecheck:
    @printf "\n\033[1;34m=== Running Type Checks ===\033[0m\n"
    uv run mypy .

# Run Tier 1 unit tests with coverage
test-unit:
    @printf "\n\033[1;34m=== Running Tier 1: Unit Tests ===\033[0m\n"
    uv run pytest --cov

# Run Tier 2 distributed sandbox tests
test-dist:
    @printf "\n\033[1;34m=== Running Tier 2: Distributed Sandbox ===\033[0m\n"
    bash scripts/test_distributed.sh

# Spawn Tier 3 Multipass VM cluster for OS field testing
test-cluster:
    @printf "\n\033[1;34m=== Provisioning Tier 3: Field Test Cluster ===\033[0m\n"
    bash scripts/spawn_cluster.sh

# Run all automated testing tiers (1 & 2)
test: test-unit test-dist
    @printf "\n\033[1;32m✔ All automated test tiers passed successfully.\033[0m\n"

# Run the exact pipeline executed by GitHub Actions
ci: install lint typecheck test
    @printf "\n\033[1;32m✔ Local CI pipeline completed successfully. Clear to push!\033[0m\n"

# Remove cache directories and test artifacts
clean:
    @printf "\n\033[1;34m=== Cleaning Workspace ===\033[0m\n"
    rm -rf .pytest_cache .mypy_cache .ruff_cache
    find . -type d -name "__pycache__" -exec rm -rf {} +
    @printf "\033[1;32m✔ Environment cleaned.\033[0m\n"
