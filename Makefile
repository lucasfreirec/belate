COMPOSE := docker compose -f deploy/compose/docker-compose.yml $(if $(wildcard .env),--env-file .env)

.PHONY: setup test lint format up down

setup: ## Install backend and frontend dependencies and the git hooks
	uv sync
	cd frontend && npm ci
	uv run pre-commit install

test: ## Run backend and frontend tests
	uv run pytest
	cd frontend && npm test

lint: ## Lint and type-check backend and frontend
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy
	cd frontend && npm run lint && npm run typecheck

format: ## Auto-format and auto-fix the backend
	uv run ruff check --fix .
	uv run ruff format .

up: ## Start PostgreSQL and Redis and wait until they are healthy
	$(COMPOSE) up -d --wait

down: ## Stop PostgreSQL and Redis (data is kept)
	$(COMPOSE) down
