from fastapi import FastAPI

from app.api import public, subscriptions

app = FastAPI(
    title="Belate",
    summary="Reliability of the RER B, from real-time data collected around the clock.",
)
app.include_router(public.router)
app.include_router(subscriptions.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
