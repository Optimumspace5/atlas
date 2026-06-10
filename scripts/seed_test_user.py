"""Seed one synthetic test user into the DB for live endpoint testing.

Creates a User + UserBook rows from the first value_investor synthetic
user's reading history (deterministic, seed 42). Prints the user_id and
reading list. Idempotent: re-running deletes and recreates the same user.

Usage:
    python scripts/seed_test_user.py
    python scripts/seed_test_user.py --delete   # remove the test user

Requires DATABASE_URL in env.
"""
import argparse
import os
import sys
import uuid
from pathlib import Path

from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.app.models import Book, User, UserBook  # noqa: E402
from scripts.generate_training_data import generate_synthetic_users  # noqa: E402

TEST_USER_ID = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
TEST_USER_EMAIL = "ce-test-user@atlas.local"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--delete", action="store_true", help="Delete the test user and exit")
    args = parser.parse_args()

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL not set")
        return 2

    engine = create_engine(db_url)
    with Session(engine) as session:
        # Always clear any existing test user first (idempotent).
        session.execute(delete(UserBook).where(UserBook.user_id == TEST_USER_ID))
        session.execute(delete(User).where(User.id == TEST_USER_ID))
        session.commit()

        if args.delete:
            print(f"Deleted test user {TEST_USER_ID}")
            return 0

        # Grab the first synthetic user's reading history (deterministic).
        users = generate_synthetic_users(session, n_users=4, seed=42)
        if not users:
            print("ERROR: no synthetic users generated")
            return 2
        seed_user = users[0]
        read_ids = seed_user.read_book_ids

        session.add(User(id=TEST_USER_ID, email=TEST_USER_EMAIL))
        session.flush()
        for bid in read_ids:
            session.add(UserBook(user_id=TEST_USER_ID, book_id=bid))
        session.commit()

        # Print the reading list for context.
        books = {
            b.id: b for b in session.execute(
                select(Book).where(Book.id.in_(read_ids))
            ).scalars().all()
        }
        print(f"Seeded test user: {TEST_USER_ID}")
        print(f"Archetype: {seed_user.archetype}")
        print(f"Reading history ({len(read_ids)} books):")
        for bid in read_ids:
            b = books.get(bid)
            if b:
                print(f"  - {b.title} — {b.author}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
