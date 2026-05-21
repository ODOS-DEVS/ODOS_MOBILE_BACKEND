from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    Order,
    PaymentTransaction,
    PlatformLedgerEntry,
    PlatformTreasuryAccount,
    ReturnRequest,
    User,
    UserRole,
    VendorWallet,
    VendorWithdrawalRequest,
)
from app.schemas.payment import (
    AdminFinanceOverviewRead,
    AdminPaymentTransactionRead,
    AdminPlatformLedgerEntryRead,
)
from app.services.finance_math import amount_from_subunit, return_reversal_breakdown, round_money, vendor_allocation_map


def require_admin(user: User) -> None:
    if user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access is required for this action.",
        )


def get_or_create_platform_treasury_account(
    db: Session,
    *,
    currency: str = "GHS",
) -> PlatformTreasuryAccount:
    def _hydrate_from_legacy_wallets(
        account: PlatformTreasuryAccount,
    ) -> PlatformTreasuryAccount:
        vendor_liability_balance = round_money(
            db.scalar(
                select(
                    func.coalesce(
                        func.sum(
                            VendorWallet.available_balance
                            + VendorWallet.pending_withdrawal_balance
                        ),
                        0,
                    )
                )
            )
            or 0
        )
        commission_balance = round_money(
            db.scalar(select(func.coalesce(func.sum(VendorWallet.total_commission), 0))) or 0
        )
        if vendor_liability_balance <= 0 and commission_balance <= 0:
            return account

        account.vendor_liability_balance = vendor_liability_balance
        account.commission_balance = commission_balance
        account.current_balance = round_money(
            vendor_liability_balance + commission_balance
        )
        account.gross_collected_total = round_money(account.current_balance)
        return account

    account = db.scalar(
        select(PlatformTreasuryAccount).where(
            PlatformTreasuryAccount.currency == currency
        )
    )
    if account:
        if (
            account.current_balance == 0
            and account.vendor_liability_balance == 0
            and account.commission_balance == 0
            and not db.scalar(
                select(PlatformLedgerEntry.id).where(
                    PlatformLedgerEntry.treasury_account_id == account.id
                )
            )
        ):
            _hydrate_from_legacy_wallets(account)
        return account

    account = PlatformTreasuryAccount(currency=currency)
    _hydrate_from_legacy_wallets(account)
    db.add(account)
    db.flush()
    return account


def _create_ledger_entry(
    db: Session,
    account: PlatformTreasuryAccount,
    *,
    kind: str,
    direction: str,
    title: str,
    description: str | None,
    amount: float,
    order_id: uuid.UUID | None = None,
    payment_transaction_id: uuid.UUID | None = None,
    return_request_id: uuid.UUID | None = None,
    vendor_withdrawal_request_id: uuid.UUID | None = None,
    metadata: dict | None = None,
) -> PlatformLedgerEntry:
    entry = PlatformLedgerEntry(
        treasury_account_id=account.id,
        kind=kind,
        direction=direction,
        title=title,
        description=description,
        amount=round_money(amount),
        current_balance_after=round_money(account.current_balance),
        vendor_liability_balance_after=round_money(account.vendor_liability_balance),
        commission_balance_after=round_money(account.commission_balance),
        order_id=order_id,
        payment_transaction_id=payment_transaction_id,
        return_request_id=return_request_id,
        vendor_withdrawal_request_id=vendor_withdrawal_request_id,
        metadata_json=metadata,
    )
    db.add(entry)
    db.flush()
    return entry


