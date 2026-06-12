"""Supabase JWT authentication for the FastAPI backend.

Verifies the Supabase access token (Authorization: Bearer <jwt>) against
the project's JWKS public keys (ES256), derives the user from the token's
`sub` claim, and just-in-time provisions a matching public.users row so
the user_books foreign key is satisfied.

Per-user endpoints depend on get_current_user and use the returned user's
id — never a user_id taken from the URL (which a client could forge).
"""
import os
import uuid

import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWTError
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.models import User

# JWKS endpoint = <project-url>/auth/v1/.well-known/jwks.json
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
JWKS_URL = os.environ.get(
    "SUPABASE_JWKS_URL",
    f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json" if SUPABASE_URL else "",
)
JWT_AUDIENCE = "authenticated"
JWT_ALGORITHMS = ["ES256"]

_bearer = HTTPBearer(auto_error=True)
# PyJWKClient fetches + caches the public keys; created once at import.
_jwks_client = PyJWKClient(JWKS_URL) if JWKS_URL else None


def _verify_token(token: str) -> dict:
    if _jwks_client is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Auth not configured: set SUPABASE_URL (or SUPABASE_JWKS_URL).",
        )
    try:
        signing_key = _jwks_client.get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=JWT_ALGORITHMS,
            audience=JWT_AUDIENCE,
        )
    except PyJWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired token: {e}",
        )


def get_current_user(
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    """Authenticated-user dependency.

    Verifies the bearer token, then get-or-creates the app's users row
    (id = Supabase auth user id, email = token email). Returns the User;
    callers use user.id for all per-user data access.
    """
    payload = _verify_token(creds.credentials)

    sub = payload.get("sub")
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing 'sub' claim",
        )
    try:
        user_id = uuid.UUID(sub)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token 'sub' is not a valid UUID",
        )

    user = db.scalar(select(User).where(User.id == user_id))
    if user is not None:
        return user

    email = payload.get("email") or f"{user_id}@atlas.local"

    # Reclaim the email if a stale ORPHAN row holds it under a different id —
    # i.e. a Supabase auth account that was deleted and recreated (deleting
    # an auth user does NOT remove its public.users row). Supabase enforces
    # unique emails at the auth layer, so a same-email/different-id row here
    # is always a dead identity; its (now inaccessible) books cascade away.
    orphan = db.scalar(
        select(User).where(User.email == email, User.id != user_id)
    )
    if orphan is not None:
        db.delete(orphan)
        db.flush()

    user = User(id=user_id, email=email)
    db.add(user)
    try:
        db.commit()
        db.refresh(user)
    except IntegrityError:
        # Concurrent first-login requests can race to create the row.
        db.rollback()
        user = db.scalar(select(User).where(User.id == user_id))
        if user is None:
            raise
    return user
