from fastapi import FastAPI, HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api import metrics, public, subscriptions
from app.api.deps import SessionDep

# Behind the web container the API is served under /api: run uvicorn with --root-path /api
# so that the documentation page asks for the schema at the right address.
app = FastAPI(
    title="Belate",
    summary="Reliability of the RER B, from real-time data collected around the clock.",
)
app.middleware("http")(metrics.observe_requests)
app.include_router(metrics.router)
app.include_router(public.router)
app.include_router(subscriptions.router)


@app.get("/health")
def health() -> dict[str, str]:
    """The process is up. Used as the liveness check."""
    return {"status": "ok"}


@app.get("/ready")
def ready(session: SessionDep) -> dict[str, str]:
    """The database answers, so requests can be served. Used as the readiness check.

    Redis is not checked: without it the API still serves every read.
    """
    try:
        session.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "The database is unavailable."
        ) from exc
    return {"status": "ready"}
