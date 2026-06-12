import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.routers import books, concepts, recommendations, users

app = FastAPI(title="Atlas API")

# CORS origins come from ALLOWED_ORIGINS (comma-separated). Defaults to
# local dev; in production set it to the deployed frontend URL, e.g.
# ALLOWED_ORIGINS="https://your-app.vercel.app"
_origins_env = os.environ.get("ALLOWED_ORIGINS", "http://localhost:3000")
ALLOWED_ORIGINS = [o.strip() for o in _origins_env.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(recommendations.router)
app.include_router(users.router)
app.include_router(books.router)
app.include_router(concepts.router)

@app.get("/health")
def health():
    return {"status": "ok"}
