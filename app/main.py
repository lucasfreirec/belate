from fastapi import FastAPI

app = FastAPI(title="Belate")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
