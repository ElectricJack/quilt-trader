"""data providers refactor: nullable account_id + provider_type + drop can_trade

Revision ID: a9f1e2d3b4c5
Revises: feec91c15462
Create Date: 2026-05-18 18:00:00.000000

- Makes live_subscriptions.account_id nullable (was NOT NULL FK).
- Adds live_subscriptions.provider_type column (nullable String).
- Drops accounts.can_trade column (was added in feec91c15462).
- Updates uq_live_subscription_account_symbol to also handle provider-based rows
  (the old unique constraint can't stay because account_id can be NULL now).
"""
from alembic import op
import sqlalchemy as sa


revision = "a9f1e2d3b4c5"
down_revision = "feec91c15462"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Rebuild live_subscriptions to make account_id nullable and add provider_type.
    #    SQLite requires batch_alter_table to change column nullability.
    with op.batch_alter_table("live_subscriptions", recreate="always") as batch_op:
        # Make account_id nullable (was NOT NULL).
        batch_op.alter_column(
            "account_id",
            existing_type=sa.String(),
            nullable=True,
        )
        # Add provider_type column (nullable).
        batch_op.add_column(
            sa.Column("provider_type", sa.String(), nullable=True)
        )
        # Drop the old unique constraint that required account_id.
        batch_op.drop_constraint("uq_live_subscription_account_symbol", type_="unique")

    # 2. Drop can_trade from accounts.
    with op.batch_alter_table("accounts") as batch_op:
        batch_op.drop_column("can_trade")


def downgrade() -> None:
    # Restore can_trade.
    with op.batch_alter_table("accounts") as batch_op:
        batch_op.add_column(
            sa.Column("can_trade", sa.Boolean(), nullable=False, server_default=sa.true())
        )

    # Restore live_subscriptions to previous state.
    with op.batch_alter_table("live_subscriptions", recreate="always") as batch_op:
        batch_op.drop_column("provider_type")
        batch_op.alter_column(
            "account_id",
            existing_type=sa.String(),
            nullable=False,
        )
        batch_op.create_unique_constraint(
            "uq_live_subscription_account_symbol",
            ["account_id", "symbol"],
        )
