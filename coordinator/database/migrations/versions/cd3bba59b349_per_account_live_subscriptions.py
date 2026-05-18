"""per account live subscriptions

Revision ID: cd3bba59b349
Revises: e7094005ebc8
Create Date: 2026-05-18 16:13:36.156310

- Adds account_id column to live_subscriptions (FK to accounts, ON DELETE CASCADE).
- Backfills account_id from Setting(live_feed_account.<broker>) if set, else first
  account matching broker_type. Rows with no resolvable account are deleted.
- Drops the (broker, symbol) unique constraint; adds (account_id, symbol) unique.
- Deletes Setting rows with key LIKE 'live_feed_account.%'.
- Strips `broker` field from each entry in algorithms.assets JSON.
"""
from alembic import op
import sqlalchemy as sa
import json


# revision identifiers, set by alembic
revision = "cd3bba59b349"
down_revision = "e7094005ebc8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Add account_id column (nullable for backfill).
    with op.batch_alter_table("live_subscriptions") as batch_op:
        batch_op.add_column(sa.Column("account_id", sa.String(), nullable=True))

    # 2. Backfill account_id.
    rows = conn.execute(sa.text(
        "SELECT id, broker FROM live_subscriptions WHERE account_id IS NULL"
    )).fetchall()
    for sub_id, broker in rows:
        setting = conn.execute(sa.text(
            "SELECT value FROM settings WHERE key = :k"
        ), {"k": f"live_feed_account.{broker}"}).fetchone()
        account_id = setting[0] if setting else None
        if account_id is None:
            account_row = conn.execute(sa.text(
                "SELECT id FROM accounts WHERE broker_type = :b LIMIT 1"
            ), {"b": broker}).fetchone()
            account_id = account_row[0] if account_row else None
        if account_id is None:
            conn.execute(sa.text(
                "DELETE FROM live_subscriptions WHERE id = :i"
            ), {"i": sub_id})
        else:
            conn.execute(sa.text(
                "UPDATE live_subscriptions SET account_id = :a WHERE id = :i"
            ), {"a": account_id, "i": sub_id})

    # 3. Make account_id NOT NULL and add FK + unique constraint; drop the old.
    with op.batch_alter_table("live_subscriptions") as batch_op:
        batch_op.alter_column("account_id", nullable=False)
        batch_op.create_foreign_key(
            "fk_live_subscriptions_account_id",
            "accounts", ["account_id"], ["id"], ondelete="CASCADE",
        )
        batch_op.drop_constraint("uq_live_subscription_broker_symbol", type_="unique")
        batch_op.create_unique_constraint(
            "uq_live_subscription_account_symbol", ["account_id", "symbol"],
        )

    # 4. Delete obsolete settings.
    conn.execute(sa.text(
        "DELETE FROM settings WHERE key LIKE 'live_feed_account.%'"
    ))

    # 5. Strip `broker` field from algorithms.assets JSON.
    algo_rows = conn.execute(sa.text(
        "SELECT id, assets FROM algorithms WHERE assets IS NOT NULL"
    )).fetchall()
    for algo_id, assets_json in algo_rows:
        if not assets_json:
            continue
        try:
            assets = json.loads(assets_json) if isinstance(assets_json, str) else assets_json
        except (TypeError, ValueError):
            continue
        if not isinstance(assets, list):
            continue
        changed = False
        for a in assets:
            if isinstance(a, dict) and "broker" in a:
                del a["broker"]
                changed = True
        if changed:
            conn.execute(sa.text(
                "UPDATE algorithms SET assets = :a WHERE id = :i"
            ), {"a": json.dumps(assets), "i": algo_id})


def downgrade() -> None:
    raise NotImplementedError("downgrade not implemented")
