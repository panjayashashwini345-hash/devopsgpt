.PHONY: help install dev run serve investigate test lint type fmt check docker docker-run clean

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install:  ## Create venv and install the package
	uv venv && uv pip install -e .

dev:  ## Install with dev dependencies
	uv venv && uv pip install -e ".[dev]"

serve:  ## Run the API server (http://localhost:8000)
	uv run devopsgpt serve --reload

investigate:  ## Run a one-shot investigation: make investigate Q="Checkout API is slow"
	uv run devopsgpt investigate "$(or $(Q),Checkout API is slow)"

test:  ## Run the test suite
	uv run pytest

lint:  ## Lint with ruff
	uv run ruff check src tests

fmt:  ## Auto-format and fix lint
	uv run ruff format src tests && uv run ruff check --fix src tests

type:  ## Type-check with mypy
	uv run mypy src

check: lint type test  ## Run lint + types + tests (CI gate)

docker:  ## Build the Docker image
	docker build -t devopsgpt:latest .

docker-run:  ## Run via docker compose
	docker compose up --build

clean:  ## Remove caches and build artifacts
	rm -rf .pytest_cache .ruff_cache .mypy_cache .coverage htmlcov dist build *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
