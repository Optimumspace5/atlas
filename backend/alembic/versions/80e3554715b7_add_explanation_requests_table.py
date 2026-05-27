"""add explanation_requests table

Revision ID: 80e3554715b7
Revises: 024e8f40067e
Create Date: 2026-05-27 20:09:46.975987

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '80e3554715b7'
down_revision: Union[str, Sequence[str], None] = '024e8f40067e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add explanation_requests table — cache + rate-limit + audit trail."""
    op.execute("""
        CREATE TABLE explanation_requests (
            id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            user_id           UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            book_id           UUID NOT NULL REFERENCES books(id) ON DELETE CASCADE,
            prompt_version    INTEGER NOT NULL,
            read_history_hash CHAR(64) NOT NULL,
            explanation       TEXT NOT NULL,
            model             VARCHAR(64) NOT NULL,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            served_count      INTEGER NOT NULL DEFAULT 1,
            last_served_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT explanation_requests_cache_key_unique
                UNIQUE (user_id, book_id, prompt_version, read_history_hash)
        )
    """)
    op.execute("""
        CREATE INDEX explanation_requests_user_created_at_idx
            ON explanation_requests (user_id, created_at)
    """)


def downgrade() -> None:
    """Drop explanation_requests table."""
    op.execute("DROP TABLE IF EXISTS explanation_requests")

