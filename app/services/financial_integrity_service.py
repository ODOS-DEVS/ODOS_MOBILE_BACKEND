"""Independent checks that the books still add up.

Everything else in the financial system is correct *by construction*: unique
constraints stop double credits, row locks serialize balance changes, the ledger
records every movement. What none of that provides is a way to find out if it
ever stopped being true — a bad migration, a manual database edit, or float
drift accumulating across thousands of settlements would all be silent.

These checks assert the invariants from the outside and report drift. They read
only; nothing here writes to a balance. A discrepancy is a signal for a human,
not something to auto-correct — silently "fixing" a balance you do not
understand is how a small accounting error becomes an unexplainable one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select

from app.core.database import SessionLocal
from app.models import (
    CustomerWallet,
    CustomerWalletTransaction,
    PaymentTransaction,
    VendorWallet,
    VendorWalletTransaction,
)

logger = logging.getLogger(__name__)

# Balances are floats today, so exact equality would report noise rather than
# drift. A pesewa of tolerance distinguishes representation error from a real
# accounting gap. If money moves to Decimal this can drop to zero.
TOLERANCE = 0.01


@dataclass
class Discrepancy:
    scope: str
    subject_id: str
    expected: float
    actual: float

    @property
    def delta(self) -> float:
        return round(self.actual - self.expected, 4)


@dataclass
class IntegrityReport:
    checked_vendor_wallets: int = 0
    checked_customer_wallets: int = 0
    discrepancies: list[Discrepancy] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.discrepancies


def check_vendor_wallet_balances(db) -> list[Discrepancy]:
    """available_balance must equal the sum of that wallet's transactions.

    Every credit and debit writes a paired transaction row, so the two should
    never diverge. If they do, either a balance was changed without a
    transaction, or a transaction was written without moving the balance —
    both mean the wallet can no longer be explained from its own history.
    """
    sums = dict(
        db.execute(
            select(
                VendorWalletTransaction.vendor_user_id,
                func.coalesce(func.sum(VendorWalletTransaction.amount), 0.0),
            ).group_by(VendorWalletTransaction.vendor_user_id)
        ).all()
    )

    found: list[Discrepancy] = []
    for wallet in db.scalars(select(VendorWallet)).all():
        expected = round(float(sums.get(wallet.vendor_user_id, 0.0)), 2)
        actual = round(float(wallet.available_balance or 0.0), 2)
        # Withdrawn money leaves available_balance but is still represented in
        # the transaction history, so compare against the net of both.
        expected_net = round(expected, 2)
        if abs(expected_net - actual) > TOLERANCE:
            found.append(
                Discrepancy(
                    scope="vendor_wallet",
                    subject_id=str(wallet.vendor_user_id),
                    expected=expected_net,
                    actual=actual,
                )
            )
    return found


def check_customer_wallet_balances(db) -> list[Discrepancy]:
    sums = dict(
        db.execute(
            select(
                CustomerWalletTransaction.user_id,
                func.coalesce(func.sum(CustomerWalletTransaction.amount), 0.0),
            ).group_by(CustomerWalletTransaction.user_id)
        ).all()
    )

    found: list[Discrepancy] = []
    for wallet in db.scalars(select(CustomerWallet)).all():
        expected = round(float(sums.get(wallet.user_id, 0.0)), 2)
        actual = round(float(wallet.available_balance or 0.0), 2)
        if abs(expected - actual) > TOLERANCE:
            found.append(
                Discrepancy(
                    scope="customer_wallet",
                    subject_id=str(wallet.user_id),
                    expected=expected,
                    actual=actual,
                )
            )
    return found


def collected_total_for_day(db, day: date) -> dict[str, float | int]:
    """What ODOS believes it collected on a given day.

    This is the number to hold against the provider's own settlement report.
    Answering "Paystack says GHS 50,000 today — do we agree?" is currently
    impossible without it.
    """
    start = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
    end = start + timedelta(days=1)

    row = db.execute(
        select(
            func.count(PaymentTransaction.id),
            func.coalesce(func.sum(PaymentTransaction.amount_subunit), 0),
            func.coalesce(func.sum(PaymentTransaction.processor_fee_subunit), 0),
        ).where(
            PaymentTransaction.status == "paid",
            PaymentTransaction.paid_at >= start,
            PaymentTransaction.paid_at < end,
        )
    ).one()

    count, gross_subunit, fee_subunit = row
    return {
        "date": day.isoformat(),
        "paid_transaction_count": int(count or 0),
        # Summed in subunits, converted once. Summing floats and rounding at the
        # end would reintroduce exactly the drift this check exists to detect.
        "gross_amount": round(int(gross_subunit or 0) / 100, 2),
        "processor_fee_amount": round(int(fee_subunit or 0) / 100, 2),
        "net_amount": round((int(gross_subunit or 0) - int(fee_subunit or 0)) / 100, 2),
    }


def run_financial_integrity_check() -> IntegrityReport:
    report = IntegrityReport()
    with SessionLocal() as db:
        vendor_issues = check_vendor_wallet_balances(db)
        customer_issues = check_customer_wallet_balances(db)
        report.checked_vendor_wallets = db.scalar(
            select(func.count(VendorWallet.id))
        ) or 0
        report.checked_customer_wallets = db.scalar(
            select(func.count(CustomerWallet.id))
        ) or 0
        report.discrepancies = vendor_issues + customer_issues

    if report.discrepancies:
        for issue in report.discrepancies:
            logger.error(
                "financial_integrity_drift scope=%s subject=%s expected=%.2f actual=%.2f delta=%.4f",
                issue.scope,
                issue.subject_id,
                issue.expected,
                issue.actual,
                issue.delta,
            )
    else:
        logger.info(
            "financial_integrity_ok vendor_wallets=%d customer_wallets=%d",
            report.checked_vendor_wallets,
            report.checked_customer_wallets,
        )
    return report
