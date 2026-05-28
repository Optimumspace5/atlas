from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.routers import recommendations, users

app = FastAPI(title="Atlas API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(recommendations.router)
app.include_router(users.router)


@app.get("/health")
def health():
    return {"status": "ok"}
