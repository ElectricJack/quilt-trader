"""backtest_report_columns

Revision ID: ae2dd86e1330
Revises: 95f4bf13d375
Create Date: 2026-05-15 12:18:35.272940
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'ae2dd86e1330'
down_revision: Union[str, None] = '95f4bf13d375'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('backtest_runs', sa.Column('key_metrics', sa.JSON(), nullable=True))
    op.add_column('backtest_runs', sa.Column('rolling_metrics', sa.JSON(), nullable=True))
    op.add_column('backtest_runs', sa.Column('monthly_returns_matrix', sa.JSON(), nullable=True))
    op.add_column('backtest_runs', sa.Column('eoy_returns', sa.JSON(), nullable=True))
    op.add_column('backtest_runs', sa.Column('benchmark_equity_curve', sa.JSON(), nullable=True))
    op.add_column('backtest_runs', sa.Column('drawdown_curve', sa.JSON(), nullable=True))
    with op.batch_alter_table('backtest_runs') as batch:
        batch.drop_column('tearsheet_path')


def downgrade() -> None:
    op.add_column('backtest_runs', sa.Column('tearsheet_path', sa.String(), nullable=True))
    with op.batch_alter_table('backtest_runs') as batch:
        batch.drop_column('drawdown_curve')
        batch.drop_column('benchmark_equity_curve')
        batch.drop_column('eoy_returns')
        batch.drop_column('monthly_returns_matrix')
        batch.drop_column('rolling_metrics')
        batch.drop_column('key_metrics')
