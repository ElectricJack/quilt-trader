"""add broker_txn_id to trade_log and account_cash_flows

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-05-13 13:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("trade_log") as batch:
        batch.add_column(sa.Column("broker_txn_id", sa.String(), nullable=True))
        batch.create_index("ix_trade_log_broker_txn_id", ["broker_txn_id"])
    with op.batch_alter_table("account_cash_flows") as batch:
        batch.add_column(sa.Column("broker_txn_id", sa.String(), nullable=True))
        batch.create_index("ix_account_cash_flows_broker_txn_id", ["broker_txn_id"])


def downgrade() -> None:
    with op.batch_alter_table("account_cash_flows") as batch:
        batch.drop_index("ix_account_cash_flows_broker_txn_id")
        batch.drop_column("broker_txn_id")
    with op.batch_alter_table("trade_log") as batch:
        batch.drop_index("ix_trade_log_broker_txn_id")
        batch.drop_column("broker_txn_id")
