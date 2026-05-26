"""Database connection plumbing for the FastAPI app.

Three things live here:

    engine        : the SQLAlchemy Engine — process-wide connection pool.
                    Built once at module import.

    SessionLocal  : a sessionmaker bound to the engine. Calling it returns
                    a fresh Session. We never use SessionLocal() directly
                    in endpoints; we go through get_db() instead.

    get_db()      : a generator that FastAPI injects into endpoints via
                    Depends(get_db). Yields a Session, guarantees close().

DATABASE_URL is read once at import time. If it changes, restart the app.
"""
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is not set. Example:\n"
        '  $env:DATABASE_URL = "postgresql://atlas:atlas@localhost:5432/atlas_dev"'
    )

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db():
    """Yield a Session for the lifetime of one request, then close it.

    FastAPI calls this via Depends(get_db). The try/finally ensures the
    session is closed even if the endpoint raises an exception — without
    that, connections would leak from the pool.
    """
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()
