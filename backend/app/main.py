from fastapi import FastAPI

from backend.app.routers import recommendations, users

app = FastAPI(title="Atlas API")

app.include_router(recommendations.router)
app.include_router(users.router)


@app.get("/health")
def health():
    return {"status": "ok"}
