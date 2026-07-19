.PHONY: help dev setup install lint fmt typecheck test test-cov docker-up docker-down migrate clean reset

# Default target
help: ## Show this help message
	@echo "Mail Intelligence Platform - Development Commands"
	@echo "================================================="
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------------------
# Environment Setup
# ---------------------------------------------------------------------------

setup: ## First-time setup: create venv, install all packages, copy .env
	python -m venv .venv
	.venv/Scripts/pip install --upgrade pip
	$(MAKE) install
	@if not exist .env copy .env.example .env
	@echo "Setup complete. Activate venv with: .venv\Scripts\activate"

install: ## Install all packages in editable mode with dev dependencies
	.venv/Scripts/pip install -e "packages/models[dev]"
	.venv/Scripts/pip install -e "packages/providers[dev]"
	.venv/Scripts/pip install -e "packages/ai[dev]"
	.venv/Scripts/pip install -e "packages/email_parser[dev]"
	.venv/Scripts/pip install -e "apps/api[dev]"
	.venv/Scripts/pip install -e "apps/workers[dev]"
	.venv/Scripts/pip install -e "apps/webhook[dev]"

dev: ## Start the FastAPI development server
	cd apps/api && python -m uvicorn app.main:create_app --factory --reload --host 0.0.0.0 --port 8000

# ---------------------------------------------------------------------------
# Code Quality
# ---------------------------------------------------------------------------

lint: ## Run ruff linter
	ruff check .

fmt: ## Format code with ruff
	ruff format .
	ruff check --fix .

typecheck: ## Run mypy type checker
	mypy packages/ apps/ --config-file pyproject.toml

# ---------------------------------------------------------------------------
# Testing
# ---------------------------------------------------------------------------

test: ## Run all tests
	pytest

test-cov: ## Run tests with coverage report
	pytest --cov --cov-report=html --cov-report=term-missing

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

migrate: ## Run database migrations
	cd apps/api && alembic upgrade head

migrate-new: ## Create a new migration (usage: make migrate-new msg="add users table")
	cd apps/api && alembic revision --autogenerate -m "$(msg)"

migrate-down: ## Rollback last migration
	cd apps/api && alembic downgrade -1

# ---------------------------------------------------------------------------
# Docker
# ---------------------------------------------------------------------------

docker-up: ## Start all infrastructure services (Postgres, Redis, ES, MinIO)
	docker compose -f infra/docker/docker-compose.yml up -d

docker-down: ## Stop all infrastructure services
	docker compose -f infra/docker/docker-compose.yml down

docker-logs: ## Tail logs from infrastructure services
	docker compose -f infra/docker/docker-compose.yml logs -f

docker-reset: ## Stop services and remove all data volumes
	docker compose -f infra/docker/docker-compose.yml down -v

# ---------------------------------------------------------------------------
# Housekeeping
# ---------------------------------------------------------------------------

clean: ## Remove build artifacts, caches, and compiled files
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	rm -rf htmlcov/ .coverage

reset: docker-reset clean ## Full reset: stop Docker, remove volumes, clean caches
	@echo "Environment reset complete."
