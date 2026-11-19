"""Give campaigns a submission window, and nominations a proposed price.

A campaign could already accept vendor opt-ins, but there was nothing to say
*until when*, nothing to record that vendors had been invited, and nowhere for
a vendor to propose what the product should cost during the campaign. Admins
were left inviting vendors by hand and agreeing prices off-platform.

Every column is nullable with no backfill: an existing campaign keeps behaving
exactly as it does today (no deadline, never notified, no discount floor), and
an existing opt-in keeps its current meaning (no proposed price).

`vendors_notified_at` is the duplicate guard for the invite fan-out — it is set
inside the same transaction that creates the notifications, so a retried or
concurrent publish cannot notify the same vendors twice.

Revision ID: m7b8j95b7j95
Revises: l6a7i84a6i84
Create Date: 2026-11-19
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "m7b8j95b7j95"
down_revision = "l6a7i84a6i84"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    campaign_columns = {c["name"] for c in inspector.get_columns("merchandising_campaigns")}
    if "submission_deadline" not in campaign_columns:
        op.add_column(
            "merchandising_campaigns",
            sa.Column("submission_deadline", sa.DateTime(timezone=True), nullable=True),
        )
    if "vendors_notified_at" not in campaign_columns:
        op.add_column(
            "merchandising_campaigns",
            sa.Column("vendors_notified_at", sa.DateTime(timezone=True), nullable=True),
        )
    if "vendors_notified_count" not in campaign_columns:
        op.add_column(
            "merchandising_campaigns",
            sa.Column(
                "vendors_notified_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
        )
    if "min_discount_percent" not in campaign_columns:
        op.add_column(
            "merchandising_campaigns",
            sa.Column("min_discount_percent", sa.Integer(), nullable=True),
        )
    if "requires_proposed_price" not in campaign_columns:
        op.add_column(
            "merchandising_campaigns",
            sa.Column(
                "requires_proposed_price",
                sa.Boolean(),
                nullable=False,
                server_default="false",
            ),
        )

    opt_in_columns = {c["name"] for c in inspector.get_columns("merchandising_campaign_opt_ins")}
    if "proposed_price" not in opt_in_columns:
        op.add_column(
            "merchandising_campaign_opt_ins",
            sa.Column("proposed_price", sa.Integer(), nullable=True),
        )
    if "proposed_old_price" not in opt_in_columns:
        # Captured at submission time: the shelf price can move afterwards, and the
        # discount the admin approved has to stay the discount that was offered.
        op.add_column(
            "merchandising_campaign_opt_ins",
            sa.Column("proposed_old_price", sa.Integer(), nullable=True),
        )
    if "vendor_note" not in opt_in_columns:
        op.add_column(
            "merchandising_campaign_opt_ins",
            sa.Column("vendor_note", sa.String(length=255), nullable=True),
        )

    indexes = {index["name"] for index in inspector.get_indexes("merchandising_campaign_opt_ins")}
    if "ix_campaign_opt_ins_vendor_campaign" not in indexes:
        # The vendor submission page reads exactly this shape on every load.
        op.create_index(
            "ix_campaign_opt_ins_vendor_campaign",
            "merchandising_campaign_opt_ins",
            ["vendor_user_id", "campaign_id"],
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    indexes = {index["name"] for index in inspector.get_indexes("merchandising_campaign_opt_ins")}
    if "ix_campaign_opt_ins_vendor_campaign" in indexes:
        op.drop_index(
            "ix_campaign_opt_ins_vendor_campaign",
            table_name="merchandising_campaign_opt_ins",
        )

    opt_in_columns = {c["name"] for c in inspector.get_columns("merchandising_campaign_opt_ins")}
    for column in ("vendor_note", "proposed_old_price", "proposed_price"):
        if column in opt_in_columns:
            op.drop_column("merchandising_campaign_opt_ins", column)

    campaign_columns = {c["name"] for c in inspector.get_columns("merchandising_campaigns")}
    for column in (
        "requires_proposed_price",
        "min_discount_percent",
        "vendors_notified_count",
        "vendors_notified_at",
        "submission_deadline",
    ):
        if column in campaign_columns:
            op.drop_column("merchandising_campaigns", column)
