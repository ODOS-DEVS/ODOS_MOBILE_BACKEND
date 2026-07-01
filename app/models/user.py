import enum
import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Enum, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class UserRole(str, enum.Enum):
    CUSTOMER = "customer"
    VENDOR = "vendor"
    ADMIN = "admin"


class VendorStatus(str, enum.Enum):
    NONE = "none"
    PENDING = "pending"
    UNDER_REVIEW = "under_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUSPENDED = "suspended"


class AuthProvider:
    GOOGLE = "google"
    PASSWORD = "password"
    CHOICES = (GOOGLE, PASSWORD)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        index=True,
        nullable=False,
    )
    phone_number: Mapped[str | None] = mapped_column(
        String(30),
        unique=True,
        index=True,
        nullable=True,
    )
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)

    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)
    gender: Mapped[str | None] = mapped_column(String(30), nullable=True)
    city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    region: Mapped[str | None] = mapped_column(String(120), nullable=True)
    expo_push_token: Mapped[str | None] = mapped_column(String(255), nullable=True)
    allow_notifications: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="false",
        nullable=False,
    )
    discount_notifications: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default="true",
        nullable=False,
    )
    store_notifications: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="false",
        nullable=False,
    )
    vendor_order_notifications: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default="true",
        nullable=False,
    )
    system_notifications: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="false",
        nullable=False,
    )
    location_notifications: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="false",
        nullable=False,
    )
    location_updates: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="false",
        nullable=False,
    )
    personalization_enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default="true",
        nullable=False,
    )
    analytics_enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default="true",
        nullable=False,
    )
    role: Mapped[UserRole] = mapped_column(
        Enum(
            UserRole,
            name="user_role",
            values_callable=lambda roles: [role.value for role in roles],
        ),
        default=UserRole.CUSTOMER,
        server_default=UserRole.CUSTOMER.value,
        nullable=False,
    )
    vendor_status: Mapped[VendorStatus] = mapped_column(
        Enum(
            VendorStatus,
            name="vendor_status",
            values_callable=lambda values: [value.value for value in values],
        ),
        default=VendorStatus.NONE,
        server_default=VendorStatus.NONE.value,
        nullable=False,
    )
    vendor_rejection_reason: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default="true",
        nullable=False,
    )
    is_verified: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="false",
        nullable=False,
    )
    phone_verified: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="false",
        nullable=False,
    )
    phone_verification_code_hash: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    phone_verification_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    phone_verification_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    phone_verification_phone: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
    )
    email_verification_code_hash: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    email_verification_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    email_verification_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    password_reset_code_hash: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    password_reset_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    password_reset_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
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
    auth_accounts: Mapped[list["UserAuthAccount"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    vendor_application: Mapped["VendorApplication | None"] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        uselist=False,
    )
    wishlist_items: Mapped[list["WishlistItem"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    cart_items: Mapped[list["CartItem"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    behavior_events: Mapped[list["UserBehaviorEvent"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    saved_addresses: Mapped[list["SavedAddress"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    saved_payment_methods: Mapped[list["SavedPaymentMethod"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    verified_phones: Mapped[list["UserVerifiedPhone"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    orders: Mapped[list["Order"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    payment_transactions: Mapped[list["PaymentTransaction"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    customer_chat_threads: Mapped[list["ChatThread"]] = relationship(
        foreign_keys="ChatThread.customer_user_id",
        back_populates="customer_user",
    )
    vendor_chat_threads: Mapped[list["ChatThread"]] = relationship(
        foreign_keys="ChatThread.vendor_user_id",
        back_populates="vendor_user",
    )
    sent_chat_messages: Mapped[list["ChatMessage"]] = relationship(
        foreign_keys="ChatMessage.sender_user_id",
        back_populates="sender_user",
    )
    received_chat_messages: Mapped[list["ChatMessage"]] = relationship(
        foreign_keys="ChatMessage.recipient_user_id",
        back_populates="recipient_user",
    )
    reviews: Mapped[list["Review"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        foreign_keys="Review.user_id",
    )
    return_requests: Mapped[list["ReturnRequest"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        foreign_keys="ReturnRequest.user_id",
    )
    moderated_reviews: Mapped[list["Review"]] = relationship(
        foreign_keys="Review.moderated_by_user_id",
        back_populates="moderated_by_user",
    )
    reviewed_return_requests: Mapped[list["ReturnRequest"]] = relationship(
        foreign_keys="ReturnRequest.reviewed_by_user_id",
        back_populates="reviewed_by_user",
    )
    notification_events: Mapped[list["NotificationEvent"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    notification_reads: Mapped[list["NotificationRead"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    vendor_wallet: Mapped["VendorWallet | None"] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        uselist=False,
    )
    vendor_wallet_transactions: Mapped[list["VendorWalletTransaction"]] = relationship(
        foreign_keys="VendorWalletTransaction.vendor_user_id",
        back_populates="vendor_user",
    )
    vendor_withdrawal_requests: Mapped[list["VendorWithdrawalRequest"]] = relationship(
        foreign_keys="VendorWithdrawalRequest.vendor_user_id",
        back_populates="vendor_user",
    )
    reviewed_vendor_withdrawal_requests: Mapped[list["VendorWithdrawalRequest"]] = relationship(
        foreign_keys="VendorWithdrawalRequest.reviewed_by_user_id",
        back_populates="reviewed_by_user",
    )
    customer_wallet: Mapped["CustomerWallet | None"] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        uselist=False,
    )
    customer_wallet_transactions: Mapped[list["CustomerWalletTransaction"]] = relationship(
        foreign_keys="CustomerWalletTransaction.user_id",
        back_populates="user",
    )
    customer_wallet_topups: Mapped[list["CustomerWalletTopUp"]] = relationship(
        foreign_keys="CustomerWalletTopUp.user_id",
        back_populates="user",
    )

    def __repr__(self) -> str:
        return f"User(id={self.id!s}, email={self.email!r}, role={self.role.value!r})"

    @property
    def roles(self) -> list[str]:
        roles = [UserRole.CUSTOMER.value]

        if self.role == UserRole.ADMIN:
            roles.append(UserRole.ADMIN.value)

        if self.role == UserRole.VENDOR or self.vendor_status == VendorStatus.APPROVED:
            roles.append(UserRole.VENDOR.value)

        return roles

    @property
    def vendor_id(self) -> str | None:
        if self.vendor_status != VendorStatus.APPROVED:
            return None

        return str(self.id)


class UserAuthAccount(Base):
    __tablename__ = "user_auth_accounts"

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
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    provider_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    user: Mapped["User"] = relationship(back_populates="auth_accounts")

    __table_args__ = (
        UniqueConstraint(
            "provider",
            "provider_user_id",
            name="uq_user_auth_accounts_provider_provider_user_id",
        ),
    )

    def __repr__(self) -> str:
        return (
            "UserAuthAccount("
            f"id={self.id!s}, provider={self.provider!r}, "
            f"provider_user_id={self.provider_user_id!r})"
        )


class WishlistItem(Base):
    __tablename__ = "wishlist_items"

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
    product_id: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    category: Mapped[str | None] = mapped_column(String(120), nullable=True)
    price: Mapped[str | None] = mapped_column(String(50), nullable=True)
    old_price: Mapped[str | None] = mapped_column(String(50), nullable=True)
    rating: Mapped[str | None] = mapped_column(String(50), nullable=True)
    reviews: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    user: Mapped["User"] = relationship(back_populates="wishlist_items")

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "product_id",
            name="uq_wishlist_items_user_id_product_id",
        ),
    )


class CartItem(Base):
    __tablename__ = "cart_items"

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
    product_id: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    image_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    category: Mapped[str | None] = mapped_column(String(120), nullable=True)
    price: Mapped[str] = mapped_column(String(50), nullable=False)
    quantity: Mapped[int] = mapped_column(nullable=False, default=1, server_default="1")
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

    user: Mapped["User"] = relationship(back_populates="cart_items")

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "product_id",
            name="uq_cart_items_user_id_product_id",
        ),
    )
