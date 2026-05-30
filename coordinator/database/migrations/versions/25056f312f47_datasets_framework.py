"""datasets framework

Revision ID: 25056f312f47
Revises: ec09e42e50b5
Create Date: 2026-05-28 19:18:41.814967
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '25056f312f47'
down_revision: Union[str, None] = 'ec09e42e50b5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('dataset_downloads',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('dataset_name', sa.String(), nullable=False),
    sa.Column('provider', sa.String(), nullable=False),
    sa.Column('request_payload', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('queued_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('rows_fetched', sa.Integer(), nullable=False),
    sa.Column('calls_consumed', sa.Integer(), nullable=False),
    sa.Column('progress_pct', sa.Float(), nullable=False),
    sa.Column('progress_message', sa.Text(), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('last_page', sa.Integer(), nullable=False),
    sa.Column('last_event_date', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_by', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('dataset_downloads', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_dataset_downloads_dataset_name'), ['dataset_name'], unique=False)
        batch_op.create_index(batch_op.f('ix_dataset_downloads_provider'), ['provider'], unique=False)
        batch_op.create_index(batch_op.f('ix_dataset_downloads_status'), ['status'], unique=False)

    op.create_table('quota_usage',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('provider', sa.String(), nullable=False),
    sa.Column('reset_window', sa.Date(), nullable=False),
    sa.Column('calls_used', sa.Integer(), nullable=False),
    sa.Column('daily_limit', sa.Integer(), nullable=False),
    sa.Column('exhausted', sa.Boolean(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('provider', 'reset_window', name='uq_quota_window')
    )
    with op.batch_alter_table('quota_usage', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_quota_usage_provider'), ['provider'], unique=False)
        batch_op.create_index(batch_op.f('ix_quota_usage_reset_window'), ['reset_window'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('quota_usage', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_quota_usage_reset_window'))
        batch_op.drop_index(batch_op.f('ix_quota_usage_provider'))

    op.drop_table('quota_usage')

    with op.batch_alter_table('dataset_downloads', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_dataset_downloads_status'))
        batch_op.drop_index(batch_op.f('ix_dataset_downloads_provider'))
        batch_op.drop_index(batch_op.f('ix_dataset_downloads_dataset_name'))

    op.drop_table('dataset_downloads')
