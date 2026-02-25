.PHONY: help install format lint typecheck test-unit test-dist test-cluster test clean all

# --- ANSI Color Codes ---
BLUE=\033[1;34m
GREEN=\033[1;32m
YELLOW=\033[1;33m
NC=\033[0m # No Color

# --- Helper Macro for Clean Output ---
define PRINT_STAGE
	@echo "\n$(BLUE)=== $(1) ===$(NC)"
endef

# Default target
all: format lint typecheck test

help: ## Show this help menu
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

install: ## Install dependencies using uv
	$(call PRINT_STAGE, Installing Dependencies)
	uv sync --all-extras --dev

format: ## Auto-format Python code using Ruff
	$(call PRINT_STAGE, Formatting Code)
	uv run ruff check --fix .
	uv run ruff format .

lint: ## Run linters (Ruff and Markdown)
	$(call PRINT_STAGE, Running Linters)
	uv run ruff check .
	uv run ruff format --check .
	npx --yes markdownlint-cli "**/*.md" --ignore ".venv"

typecheck: ## Run static type checking with Mypy
	$(call PRINT_STAGE, Running Type Checks)
	uv run mypy .

test-unit: ## Run Tier 1 unit tests
	$(call PRINT_STAGE, Running Tier 1: Unit Tests)
	uv run pytest

test-dist: ## Run Tier 2 distributed sandbox tests
	$(call PRINT_STAGE, Running Tier 2: Distributed Sandbox)
	bash scripts/test_distributed.sh

test-cluster: ## Spawn Tier 3 Multipass VM cluster for OS field testing
	$(call PRINT_STAGE, Provisioning Tier 3: Field Test Cluster)
	bash scripts/spawn_cluster.sh

test: test-unit test-dist ## Run all automated testing tiers (1 & 2)
	@echo "\n$(GREEN)✔ All automated test tiers passed successfully.$(NC)"

clean: ## Remove cache directories and test artifacts
	$(call PRINT_STAGE, Cleaning Workspace)
	rm -rf .pytest_cache .mypy_cache .ruff_cache
	find . -type d -name "__pycache__" -exec rm -rf {} +
	@echo "$(GREEN)✔ Environment cleaned.$(NC)"
