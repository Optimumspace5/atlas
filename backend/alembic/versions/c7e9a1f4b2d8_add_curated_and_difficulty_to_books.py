"""add curated flag and difficulty_tier to books

Revision ID: c7e9a1f4b2d8
Revises: fbad272ac42a
Create Date: 2026-06-13

curated  : only books in the curated catalog (v2) are served to recommendations
           and search. Defaults FALSE so the existing scrape stays in the DB but
           is hidden until explicitly flagged true.
difficulty_tier : 1 intro / 2 core / 3 deep, used by the Track D roadmaps.
                  Nullable (only the grounded must-adds have it for now).
"""
from typing import Sequence, Union

from alembic import op

revision: str = "c7e9a1f4b2d8"
down_revision: Union[str, Sequence[str], None] = "fbad272ac42a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE books ADD COLUMN IF NOT EXISTS curated BOOLEAN NOT NULL DEFAULT FALSE")
    op.execute("ALTER TABLE books ADD COLUMN IF NOT EXISTS difficulty_tier SMALLINT")


def downgrade() -> None:
    op.execute("ALTER TABLE books DROP COLUMN IF EXISTS difficulty_tier")
    op.execute("ALTER TABLE books DROP COLUMN IF EXISTS curated")
