import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.user import VendorStatus


class VendorApplication(Base):
    __tablename__ = "vendor_applications"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[VendorStatus] = mapped_column(
        Enum(
            VendorStatus,
            name="vendor_status",
            values_callable=lambda values: [value.value for value in values],
        ),
        default=VendorStatus.PENDING,
        server_default=VendorStatus.PENDING.value,
        nullable=False,
    )
    business_name: Mapped[str] = mapped_column(String(160), nullable=False)
    business_category: Mapped[str] = mapped_column(String(120), nullable=False)
    business_description: Mapped[str] = mapped_column(String(1000), nullable=False)
    phone_number: Mapped[str] = mapped_column(String(30), nullable=False)
    whatsapp_number: Mapped[str | None] = mapped_column(String(30), nullable=True)
    region: Mapped[str] = mapped_column(String(120), nullable=False)
    city: Mapped[str] = mapped_column(String(120), nullable=False)
    market_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    store_location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    store_latitude: Mapped[float | None] = mapped_column(nullable=True)
    store_longitude: Mapped[float | None] = mapped_column(nullable=True)
    store_name: Mapped[str] = mapped_column(String(160), nullable=False)
    store_description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    ghana_card_number: Mapped[str | None] = mapped_column(String(60), nullable=True)
    business_registration_number: Mapped[str | None] = mapped_column(String(120), nullable=True)
    logo_image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    banner_image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    shop_image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    user: Mapped["User"] = relationship(back_populates="vendor_application")

    __table_args__ = (
        UniqueConstraint("user_id", name="uq_vendor_applications_user_id"),
    )
