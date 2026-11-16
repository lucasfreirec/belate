# One image for the workers, the scheduler and the migrations; the command selects the role.
# Build from the repository root: docker build -f deploy/docker/Dockerfile .

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
USER app
CMD ["celery", "-A", "app.worker.celery_app", "worker", "--loglevel=INFO", "--concurrency=2"]
