"""promote book_concept_annotations unique to composite primary key

Revision ID: 024e8f40067e
Revises: d4849afac9e5
Create Date: 2026-05-19 10:23:14.243812

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '024e8f40067e'
down_revision: Union[str, Sequence[str], None] = 'd4849afac9e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Promote UNIQUE constraint to composite PRIMARY KEY.

    The original migration declared
        UNIQUE (book_id, concept_id, annotation_type)
    on book_concept_annotations. The table's true identity is this
    combination, so we promote it to a PRIMARY KEY. This aligns the
    database with the SQLAlchemy ORM's requirement for a primary key
    on every mapped table.
    """
    op.execute(
        "ALTER TABLE book_concept_annotations "
        "DROP CONSTRAINT book_concept_annotations_book_id_concept_id_annotation_type_key"
    )
    op.execute(
        "ALTER TABLE book_concept_annotations "
        "ADD PRIMARY KEY (book_id, concept_id, annotation_type)"
    )


def downgrade() -> None:
    """Revert composite PRIMARY KEY back to UNIQUE constraint."""
    op.execute(
        "ALTER TABLE book_concept_annotations "
        "DROP CONSTRAINT book_concept_annotations_pkey"
    )
    op.execute(
        "ALTER TABLE book_concept_annotations "
        "ADD CONSTRAINT book_concept_annotations_book_id_concept_id_annotation_type_key "
        "UNIQUE (book_id, concept_id, annotation_type)"
    )

