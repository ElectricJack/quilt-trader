"""add_result_to_research_jobs

Revision ID: bf1948722960
Revises: 1c1275e37506
Create Date: 2026-06-11 05:45:44.252426
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'bf1948722960'
down_revision: Union[str, None] = '1c1275e37506'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('research_jobs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('result', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('research_jobs', schema=None) as batch_op:
        batch_op.drop_column('result')
