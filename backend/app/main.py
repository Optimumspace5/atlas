from fastapi import FastAPI

from backend.app.routers import recommendations

app = FastAPI(title="Atlas API")

app.include_router(recommendations.router)


@app.get("/health")
def health():
    return {"status": "ok"}
