"""Shared pytest fixtures for Atlas tests.

Two key fixtures:
    client  : a FastAPI TestClient bound to the live app
    db      : a SQLAlchemy session against the running Postgres DB

These tests run against the SAME database the dev server uses — not an
isolated test DB. Cleanup happens per-test via explicit DELETEs.
"""
import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.app.main import app
from backend.app.models import UserBook


TEST_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture(scope="session")
def db() -> Session:
    """Session-scoped DB session for tests that need direct DB access."""
    db_url = os.environ["DATABASE_URL"]
    engine = create_engine(db_url)
    session = Session(engine)
    yield session
    session.close()


@pytest.fixture
def client() -> TestClient:
    """A FastAPI TestClient bound to the Atlas app."""
    return TestClient(app)


@pytest.fixture
def clean_user_books(db: Session):
    """Wipe user_books for the test user before AND after the test runs.

    Setup: DELETE rows so the test starts from a known state.
    Teardown: DELETE again so the next test isn't polluted by this one.
    """
    db.query(UserBook).filter(UserBook.user_id == TEST_USER_ID).delete()
    db.commit()
    yield
    db.query(UserBook).filter(UserBook.user_id == TEST_USER_ID).delete()
    db.commit()
