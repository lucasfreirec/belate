COMPOSE := docker compose -f deploy/compose/docker-compose.yml $(if $(wildcard .env),--env-file .env)

.PHONY: setup test lint format up down stations snapshot watch api web demo-data migrate worker beat status \
	collector-up collector-down collector-status collector-logs monitoring-up monitoring-down \
	stack-up stack-down stack-logs k8s-up k8s-open k8s-status k8s-backup k8s-down k8s-delete

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

api: ## Run the API on http://localhost:8000 (docs at /docs)
	uv run uvicorn app.main:app --reload

web: ## Run the frontend on http://localhost:5173 (needs `make api`)
	cd frontend && npm run dev

demo-data: ## Fill a separate database with 30 invented days, for trying the frontend
	uv run python -m scripts.bench_queries --days 30 --keep

migrate: ## Apply database migrations
	uv run alembic upgrade head

worker: ## Run a Celery worker (needs `make up` and `make migrate`)
	uv run celery -A app.worker.celery_app worker --loglevel=INFO --concurrency=2

beat: ## Run the Celery scheduler; run exactly one
	uv run celery -A app.worker.celery_app beat --loglevel=INFO --schedule=data/celerybeat-schedule

status: ## Show the age of the latest data and the quota used today
	uv run python -m app.status

COLLECTOR := docker compose -f deploy/compose/docker-compose.collector.yml --env-file .env

collector-up: ## Build and start the collector stack (on the server)
	$(COLLECTOR) up -d --build

collector-down: ## Stop the collector stack (data is kept)
	$(COLLECTOR) down

collector-status: ## Show the collector's data age and quota use
	$(COLLECTOR) exec worker python -m app.status

collector-logs: ## Follow the collector's logs
	$(COLLECTOR) logs -f --tail=50 worker beat

MONITORING := docker compose -f deploy/compose/docker-compose.monitoring.yml

monitoring-up: ## Start Prometheus (localhost:9090) and Grafana (localhost:3000, admin / admin)
	$(MONITORING) up -d

monitoring-down: ## Stop Prometheus and Grafana (their data is kept)
	$(MONITORING) down

STACK := docker compose -f deploy/compose/docker-compose.collector.yml \
	-f deploy/compose/docker-compose.stack.yml --env-file .env

stack-up: ## Build and start the whole system in containers, then open http://localhost:8080
	$(STACK) up -d --build --wait

stack-down: ## Stop the whole system (data is kept)
	$(STACK) down

stack-logs: ## Follow the logs of the whole system
	$(STACK) logs -f --tail=50

# Every Kubernetes target names the minikube context, so it can never touch another cluster.
KUBECTL := kubectl --context minikube
K8S_APPS := api worker beat web

k8s-up: ## Run the whole system on minikube (starts it if needed); see k8s-open
	minikube status >/dev/null 2>&1 || minikube start --memory=4g --cpus=4
	docker build -q -f deploy/docker/Dockerfile -t belate-app:dev .
	docker build -q -f deploy/docker/web.Dockerfile -t belate-web:dev .
	minikube image load belate-app:dev
	minikube image load belate-web:dev
	KUBECTL="$(KUBECTL)" deploy/k8s/create-secrets.sh .env
	$(KUBECTL) apply -k deploy/k8s/overlays/dev
	@# The image tag stays "dev", so restart the pods to pick up the image just loaded.
	$(KUBECTL) -n belate rollout restart $(addprefix deployment/,$(K8S_APPS))
	for name in $(K8S_APPS) prometheus grafana; do \
		$(KUBECTL) -n belate rollout status deployment/$$name --timeout=5m || exit 1; \
	done

k8s-open: ## Reach minikube: frontend on localhost:8081, Grafana on 3002, Prometheus on 9092
	@echo "Frontend http://localhost:8081  Grafana http://localhost:3002  Prometheus http://localhost:9092"
	@echo "Ctrl-C to stop."
	$(KUBECTL) -n belate port-forward service/web 8081:80 & \
	$(KUBECTL) -n belate port-forward service/grafana 3002:3000 & \
	$(KUBECTL) -n belate port-forward service/prometheus 9092:9090 & \
	wait

k8s-status: ## Show the pods in minikube, the data age and the quota used today
	$(KUBECTL) -n belate get pods,pvc,cronjobs
	$(KUBECTL) -n belate exec deployment/worker -c worker -- python -m app.status

k8s-backup: ## Take a database dump in minikube now and list the dumps
	KUBECTL="$(KUBECTL)" deploy/k8s/backup.sh now

k8s-down: ## Stop minikube (everything in it, data included, is kept)
	minikube stop

k8s-delete: ## Delete the whole system from minikube, data included
	$(KUBECTL) delete namespace belate
