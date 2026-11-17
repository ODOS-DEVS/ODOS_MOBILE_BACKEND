"""Services marketplace: providers, listings, availability and bookings.

Adds the services domain alongside products. Nothing here alters existing
product, order or payment behaviour: the only change to an existing table is
additive columns on `stores`, defaulted so every current shop stays a
products-only seller until someone explicitly enables services.

The overlap guarantee lives in this migration rather than in application
code. `ix_bookings_no_overlap` is a GiST exclusion constraint over
(service, [start, end)) restricted to the statuses that actually occupy a
calendar, so two customers racing for the same slot cannot both win no matter
how the requests interleave — the second INSERT fails at the database.

Revision ID: i3x4f51x3f51
Revises: h2w3e40w2e40
Create Date: 2026-11-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "i3x4f51x3f51"
down_revision = "h2w3e40w2e40"
branch_labels = None
depends_on = None


BLOCKING = "'pending_payment','confirmed','in_progress'"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())

    # Required for the exclusion constraint below: it mixes an equality test
    # on a text column with an overlap test on a range, and plain GiST cannot
    # index the former without this.
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    store_columns = {col["name"] for col in inspector.get_columns("stores")}
    if "offerings" not in store_columns:
        op.add_column(
            "stores",
            sa.Column(
                "offerings",
                postgresql.ARRAY(sa.String(length=20)),
                nullable=True,
                server_default="{products}",
            ),
        )
        op.add_column(
            "stores",
            sa.Column(
                "provider_verification",
                sa.String(length=20),
                nullable=False,
                server_default="unverified",
            ),
        )
        op.add_column("stores", sa.Column("provider_headline", sa.String(length=200), nullable=True))
        op.add_column("stores", sa.Column("provider_bio", sa.Text(), nullable=True))
        op.add_column("stores", sa.Column("provider_years_experience", sa.Integer(), nullable=True))
        op.add_column(
            "stores",
            sa.Column("provider_credentials", postgresql.ARRAY(sa.String(length=200)), nullable=True),
        )
        # Existing rows predate the column; make them explicit rather than
        # leaving NULL to mean "products" by convention.
        op.execute("UPDATE stores SET offerings = '{products}' WHERE offerings IS NULL")
        op.create_index(
            "ix_stores_offerings", "stores", ["offerings"], postgresql_using="gin"
        )

    if "service_categories" not in existing:
        op.create_table(
            "service_categories",
            sa.Column("id", sa.String(length=50), primary_key=True),
            sa.Column("slug", sa.String(length=80), nullable=False, unique=True),
            sa.Column("title", sa.String(length=120), nullable=False),
            sa.Column("subtitle", sa.String(length=160), nullable=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("icon", sa.String(length=60), nullable=True),
            sa.Column("image_key", sa.String(length=100), nullable=True),
            sa.Column("image_url", sa.String(length=500), nullable=True),
            sa.Column(
                "parent_id",
                sa.String(length=50),
                sa.ForeignKey("service_categories.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_service_categories_slug", "service_categories", ["slug"])
        op.create_index("ix_service_categories_parent_id", "service_categories", ["parent_id"])

    if "services" not in existing:
        op.create_table(
            "services",
            sa.Column("id", sa.String(length=100), primary_key=True),
            sa.Column("slug", sa.String(length=140), nullable=False, unique=True),
            sa.Column("store_id", sa.String(length=50), sa.ForeignKey("stores.id", ondelete="CASCADE"), nullable=False),
            sa.Column("vendor_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("category_id", sa.String(length=50), sa.ForeignKey("service_categories.id", ondelete="SET NULL"), nullable=True),
            sa.Column("subcategory_id", sa.String(length=50), sa.ForeignKey("service_categories.id", ondelete="SET NULL"), nullable=True),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("summary", sa.String(length=300), nullable=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("pricing_model", sa.String(length=20), nullable=False, server_default="fixed"),
            sa.Column("booking_type", sa.String(length=20), nullable=False, server_default="request"),
            sa.Column("payment_timing", sa.String(length=20), nullable=False, server_default="on_completion"),
            sa.Column("base_price", sa.Float(), nullable=False, server_default="0"),
            sa.Column("currency", sa.String(length=10), nullable=False, server_default="GHS"),
            sa.Column("deposit_percent", sa.Float(), nullable=True),
            sa.Column("duration_minutes", sa.Integer(), nullable=True),
            sa.Column("buffer_minutes", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("location_modes", postgresql.ARRAY(sa.String(length=20)), nullable=True),
            sa.Column("service_areas", postgresql.ARRAY(sa.String(length=120)), nullable=True),
            sa.Column("lead_time_hours", sa.Integer(), nullable=False, server_default="2"),
            sa.Column("max_advance_days", sa.Integer(), nullable=False, server_default="60"),
            sa.Column("requirements_note", sa.Text(), nullable=True),
            sa.Column("cancellation_policy", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="draft"),
            sa.Column("rejection_reason", sa.String(length=500), nullable=True),
            sa.Column("rating", sa.Float(), nullable=True),
            sa.Column("review_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("booking_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.CheckConstraint(
                "pricing_model IN ('fixed','starting_at','hourly','package','quote','consultation')",
                name="ck_services_pricing_model",
            ),
            sa.CheckConstraint(
                "booking_type IN ('instant','request','quote','consultation')",
                name="ck_services_booking_type",
            ),
            sa.CheckConstraint(
                "payment_timing IN ('upfront','deposit','on_completion','free')",
                name="ck_services_payment_timing",
            ),
            sa.CheckConstraint("base_price >= 0", name="ck_services_base_price_non_negative"),
            sa.CheckConstraint(
                "deposit_percent IS NULL OR (deposit_percent > 0 AND deposit_percent <= 100)",
                name="ck_services_deposit_percent_range",
            ),
            sa.CheckConstraint(
                "duration_minutes IS NULL OR duration_minutes > 0",
                name="ck_services_duration_positive",
            ),
        )
        op.create_index("ix_services_slug", "services", ["slug"])
        op.create_index("ix_services_store_status", "services", ["store_id", "status"])
        op.create_index("ix_services_category_status", "services", ["category_id", "status"])
        op.create_index("ix_services_vendor_user_id", "services", ["vendor_user_id"])

    if "service_images" not in existing:
        op.create_table(
            "service_images",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("service_id", sa.String(length=100), sa.ForeignKey("services.id", ondelete="CASCADE"), nullable=False),
            sa.Column("image_key", sa.String(length=160), nullable=True),
            sa.Column("image_url", sa.String(length=600), nullable=True),
            sa.Column("caption", sa.String(length=255), nullable=True),
            sa.Column("is_portfolio", sa.Boolean(), nullable=False, server_default="false"),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_service_images_service_id", "service_images", ["service_id"])

    if "service_packages" not in existing:
        op.create_table(
            "service_packages",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("service_id", sa.String(length=100), sa.ForeignKey("services.id", ondelete="CASCADE"), nullable=False),
            sa.Column("slug", sa.String(length=80), nullable=False),
            sa.Column("title", sa.String(length=140), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("price", sa.Float(), nullable=False, server_default="0"),
            sa.Column("duration_minutes", sa.Integer(), nullable=True),
            sa.Column("inclusions", postgresql.ARRAY(sa.String(length=200)), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.CheckConstraint("price >= 0", name="ck_service_packages_price_non_negative"),
            sa.UniqueConstraint("service_id", "slug", name="uq_service_packages_service_slug"),
        )
        op.create_index("ix_service_packages_service_id", "service_packages", ["service_id"])

    if "service_addons" not in existing:
        op.create_table(
            "service_addons",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("service_id", sa.String(length=100), sa.ForeignKey("services.id", ondelete="CASCADE"), nullable=False),
            sa.Column("slug", sa.String(length=80), nullable=False),
            sa.Column("title", sa.String(length=140), nullable=False),
            sa.Column("description", sa.String(length=400), nullable=True),
            sa.Column("price", sa.Float(), nullable=False, server_default="0"),
            sa.Column("duration_minutes", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("max_quantity", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.CheckConstraint("price >= 0", name="ck_service_addons_price_non_negative"),
            sa.CheckConstraint("max_quantity >= 1", name="ck_service_addons_max_quantity"),
            sa.UniqueConstraint("service_id", "slug", name="uq_service_addons_service_slug"),
        )
        op.create_index("ix_service_addons_service_id", "service_addons", ["service_id"])

    if "service_availability_rules" not in existing:
        op.create_table(
            "service_availability_rules",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("service_id", sa.String(length=100), sa.ForeignKey("services.id", ondelete="CASCADE"), nullable=False),
            sa.Column("weekday", sa.Integer(), nullable=False),
            sa.Column("start_time", sa.Time(), nullable=False),
            sa.Column("end_time", sa.Time(), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
            sa.CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_availability_weekday_range"),
            sa.CheckConstraint("end_time > start_time", name="ck_availability_time_order"),
        )
        op.create_index(
            "ix_availability_service_weekday", "service_availability_rules", ["service_id", "weekday"]
        )

    if "service_availability_exceptions" not in existing:
        op.create_table(
            "service_availability_exceptions",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("store_id", sa.String(length=50), sa.ForeignKey("stores.id", ondelete="CASCADE"), nullable=False),
            sa.Column("service_id", sa.String(length=100), sa.ForeignKey("services.id", ondelete="CASCADE"), nullable=True),
            sa.Column("exception_date", sa.Date(), nullable=False),
            sa.Column("is_available", sa.Boolean(), nullable=False, server_default="false"),
            sa.Column("start_time", sa.Time(), nullable=True),
            sa.Column("end_time", sa.Time(), nullable=True),
            sa.Column("note", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint(
                "store_id", "service_id", "exception_date", name="uq_availability_exception_day"
            ),
        )
        op.create_index(
            "ix_availability_exceptions_date", "service_availability_exceptions", ["exception_date"]
        )

    if "bookings" not in existing:
        op.create_table(
            "bookings",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("booking_number", sa.String(length=30), nullable=False, unique=True),
            sa.Column("service_id", sa.String(length=100), sa.ForeignKey("services.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("store_id", sa.String(length=50), sa.ForeignKey("stores.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("provider_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("customer_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("package_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("service_packages.id", ondelete="SET NULL"), nullable=True),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="pending_provider"),
            sa.Column("booking_type", sa.String(length=20), nullable=False),
            sa.Column("scheduled_start_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("scheduled_end_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("timezone_name", sa.String(length=60), nullable=False, server_default="Africa/Accra"),
            sa.Column("location_mode", sa.String(length=20), nullable=True),
            sa.Column("location_address", sa.String(length=500), nullable=True),
            sa.Column("location_city", sa.String(length=120), nullable=True),
            sa.Column("location_region", sa.String(length=120), nullable=True),
            sa.Column("location_latitude", sa.Float(), nullable=True),
            sa.Column("location_longitude", sa.Float(), nullable=True),
            sa.Column("customer_note", sa.Text(), nullable=True),
            sa.Column("currency", sa.String(length=10), nullable=False, server_default="GHS"),
            sa.Column("base_amount", sa.Float(), nullable=False, server_default="0"),
            sa.Column("addons_amount", sa.Float(), nullable=False, server_default="0"),
            sa.Column("total_amount", sa.Float(), nullable=False, server_default="0"),
            sa.Column("deposit_amount", sa.Float(), nullable=False, server_default="0"),
            sa.Column("amount_paid", sa.Float(), nullable=False, server_default="0"),
            sa.Column("commission_rate", sa.Float(), nullable=False, server_default="0"),
            sa.Column("commission_amount", sa.Float(), nullable=False, server_default="0"),
            sa.Column("provider_net_amount", sa.Float(), nullable=False, server_default="0"),
            sa.Column("payment_status", sa.String(length=30), nullable=False, server_default="unpaid"),
            sa.Column("pricing_breakdown", postgresql.JSONB(), nullable=True),
            sa.Column("payment_deadline_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("cancelled_by", sa.String(length=20), nullable=True),
            sa.Column("cancellation_reason", sa.String(length=500), nullable=True),
            sa.Column("earnings_released_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.CheckConstraint(
                "status IN ('pending_provider','pending_payment','confirmed','in_progress',"
                "'completed','cancelled','declined','expired','disputed')",
                name="ck_bookings_status",
            ),
            sa.CheckConstraint("total_amount >= 0", name="ck_bookings_total_non_negative"),
            sa.CheckConstraint("amount_paid >= 0", name="ck_bookings_paid_non_negative"),
            sa.CheckConstraint(
                "scheduled_end_at IS NULL OR scheduled_start_at IS NULL "
                "OR scheduled_end_at > scheduled_start_at",
                name="ck_bookings_schedule_order",
            ),
        )
        op.create_index("ix_bookings_customer_status", "bookings", ["customer_user_id", "status"])
        op.create_index("ix_bookings_store_status", "bookings", ["store_id", "status"])
        op.create_index("ix_bookings_service_start", "bookings", ["service_id", "scheduled_start_at"])
        op.create_index("ix_bookings_payment_status", "bookings", ["payment_status"])

        # The double-booking guarantee.
        #
        # Application-level "is this slot free?" checks lose the race: two
        # requests can both read an empty calendar before either writes. This
        # pushes the invariant into Postgres, where the second overlapping
        # INSERT is rejected outright. Restricted to statuses that actually
        # hold a slot, so declining or expiring a booking frees it with no
        # extra bookkeeping.
        op.execute(
            f"""
            ALTER TABLE bookings ADD CONSTRAINT ix_bookings_no_overlap
            EXCLUDE USING gist (
                service_id WITH =,
                tstzrange(scheduled_start_at, scheduled_end_at, '[)') WITH &&
            )
            WHERE (
                scheduled_start_at IS NOT NULL
                AND scheduled_end_at IS NOT NULL
                AND status IN ({BLOCKING})
            )
            """
        )

    if "booking_addons" not in existing:
        op.create_table(
            "booking_addons",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("booking_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("bookings.id", ondelete="CASCADE"), nullable=False),
            sa.Column("addon_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("service_addons.id", ondelete="SET NULL"), nullable=True),
            sa.Column("title", sa.String(length=140), nullable=False),
            sa.Column("unit_price", sa.Float(), nullable=False),
            sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("duration_minutes", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("line_total", sa.Float(), nullable=False),
            sa.CheckConstraint("quantity >= 1", name="ck_booking_addons_quantity"),
            sa.CheckConstraint("unit_price >= 0", name="ck_booking_addons_price"),
        )
        op.create_index("ix_booking_addons_booking_id", "booking_addons", ["booking_id"])

    if "booking_status_events" not in existing:
        op.create_table(
            "booking_status_events",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("booking_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("bookings.id", ondelete="CASCADE"), nullable=False),
            sa.Column("from_status", sa.String(length=30), nullable=True),
            sa.Column("to_status", sa.String(length=30), nullable=False),
            sa.Column("actor_role", sa.String(length=20), nullable=False),
            sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("note", sa.String(length=500), nullable=True),
            sa.Column("metadata_json", postgresql.JSONB(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_booking_status_events_booking_id", "booking_status_events", ["booking_id"])
        op.create_index("ix_booking_status_events_created_at", "booking_status_events", ["created_at"])

    if "service_quotations" not in existing:
        op.create_table(
            "service_quotations",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("reference", sa.String(length=30), nullable=False, unique=True),
            sa.Column("service_id", sa.String(length=100), sa.ForeignKey("services.id", ondelete="CASCADE"), nullable=False),
            sa.Column("store_id", sa.String(length=50), sa.ForeignKey("stores.id", ondelete="CASCADE"), nullable=False),
            sa.Column("customer_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="requested"),
            sa.Column("customer_brief", sa.Text(), nullable=False),
            sa.Column("preferred_date", sa.Date(), nullable=True),
            sa.Column("location_mode", sa.String(length=20), nullable=True),
            sa.Column("location_city", sa.String(length=120), nullable=True),
            sa.Column("quoted_amount", sa.Float(), nullable=True),
            sa.Column("currency", sa.String(length=10), nullable=False, server_default="GHS"),
            sa.Column("provider_message", sa.Text(), nullable=True),
            sa.Column("estimated_duration_minutes", sa.Integer(), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("booking_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("bookings.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.CheckConstraint(
                "status IN ('requested','sent','accepted','declined','expired','withdrawn')",
                name="ck_quotations_status",
            ),
            sa.CheckConstraint("quoted_amount IS NULL OR quoted_amount >= 0", name="ck_quotations_amount"),
        )
        op.create_index("ix_quotations_store_status", "service_quotations", ["store_id", "status"])
        op.create_index("ix_quotations_customer", "service_quotations", ["customer_user_id"])

    if "service_reviews" not in existing:
        op.create_table(
            "service_reviews",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("booking_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("bookings.id", ondelete="CASCADE"), nullable=False),
            sa.Column("service_id", sa.String(length=100), sa.ForeignKey("services.id", ondelete="CASCADE"), nullable=False),
            sa.Column("store_id", sa.String(length=50), sa.ForeignKey("stores.id", ondelete="CASCADE"), nullable=False),
            sa.Column("customer_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("rating", sa.Integer(), nullable=False),
            sa.Column("comment", sa.Text(), nullable=True),
            sa.Column("provider_response", sa.Text(), nullable=True),
            sa.Column("provider_responded_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="published"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.CheckConstraint("rating BETWEEN 1 AND 5", name="ck_service_reviews_rating"),
            # One review per booking, enforced here so it cannot be raced.
            sa.UniqueConstraint("booking_id", name="uq_service_reviews_booking"),
        )
        op.create_index("ix_service_reviews_service_id", "service_reviews", ["service_id"])
        op.create_index("ix_service_reviews_store_id", "service_reviews", ["store_id"])


def downgrade() -> None:
    for table in (
        "service_reviews",
        "service_quotations",
        "booking_status_events",
        "booking_addons",
        "bookings",
        "service_availability_exceptions",
        "service_availability_rules",
        "service_addons",
        "service_packages",
        "service_images",
        "services",
        "service_categories",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")

    op.execute("DROP INDEX IF EXISTS ix_stores_offerings")
    for column in (
        "provider_credentials",
        "provider_years_experience",
        "provider_bio",
        "provider_headline",
        "provider_verification",
        "offerings",
    ):
        op.execute(f"ALTER TABLE stores DROP COLUMN IF EXISTS {column}")
