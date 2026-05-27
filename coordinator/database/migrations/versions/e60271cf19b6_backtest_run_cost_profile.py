"""backtest_run_cost_profile

Revision ID: e60271cf19b6
Revises: 3fd58eade4c4
Create Date: 2026-05-27 09:25:20.151656
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e60271cf19b6'
down_revision: Union[str, None] = '3fd58eade4c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "backtest_runs",
        sa.Column("cost_profile", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("backtest_runs", "cost_profile")
