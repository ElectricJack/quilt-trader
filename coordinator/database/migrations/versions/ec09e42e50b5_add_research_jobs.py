"""add_research_jobs

Revision ID: ec09e42e50b5
Revises: 02fbe1576c77
Create Date: 2026-05-28 17:07:45.292183
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'ec09e42e50b5'
down_revision: Union[str, None] = '02fbe1576c77'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "research_jobs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),  # sweep | walk-forward
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("progress_pct", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("progress_message", sa.Text(), nullable=True),
        sa.Column("request_payload", sa.JSON(), nullable=False),
        sa.Column("run_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.current_timestamp()),
        sa.ForeignKeyConstraint(["session_id"], ["optimization_sessions.id"]),
    )
    op.create_index(
        "ix_research_jobs_session_id", "research_jobs", ["session_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_research_jobs_session_id", table_name="research_jobs")
    op.drop_table("research_jobs")
