from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'd4849afac9e5'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.execute(open('/app/db/migrations/001_initial_schema.sql').read())

def downgrade() -> None:
    op.execute("""
        DROP TABLE IF EXISTS user_books CASCADE;
        DROP TABLE IF EXISTS book_concept_annotations CASCADE;
        DROP TABLE IF EXISTS users CASCADE;
        DROP TABLE IF EXISTS concepts CASCADE;
        DROP TABLE IF EXISTS books CASCADE;
    """)
