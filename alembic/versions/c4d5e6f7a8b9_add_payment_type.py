"""add payment_type column

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-04-13 16:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'c4d5e6f7a8b9'
down_revision: Union[str, Sequence[str], None] = 'b3c4d5e6f7a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    result = conn.execute(sa.text("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'payments' AND column_name = 'payment_type'
    """))
    if not result.fetchone():
        op.add_column(
            'payments',
            sa.Column('payment_type', sa.String(32), nullable=False, server_default='subscription')
        )


def downgrade() -> None:
    conn = op.get_bind()
    result = conn.execute(sa.text("""
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'payments' AND column_name = 'payment_type'
    """))
    if result.fetchone():
        op.drop_column('payments', 'payment_type')
