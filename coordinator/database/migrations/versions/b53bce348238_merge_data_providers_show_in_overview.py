"""merge data_providers + show_in_overview

Revision ID: b53bce348238
Revises: a3f7c2e1d8b5, a9f1e2d3b4c5
Create Date: 2026-05-19 12:55:31.374799
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b53bce348238'
down_revision: Union[str, None] = ('a3f7c2e1d8b5', 'a9f1e2d3b4c5')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
