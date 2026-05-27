"""optimization_sessions

Revision ID: 02fbe1576c77
Revises: e60271cf19b6
Create Date: 2026-05-27 09:30:14.854497
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '02fbe1576c77'
down_revision: Union[str, None] = 'e60271cf19b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "optimization_sessions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(), nullable=False, unique=True),
        sa.Column("hypothesis", sa.Text(), nullable=False),
        sa.Column("parameter_space", sa.Text(), nullable=False),
        sa.Column("pre_registered_criteria", sa.Text(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="open"),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    with op.batch_alter_table("backtest_runs") as batch_op:
        batch_op.add_column(sa.Column("optimization_session_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("config_hash", sa.String(length=64), nullable=True))
        batch_op.create_foreign_key(
            "fk_backtest_runs_optimization_session",
            "optimization_sessions",
            ["optimization_session_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("backtest_runs") as batch_op:
        batch_op.drop_column("config_hash")
        batch_op.drop_constraint("fk_backtest_runs_optimization_session", type_="foreignkey")
        batch_op.drop_column("optimization_session_id")
    op.drop_table("optimization_sessions")
