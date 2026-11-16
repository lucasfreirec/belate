# One image for the API, the workers, the scheduler and the migrations; the command selects
# the role. Build from the repository root: docker build -f deploy/docker/Dockerfile .
#
#   API        uvicorn app.main:app --host 0.0.0.0 --port 8000 --root-path /api
#   worker     celery -A app.worker.celery_app worker --concurrency=1   (the default)
#   scheduler  celery -A app.worker.celery_app beat --schedule=/tmp/celerybeat-schedule
#   migrations alembic upgrade head

FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
# Dependencies first, so this layer is rebuilt only when the lock file changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

FROM python:3.12-slim
RUN useradd --create-home --uid 1000 app
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
# Fail the build, not a running pod, if a module needed at run time is missing. Development
# tools install some packages as a side effect, so the tests cannot catch this.
RUN python -c "import app.main, app.worker.tasks, kombu.transport.redis, psycopg, uvicorn, alembic"
USER app
# One task at a time: parsing the global feed takes about 450 MB. Run more replicas instead.
CMD ["celery", "-A", "app.worker.celery_app", "worker", "--loglevel=INFO", "--concurrency=1"]