def record_payment_collection(
    db: Session,
    order: Order,
    payment_transaction: PaymentTransaction,
    *,
    processor_fee_amount: float,
) -> PlatformTreasuryAccount:
    existing_entry = db.scalar(
        select(PlatformLedgerEntry.id).where(
            PlatformLedgerEntry.payment_transaction_id == payment_transaction.id,
            PlatformLedgerEntry.kind == "customer_payment_received",
        )
    )
    if existing_entry:
        return get_or_create_platform_treasury_account(db, currency=payment_transaction.currency)

    account = get_or_create_platform_treasury_account(db, currency=payment_transaction.currency)
    allocations = vendor_allocation_map(order)
    vendor_net_total = round_money(
        sum(allocation["net_amount"] for allocation in allocations.values())
    )
    commission_total = round_money(
        sum(allocation["commission_amount"] for allocation in allocations.values())
    )
    gross_amount = round_money(order.total_amount)
    fee_amount = round_money(processor_fee_amount)

    account.current_balance = round_money(account.current_balance + gross_amount)
    account.gross_collected_total = round_money(
        account.gross_collected_total + gross_amount
    )
    _create_ledger_entry(
        db,
        account,
        kind="customer_payment_received",
        direction="credit",
        title=f"Customer payment received for order #{order.order_number}",
        description="Paystack charge confirmed successfully.",
        amount=gross_amount,
        order_id=order.id,
        payment_transaction_id=payment_transaction.id,
        metadata={
            "payment_reference": payment_transaction.reference,
            "order_number": order.order_number,
        },
    )

    if fee_amount > 0:
        account.current_balance = round_money(account.current_balance - fee_amount)
        account.processor_fee_total = round_money(account.processor_fee_total + fee_amount)
        _create_ledger_entry(
            db,
            account,
            kind="processor_fee_recorded",
            direction="debit",
            title=f"Processor fee recorded for order #{order.order_number}",
            description="Paystack processor fee deducted from collected funds.",
            amount=fee_amount,
            order_id=order.id,
            payment_transaction_id=payment_transaction.id,
            metadata={
                "payment_reference": payment_transaction.reference,
                "order_number": order.order_number,
            },
        )

    if vendor_net_total > 0:
        account.vendor_liability_balance = round_money(
            account.vendor_liability_balance + vendor_net_total
        )
        _create_ledger_entry(
            db,
            account,
            kind="vendor_liability_reserved",
            direction="credit",
            title=f"Vendor settlement reserved for order #{order.order_number}",
            description="Vendor funds are now held in treasury until payout.",
            amount=vendor_net_total,
            order_id=order.id,
            payment_transaction_id=payment_transaction.id,
            metadata={
                "payment_reference": payment_transaction.reference,
                "vendor_count": len(allocations),
            },
        )

    if commission_total > 0:
        account.commission_balance = round_money(
            account.commission_balance + commission_total
        )
        _create_ledger_entry(
            db,
            account,
            kind="platform_commission_earned",
            direction="credit",
            title=f"Platform commission earned on order #{order.order_number}",
            description="ODOS commission recognized from the verified payment.",
            amount=commission_total,
            order_id=order.id,
            payment_transaction_id=payment_transaction.id,
            metadata={
                "payment_reference": payment_transaction.reference,
                "order_number": order.order_number,
            },
        )

    return account


