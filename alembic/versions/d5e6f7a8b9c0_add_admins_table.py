"""
add admins table

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-04-14 10:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision: str = 'd5e6f7a8b9c0'
down_revision: Union[str, Sequence[str], None] = 'c4d5e6f7a8b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create admins table
    op.create_table(
        'admins',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('username', sa.String(64), nullable=False),
        sa.Column('password_hash', sa.String(256), nullable=False),
        sa.Column('role', sa.String(32), nullable=False, server_default='operator'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('username'),
    )
    op.create_index('ix_admins_username', 'admins', ['username'])

    # Seed initial superadmin from env (if available)
    import os
    env_user = os.environ.get('WEB_SUPERADMIN_USERNAME', '').strip()
    env_pass = os.environ.get('WEB_SUPERADMIN_PASSWORD', '').strip()
    if env_user and env_pass:
        try:
            import bcrypt
            # bcrypt has 72-byte limit; truncate explicitly
            pw_bytes = env_pass.encode('utf-8')[:72]
            hashed = bcrypt.hashpw(pw_bytes, bcrypt.gensalt(rounds=12)).decode('ascii')
            op.execute(
                text(
                    "INSERT INTO admins (username, password_hash, role, is_active) "
                    "VALUES (:username, :password_hash, 'superadmin', true) "
                    "ON CONFLICT (username) DO NOTHING"
                ).bindparams(username=env_user, password_hash=hashed)
            )
        except Exception:
            pass  # If bcrypt is not available, skip seeding


def downgrade() -> None:
    op.drop_index('ix_admins_username', table_name='admins')
    op.drop_table('admins')
