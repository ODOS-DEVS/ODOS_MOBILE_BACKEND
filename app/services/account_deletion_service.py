"""Deleting a user account, without destroying anyone else's records.

Apple requires that an app which creates accounts lets a person delete theirs
from inside the app (Guideline 5.1.1(v)). On a marketplace that is less simple
than it sounds.

A hard delete is not available. User.orders cascades, so removing the row would
take the order history with it -- and those orders are also a vendor's sales
record, the platform's financial record, and the basis for tax reporting. The
data belongs to more parties than the person leaving. What the departing user
is entitled to is that their *personal* data stops being personal, which is
what anonymisation does.

So: refuse while obligations are outstanding, then anonymise the person and
keep the commerce.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import (
    CartItem,
    CustomerWallet,
    Order,
    OrderPackage,
    SavedAddress,
    SavedPaymentMethod,
    User,
    UserAuthAccount,
    UserBehaviorEvent,
    UserVerifiedPhone,
    VendorWallet,
    VendorWithdrawalRequest,
    WishlistItem,
)

# An order still moving is an obligation between two people. Neither side gets
# to walk away from it by closing an account.
OPEN_ORDER_STATUSES = ("pending", "confirmed", "processing", "ready", "out_for_delivery")

# A withdrawal that has been asked for but not settled is money in flight.
OPEN_WITHDRAWAL_STATUSES = ("pending", "approved", "processing")


def deletion_blockers(db: Session, user: User) -> list[str]:
    """Reasons this account cannot be deleted yet, phrased for the account holder.

    Returned rather than raised so the app can show all of them at once instead
    of revealing them one refusal at a time.
    """
    blockers: list[str] = []

    open_orders = db.scalar(
        select(func.count(Order.id)).where(
            Order.user_id == user.id,
            Order.status.in_(OPEN_ORDER_STATUSES),
        )
    )
    if open_orders:
        blockers.append(
            f"You have {open_orders} order(s) still in progress. "
            "They need to arrive or be cancelled first."
        )

    customer_balance = db.scalar(
        select(CustomerWallet.available_balance).where(CustomerWallet.user_id == user.id)
    )
    if customer_balance and customer_balance > 0:
        blockers.append(
            f"Your wallet still holds {customer_balance:.2f}. "
            "Spend it or contact support for a refund first."
        )

    # Vendor obligations. A vendor who still owes fulfilment or is owed money
    # cannot disappear -- customers are waiting on one, and the platform owes
    # the other.
    open_packages = db.scalar(
        select(func.count(OrderPackage.id)).where(
            OrderPackage.vendor_user_id == user.id,
            OrderPackage.vendor_status.in_(OPEN_ORDER_STATUSES),
        )
    )
    if open_packages:
        blockers.append(
            f"You have {open_packages} customer order(s) left to fulfil. "
            "Complete or cancel them first."
        )

    vendor_wallet = db.scalar(
        select(VendorWallet).where(VendorWallet.vendor_user_id == user.id)
    )
    if vendor_wallet:
        owed = (vendor_wallet.available_balance or 0) + (
            vendor_wallet.pending_withdrawal_balance or 0
        )
        if owed > 0:
            blockers.append(
                f"Your vendor wallet still holds {owed:.2f}. "
                "Withdraw it before deleting your account."
            )

    open_withdrawals = db.scalar(
        select(func.count(VendorWithdrawalRequest.id)).where(
            VendorWithdrawalRequest.vendor_user_id == user.id,
            VendorWithdrawalRequest.status.in_(OPEN_WITHDRAWAL_STATUSES),
        )
    )
    if open_withdrawals:
        blockers.append(
            f"You have {open_withdrawals} withdrawal(s) being processed. "
            "Wait for them to complete."
        )

    return blockers


def delete_account(db: Session, user: User) -> None:
    """Anonymise the account and lock it out. Assumes blockers were checked.

    What goes is anything that only describes the person: their name, contact
    details, addresses, saved cards, cart, wishlist and browsing history.

    What stays is anything that also describes a transaction: orders, payments,
    wallet ledger entries and reviews. Those are deliberately kept and merely
    detached from a real identity -- a vendor's sales history should not develop
    holes because a customer left.
    """
    marker = uuid.uuid4().hex[:12]

    # Email and phone are unique columns, so they must be replaced rather than
    # nulled -- two deleted accounts would otherwise collide. .invalid is
    # reserved by RFC 2606 and can never be a real address.
    user.email = f"deleted-{marker}@odos.invalid"
    user.phone_number = None
    user.full_name = "Deleted user"
    user.avatar_url = None
    user.date_of_birth = None
    user.gender = None
    user.city = None
    user.region = None

    # Nothing should be able to sign in as this account again: no password, no
    # linked provider, and every issued token invalidated.
    user.hashed_password = None
    user.expo_push_token = None
    user.is_active = False
    user.is_verified = False
    user.phone_verified = False
    user.deleted_at = datetime.now(UTC)
    user.token_version = (user.token_version or 0) + 1

    # Verification and reset material is worthless now and is still personal data.
    user.phone_verification_code_hash = None
    user.phone_verification_phone = None
    user.email_verification_code_hash = None
    user.password_reset_code_hash = None

    for model, column in (
        (CartItem, CartItem.user_id),
        (WishlistItem, WishlistItem.user_id),
        (SavedAddress, SavedAddress.user_id),
        (SavedPaymentMethod, SavedPaymentMethod.user_id),
        (UserBehaviorEvent, UserBehaviorEvent.user_id),
        (UserVerifiedPhone, UserVerifiedPhone.user_id),
        (UserAuthAccount, UserAuthAccount.user_id),
    ):
        db.execute(delete(model).where(column == user.id))

    db.commit()
