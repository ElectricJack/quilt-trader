"""session experiment scope

Revision ID: a9881e979b4f
Revises: 1c9809883c8a
Create Date: 2026-05-31 17:03:11.568640
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a9881e979b4f'
down_revision: Union[str, None] = '1c9809883c8a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. NULL out optimization_session_id on backtest_runs to preserve them.
    op.execute(
        "UPDATE backtest_runs SET optimization_session_id = NULL "
        "WHERE optimization_session_id IS NOT NULL"
    )
    # 2. Drop research_jobs that reference the 1 legacy session.
    op.execute(
        "DELETE FROM research_jobs WHERE session_id IN "
        "(SELECT id FROM optimization_sessions)"
    )
    # 3. Wipe the 1 legacy session.
    op.execute("DELETE FROM optimization_sessions")
    # 4. Add the 6 new columns NOT NULL (safe — table is now empty).
    with op.batch_alter_table("optimization_sessions") as batch:
        batch.add_column(sa.Column("date_range_start", sa.Date(), nullable=False))
        batch.add_column(sa.Column("date_range_end", sa.Date(), nullable=False))
        batch.add_column(sa.Column(
            "initial_cash", sa.Float(),
            nullable=False, server_default="10000.0",
        ))
        batch.add_column(sa.Column(
            "cost_profile", sa.String(32),
            nullable=False, server_default="default",
        ))
        batch.add_column(sa.Column("benchmark_symbol", sa.String(32), nullable=True))
        batch.add_column(sa.Column("benchmark_source", sa.String(32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("optimization_sessions") as batch:
        batch.drop_column("benchmark_source")
        batch.drop_column("benchmark_symbol")
        batch.drop_column("cost_profile")
        batch.drop_column("initial_cash")
        batch.drop_column("date_range_end")
        batch.drop_column("date_range_start")
