"""Service for managing withdrawal limits and preventing fraud."""

from datetime import datetime, timedelta, timezone
from sqlalchemy import select, and_, func
from sqlalchemy.orm import Session
from typing import Optional, Tuple
import logging

logger = logging.getLogger(__name__)


class WithdrawalLimiterService:
    """Manage vendor withdrawal limits and prevent fraudulent activity."""

    # Configuration
    DAILY_LIMIT_GHS = 50000
    WEEKLY_LIMIT_GHS = 200000
    MONTHLY_LIMIT_GHS = 500000
    LARGE_WITHDRAWAL_THRESHOLD_GHS = 5000  # Requires 2FA
    MAXIMUM_SINGLE_WITHDRAWAL_GHS = 100000

    @staticmethod
    def check_withdrawal_limits(
        db: Session,
        vendor_id: str,
        requested_amount: float,
    ) -> Tuple[bool, str, dict]:
        """
        Check if a withdrawal request is within allowed limits.

        Returns:
            (is_allowed, message, limit_info)
        """
        from app.models import VendorWithdrawalRequest

        now = datetime.now(timezone.utc)

        # Check single withdrawal limit
        if requested_amount > WithdrawalLimiterService.MAXIMUM_SINGLE_WITHDRAWAL_GHS:
            return False, (
                f"Withdrawal exceeds maximum single transaction limit of "
                f"GHS {WithdrawalLimiterService.MAXIMUM_SINGLE_WITHDRAWAL_GHS}"
            ), {}

        # Get daily total (last 24 hours)
        daily_cutoff = now - timedelta(days=1)
        daily_total = db.scalar(
            select(func.coalesce(func.sum(VendorerWithdrawalRequest.amount), 0))
            .where(
                and_(
                    VendorWithdrawalRequest.vendor_id == vendor_id,
                    VendorWithdrawalRequest.status.in_(["approved", "processing", "paid"]),
                    VendorWithdrawalRequest.created_at >= daily_cutoff,
                )
            )
        ) or 0

        daily_remaining = WithdrawalLimiterService.DAILY_LIMIT_GHS - daily_total

        if requested_amount + daily_total > WithdrawalLimiterService.DAILY_LIMIT_GHS:
            return False, (
                f"Daily withdrawal limit exceeded. "
                f"Requested: GHS {requested_amount}, "
                f"Remaining today: GHS {daily_remaining}"
            ), {
                "daily_total": daily_total,
                "daily_limit": WithdrawalLimiterService.DAILY_LIMIT_GHS,
                "daily_remaining": daily_remaining,
            }

        # Get weekly total (last 7 days)
        weekly_cutoff = now - timedelta(days=7)
        weekly_total = db.scalar(
            select(func.coalesce(func.sum(VendorWithdrawalRequest.amount), 0))
            .where(
                and_(
                    VendorWithdrawalRequest.vendor_id == vendor_id,
                    VendorWithdrawalRequest.status.in_(["approved", "processing", "paid"]),
                    VendorWithdrawalRequest.created_at >= weekly_cutoff,
                )
            )
        ) or 0

        weekly_remaining = WithdrawalLimiterService.WEEKLY_LIMIT_GHS - weekly_total

        if requested_amount + weekly_total > WithdrawalLimiterService.WEEKLY_LIMIT_GHS:
            return False, (
                f"Weekly withdrawal limit exceeded. "
                f"Requested: GHS {requested_amount}, "
                f"Remaining this week: GHS {weekly_remaining}"
            ), {
                "weekly_total": weekly_total,
                "weekly_limit": WithdrawalLimiterService.WEEKLY_LIMIT_GHS,
                "weekly_remaining": weekly_remaining,
            }

        # Get monthly total (last 30 days)
        monthly_cutoff = now - timedelta(days=30)
        monthly_total = db.scalar(
            select(func.coalesce(func.sum(VendorWithdrawalRequest.amount), 0))
            .where(
                and_(
                    VendorWithdrawalRequest.vendor_id == vendor_id,
                    VendorWithdrawalRequest.status.in_(["approved", "processing", "paid"]),
                    VendorWithdrawalRequest.created_at >= monthly_cutoff,
                )
            )
        ) or 0

        monthly_remaining = WithdrawalLimiterService.MONTHLY_LIMIT_GHS - monthly_total

        if requested_amount + monthly_total > WithdrawalLimiterService.MONTHLY_LIMIT_GHS:
            return False, (
                f"Monthly withdrawal limit exceeded. "
                f"Requested: GHS {requested_amount}, "
                f"Remaining this month: GHS {monthly_remaining}"
            ), {
                "monthly_total": monthly_total,
                "monthly_limit": WithdrawalLimiterService.MONTHLY_LIMIT_GHS,
                "monthly_remaining": monthly_remaining,
            }

        # All checks passed
        limit_info = {
            "daily_total": daily_total,
            "daily_limit": WithdrawalLimiterService.DAILY_LIMIT_GHS,
            "daily_remaining": daily_remaining,
            "weekly_total": weekly_total,
            "weekly_limit": WithdrawalLimiterService.WEEKLY_LIMIT_GHS,
            "weekly_remaining": weekly_remaining,
            "monthly_total": monthly_total,
            "monthly_limit": WithdrawalLimiterService.MONTHLY_LIMIT_GHS,
            "monthly_remaining": monthly_remaining,
            "requires_2fa": requested_amount >= WithdrawalLimiterService.LARGE_WITHDRAWAL_THRESHOLD_GHS,
        }

        return True, "Withdrawal within limits", limit_info

    @staticmethod
    def get_withdrawal_limits_info(
        db: Session,
        vendor_id: str,
    ) -> dict:
        """Get current withdrawal limit status for a vendor."""
        from app.models import VendorWithdrawalRequest

        now = datetime.now(timezone.utc)

        # Daily
        daily_cutoff = now - timedelta(days=1)
        daily_total = db.scalar(
            select(func.coalesce(func.sum(VendorWithdrawalRequest.amount), 0))
            .where(
                and_(
                    VendorWithdrawalRequest.vendor_id == vendor_id,
                    VendorWithdrawalRequest.status.in_(["approved", "processing", "paid"]),
                    VendorWithdrawalRequest.created_at >= daily_cutoff,
                )
            )
        ) or 0

        # Weekly
        weekly_cutoff = now - timedelta(days=7)
        weekly_total = db.scalar(
            select(func.coalesce(func.sum(VendorWithdrawalRequest.amount), 0))
            .where(
                and_(
                    VendorWithdrawalRequest.vendor_id == vendor_id,
                    VendorWithdrawalRequest.status.in_(["approved", "processing", "paid"]),
                    VendorWithdrawalRequest.created_at >= weekly_cutoff,
                )
            )
        ) or 0

        # Monthly
        monthly_cutoff = now - timedelta(days=30)
        monthly_total = db.scalar(
            select(func.coalesce(func.sum(VendorWithdrawalRequest.amount), 0))
            .where(
                and_(
                    VendorWithdrawalRequest.vendor_id == vendor_id,
                    VendorWithdrawalRequest.status.in_(["approved", "processing", "paid"]),
                    VendorWithdrawalRequest.created_at >= monthly_cutoff,
                )
            )
        ) or 0

        return {
            "daily": {
                "used": daily_total,
                "limit": WithdrawalLimiterService.DAILY_LIMIT_GHS,
                "remaining": WithdrawalLimiterService.DAILY_LIMIT_GHS - daily_total,
                "percentage": (daily_total / WithdrawalLimiterService.DAILY_LIMIT_GHS) * 100,
            },
            "weekly": {
                "used": weekly_total,
                "limit": WithdrawalLimiterService.WEEKLY_LIMIT_GHS,
                "remaining": WithdrawalLimiterService.WEEKLY_LIMIT_GHS - weekly_total,
                "percentage": (weekly_total / WithdrawalLimiterService.WEEKLY_LIMIT_GHS) * 100,
            },
            "monthly": {
                "used": monthly_total,
                "limit": WithdrawalLimiterService.MONTHLY_LIMIT_GHS,
                "remaining": WithdrawalLimiterService.MONTHLY_LIMIT_GHS - monthly_total,
                "percentage": (monthly_total / WithdrawalLimiterService.MONTHLY_LIMIT_GHS) * 100,
            },
            "single_withdrawal_limit": WithdrawalLimiterService.MAXIMUM_SINGLE_WITHDRAWAL_GHS,
            "large_withdrawal_threshold": WithdrawalLimiterService.LARGE_WITHDRAWAL_THRESHOLD_GHS,
        }

    @staticmethod
    def check_suspicious_activity(
        db: Session,
        vendor_id: str,
    ) -> Tuple[bool, str]:
        """
        Check for suspicious withdrawal patterns.

        Returns:
            (is_suspicious, reason)
        """
        from app.models import VendorWithdrawalRequest

        now = datetime.now(timezone.utc)

        # Check for multiple withdrawals in short time (last hour)
        hourly_cutoff = now - timedelta(hours=1)
        hourly_count = db.scalar(
            select(func.count(VendorWithdrawalRequest.id))
            .where(
                and_(
                    VendorWithdrawalRequest.vendor_id == vendor_id,
                    VendorWithdrawalRequest.created_at >= hourly_cutoff,
                )
            )
        ) or 0

        if hourly_count > 5:
            return True, f"Excessive withdrawal requests in past hour ({hourly_count})"

        # Check for rapid daily limit cycling (4+ days at/near limit)
        daily_cutoff = now - timedelta(days=14)
        high_activity_days = db.scalar(
            select(func.count(func.distinct(func.date(VendorWithdrawalRequest.created_at))))
            .where(
                and_(
                    VendorWithdrawalRequest.vendor_id == vendor_id,
                    VendorWithdrawalRequest.created_at >= daily_cutoff,
                )
            )
        ) or 0

        if high_activity_days >= 7:
            return True, "Frequent withdrawal activity detected"

        return False, "No suspicious activity"
