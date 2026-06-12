import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.routers import books, concepts, recommendations, users

app = FastAPI(title="Atlas API")

# Static allowed origins (local dev + any explicit ones via env, comma-separated).
_origins_env = os.environ.get("ALLOWED_ORIGINS", "http://localhost:3000")
ALLOWED_ORIGINS = [o.strip() for o in _origins_env.split(",") if o.strip()]

# Vercel assigns a new URL on every deploy (per-deploy hash + branch previews),
# so match the whole project's *.vercel.app domains with a regex instead of
# pinning one URL. Override via ALLOWED_ORIGIN_REGEX if the project slug changes.
ALLOWED_ORIGIN_REGEX = os.environ.get(
    "ALLOWED_ORIGIN_REGEX",
    r"https://atlas-.*\.vercel\.app",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_origin_regex=ALLOWED_ORIGIN_REGEX,
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
