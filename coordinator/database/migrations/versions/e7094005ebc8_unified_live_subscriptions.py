"""unified live subscriptions

Revision ID: e7094005ebc8
Revises: 64b8f4780606
Create Date: 2026-05-18 12:41:01.583946

- Adds asset_class column to live_subscriptions (default 'equities').
- Adds subscription_consumers table.
- Backfills one 'manual' consumer row per existing live_subscription with
  dependent_count >= 1 (existing rows under today's behavior are user-initiated).
- Drops dependent_count from live_subscriptions.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, set by alembic
revision = "e7094005ebc8"
down_revision = "64b8f4780606"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add asset_class column.
    with op.batch_alter_table("live_subscriptions") as batch_op:
        batch_op.add_column(
            sa.Column("asset_class", sa.String(), nullable=False, server_default="equities"),
        )

    # 2. Create subscription_consumers.
    op.create_table(
        "subscription_consumers",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "subscription_id",
            sa.String(),
            sa.ForeignKey("live_subscriptions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("consumer_type", sa.String(), nullable=False),
        sa.Column("consumer_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "subscription_id", "consumer_type", "consumer_id",
            name="uq_subscription_consumer",
        ),
    )

    # 3. Backfill — every existing live_subscription with dependent_count >= 1 gets one 'manual' consumer row.
    conn = op.get_bind()
    import uuid
    from datetime import datetime, timezone
    rows = conn.execute(sa.text(
        "SELECT id FROM live_subscriptions WHERE dependent_count >= 1"
    )).fetchall()
    for r in rows:
        conn.execute(sa.text(
            "INSERT INTO subscription_consumers "
            "(id, subscription_id, consumer_type, consumer_id, created_at) "
            "VALUES (:id, :sid, 'manual', NULL, :now)"
        ), {"id": str(uuid.uuid4()), "sid": r[0], "now": datetime.now(timezone.utc)})

    # 4. Drop dependent_count.
    with op.batch_alter_table("live_subscriptions") as batch_op:
        batch_op.drop_column("dependent_count")


def downgrade() -> None:
    # Forward-only migration per spec; no rollback path implemented.
    raise NotImplementedError("downgrade not implemented")
