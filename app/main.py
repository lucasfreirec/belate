from fastapi import FastAPI

app = FastAPI(title="RER B Reliability")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