def record_refund_adjustments(
    db: Session,
    request: ReturnRequest,
) -> PlatformTreasuryAccount | None:
    if request.status != "refunded":
        return None

    existing_entry = db.scalar(
        select(PlatformLedgerEntry.id).where(
            PlatformLedgerEntry.return_request_id == request.id,
            PlatformLedgerEntry.kind == "refund_cash_out",
        )
    )
    if existing_entry:
        return get_or_create_platform_treasury_account(db)

    order = request.order
    order_item = request.order_item
    if not order or not order_item:
        return None

    account = get_or_create_platform_treasury_account(db)
    breakdown = return_reversal_breakdown(order, order_item, request.quantity)
    refund_amount = round_money(request.refund_amount or 0)

    if refund_amount > 0:
        account.current_balance = round_money(account.current_balance - refund_amount)
        account.refunded_total = round_money(account.refunded_total + refund_amount)
        _create_ledger_entry(
            db,
            account,
            kind="refund_cash_out",
            direction="debit",
            title=f"Customer refund processed for order #{order.order_number}",
            description=f"{order_item.title} refund issued to customer.",
            amount=refund_amount,
            order_id=order.id,
            return_request_id=request.id,
            metadata={
                "product_id": order_item.product_id,
                "quantity": request.quantity,
            },
        )

    if breakdown["net_amount"] > 0:
        account.vendor_liability_balance = round_money(
            account.vendor_liability_balance - breakdown["net_amount"]
        )
        _create_ledger_entry(
            db,
            account,
            kind="vendor_liability_reversed",
            direction="debit",
            title=f"Vendor liability reversed for order #{order.order_number}",
            description=f"{order_item.title} vendor settlement reversed after refund.",
            amount=breakdown["net_amount"],
            order_id=order.id,
            return_request_id=request.id,
            metadata={
                "product_id": order_item.product_id,
                "quantity": request.quantity,
            },
        )

    if breakdown["commission_amount"] > 0:
        account.commission_balance = round_money(
            account.commission_balance - breakdown["commission_amount"]
        )
        _create_ledger_entry(
            db,
            account,
            kind="platform_commission_reversed",
            direction="debit",
            title=f"Platform commission reversed for order #{order.order_number}",
            description=f"{order_item.title} commission reversed after refund.",
            amount=breakdown["commission_amount"],
            order_id=order.id,
            return_request_id=request.id,
            metadata={
                "product_id": order_item.product_id,
                "quantity": request.quantity,
            },
        )

    total_refunded = round_money(
        sum(
            refunded_request.refund_amount or 0
            for refunded_request in order.return_requests
            if refunded_request.status == "refunded"
        )
    )
    if total_refunded > 0:
        order.payment_status = (
            "refunded"
            if total_refunded >= round_money(order.total_amount)
            else "partially_refunded"
        )
        order.refunded_at = request.resolved_at or request.reviewed_at
        if order.payment_transaction:
            order.payment_transaction.status = order.payment_status
            order.payment_transaction.refunded_at = order.refunded_at

    return account


def record_vendor_payout_paid(
    db: Session,
    withdrawal_request: VendorWithdrawalRequest,
) -> PlatformTreasuryAccount:
    existing_entry = db.scalar(
        select(PlatformLedgerEntry.id).where(
            PlatformLedgerEntry.vendor_withdrawal_request_id == withdrawal_request.id,
            PlatformLedgerEntry.kind == "vendor_payout_paid",
        )
    )
    if existing_entry:
        return get_or_create_platform_treasury_account(db)

    account = get_or_create_platform_treasury_account(db)
    amount = round_money(withdrawal_request.amount)
    if amount <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The payout amount must be greater than zero.",
        )
    if account.current_balance + 0.01 < amount:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Treasury cash balance is not sufficient for this payout.",
        )
    if account.vendor_liability_balance + 0.01 < amount:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Treasury vendor liability is lower than the requested payout.",
        )

    account.current_balance = round_money(account.current_balance - amount)
    account.vendor_liability_balance = round_money(
        account.vendor_liability_balance - amount
    )
    account.total_payouts_sent = round_money(account.total_payouts_sent + amount)
    _create_ledger_entry(
        db,
        account,
        kind="vendor_payout_paid",
        direction="debit",
        title="Vendor payout marked paid",
        description=f"Payout sent to {withdrawal_request.payout_account_name}.",
        amount=amount,
        vendor_withdrawal_request_id=withdrawal_request.id,
        metadata={
            "vendor_user_id": str(withdrawal_request.vendor_user_id),
            "payout_method_type": withdrawal_request.payout_method_type,
            "payout_provider": withdrawal_request.payout_provider,
        },
    )
    return account


