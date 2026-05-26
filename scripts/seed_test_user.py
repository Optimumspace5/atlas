"""Seed a single deterministic test user for endpoint testing.

Idempotent: re-running does nothing if the user already exists.

The test user has a hardcoded UUID so tests and curl commands can
reference it by a known value:

    00000000-0000-0000-0000-000000000001

Usage:
    python scripts/seed_test_user.py

Requires DATABASE_URL in env.
"""
import os
import sys
import uuid
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.app.models import User  # noqa: E402


TEST_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
TEST_USER_EMAIL = "test@atlas.local"


def main() -> int:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set")
        return 2

    engine = create_engine(db_url)
    with Session(engine) as session:
        existing = session.scalar(select(User).where(User.id == TEST_USER_ID))
        if existing is not None:
            print(f"[SKIP] User {TEST_USER_ID} already exists ({existing.email})")
            return 0

        user = User(id=TEST_USER_ID, email=TEST_USER_EMAIL)
        session.add(user)
        session.commit()
        print(f"[ADD ] Seeded test user {TEST_USER_ID} ({TEST_USER_EMAIL})")
        return 0


if __name__ == "__main__":
    sys.exit(main())
