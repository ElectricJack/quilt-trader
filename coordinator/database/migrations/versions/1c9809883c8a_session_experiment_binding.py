"""session experiment binding

Revision ID: 1c9809883c8a
Revises: 25056f312f47
Create Date: 2026-05-31 15:55:35.673871
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '1c9809883c8a'
down_revision: Union[str, None] = '25056f312f47'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. NULL out optimization_session_id on backtest_runs so historical run
    #    data survives the session wipe.
    op.execute(
        "UPDATE backtest_runs SET optimization_session_id = NULL "
        "WHERE optimization_session_id IS NOT NULL"
    )
    # 2. Drop any research_jobs that reference legacy sessions. Live DB has
    #    zero per exploration; this avoids a FK constraint failure if any
    #    arrived between spec write and migration run.
    op.execute(
        "DELETE FROM research_jobs WHERE session_id IN "
        "(SELECT id FROM optimization_sessions)"
    )
    # 3. Wipe the 4 legacy sessions.
    op.execute("DELETE FROM optimization_sessions")
    # 4. Add the two new columns via batch (required for SQLite FK support).
    #    Table is empty, so NOT NULL is safe.
    with op.batch_alter_table("optimization_sessions") as batch_op:
        batch_op.add_column(
            sa.Column("algorithm_id", sa.String(64), nullable=False),
        )
        batch_op.create_foreign_key(
            "fk_session_algorithm",
            "algorithms",
            ["algorithm_id"], ["id"],
            ondelete="RESTRICT",
        )
        batch_op.add_column(
            sa.Column("base_config", sa.JSON(), nullable=False, server_default="{}"),
        )


def downgrade() -> None:
    with op.batch_alter_table("optimization_sessions") as batch_op:
        batch_op.drop_constraint("fk_session_algorithm", type_="foreignkey")
        batch_op.drop_column("algorithm_id")
        batch_op.drop_column("base_config")
