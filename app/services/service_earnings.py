"""Paying a provider for completed work.

Mirrors how order settlement credits a vendor wallet, with one difference
that matters: a booking's commission split was frozen onto the row when the
booking was agreed, so this pays what the provider was promised rather than
recomputing against whatever the platform rate happens to be today.

Only money actually collected is released. A free consultation credits
nothing, and the proportional split is kept rather than assuming the full
amount: it costs nothing when a booking is paid in full, which is now every
paid booking, and it keeps the arithmetic correct for bookings taken under
the earlier deposit rules and for any future partial refund.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.controllers.wallet_controller import _lock_vendor_wallet, _round_money
from app.models.service import Booking
from app.models.wallet import VendorWalletTransaction


class EarningsError(RuntimeError):
    """Earnings could not be released."""


def release_booking_earnings(db: Session, booking: Booking) -> float:
    """Credit the provider for a completed booking. Returns the amount.

    Idempotent via `earnings_released_at`: a retried completion, a webhook
    replay or an admin re-running the step cannot pay twice.
    """

    if booking.status != "completed":
        raise EarningsError("Earnings are released when a booking completes.")
    if booking.earnings_released_at is not None:
        return 0.0
    if booking.provider_user_id is None:
        # The provider account was removed. The liability stays in treasury
        # rather than being silently written off; finance resolves it.
        return 0.0

    collected = _round_money(booking.amount_paid)
    if collected <= 0:
        # Nothing was taken through the platform -- a pay-in-person booking.
        # Mark it settled so the sweeper stops reconsidering it.
        booking.earnings_released_at = datetime.now(timezone.utc)
        return 0.0

    # Release in proportion to what was actually collected, using the rate
    # agreed at booking time.
    total = _round_money(booking.total_amount)
    share = (collected / total) if total > 0 else 1.0
    commission_amount = _round_money(booking.commission_amount * share)
    net_amount = _round_money(collected - commission_amount)

    wallet = _lock_vendor_wallet(db, booking.provider_user_id)
    wallet.available_balance = _round_money(wallet.available_balance + net_amount)
    wallet.lifetime_earnings = _round_money(wallet.lifetime_earnings + net_amount)
    wallet.total_commission = _round_money(wallet.total_commission + commission_amount)

    db.add(
        VendorWalletTransaction(
            wallet_id=wallet.id,
            vendor_user_id=booking.provider_user_id,
            booking_id=booking.id,
            kind="booking_earning",
            title=f"Service booking #{booking.booking_number}",
            amount=net_amount,
            gross_amount=collected,
            commission_amount=commission_amount,
            balance_after=wallet.available_balance,
        )
    )

    booking.earnings_released_at = datetime.now(timezone.utc)
    return net_amount
