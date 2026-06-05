"""add mtm_realism

Revision ID: 1c1275e37506
Revises: a9881e979b4f
Create Date: 2026-06-04 17:51:46.303920
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '1c1275e37506'
down_revision: Union[str, None] = 'a9881e979b4f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("optimization_sessions") as batch:
        batch.add_column(
            sa.Column(
                "mtm_realism", sa.Float(),
                nullable=False, server_default="0.0",
            ),
        )
    with op.batch_alter_table("backtest_runs") as batch:
        batch.add_column(
            sa.Column(
                "mtm_realism", sa.Float(),
                nullable=False, server_default="0.0",
            ),
        )


def downgrade() -> None:
    with op.batch_alter_table("backtest_runs") as batch:
        batch.drop_column("mtm_realism")
    with op.batch_alter_table("optimization_sessions") as batch:
        batch.drop_column("mtm_realism")
