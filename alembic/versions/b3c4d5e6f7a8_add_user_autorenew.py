"""add user autorenew

Revision ID: b3c4d5e6f7a8
Revises: a1b2c3d4e5f6
Create Date: 2026-04-13 15:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'b3c4d5e6f7a8'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    result = conn.execute(sa.text("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'users' AND column_name = 'autorenew'
    """))
    if not result.fetchone():
        op.add_column('users', sa.Column('autorenew', sa.Boolean(), nullable=False, server_default='false'))


def downgrade() -> None:
    conn = op.get_bind()
    result = conn.execute(sa.text("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'users' AND column_name = 'autorenew'
    """))
    if result.fetchone():
        op.drop_column('users', 'autorenew')
