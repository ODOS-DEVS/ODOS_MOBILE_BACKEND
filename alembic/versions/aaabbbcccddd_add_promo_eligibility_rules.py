"""Add eligibility_rules JSONB column to vouchers and merchandising_campaigns for
enhanced user targeting and personalization."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision = "aaabbbcccddd"
down_revision = "f81f085cfcda"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    voucher_columns = {col["name"] for col in inspector.get_columns("vouchers")}
    if "eligibility_rules" not in voucher_columns:
        op.add_column(
            "vouchers",
            sa.Column(
                "eligibility_rules",
                postgresql.JSONB(),
                nullable=True,
            ),
        )

    campaign_columns = {
        col["name"] for col in inspector.get_columns("merchandising_campaigns")
    }
    if "eligibility_rules" not in campaign_columns:
        op.add_column(
            "merchandising_campaigns",
            sa.Column(
                "eligibility_rules",
                postgresql.JSONB(),
                nullable=True,
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    campaign_columns = {
        col["name"] for col in inspector.get_columns("merchandising_campaigns")
    }
    if "eligibility_rules" in campaign_columns:
        op.drop_column("merchandising_campaigns", "eligibility_rules")

    voucher_columns = {col["name"] for col in inspector.get_columns("vouchers")}
    if "eligibility_rules" in voucher_columns:
        op.drop_column("vouchers", "eligibility_rules")