def list_admin_payment_transactions(
    db: Session,
    current_user: User,
) -> list[AdminPaymentTransactionRead]:
    require_admin(current_user)
    transactions = list(
        db.scalars(
            select(PaymentTransaction)
            .options(
                selectinload(PaymentTransaction.order),
                selectinload(PaymentTransaction.user),
            )
            .order_by(PaymentTransaction.created_at.desc())
        ).all()
    )
    return [
        AdminPaymentTransactionRead(
            id=transaction.id,
            order_id=transaction.order_id,
            order_number=transaction.order.order_number,
            user_id=transaction.user_id,
            customer_email=transaction.user.email,
            provider=transaction.provider,
            reference=transaction.reference,
            amount=round_money(transaction.order.total_amount),
            currency=transaction.currency,
            status=transaction.status,
            preferred_channel=transaction.preferred_channel,
            processor_fee_amount=amount_from_subunit(transaction.processor_fee_subunit),
            gateway_response=transaction.gateway_response,
            provider_transaction_id=transaction.provider_transaction_id,
            paid_at=transaction.paid_at,
            verified_at=transaction.verified_at,
            created_at=transaction.created_at,
            updated_at=transaction.updated_at,
        )
        for transaction in transactions
        if transaction.order and transaction.user
    ]


def list_admin_platform_ledger_entries(
    db: Session,
    current_user: User,
) -> list[AdminPlatformLedgerEntryRead]:
    require_admin(current_user)
    entries = list(
        db.scalars(
            select(PlatformLedgerEntry)
            .options(
                selectinload(PlatformLedgerEntry.order),
                selectinload(PlatformLedgerEntry.payment_transaction),
            )
            .order_by(PlatformLedgerEntry.created_at.desc())
        ).all()
    )
    return [
        AdminPlatformLedgerEntryRead(
            id=entry.id,
            kind=entry.kind,
            direction=entry.direction,
            title=entry.title,
            description=entry.description,
            amount=round_money(entry.amount),
            current_balance_after=round_money(entry.current_balance_after),
            vendor_liability_balance_after=round_money(entry.vendor_liability_balance_after),
            commission_balance_after=round_money(entry.commission_balance_after),
            order_id=entry.order_id,
            order_number=entry.order.order_number if entry.order else None,
            payment_transaction_id=entry.payment_transaction_id,
            payment_reference=(
                entry.payment_transaction.reference if entry.payment_transaction else None
            ),
            return_request_id=entry.return_request_id,
            vendor_withdrawal_request_id=entry.vendor_withdrawal_request_id,
            created_at=entry.created_at,
        )
        for entry in entries
    ]


def get_admin_finance_overview(
    db: Session,
    current_user: User,
) -> AdminFinanceOverviewRead:
    require_admin(current_user)
    account = get_or_create_platform_treasury_account(db)
    pending_withdrawal_total = round_money(
        db.scalar(
            select(func.coalesce(func.sum(VendorWithdrawalRequest.amount), 0)).where(
                VendorWithdrawalRequest.status == "pending"
            )
        )
        or 0
    )
    approved_withdrawal_total = round_money(
        db.scalar(
            select(func.coalesce(func.sum(VendorWithdrawalRequest.amount), 0)).where(
                VendorWithdrawalRequest.status == "approved"
            )
        )
        or 0
    )
    paid_order_count = int(
        db.scalar(select(func.count(Order.id)).where(Order.payment_status == "paid")) or 0
    )
    paid_order_volume = round_money(
        db.scalar(
            select(func.coalesce(func.sum(Order.total_amount), 0)).where(
                Order.payment_status.in_(["paid", "partially_refunded", "refunded"])
            )
        )
        or 0
    )
    return AdminFinanceOverviewRead(
        currency=account.currency,
        current_balance=round_money(account.current_balance),
        vendor_liability_balance=round_money(account.vendor_liability_balance),
        commission_balance=round_money(account.commission_balance),
        gross_collected_total=round_money(account.gross_collected_total),
        processor_fee_total=round_money(account.processor_fee_total),
        refunded_total=round_money(account.refunded_total),
        total_payouts_sent=round_money(account.total_payouts_sent),
        pending_withdrawal_total=pending_withdrawal_total,
        approved_withdrawal_total=approved_withdrawal_total,
        paid_order_count=paid_order_count,
        paid_order_volume=paid_order_volume,
    )
