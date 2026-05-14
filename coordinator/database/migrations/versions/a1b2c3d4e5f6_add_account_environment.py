"""add environment column to accounts

Revision ID: a1b2c3d4e5f6
Revises: 392143345ff0
Create Date: 2026-05-13 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "392143345ff0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("accounts") as batch:
        batch.add_column(
            sa.Column(
                "environment",
                sa.String(),
                nullable=False,
                server_default="paper",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("accounts") as batch:
        batch.drop_column("environment")
