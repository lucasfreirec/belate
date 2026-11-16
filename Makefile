COMPOSE := docker compose -f deploy/compose/docker-compose.yml $(if $(wildcard .env),--env-file .env)

.PHONY: setup test lint format up down stations snapshot watch

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

stations: ## List the RER B stations and their monitoring refs (no API key needed)
	uv run python scripts/explore_prim.py stations

snapshot: ## Call each PRIM API once and save the raw responses under data/raw/ (uses quota)
	uv run --env-file .env python scripts/explore_prim.py snapshot

watch: ## Poll the global and unitary APIs 10 times, 30 s apart (uses 10 requests on each quota)
	uv run --env-file .env python scripts/explore_prim.py watch
