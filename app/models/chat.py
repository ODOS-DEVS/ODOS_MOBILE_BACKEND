import uuid
from datetime import datetime
import enum

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class ChatThreadType(str, enum.Enum):
    STORE = "store"
    SUPPORT = "support"


class SupportChatStatus(str, enum.Enum):
    WAITING_ON_ADMIN = "waiting_on_admin"
    WAITING_ON_CUSTOMER = "waiting_on_customer"
    RESOLVED = "resolved"


class ChatThread(Base):
    __tablename__ = "chat_threads"
    __table_args__ = (
        UniqueConstraint(
            "customer_user_id",
            "store_id",
            "thread_type",
            name="uq_chat_threads_customer_store_type",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    customer_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    vendor_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    store_id: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("stores.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    thread_type: Mapped[ChatThreadType] = mapped_column(
        Enum(
            ChatThreadType,
            name="chat_thread_type",
            values_callable=lambda values: [value.value for value in values],
        ),
        default=ChatThreadType.STORE,
        server_default=ChatThreadType.STORE.value,
        nullable=False,
        index=True,
    )
    subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    product_id: Mapped[str | None] = mapped_column(
        String(100),
        ForeignKey("products.id", ondelete="SET NULL"),
        nullable=True,
    )
    product_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    product_image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    support_status: Mapped[SupportChatStatus | None] = mapped_column(
        Enum(
            SupportChatStatus,
            name="support_chat_status",
            values_callable=lambda values: [value.value for value in values],
        ),
        nullable=True,
        index=True,
    )
    assigned_admin_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    assigned_admin_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_message_text: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
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

    customer_user: Mapped["User"] = relationship(
        foreign_keys=[customer_user_id],
        back_populates="customer_chat_threads",
    )
    vendor_user: Mapped["User"] = relationship(
        foreign_keys=[vendor_user_id],
        back_populates="vendor_chat_threads",
    )
    assigned_admin_user: Mapped["User | None"] = relationship(
        foreign_keys=[assigned_admin_user_id],
    )
    store: Mapped["Store"] = relationship()
    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="thread",
        cascade="all, delete-orphan",
        order_by="ChatMessage.created_at.asc()",
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    thread_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_threads.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sender_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    recipient_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    body: Mapped[str] = mapped_column(String(2000), nullable=False)
    is_read: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="false",
        nullable=False,
    )
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    thread: Mapped["ChatThread"] = relationship(back_populates="messages")
    sender_user: Mapped["User"] = relationship(
        foreign_keys=[sender_user_id],
        back_populates="sent_chat_messages",
    )
    recipient_user: Mapped["User"] = relationship(
        foreign_keys=[recipient_user_id],
        back_populates="received_chat_messages",
    )
