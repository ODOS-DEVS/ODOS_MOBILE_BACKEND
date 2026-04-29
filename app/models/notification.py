import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class NotificationRead(Base):
    __tablename__ = "notification_reads"

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
    notification_key: Mapped[str] = mapped_column(String(120), nullable=False)
    read_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    user: Mapped["User"] = relationship(back_populates="notification_reads")

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "notification_key",
            name="uq_notification_reads_user_id_notification_key",
        ),
    )


class NotificationEvent(Base):
    __tablename__ = "notification_events"

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
    kind: Mapped[str] = mapped_column(String(60), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    body: Mapped[str] = mapped_column(String(500), nullable=False)
    icon: Mapped[str] = mapped_column(String(60), nullable=False)
    accent: Mapped[str] = mapped_column(String(30), nullable=False, default="neutral")
    action_label: Mapped[str | None] = mapped_column(String(80), nullable=True)
    route_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    route_target_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    image_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    user: Mapped["User"] = relationship(back_populates="notification_events")
