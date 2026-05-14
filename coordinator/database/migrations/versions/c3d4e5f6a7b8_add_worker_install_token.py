"""add install_token + install_status to workers

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-05-13 14:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("workers") as batch:
        batch.add_column(sa.Column("install_token", sa.String(), nullable=True))
        batch.add_column(
            sa.Column("install_status", sa.String(), nullable=False, server_default="pending")
        )
        batch.create_index("ix_workers_install_token", ["install_token"], unique=True)


def downgrade() -> None:
    with op.batch_alter_table("workers") as batch:
        batch.drop_index("ix_workers_install_token")
        batch.drop_column("install_status")
        batch.drop_column("install_token")
